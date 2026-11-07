//! Sports CLI in a native window. Python (`sports-cli --web`) owns every screen and key, as it
//! does for the terminal; this draws its screens with Rune and plays the chosen stream inside
//! the window. See docs/native-ui.md for the design and verification/native for the model.

mod server;
mod ui;
mod video;

use rune::{AppSpec, Cx, El, Key, KeyEvent, Mods, Tx, Waker};
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
    /// Replies already queued when a click's keys were sent, then those keys.
    replies_before_click: usize,
    click_keys_left: usize,
    /// The frame that finishes a click still has the second press queued behind the replies.
    press_held: bool,
    pub error: Option<String>,
    pub overlay: Overlay,
    pub playing: Option<Playing>,
    pub now: f64,
    pub chrome_until: f64,
    pub phone: Option<String>,
    /// +1 when the last screen change went deeper, -1 when it came back.
    pub direction: f32,
    #[cfg(test)]
    sent_keys: Vec<String>,
}

fn init() -> State {
    State {
        boot: None, server: None, waker: None, view: View { mode: "boot".into(), ..View::default() }, inflight: 0,
        replies_before_click: 0, click_keys_left: 0, press_held: false,
        error: None, overlay: Overlay::None, playing: None, now: 0.0, chrome_until: 0.0, phone: None, direction: 1.0,
        #[cfg(test)]
        sent_keys: Vec::new(),
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
    fn press_row(&mut self, index: usize) {
        if self.click_keys_left > 0 || self.press_held { return }
        if !matches!(self.overlay, Overlay::None) { return }
        if self.playing.as_ref().is_some_and(|p| p.stage == Stage::Full) { return }
        let keys = keys_for_row(&self.view, index);
        if keys.is_empty() { return }
        self.replies_before_click = self.inflight;
        self.click_keys_left = keys.len();
        for key in keys {
            self.send(Request::Key(key, None));
        }
    }

    fn note_reply(&mut self) {
        if self.replies_before_click > 0 {
            self.replies_before_click -= 1;
        } else if self.click_keys_left > 0 {
            self.click_keys_left -= 1;
            if self.click_keys_left == 0 { self.press_held = true; }
        }
    }

    fn send(&mut self, request: Request) {
        #[cfg(test)]
        if let Request::Key(ref name, _) = request {
            self.sent_keys.push(name.clone());
        }
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
    if s.click_keys_left == 0 { s.press_held = false; }
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
        s.note_reply();
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

fn keys_for_row(view: &View, index: usize) -> Vec<String> {
    if !view.rows.get(index).is_some_and(|row| row.pick) { return Vec::new() }
    let target = list_cursor(view, index);
    let current = if view.selected < 0 { 0 } else { list_cursor(view, view.selected.max(0) as usize) };
    let mut keys = Vec::new();
    let delta = target - current;
    let step = if delta > 0 { "down" } else { "up" };
    for _ in 0..delta.abs() { keys.push(step.to_string()) }
    keys.push("enter".into());
    keys
}

fn list_cursor(view: &View, visual: usize) -> i64 {
    view.rows.get(visual).and_then(|row| row.cursor).unwrap_or(visual as i64)
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
        // Home's own Tab moves to the sections line. Its footer says so, and the corner player must not take the key.
        if matches!(e.key, Key::Tab) && !matches!(s.view.mode.as_str(), "search" | "home") {
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

#[cfg(test)]
mod click {
    use super::*;
    use rune::{Env, Runtime};
    use server::Row;

    #[derive(Clone, Copy)]
    enum Slot { Result(i64), Heading }

    fn results(n: usize) -> Vec<Slot> {
        (0..n).map(|i| Slot::Result(i as i64)).collect()
    }

    fn rows(slots: &[Slot]) -> Vec<Row> {
        slots.iter().enumerate().map(|(i, slot)| {
            let (pick, cursor) = match slot {
                Slot::Result(cursor) => (true, Some(*cursor)),
                Slot::Heading => (false, None),
            };
            Row {
                text: format!("row {i}"),
                cells: vec![format!("{i}"), format!("Row {i}"), String::new()],
                pick, cursor,
                style: if pick { String::new() } else { "heading".into() },
                ..Row::default()
            }
        }).collect()
    }

    fn screen(mode: &str, slots: &[Slot], selected: i64) -> State {
        let mut state = init();
        state.view.mode = mode.into();
        state.view.title = "Home".into();
        state.view.rows = rows(slots);
        state.view.selected = selected;
        state
    }

    fn opened_row(mode: &str, slots: &[Slot], selected: i64, keys: &[String]) -> Option<i64> {
        let cursor_of = |visual: usize| match slots.get(visual) {
            Some(Slot::Result(cursor)) => *cursor,
            _ => visual as i64,
        };
        let len = slots.iter().filter_map(|slot| match slot { Slot::Result(cursor) => Some(*cursor), Slot::Heading => None }).max().map(|cursor| cursor + 1).unwrap_or(0);
        if len == 0 { return None }
        let mut cursor = (if selected < 0 { 0 } else { cursor_of(selected as usize) }).clamp(0, len - 1);
        let mut opened = None;
        for key in keys {
            match key.as_str() {
                "up" => cursor = (cursor - 1).max(0),
                "down" => cursor = (cursor + 1).min(len - 1),
                "enter" => opened = Some(cursor),
                digit if mode != "search" && digit.len() == 1 => {
                    let Some(n) = digit.chars().next().and_then(|c| c.to_digit(10)) else { continue };
                    if (1..=9).contains(&n) && (n as i64) <= len {
                        cursor = n as i64 - 1;
                        opened = Some(cursor);
                    }
                }
                _ => {}
            }
        }
        opened
    }

    fn tap(state: State, key: &str) -> Vec<String> {
        tap_in(state, key, (1120.0, 720.0))
    }

    fn tap_in(state: State, key: &str, size: (f32, f32)) -> Vec<String> {
        let mut rt = Runtime::new(state, view, size, Env::new(true, 1.0));
        rt.frame(0.0);
        let (x, y) = rt.find(key).unwrap_or_else(|| panic!("{key} is not on screen"));
        rt.pointer_move(x, y);
        rt.pointer_down(x, y);
        rt.pointer_up(x, y);
        rt.frame(1.0 / 60.0);
        rt.state.sent_keys
    }

    fn click_at(rt: &mut Runtime<State>, key: &str) {
        let (x, y) = rt.find(key).unwrap_or_else(|| panic!("{key} is not on screen"));
        rt.pointer_move(x, y);
        rt.pointer_down(x, y);
        rt.pointer_up(x, y);
        rt.frame(1.0 / 60.0);
    }

    fn runtime(state: State) -> Runtime<State> {
        let mut rt = Runtime::new(state, view, (1120.0, 720.0), Env::new(true, 1.0));
        rt.frame(0.0);
        rt
    }

    #[test]
    fn a_pointer_on_a_row_selects_that_row() {
        let slots = results(4);
        let sent = tap(screen("home", &slots, 0), "row-2");
        assert_eq!(opened_row("home", &slots, 0, &sent), Some(2), "a pointer on row 2 selects that row, keys were {sent:?}");
    }

    #[test]
    fn a_pointer_on_a_search_row_selects_that_result() {
        let slots = &[Slot::Result(0), Slot::Heading, Slot::Result(3), Slot::Heading, Slot::Result(1), Slot::Result(2)];
        let sent = tap(screen("search", slots, 0), "row-2");
        assert_eq!(opened_row("search", slots, 0, &sent), Some(3), "a pointer on result 3 selects it, keys were {sent:?}");
    }

    #[test]
    fn a_pointer_on_a_heading_selects_nothing() {
        let slots = &[Slot::Result(0), Slot::Heading, Slot::Result(2)];
        let sent = tap(screen("home", slots, 0), "row-1");
        assert!(sent.is_empty(), "a heading is not a row you can open, keys were {sent:?}");
    }

    #[test]
    fn a_pointer_above_the_selection_selects_that_row() {
        let slots = results(4);
        let sent = tap(screen("channels", &slots, 3), "row-1");
        assert_eq!(opened_row("channels", &slots, 3, &sent), Some(1), "a pointer above the highlight selects that row, keys were {sent:?}");
    }

    /// Home as sportscli/remote.py describes it: headings, tonight's games, then the sections.
    fn tonight() -> State {
        let heading = |text: &str| Row { text: text.into(), style: "heading".into(), ..Row::default() };
        let game = |cursor: i64, title: &str, when: &str, style: &str, league: &str| Row {
            text: title.into(), cells: vec![league.to_uppercase(), when.into(), title.into()], detail: "ESPN".into(),
            style: style.into(), pick: true, cursor: Some(cursor), league: league.into(),
        };
        let section = |cursor: i64, label: &str| Row { text: label.into(), style: "section".into(), pick: true, cursor: Some(cursor), ..Row::default() };
        let mut state = init();
        state.view = View {
            mode: "home".into(), title: "Tonight".into(), selected: 1,
            rows: vec![heading("LIVE"), game(0, "Nuggets at Pacers", "7:00 PM", "live", "nba"), heading("LATER TONIGHT"),
                       game(1, "Maple Leafs at Bruins", "7:30 PM", "", "nhl"), game(2, "Nets at Hawks", "8:00 PM", "", "nba"),
                       section(3, "Replays"), section(4, "Live TV"), section(5, "Search"), section(6, "Settings")],
            ..View::default()
        };
        state
    }

    #[test]
    fn home_is_tonights_games_with_league_marks_and_no_row_numbers() {
        let rt = runtime(tonight());
        let elements = rt.elements();
        let drawn: Vec<&str> = elements.iter().filter_map(|e| e.text.as_deref()).collect();
        for title in ["Nuggets at Pacers", "Maple Leafs at Bruins", "Nets at Hawks"] {
            assert!(drawn.contains(&title), "{title} is not on Home: {drawn:?}");
        }
        assert_eq!(drawn.iter().filter(|t| **t == "NBA").count(), 2, "each NBA game carries its mark: {drawn:?}");
        assert!(drawn.contains(&"NHL"), "the NHL game carries its mark: {drawn:?}");
        assert!(!drawn.iter().any(|t| !t.is_empty() && t.chars().all(|c| c.is_ascii_digit())), "a row draws a side number: {drawn:?}");
        for label in ["Replays", "Live TV", "Search", "Settings"] {
            let (_, y) = rt.find("sections").expect("the sections line");
            let at = elements.iter().find(|e| e.text.as_deref() == Some(label)).unwrap_or_else(|| panic!("{label} is missing"));
            assert!((at.rect[1] + at.rect[3] / 2.0 - y).abs() < 1.0, "{label} is not on the sections line");
        }
        let lights: Vec<String> = elements.iter().filter(|e| e.key.as_deref() == Some("light")).map(|e| e.fill.hex()).collect();
        assert_eq!(lights, ["#ff3b30ff", "#00000000", "#00000000"], "only the live game carries the red light");
    }

    #[test]
    fn a_pointer_on_a_section_opens_it() {
        let sent = tap(tonight(), "row-6");
        assert_eq!(sent, ["down", "down", "down", "down", "enter"], "Live TV is four moves below the Nuggets game");
    }

    #[test]
    fn a_pointer_on_a_far_row_selects_that_row() {
        let slots = results(12);
        let sent = tap_in(screen("games", &slots, 0), "row-10", (1120.0, 1600.0));
        assert_eq!(opened_row("games", &slots, 0, &sent), Some(10), "a pointer on row 10 selects that row, keys were {sent:?}");
    }

    #[test]
    fn a_second_press_during_a_click_does_not_enqueue_another_enter() {
        let mut rt = runtime(screen("home", &results(4), 0));
        click_at(&mut rt, "row-2");
        click_at(&mut rt, "row-2");
        let enters = rt.state.sent_keys.iter().filter(|key| key.as_str() == "enter").count();
        assert_eq!(enters, 1, "a second press while the first click is unanswered sends another enter: {:?}", rt.state.sent_keys);
    }

    #[test]
    fn a_prompt_ignores_a_click_on_the_row_behind_it() {
        let mut state = screen("home", &results(4), 0);
        state.overlay = Overlay::Prompt { label: "Filter".into(), key: "f".into(), typed: String::new() };
        let mut rt = runtime(state);
        click_at(&mut rt, "row-2");
        assert!(rt.state.sent_keys.is_empty(), "a prompt lets the click through: {:?}", rt.state.sent_keys);
    }

    #[cfg(not(target_os = "macos"))]
    fn corner_player(state: &mut State) {
        state.playing = Some(Playing {
            stream: video::Stream { surface: rune::NativeSurface::new() },
            title: "Live".into(), command: vec![], phase: video::Phase::Playing, stage: Stage::Mini,
            paused: false, muted: false, reported: false,
        });
    }

    #[cfg(not(target_os = "macos"))]
    fn press_tab(state: State) -> State {
        let mut rt = runtime(state);
        rt.on_key = Some(on_key);
        rt.key(KeyEvent { key: Key::Tab, mods: Mods::default() });
        rt.frame(0.0);
        rt.state
    }

    #[cfg(not(target_os = "macos"))]
    #[test]
    fn tab_on_home_reaches_the_sections_line_while_the_corner_player_is_up() {
        let mut state = screen("home", &results(2), 0);
        corner_player(&mut state);
        let state = press_tab(state);
        assert_eq!(state.sent_keys, ["tab"], "Tab opened the corner player instead of the sections line");
        assert!(matches!(state.playing.as_ref().map(|p| p.stage), Some(Stage::Mini)));
    }

    #[cfg(not(target_os = "macos"))]
    #[test]
    fn tab_beside_a_game_list_still_opens_the_corner_player() {
        let mut state = screen("games", &results(2), 0);
        corner_player(&mut state);
        let state = press_tab(state);
        assert!(state.sent_keys.is_empty(), "Tab left the corner player: {:?}", state.sent_keys);
        assert!(matches!(state.playing.as_ref().map(|p| p.stage), Some(Stage::Full)));
    }

    #[cfg(not(target_os = "macos"))]
    #[test]
    fn a_full_screen_video_ignores_a_click_on_the_row_behind_it() {
        let mut state = screen("feeds", &results(4), 0);
        state.playing = Some(Playing {
            stream: video::Stream { surface: rune::NativeSurface::new() },
            title: "Live".into(), command: vec![], phase: video::Phase::Playing, stage: Stage::Full,
            paused: false, muted: false, reported: false,
        });
        let mut rt = runtime(state);
        let (x, y) = rt.find("row-2").unwrap_or((300.0, 300.0));
        rt.pointer_move(x, y);
        rt.pointer_down(x, y);
        rt.pointer_up(x, y);
        rt.frame(1.0 / 60.0);
        assert!(rt.state.sent_keys.is_empty(), "full-screen video lets the click through: {:?}", rt.state.sent_keys);
    }
}
