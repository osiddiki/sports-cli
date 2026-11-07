//! Sports CLI in a native window. Python (`sports-cli --web`) owns every screen and key, as it
//! does for the terminal; this draws its screens with Rune and plays the chosen stream inside
//! the window. See docs/native-ui.md for the design and verification/native for the model.

mod server;
mod ui;
mod video;

use rune::{AppSpec, Cx, El, Key, KeyEvent, Tx, Waker};
use server::{Event, Reply, Request, Server, View};
use std::sync::mpsc::{Receiver, TryRecvError, channel};
use std::time::Duration;
use video::{Phase, Stream};

static START: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
const POLL: Duration = Duration::from_millis(150);
const CHROME_SECONDS: f64 = 2.6;

/// What sits over the screens. One at a time, and keys go to it first.
pub enum Overlay {
    None,
    Prompt { label: String, key: String, typed: String },
    Inspect { url: String },
}

#[derive(Clone, Copy, PartialEq)]
pub enum Stage {
    /// The video fills the window.
    Full,
    /// The video rides in a corner while you browse.
    Mini,
}

pub struct Playing {
    pub stream: Stream,
    pub title: String,
    pub command: Vec<String>,
    pub phase: Phase,
    pub stage: Stage,
    pub paused: bool,
    pub muted: bool,
    /// The app hears how this player ended exactly once, so a failed source is hidden once.
    reported: bool,
}

pub struct State {
    boot: Option<Receiver<Result<Server, String>>>,
    server: Option<Server>,
    waker: Option<Waker>,
    pub view: View,
    /// Requests sent and not yet answered. A poll goes out only when this is 0.
    inflight: usize,
    pub error: Option<String>,
    pub overlay: Overlay,
    pub playing: Option<Playing>,
    pub now: f64,
    pub chrome_until: f64,
    pub phone: Option<String>,
    /// +1 when the last screen change went deeper, -1 when it came back.
    pub direction: f32,
}

fn init() -> State {
    State {
        boot: None, server: None, waker: None, view: View { mode: "boot".into(), ..View::default() }, inflight: 0,
        error: None, overlay: Overlay::None, playing: None, now: 0.0, chrome_until: 0.0, phone: None, direction: 1.0,
    }
}

fn setup(s: &mut State, waker: Waker) {
    let (tx, rx) = channel();
    let wake = waker.clone();
    std::thread::spawn(move || {
        let _ = tx.send(Server::start(wake.clone()));
        wake();
    });
    s.boot = Some(rx);
    s.waker = Some(waker);
}

pub fn depth(mode: &str) -> i32 {
    match mode {
        "home" | "boot" => 0,
        "leagues" => 1,
        "replay_sources" => 3,
        "feeds" => 4,
        _ => 2,
    }
}

impl State {
    fn send(&mut self, request: Request) {
        if let Some(server) = &self.server {
            server.send(request);
            self.inflight += 1;
        }
    }

    /// The single place the player ends: report it once, then drop it.
    fn stop(&mut self, code: i32) {
        if let Some(mut playing) = self.playing.take()
            && !playing.reported {
                playing.reported = true;
                self.send(Request::Exit(code));
            }
    }

    fn apply(&mut self, next: View, tx: &mut Tx) {
        for event in &next.events {
            match event {
                Event::Quit => tx.quit(),
                Event::Prompt { label, initial, key } => {
                    self.overlay = Overlay::Prompt { label: label.clone(), key: key.clone(), typed: initial.clone() };
                }
                Event::Inspect { url } => self.overlay = Overlay::Inspect { url: url.clone() },
                Event::Play { title, url, headers, command } => {
                    // A new choice replaces the old player; the app already moved on, so it hears nothing.
                    if let Some(mut old) = self.playing.take() { old.reported = true }
                    let waker = self.waker.clone().unwrap_or_else(|| std::sync::Arc::new(|| {}));
                    match Stream::open(url, headers, waker) {
                        Ok(stream) => {
                            self.playing = Some(Playing {
                                stream, title: title.clone(), command: command.clone(), phase: Phase::Loading,
                                stage: Stage::Full, paused: false, muted: false, reported: false,
                            });
                            self.chrome_until = self.now + CHROME_SECONDS;
                        }
                        Err(why) => {
                            self.error = Some(why);
                            self.send(Request::Exit(1));
                        }
                    }
                }
            }
        }
        if next.seq > self.view.seq {
            let before = (depth(&self.view.mode), self.view.selected, self.view.mode.clone());
            self.direction = if depth(&next.mode) < before.0 { -1.0 } else { 1.0 };
            if next.selected >= 0 && (next.selected != before.1 || next.mode != before.2) {
                tx.scroll_into_view(format!("row-{}", next.selected));
            }
            self.view = next;
        }
    }

    fn open_in_mpv(&mut self) {
        if let Some(playing) = &self.playing
            && let Some((program, args)) = playing.command.split_first()
        {
            let _ = std::process::Command::new(program).args(args).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn();
        }
        // mpv takes over; the app does not hide a source that played.
        if let Some(mut playing) = self.playing.take() { playing.reported = true; self.send(Request::Exit(0)) }
    }
}

fn poll(s: &mut State, now: f64, tx: &mut Tx) -> bool {
    s.now = now;
    let mut changed = false;
    // Headless scripts run on a virtual clock; this makes each frame wait for the app's answer.
    let sync = std::env::var_os("SPORTS_NATIVE_SYNC").is_some();
    if sync {
        // Hold the virtual clock to the wall clock, so background loading has real time to finish.
        let start = *START.get_or_init(std::time::Instant::now);
        let behind = Duration::from_secs_f64(now.max(0.0)).saturating_sub(start.elapsed());
        video::idle(behind);
    }
    if let Some(boot) = &s.boot {
        let answer = if sync { boot.recv_timeout(Duration::from_secs(30)).map_err(|_| TryRecvError::Empty) } else { boot.try_recv() };
        match answer {
            Ok(Ok(server)) => {
                s.phone = server.phone.clone();
                s.server = Some(server);
                s.boot = None;
                s.send(Request::View);
                changed = true;
            }
            Ok(Err(why)) => { s.error = Some(why); s.boot = None; changed = true }
            Err(TryRecvError::Empty) => {}
            Err(TryRecvError::Disconnected) => s.boot = None,
        }
    }
    let mut replies: Vec<Reply> = Vec::new();
    if let Some(server) = &s.server {
        if sync {
            for _ in 0..s.inflight { replies.extend(server.replies.recv_timeout(Duration::from_secs(20)).ok()) }
        }
        replies.extend(server.replies.try_iter());
    }
    for reply in replies {
        s.inflight = s.inflight.saturating_sub(1);
        match reply {
            Reply::View(next) => { s.error = None; s.apply(*next, tx) }
            Reply::Error(why) => s.error = Some(why),
        }
        changed = true;
    }
    if let Some(playing) = &mut s.playing {
        let phase = playing.stream.phase();
        if std::env::var_os("SPORTS_NATIVE_LOG").is_some() {
            let surface = &playing.stream.surface;
            eprintln!("video t={now:.1} phase={phase:?} frames={} draws={:?} error={:?}", playing.stream.frames(), surface.counts(), surface.last_error());
        }
        if phase != playing.phase {
            playing.phase = phase;
            changed = true;
        }
    }
    if matches!(s.playing.as_ref().map(|p| &p.phase), Some(Phase::Failed(_))) {
        let failed = s.playing.as_mut().unwrap();
        if !failed.reported {
            failed.reported = true;
            s.send(Request::Exit(1));
        }
    }
    if s.server.is_some() && s.inflight == 0 {
        s.send(Request::View);
    }
    tx.wake_after(POLL);
    if s.playing.as_ref().is_some_and(|p| p.stage == Stage::Full) && now < s.chrome_until + 0.5 {
        tx.wake_after(Duration::from_millis(250));
        changed = true;
    }
    changed
}

/// The name the Python app gives a key (remote.CODES), or a typed character.
fn key_name(e: &KeyEvent) -> Option<String> {
    Some(match &e.key {
        Key::Up => "up".into(),
        Key::Down => "down".into(),
        Key::Left => "left".into(),
        Key::Right => "right".into(),
        Key::Enter => "enter".into(),
        Key::Escape => "escape".into(),
        Key::Backspace => "backspace".into(),
        Key::Tab => "tab".into(),
        Key::Text(t) if e.mods.ctrl && t.len() == 1 => format!("ctrl-{}", t.to_ascii_lowercase()),
        Key::Text(t) if t.chars().count() == 1 && !e.mods.cmd => t.clone(),
        _ => return None,
    })
}

fn on_key(s: &mut State, e: &KeyEvent, tx: &mut Tx) -> bool {
    match &mut s.overlay {
        Overlay::Prompt { key, typed, .. } => {
            match &e.key {
                Key::Enter => {
                    let (key, typed) = (key.clone(), typed.clone());
                    s.overlay = Overlay::None;
                    s.send(Request::Key(key, Some(typed)));
                }
                Key::Escape => s.overlay = Overlay::None,
                Key::Backspace => { typed.pop(); }
                Key::Text(t) if !e.mods.cmd => typed.push_str(t),
                _ => return false,
            }
            return true;
        }
        Overlay::Inspect { url } => {
            if e.is_command('c') { tx.copy(url.clone()) } else { s.overlay = Overlay::None }
            return true;
        }
        Overlay::None => {}
    }
    if s.playing.is_some() && e.is_command('.') {
        s.stop(0);
        return true;
    }
    if let Some(playing) = &mut s.playing {
        if playing.stage == Stage::Full {
            s.chrome_until = s.now + CHROME_SECONDS;
            match &e.key {
                Key::Escape | Key::Tab => playing.stage = Stage::Mini,
                Key::Text(t) if t == " " => { playing.paused = !playing.paused; playing.stream.set_paused(playing.paused) }
                Key::Text(t) if t == "m" => { playing.muted = !playing.muted; playing.stream.set_muted(playing.muted) }
                Key::Text(t) if t == "o" => s.open_in_mpv(),
                Key::Backspace => s.stop(0),
                _ => {}
            }
            return true;
        }
        if matches!(e.key, Key::Tab) && s.view.mode != "search" {
            playing.stage = Stage::Full;
            s.chrome_until = s.now + CHROME_SECONDS;
            return true;
        }
    }
    let Some(name) = key_name(e) else { return false };
    s.send(Request::Key(name, None));
    true
}

fn view(s: &State, cx: &mut Cx<State>) -> El {
    ui::root(s, cx)
}

fn main() {
    rune::prewarm_gpu();
    rune::run(AppSpec {
        setup: Some(setup),
        poll: Some(poll),
        on_key: Some(on_key),
        min_size: Some((760.0, 520.0)),
        ..AppSpec::new("Sports CLI", (1120.0, 720.0), init, view)
    });
}
