//! Live HLS inside the window. Rune's own `Player` reads an asset's video tracks before it
//! plays, and an HLS asset has none, so live streams fail there. This drives AVPlayer
//! directly: an AVPlayerItemVideoOutput hands IOSurface frames to a pull thread, which
//! pushes them into a `NativeSurface` without a copy. AVPlayer plays the sound itself.

use rune::NativeSurface;
use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

#[derive(Clone, Debug, PartialEq)]
pub enum Phase {
    /// Asked to play; no frame yet.
    Loading,
    Playing,
    /// Frames stopped arriving for a while; AVPlayer may still recover.
    Stalled,
    Failed(String),
}

#[cfg(target_os = "macos")]
mod mac {
    use super::*;
    use objc2::rc::Retained;
    use objc2::runtime::AnyObject;
    use objc2::{AnyThread, MainThreadMarker};
    use objc2_av_foundation::{AVPlayer, AVPlayerItem, AVPlayerItemStatus, AVPlayerItemVideoOutput, AVURLAsset};
    use objc2_core_foundation::{CFBoolean, CFDictionary, CFNumber, CFRetained, CFString, CFType};
    use objc2_core_video::{
        CVGetCurrentHostTime, CVGetHostClockFrequency, kCVPixelBufferIOSurfacePropertiesKey, kCVPixelBufferMetalCompatibilityKey,
        kCVPixelBufferPixelFormatTypeKey, kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
    };
    use objc2_foundation::{NSDictionary, NSString, NSURL};
    use rune::native_surface::macos::PixelBuffer;

    /// Broadcast SDR is tagged SMPTE 170M or BT.709, and the two share one transfer curve.
    /// Rune draws BT.709 and sRGB, so relabel any SDR transfer as BT.709; HDR keeps its tag
    /// and is refused.
    #[allow(deprecated)]
    fn sdr_as_709(buffer: &objc2_core_video::CVBuffer) {
        use objc2_core_video::{
            CVAttachmentMode, kCVImageBufferTransferFunctionKey, kCVImageBufferTransferFunction_ITU_R_709_2,
            kCVImageBufferTransferFunction_ITU_R_2100_HLG, kCVImageBufferTransferFunction_SMPTE_ST_2084_PQ,
        };
        unsafe {
            let tag = buffer.get_attachment(kCVImageBufferTransferFunctionKey, std::ptr::null_mut());
            let hdr = tag.as_deref().is_some_and(|t| {
                let t: &CFType = t;
                t == AsRef::<CFType>::as_ref(kCVImageBufferTransferFunction_SMPTE_ST_2084_PQ)
                    || t == AsRef::<CFType>::as_ref(kCVImageBufferTransferFunction_ITU_R_2100_HLG)
            });
            if !hdr {
                buffer.set_attachment(kCVImageBufferTransferFunctionKey, kCVImageBufferTransferFunction_ITU_R_709_2.as_ref(), CVAttachmentMode::ShouldPropagate);
            }
        }
    }

    struct Output(Retained<AVPlayerItemVideoOutput>);
    // AVPlayerItemVideoOutput's frame queries are made from a display-link thread in Apple's own samples.
    unsafe impl Send for Output {}

    pub struct Stream {
        player: Retained<AVPlayer>,
        item: Retained<AVPlayerItem>,
        stop: Arc<AtomicBool>,
        frames: Arc<AtomicU64>,
        last_frame: Arc<std::sync::Mutex<Option<Instant>>>,
        started: Instant,
        pub surface: NativeSurface,
    }

    impl Stream {
        pub fn open(url: &str, headers: &HashMap<String, String>, waker: rune::Waker) -> Result<Stream, String> {
            let mtm = MainThreadMarker::new().ok_or("video must start on the main thread")?;
            let url = NSURL::URLWithString(&NSString::from_str(url)).ok_or_else(|| format!("not a URL: {url}"))?;
            let asset = unsafe {
                if headers.is_empty() {
                    AVURLAsset::URLAssetWithURL_options(&url, None)
                } else {
                    let names: Vec<_> = headers.keys().map(|k| NSString::from_str(k)).collect();
                    let values: Vec<_> = headers.values().map(|v| NSString::from_str(v)).collect();
                    let fields = NSDictionary::from_slices(&names.iter().map(|n| &**n).collect::<Vec<_>>(), &values.iter().map(|v| &**v).collect::<Vec<_>>());
                    let key = NSString::from_str("AVURLAssetHTTPHeaderFieldsKey");
                    let fields: &AnyObject = &fields;
                    let options = NSDictionary::<NSString, AnyObject>::from_slices(&[&*key], &[fields]);
                    AVURLAsset::URLAssetWithURL_options(&url, Some(&options))
                }
            };
            let item = unsafe { AVPlayerItem::playerItemWithAsset(&asset, mtm) };
            let empty = CFDictionary::<CFString, CFType>::from_slices(&[], &[]);
            let format = CFNumber::new_i32(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange as i32);
            let attributes = unsafe {
                CFDictionary::<CFString, CFType>::from_slices(
                    &[kCVPixelBufferPixelFormatTypeKey, kCVPixelBufferIOSurfacePropertiesKey, kCVPixelBufferMetalCompatibilityKey],
                    &[format.as_ref(), AsRef::<CFType>::as_ref(&*empty), CFBoolean::new(true).as_ref()],
                )
            };
            let attributes = unsafe { &*(CFRetained::as_ptr(&attributes).as_ptr() as *const NSDictionary<NSString, AnyObject>) };
            let output = unsafe {
                let output = AVPlayerItemVideoOutput::initWithPixelBufferAttributes(AVPlayerItemVideoOutput::alloc(), Some(attributes));
                output.setSuppressesPlayerRendering(true);
                item.addOutput(&output);
                output
            };
            let player = unsafe { AVPlayer::playerWithPlayerItem(Some(&item), mtm) };
            unsafe { player.play() };

            let surface = NativeSurface::new();
            surface.set_waker(waker);
            let (stop, frames, last_frame) = (Arc::new(AtomicBool::new(false)), Arc::new(AtomicU64::new(0)), Arc::new(std::sync::Mutex::new(None)));
            let output = Output(output);
            let (s, f, l, target) = (stop.clone(), frames.clone(), last_frame.clone(), surface.clone());
            std::thread::Builder::new()
                .name("video-pull".into())
                .spawn(move || {
                    let output = output;
                    while !s.load(Ordering::Relaxed) {
                        let host = CVGetCurrentHostTime() as f64 / CVGetHostClockFrequency();
                        let time = unsafe { output.0.itemTimeForHostTime(host) };
                        let mut shown = time;
                        if unsafe { output.0.hasNewPixelBufferForItemTime(time) }
                            && let Some(buffer) = unsafe { output.0.copyPixelBufferForItemTime_itemTimeForDisplay(time, &mut shown) }
                        {
                            let id = f.fetch_add(1, Ordering::Relaxed) + 1;
                            sdr_as_709(&buffer);
                            // SAFETY: the output hands out finished buffers it no longer writes.
                            if let Ok(frame) = unsafe { PixelBuffer::from_raw(Retained::as_ptr(&buffer) as *mut _) }
                                && let Err(why) = target.push(frame, id) && id % 100 == 1 {
                                    eprintln!("video frame rejected: {why}");
                                }
                            *l.lock().unwrap() = Some(Instant::now());
                        }
                        std::thread::sleep(Duration::from_millis(4));
                    }
                })
                .map_err(|e| e.to_string())?;
            Ok(Stream { player, item, stop, frames, last_frame, started: Instant::now(), surface })
        }

        pub fn phase(&self) -> Phase {
            if unsafe { self.item.status() } == AVPlayerItemStatus::Failed {
                let why = unsafe { self.item.error() }.map(|e| e.localizedDescription().to_string());
                return Phase::Failed(why.unwrap_or_else(|| "the stream would not play".into()));
            }
            let last = *self.last_frame.lock().unwrap();
            match last {
                None if self.started.elapsed() > Duration::from_secs(25) => Phase::Failed("no picture after 25 seconds".into()),
                None => Phase::Loading,
                Some(at) if at.elapsed() > Duration::from_secs(4) => Phase::Stalled,
                Some(_) => Phase::Playing,
            }
        }

        pub fn frames(&self) -> u64 {
            self.frames.load(Ordering::Relaxed)
        }

        pub fn size(&self) -> Option<(f32, f32)> {
            let size = unsafe { self.item.presentationSize() };
            (size.width > 1.0 && size.height > 1.0).then_some((size.width as f32, size.height as f32))
        }

        pub fn set_paused(&self, paused: bool) {
            unsafe { if paused { self.player.pause() } else { self.player.play() } }
        }

        pub fn set_muted(&self, muted: bool) {
            unsafe { self.player.setMuted(muted) };
        }
    }

    impl Drop for Stream {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::Relaxed);
            unsafe { self.player.pause() };
            self.surface.clear();
        }
    }
}

#[cfg(target_os = "macos")]
pub use mac::Stream;

/// Wait by running the main run loop, where AVPlayer delivers its loading callbacks. Only
/// headless runs need this: a window's event loop runs it already.
pub fn idle(time: Duration) {
    #[cfg(target_os = "macos")]
    {
        use objc2_core_foundation::{CFRunLoop, kCFRunLoopDefaultMode};
        let end = Instant::now() + time;
        while Instant::now() < end {
            CFRunLoop::run_in_mode(unsafe { kCFRunLoopDefaultMode }, (end - Instant::now()).as_secs_f64(), true);
        }
    }
    #[cfg(not(target_os = "macos"))]
    std::thread::sleep(time);
}

#[cfg(not(target_os = "macos"))]
pub struct Stream {
    pub surface: NativeSurface,
}

#[cfg(not(target_os = "macos"))]
impl Stream {
    pub fn open(_: &str, _: &HashMap<String, String>, _: rune::Waker) -> Result<Stream, String> {
        Err("in-window video is macOS only; press o to open mpv".into())
    }
    pub fn phase(&self) -> Phase { Phase::Failed("unsupported".into()) }
    pub fn frames(&self) -> u64 { 0 }
    pub fn size(&self) -> Option<(f32, f32)> { None }
    pub fn set_paused(&self, _: bool) {}
    pub fn set_muted(&self, _: bool) {}
}
