//! The look: a dark field, one accent per league, a beam of light that marks the selection,
//! and a signal ruler on every source. docs/native-ui.md explains the choices.

use crate::server::{Row, View};
use crate::video::Phase;
use crate::{Overlay, Playing, Stage, State, depth};
use rune::color::{oklch, oklcha};
use rune::tok::motion;
use rune::{Color, Cx, El, ScrollSpec, SurfaceFit, TextStyle, Transition, col, el, row, text};
use rune::LoopSpec;

pub const ROW: f32 = 46.0;
const TICKS: usize = 18;

pub struct Ink {
    pub bg: Color,
    pub panel: Color,
    pub line: Color,
    pub text: Color,
    pub dim: Color,
    pub faint: Color,
    pub mid: Color,
    pub accent: Color,
    /// The on-air light. Red means a game is live and nothing else.
    pub on_air: Color,
}

const FIELD: &str = "#0B0D10";
const PRIMARY: &str = "#ECE9E2";
const SECONDARY: &str = "#7E848D";
const ON_AIR: &str = "#FF3B30";

fn srgb(hex: &str) -> Color {
    rune::color::hex(hex).expect("a palette colour")
}

pub fn accent(key: &str) -> Color {
    match key {
        "nba" => oklch(0.74, 0.17, 48.0),
        "wnba" => oklch(0.74, 0.16, 28.0),
        "ncaam" | "ncaaw" => oklch(0.72, 0.14, 255.0),
        "nfl" => oklch(0.76, 0.17, 148.0),
        "ncaaf" => oklch(0.82, 0.15, 88.0),
        "mlb" => oklch(0.7, 0.18, 22.0),
        "nhl" => oklch(0.82, 0.11, 212.0),
        "mls" | "ligamx" => oklch(0.78, 0.15, 165.0),
        "ucl" => oklch(0.72, 0.15, 272.0),
        "epl" | "laliga" | "bundesliga" | "seriea" | "ligue1" => oklch(0.72, 0.17, 312.0),
        "tv" => oklch(0.78, 0.13, 232.0),
        _ => srgb(PRIMARY),
    }
}

fn ink(v: &View) -> Ink {
    let key = if matches!(v.mode.as_str(), "home" | "boot" | "settings" | "help" | "search") { "" } else { v.accent.as_str() };
    let (text, secondary) = (srgb(PRIMARY), srgb(SECONDARY));
    Ink {
        bg: srgb(FIELD),
        panel: srgb("#15181C"),
        line: text.alpha(0.07),
        text,
        dim: secondary,
        faint: secondary.alpha(0.7),
        mid: text.mix(secondary, 0.3),
        accent: accent(key),
        on_air: srgb(ON_AIR),
    }
}

const TITLE: TextStyle = TextStyle::new(26.0, 640.0);
const BODY: TextStyle = TextStyle::new(14.0, 500.0);
const LIVE: TextStyle = TextStyle::new(14.0, 640.0);
const SMALL: TextStyle = TextStyle::new(12.0, 450.0);
const CAPS: TextStyle = TextStyle::new(10.5, 650.0);
const MONO: TextStyle = TextStyle::new(11.5, 500.0).mono();

pub fn root(s: &State, _cx: &mut Cx<State>) -> El {
    let k = ink(&s.view);
    let full = s.playing.as_ref().is_some_and(|p| p.stage == Stage::Full);
    let mut root = el().key("root").w_full().h_full().fill(k.bg);
    root = root.child(row().key("app").w_full().h_full().pl(30.0).child(main(s, &k)));
    if let Some(playing) = &s.playing {
        root = root.child(if full { stage(s, playing, &k) } else { mini(playing, &k) });
    }
    match &s.overlay {
        Overlay::Prompt { label, typed, .. } => root.child(prompt(label, typed, &k)),
        Overlay::Inspect { url } => root.child(card("Stream link", url, "⌘C copy   any key closes", &k)),
        Overlay::None => root,
    }
}

/// Three bars of a signal meter: the app's mark.
fn logo(k: &Ink) -> El {
    row().key("logo").gap(3.0).items_end().h(16.0)
        .child(el().w(3.0).h(6.0).radius(1.5).fill(k.dim))
        .child(el().w(3.0).h(11.0).radius(1.5).fill(k.dim))
        .child(el().w(3.0).h(16.0).radius(1.5).fill(k.accent).motion(motion::SETTLE))
}

fn main(s: &State, k: &Ink) -> El {
    let v = &s.view;
    let crumbs = crumbs(v);
    let header = col().key("header").gap(6.0)
        .child(row().gap(10.0).items_center()
            .child(logo(k))
            .child(text(crumbs).font(CAPS).color(k.faint))
            .child(el().grow(1.0))
            .child(pulse(v.busy, k)))
        .child(row().gap(12.0).items_center()
            .child(if v.mark.is_empty() { el().key("no-mark").w(0.0) } else { mark(&v.mark, k.accent) })
            .child(text(if v.mode == "boot" { "Tuning in" } else { v.title.as_str() }).font(TITLE).color(k.text)))
        .children(v.info.iter().enumerate().map(|(i, line)| text(line.as_str()).font(if i == 0 { BODY } else { SMALL }).color(if i == 0 { k.dim } else { k.faint }).truncate()));
    let body = col().key(format!("screen-{}", v.mode)).grow(1.0).min_h(0.0).w_full()
        .transition(Transition::offset(14.0 * s.direction, 0.0).combined(Transition::opacity()))
        .child(listing(s, k));
    let main = col().key("main").grow(1.0).h_full().min_h(0.0).pt(26.0).pr(30.0).pb(14.0).gap(18.0)
        .child(header)
        .child(search_line(v, k));
    let main = if v.mode == "home" { main.child(sections(v, k)) } else { main };
    main.child(body).child(footer(s, k))
}

/// A league's short code in its accent, where lists used to show a row number.
fn mark(code: &str, color: Color) -> El {
    el().key("mark").w(30.0).h(18.0).center().radius(5.0).fill(color.alpha(0.14))
        .child(text(code).font(CAPS).color(color))
}

/// Home's Replays, Live TV, Search and Settings, on one line above the games, so none is ever scrolled away.
fn sections(v: &View, k: &Ink) -> El {
    let mut line = row().key("sections").gap(8.0).items_center();
    for (i, r) in v.rows.iter().enumerate().filter(|(_, r)| r.style == "section") {
        let on = i as i64 == v.selected;
        line = line.child(el().key(format!("row-{i}")).h(32.0).px(14.0).center().radius(8.0)
            .fill(if on { k.panel } else { Color::TRANSPARENT }).border(1.0, if on { k.text.alpha(0.35) } else { k.line })
            .on_press(move |s: &mut State| s.press_row(i))
            .child(text(r.text.as_str()).font(BODY).color(if on { k.text } else { k.mid })));
    }
    line
}

fn crumbs(v: &View) -> String {
    let mut parts = vec!["SPORTS CLI".to_string()];
    if depth(&v.mode) >= 1 && !v.accent.is_empty() && v.mode != "settings" && v.mode != "help" && v.mode != "search" {
        parts.push(if v.accent == "tv" { "LIVE TV".into() } else { v.accent.to_uppercase() });
    }
    if v.mode == "feeds" { parts.push("SOURCES".into()) }
    parts.join("  ·  ")
}

/// A quiet dot that breathes while the app works in the background.
fn pulse(busy: bool, k: &Ink) -> El {
    let dot = el().key("pulse").size(7.0, 7.0).radius(3.5).fill(if busy { k.accent } else { k.line });
    if busy { dot.looping(LoopSpec::opacity(1.1, 0.0, 0.25, 1.0)) } else { dot }
}

fn search_line(v: &View, k: &Ink) -> El {
    let Some(query) = &v.query else { return el().key("no-search").h(0.0) };
    row().key("search").h(40.0).px(14.0).gap(10.0).items_center().radius(10.0).fill(k.panel).border(1.0, k.line)
        .child(text("/").font(MONO).color(k.accent))
        .child(text(query.as_str()).font(BODY).color(k.text))
        .child(el().w(2.0).h(16.0).fill(k.accent).looping(LoopSpec::opacity(1.0, 0.0, 0.0, 1.0)))
}

fn listing(s: &State, k: &Ink) -> El {
    let v = &s.view;
    let listed: Vec<(usize, &Row)> = v.rows.iter().enumerate().filter(|(_, r)| r.style != "section").collect();
    if listed.is_empty() {
        let message = if v.mode == "boot" { s.error.clone().unwrap_or_else(|| "Starting the app".into()) } else { v.empty.clone() };
        return col().key("empty").grow(1.0).center().gap(14.0)
            .child(ruler(&Row { style: if v.busy || v.mode == "boot" { "busy".into() } else { "fail".into() }, ..Row::default() }, "", true, k))
            .child(text(message).font(BODY).color(k.dim));
    }
    let mut list = col().key("rows").w_full();
    if let Some(columns) = &v.columns {
        list = list.child(columns_header(columns, k));
    }
    let top = if v.columns.is_some() { 24.0 } else { 0.0 };
    if let Some(at) = listed.iter().position(|(i, _)| *i as i64 == v.selected) {
        let y = top + at as f32 * ROW;
        list = list
            .child(el().key("wash").absolute(0.0, y).w_full().h(ROW).radius(12.0)
                .linear_gradient(std::f32::consts::FRAC_PI_2, [(0.0, k.accent.alpha(0.17)), (0.55, k.accent.alpha(0.03)), (1.0, k.accent.alpha(0.0))])
                .motion(motion::SNAP))
            .child(el().key("beam").absolute(0.0, y + 11.0).w(3.0).h(ROW - 22.0).radius(1.5).fill(k.accent)
                .shadow(0.0, 12.0, k.accent.alpha(0.7)).motion(motion::SNAP));
    }
    for (i, r) in listed {
        list = list.child(line(v, i, r, i as i64 == v.selected, k));
    }
    col().key("scroller").grow(1.0).min_h(0.0).w_full().scroll(ScrollSpec { stick_bottom: false }).scroll_edge_fade(18.0).scroll_margin(ROW).child(list)
}

fn columns_header(columns: &[String], k: &Ink) -> El {
    let name = columns.first().cloned().unwrap_or_default();
    row().key("columns").h(24.0).pl(22.0).pr(14.0).items_center()
        .child(text(name).font(CAPS).color(k.faint).grow(1.0))
        .child(text(columns.get(1).cloned().unwrap_or_default()).font(CAPS).color(k.faint).w(70.0))
        .child(text(columns.get(2).cloned().unwrap_or_default()).font(CAPS).color(k.faint).w(90.0))
        .child(text("SIGNAL").font(CAPS).color(k.faint).w(TICKS as f32 * 5.0 + 140.0))
}

/// A game as a list row draws it. Home names each game's league with its mark; a league's own
/// schedule shows the mark beside its title instead.
struct GameRow<'a> {
    mark: Option<(&'a str, Color)>,
    on_air: bool,
    when: &'a str,
    title: &'a str,
    channels: &'a str,
}

impl<'a> GameRow<'a> {
    fn of(v: &View, r: &'a Row) -> GameRow<'a> {
        let cell = |n: usize| r.cells.get(n).map(String::as_str).unwrap_or("");
        GameRow {
            mark: (v.mode == "home").then(|| (cell(0), accent(&r.league))),
            on_air: r.style == "live",
            when: cell(1),
            title: cell(2),
            channels: &r.detail,
        }
    }
}

/// Live games carry a breathing red light and the brightest title; later games stay quiet with a time.
fn game(base: El, i: usize, g: &GameRow, selected: bool, k: &Ink) -> El {
    let base = match g.mark { Some((code, color)) => base.child(mark(code, color)), None => base };
    let light = el().key("light").size(8.0, 8.0).radius(4.0);
    let light = if g.on_air { light.fill(k.on_air).shadow(0.0, 10.0, k.on_air.alpha(0.7)).looping(LoopSpec::opacity(1.6, i as f64 * 0.13, 0.35, 1.0)) } else { light };
    base.child(light)
        .child(text(g.title).font(if g.on_air { LIVE } else { BODY }).color(if g.on_air || selected { k.text } else { k.mid }).grow(1.0).truncate())
        .child(text(if g.on_air { "LIVE" } else { g.when }).font(MONO).color(if g.on_air { k.on_air } else { k.dim }).w(96.0))
        .child(text(g.channels).font(SMALL).color(k.faint).w(170.0).truncate())
}

fn line(v: &View, i: usize, r: &Row, selected: bool, k: &Ink) -> El {
    let tone = if !r.pick && r.style == "heading" { k.faint } else if selected { k.text } else { k.mid };
    let mut base = row().key(format!("row-{i}")).w_full().h(ROW).pl(22.0).pr(14.0).gap(14.0).items_center();
    if r.pick {
        base = base.on_press(move |s: &mut State| s.press_row(i));
    }
    if r.style == "heading" {
        return base.child(text(r.text.trim().to_uppercase()).font(CAPS).color(k.faint));
    }
    match v.mode.as_str() {
        "feeds" => {
            let cell = |n: usize| r.cells.get(n).cloned().unwrap_or_default();
            base.child(col().grow(1.0).min_w(0.0).gap(2.0)
                    .child(text(cell(0)).font(BODY).color(tone).truncate())
                    .child(text(r.detail.as_str()).font(SMALL).color(k.faint).truncate()))
                .child(text(cell(1)).font(SMALL).color(k.dim).w(70.0))
                .child(text(cell(2)).font(MONO).color(if selected { k.text } else { k.dim }).w(90.0))
                .child(row().w(TICKS as f32 * 5.0 + 140.0).gap(12.0).items_center()
                    .child(ruler(r, &cell(2), selected, k))
                    .child(text(cell(3)).font(SMALL).color(if r.style == "fail" { oklch(0.68, 0.14, 25.0) } else { k.faint }).truncate()))
        }
        "games" | "home" => game(base, i, &GameRow::of(v, r), selected, k),
        "settings" => {
            let cell = |n: usize| r.cells.get(n).cloned().unwrap_or_default();
            base.child(text(cell(0)).font(BODY).color(tone).w(260.0))
                .child(text(cell(1)).font(SMALL).color(if selected { k.accent } else { k.faint }).truncate())
        }
        _ => base
            .child(text(r.text.trim()).font(BODY).color(tone).grow(1.0).truncate())
            .child(text(r.detail.as_str()).font(SMALL).color(k.faint).truncate()),
    }
}

/// The signal ruler: lit ticks are how well a source plays, tall ticks how sharp it is.
/// A sweep runs through it while the source is checked; a failed source lies flat.
fn ruler(r: &Row, quality: &str, selected: bool, k: &Ink) -> El {
    let sharp = ["2160", "1440", "1080", "720", "576", "540", "480", "360"].iter().position(|p| quality.contains(p));
    let lit = match r.style.as_str() {
        "fail" => 0,
        "warn" => 5,
        "busy" => TICKS,
        "ok" => match sharp { Some(0..=2) => TICKS, Some(3) => 14, Some(4..=6) => 10, Some(_) => 7, None => 12 },
        _ => 3,
    };
    let on = if selected { k.accent } else { k.dim.alpha(0.75) };
    let mut ruler = row().key("ruler").gap(2.0).items_center().h(14.0);
    for t in 0..TICKS {
        let tall = t % 6 == 0;
        let tick = el().w(3.0).radius(1.0).h(if t < lit { if tall { 14.0 } else { 10.0 } } else { 4.0 })
            .fill(if t < lit { on } else { k.line }).motion(motion::SETTLE);
        ruler = ruler.child(if r.style == "busy" { tick.looping(LoopSpec::opacity(0.9, t as f64 * 0.045, 0.15, 1.0)) } else { tick });
    }
    ruler
}

fn keycap(key: &str, on: bool, k: &Ink) -> El {
    el().h(22.0).min_w(24.0).px(7.0).center().radius(6.0).fill(if on { k.accent.alpha(0.18) } else { k.panel }).border(1.0, if on { k.accent.alpha(0.6) } else { k.line })
        .child(text(key).font(MONO).color(if on { k.text } else { k.dim }))
}

fn footer(s: &State, k: &Ink) -> El {
    let v = &s.view;
    let mut keys = row().gap(14.0).items_center();
    for action in v.actions.split("   ").filter(|a| !a.trim().is_empty()).take(9) {
        let (key, label) = action.trim().split_once(' ').unwrap_or((action.trim(), ""));
        keys = keys.child(row().gap(6.0).items_center().child(keycap(key, false, k)).child(text(label).font(SMALL).color(k.faint)));
    }
    if s.playing.as_ref().is_some_and(|p| p.stage == Stage::Mini) {
        keys = keys.child(row().gap(6.0).items_center().child(keycap("⇥", true, k)).child(text("player").font(SMALL).color(k.dim)));
    }
    let message = s.error.clone().unwrap_or_else(|| v.message.clone());
    row().key("footer").w_full().h(30.0).items_center().gap(16.0)
        .child(keys)
        .child(el().grow(1.0))
        .child(text(message).font(SMALL).color(if s.error.is_some() { oklch(0.7, 0.14, 25.0) } else { k.dim }).truncate())
}

fn video(playing: &Playing, radius: f32) -> El {
    el().key("video").matched("player").w_full().h_full().radius(radius).clip_rounded(radius).fill(Color::BLACK)
        .child(el().w_full().h_full().native_surface(&playing.stream.surface, SurfaceFit::Fit))
}

fn stage(s: &State, playing: &Playing, k: &Ink) -> El {
    let chrome = s.now < s.chrome_until || playing.phase != Phase::Playing || playing.paused;
    let (label, tone) = match &playing.phase {
        Phase::Loading => ("Tuning in".to_string(), k.dim),
        Phase::Playing if playing.paused => ("Paused".to_string(), k.dim),
        Phase::Playing if playing.timeline.is_some() => ("Replay".to_string(), k.dim),
        Phase::Playing => ("Live".to_string(), k.accent),
        Phase::Stalled => ("Signal weak, holding".to_string(), oklch(0.8, 0.13, 85.0)),
        Phase::Failed(why) => (format!("No picture: {why}"), oklch(0.7, 0.15, 25.0)),
    };
    let size = playing.stream.size().map(|(w, h)| format!("{w:.0}×{h:.0}")).unwrap_or_default();
    let mut frame = el().key("stage").absolute(0.0, 0.0).w_full().h_full().fill(Color::BLACK).transition(Transition::opacity())
        .child(video(playing, 0.0));
    if matches!(playing.phase, Phase::Loading | Phase::Failed(_)) {
        let row_style = Row { style: if playing.phase == Phase::Loading { "busy".into() } else { "fail".into() }, ..Row::default() };
        frame = frame.child(col().key("tuning").absolute(0.0, 0.0).w_full().h_full().center().gap(16.0)
            .child(ruler(&row_style, "", true, k).scale(2.0))
            .child(text(playing.title.as_str()).font(TextStyle::new(18.0, 600.0)).color(k.text)));
    }
    let top = row().key("chrome-top").absolute(0.0, 0.0).w_full().h(86.0).px(28.0).pt(22.0).gap(12.0).items_start()
        .linear_gradient(std::f32::consts::PI, [(0.0, oklcha(0.0, 0.0, 0.0, 0.7)), (1.0, oklcha(0.0, 0.0, 0.0, 0.0))])
        .child(el().size(8.0, 8.0).mt(6.0).radius(4.0).fill(tone))
        .child(col().gap(3.0).child(text(playing.title.as_str()).font(TextStyle::new(16.0, 620.0)).color(k.text))
            .child(text(format!("{label}{}{size}{}", if size.is_empty() { "" } else { "  ·  " }, if playing.muted { "  ·  muted" } else { "" })).font(SMALL).color(tone)));
    let mut hints = vec![("space", "pause"), ("m", "mute"), ("esc", "browse"), ("o", "mpv"), ("⌘.", "stop")];
    if playing.timeline.is_some() {
        hints.splice(0..0, [("← →", "10s"), ("⇧←", "1m"), ("[ ]", "period")]);
    }
    let mut bottom = row().key("chrome-bottom").gap(14.0).items_center();
    for (key, what) in hints {
        bottom = bottom.child(row().gap(6.0).items_center().child(keycap(key, false, k)).child(text(what).font(SMALL).color(k.dim)));
    }
    let bottom = col().key("chrome-bottom-wrap").absolute(0.0, 0.0).w_full().h_full().justify_end().px(28.0).pb(22.0).child(bottom);
    let mut frame = frame.child(col().key("chrome").absolute(0.0, 0.0).w_full().h_full().opacity(if chrome { 1.0 } else { 0.0 }).motion(motion::SETTLE).child(top).child(bottom));
    if playing.timeline.is_some() {
        let lift = if chrome { 64.0 } else { 28.0 };
        frame = frame.child(col().key("replay-time").absolute(0.0, 0.0).w_full().h_full().justify_end().px(28.0).pb(lift).child(replay_bar(s, playing, k)));
    }
    frame
}

fn replay_bar(s: &State, playing: &Playing, k: &Ink) -> El {
    let marks = playing.timeline.as_ref().expect("replay bar is only built when a timeline exists");
    let duration = marks.duration.max(0.001);
    let played = (playing.position / duration).clamp(0.0, 1.0) as f32;
    let show = playing.scrubbing || s.now < playing.show_chapter_until;
    let name = if show { playing.chapter.as_deref().and_then(crate::timeline::safe_name).unwrap_or("") } else { "" };
    let when = format!("{}  /  {}", crate::timeline::clock(playing.position), crate::timeline::clock(duration));
    let track = row().key("timeline-track").absolute(0.0, 7.0).w_full().h(4.0).radius(2.0)
        .child(el().grow(played.max(0.0001)).h_full().fill(k.accent))
        .child(el().grow((1.0 - played).max(0.0001)).h_full().fill(k.line));
    let mut ticks = row().key("timeline-ticks").absolute(0.0, 5.0).w_full().h(8.0);
    let chapters = &marks.chapters;
    if let Some(first) = chapters.first() {
        if first.start > 0.5 {
            ticks = ticks.child(el().grow((first.start / duration) as f32).h_full());
        }
    }
    for (index, chapter) in chapters.iter().enumerate() {
        let end = chapters.get(index + 1).map(|next| next.start).unwrap_or(duration);
        let span = ((end - chapter.start) / duration).max(0.001) as f32;
        ticks = ticks.child(row().grow(span).h_full().child(el().w(2.0).h(8.0).fill(k.dim)).child(el().grow(1.0).h_full()));
    }
    if chapters.is_empty() {
        ticks = ticks.child(el().grow(1.0).h_full());
    }
    col().key("timeline").w_full().gap(4.0)
        .on_drag(|state: &mut State, drag, _| state.scrub(drag))
        .child(row().h(16.0).items_center()
            .child(text(name).font(SMALL).color(k.text))
            .child(el().grow(1.0))
            .child(text(when.as_str()).font(SMALL).color(k.dim)))
        .child(el().key("timeline-bar").w_full().h(18.0).child(track).child(ticks))
}

fn mini(playing: &Playing, k: &Ink) -> El {
    let live = playing.phase == Phase::Playing;
    col().key("mini-wrap").absolute(0.0, 0.0).w_full().h_full().justify_end().items_end().pr(22.0).pb(52.0).inert()
        .child(col().key("mini").w(300.0).h(169.0).radius(14.0).shadow(10.0, 34.0, oklcha(0.0, 0.0, 0.0, 0.55)).border(1.0, k.accent.alpha(0.5))
            .transition(Transition::scale(0.9).combined(Transition::opacity()))
            .child(video(playing, 14.0))
            .child(row().absolute(10.0, 10.0).gap(6.0).items_center().px(8.0).py(4.0).radius(8.0).fill(oklcha(0.0, 0.0, 0.0, 0.55))
                .child(el().size(6.0, 6.0).radius(3.0).fill(if live { k.accent } else { k.faint }))
                .child(text(playing.title.as_str()).font(SMALL).color(k.text).truncate())))
}

fn prompt(label: &str, typed: &str, k: &Ink) -> El {
    let field = row().h(44.0).px(14.0).gap(2.0).items_center().radius(10.0).fill(k.bg).border(1.0, k.accent.alpha(0.6))
        .child(text(typed).font(BODY).color(k.text))
        .child(el().w(2.0).h(18.0).fill(k.accent).looping(LoopSpec::opacity(1.0, 0.0, 0.0, 1.0)));
    sheet(col().gap(14.0)
        .child(text(label).font(BODY).color(k.dim))
        .child(field)
        .child(text("enter confirm   esc cancel   empty clears").font(SMALL).color(k.faint)), k)
}

fn card(title: &str, body: &str, hint: &str, k: &Ink) -> El {
    sheet(col().gap(12.0).child(text(title).font(BODY).color(k.dim)).child(text(body).font(MONO).color(k.text).wrap_anywhere()).child(text(hint).font(SMALL).color(k.faint)), k)
}

fn sheet(content: El, k: &Ink) -> El {
    el().key("scrim").absolute(0.0, 0.0).w_full().h_full().center().fill(oklcha(0.05, 0.01, 268.0, 0.55)).transition(Transition::opacity())
        .child(el().key("sheet").w(520.0).padding(22.0).radius(16.0).fill(k.panel).border(1.0, k.line).shadow(18.0, 48.0, oklcha(0.0, 0.0, 0.0, 0.5))
            .transition(Transition::scale(0.96).combined(Transition::opacity())).child(content))
}
