//! The replay playhead. Same rules as sportscli/timeline.py: a seek lands on a
//! time, and the only label is a period name. A name that looks like a score is
//! dropped, so the bar cannot show one.

use crate::server::{Chapter, Timeline};

pub const STEP: f64 = 10.0;
pub const MINUTE: f64 = 60.0;

pub enum Seek {
    By(f64),
    To(f64),
    Chapter(i32),
}

pub fn apply(timeline: &Timeline, playhead: f64, seek: Seek) -> (f64, Option<String>) {
    if !timeline.seekable || !timeline.duration.is_finite() || timeline.duration <= 0.0 {
        return (playhead, None);
    }
    let landing = match seek {
        Seek::To(seconds) => seconds,
        Seek::By(delta) => playhead + delta,
        Seek::Chapter(direction) => chapter_landing(&timeline.chapters, playhead, direction).unwrap_or(playhead),
    };
    let landing = landing.clamp(0.0, timeline.duration);
    let name = chapter_at(&timeline.chapters, landing).and_then(|name| safe_name(&name).map(str::to_string));
    (landing, name)
}

pub fn chapter_at(chapters: &[Chapter], playhead: f64) -> Option<String> {
    chapters.iter().filter(|chapter| chapter.start <= playhead + 0.001).max_by(|a, b| a.start.total_cmp(&b.start)).map(|chapter| chapter.name.clone())
}

/// `add chapter` steps the index by one, including from the middle of a period.
fn chapter_landing(chapters: &[Chapter], playhead: f64, direction: i32) -> Option<f64> {
    if chapters.is_empty() || direction == 0 {
        return None;
    }
    let current = chapters.iter().rposition(|chapter| chapter.start <= playhead + 0.001).unwrap_or(0);
    let next = current as i32 + direction;
    chapters.get(next as usize).map(|chapter| chapter.start)
}

/// Period labels stay. A cue titled like a final score does not.
pub fn safe_name(name: &str) -> Option<&str> {
    let name = name.trim();
    if name.is_empty() {
        return None;
    }
    let score = name.chars().any(|c| c.is_ascii_digit()) && (name.contains('-') || name.contains('–') || name.contains('—'));
    if score { None } else { Some(name) }
}

pub fn clock(seconds: f64) -> String {
    if !seconds.is_finite() || seconds < 0.0 {
        return "0:00".into();
    }
    let total = seconds.round() as u64;
    let (hours, minutes, secs) = (total / 3600, (total % 3600) / 60, total % 60);
    if hours > 0 {
        format!("{hours}:{minutes:02}:{secs:02}")
    } else {
        format!("{minutes}:{secs:02}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn game() -> Timeline {
        Timeline {
            duration: 4800.0,
            seekable: true,
            chapters: vec![
                Chapter { name: "Q1".into(), start: 0.0 },
                Chapter { name: "Q2".into(), start: 1200.0 },
                Chapter { name: "Q3".into(), start: 2400.0 },
                Chapter { name: "Q4".into(), start: 3600.0 },
            ],
        }
    }

    #[test]
    fn a_seek_moves_the_playhead_and_does_not_expose_a_score() {
        let timeline = game();
        let (playhead, chapter) = apply(&timeline, 30.0, Seek::By(STEP));
        assert_eq!(playhead, 40.0, "ten seconds forward");
        assert_eq!(chapter.as_deref(), Some("Q1"));
        let (playhead, _) = apply(&timeline, playhead, Seek::By(MINUTE));
        assert_eq!(playhead, 100.0, "a minute forward");
        let shown = format!("{playhead} {chapter:?} {}", clock(playhead));
        assert!(!shown.to_ascii_lowercase().contains("score"));
        assert!(safe_name("108-99").is_none(), "a score-shaped cue is not a chapter name");
        assert_eq!(safe_name("Q3"), Some("Q3"));
    }
}
