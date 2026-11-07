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
const RAIL: f32 = 64.0;
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
        _ => oklch(0.8, 0.09, 285.0),
    }
}

fn ink(v: &View) -> Ink {
    let key = if matches!(v.mode.as_str(), "home" | "boot" | "settings" | "help" | "search") { "" } else { v.accent.as_str() };
    Ink {
        bg: oklch(0.155, 0.012, 268.0),
        panel: oklch(0.19, 0.014, 268.0),
        line: oklcha(0.9, 0.01, 268.0, 0.07),
        text: oklch(0.95, 0.006, 268.0),
        dim: oklch(0.68, 0.012, 268.0),
        faint: oklch(0.44, 0.012, 268.0),
        mid: oklch(0.82, 0.01, 268.0),
        accent: accent(key),
    }
}

const TITLE: TextStyle = TextStyle::new(26.0, 640.0);
const BODY: TextStyle = TextStyle::new(14.0, 500.0);
const SMALL: TextStyle = TextStyle::new(12.0, 450.0);
const CAPS: TextStyle = TextStyle::new(10.5, 650.0);
const MONO: TextStyle = TextStyle::new(11.5, 500.0).mono();

pub fn root(s: &State, _cx: &mut Cx<State>) -> El {
    let k = ink(&s.view);
    let full = s.playing.as_ref().is_some_and(|p| p.stage == Stage::Full);
    let mut root = el().key("root").w_full().h_full().fill(k.bg);
    root = root.child(row().key("app").w_full().h_full().child(rail(s, &k)).child(main(s, &k)));
    if let Some(playing) = &s.playing {
        root = root.child(if full { stage(s, playing, &k) } else { mini(playing, &k) });
    }
    match &s.overlay {
        Overlay::Prompt { label, typed, .. } => root.child(prompt(label, typed, &k)),
        Overlay::Inspect { url } => root.child(card("Stream link", url, "⌘C copy   any key closes", &k)),
        Overlay::None => root,
    }
}

fn rail(s: &State, k: &Ink) -> El {
    let section = match s.view.mode.as_str() {
        "channels" => "3",
        "replays" => "2",
        "search" | "replay_sources" => "/",
        "settings" => ",",
        "home" | "boot" | "help" => "",
        "feeds" if s.view.accent == "tv" => "3",
        _ => "1",
    };
    let mark = |glyph: &'static str, label: &'static str| {
        let on = glyph == section;
        col().key(format!("rail-{label}")).items_center().gap(5.0).w(RAIL)
            .child(el().w(if on { 18.0 } else { 0.0 }).h(2.0).radius(1.0).fill(k.accent).opacity(if on { 1.0 } else { 0.0 }).motion(motion::SNAP))
            .child(text(glyph).font(MONO).color(if on { k.text } else { k.faint }))
            .child(text(label).font(CAPS).color(if on { k.dim } else { k.faint }))
    };
    col().key("rail").w(RAIL).h_full().pt(26.0).pb(22.0).gap(26.0).items_center()
        .child(logo(k))
        .child(col().gap(22.0).child(mark("1", "LIVE")).child(mark("2", "REPLAY")).child(mark("3", "TV")).child(mark("/", "FIND")))
        .child(el().grow(1.0))
        .child(mark(",", "SET"))
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
            .child(text(crumbs).font(CAPS).color(k.faint))
            .child(el().grow(1.0))
            .child(pulse(v.busy, k)))
        .child(text(if v.mode == "boot" { "Tuning in" } else { v.title.as_str() }).font(TITLE).color(k.text))
        .children(v.info.iter().enumerate().map(|(i, line)| text(line.as_str()).font(if i == 0 { BODY } else { SMALL }).color(if i == 0 { k.dim } else { k.faint }).truncate()));
    let body = col().key(format!("screen-{}", v.mode)).grow(1.0).min_h(0.0).w_full()
        .transition(Transition::offset(14.0 * s.direction, 0.0).combined(Transition::opacity()))
        .child(listing(s, k));
    col().key("main").grow(1.0).h_full().min_h(0.0).pt(26.0).pr(30.0).pb(14.0).gap(18.0)
        .child(header)
        .child(search_line(v, k))
        .child(body)
        .child(footer(s, k))
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
    if v.rows.is_empty() {
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
    if v.selected >= 0 {
        let y = top + v.selected as f32 * ROW;
        list = list
            .child(el().key("wash").absolute(0.0, y).w_full().h(ROW).radius(12.0)
                .linear_gradient(std::f32::consts::FRAC_PI_2, [(0.0, k.accent.alpha(0.17)), (0.55, k.accent.alpha(0.03)), (1.0, k.accent.alpha(0.0))])
                .motion(motion::SNAP))
            .child(el().key("beam").absolute(0.0, y + 11.0).w(3.0).h(ROW - 22.0).radius(1.5).fill(k.accent)
                .shadow(0.0, 12.0, k.accent.alpha(0.7)).motion(motion::SNAP));
    }
    for (i, r) in v.rows.iter().enumerate() {
        list = list.child(line(v, i, r, i as i64 == v.selected, k));
    }
    col().key("scroller").grow(1.0).min_h(0.0).w_full().scroll(ScrollSpec { stick_bottom: false }).scroll_edge_fade(18.0).scroll_margin(ROW).child(list)
}

fn columns_header(columns: &[String], k: &Ink) -> El {
    let name = columns.first().cloned().unwrap_or_default();
    row().key("columns").h(24.0).pl(22.0).pr(14.0).items_center()
        .child(text(name).font(CAPS).color(k.faint).grow(1.0))
        .child(text("SIDE").font(CAPS).color(k.faint).w(70.0))
        .child(text("QUALITY").font(CAPS).color(k.faint).w(90.0))
        .child(text("SIGNAL").font(CAPS).color(k.faint).w(TICKS as f32 * 5.0 + 140.0))
}

fn line(v: &View, i: usize, r: &Row, selected: bool, k: &Ink) -> El {
    let tone = if !r.pick && r.style == "heading" { k.faint } else if selected { k.text } else { k.mid };
    let base = row().key(format!("row-{i}")).w_full().h(ROW).pl(22.0).pr(14.0).gap(14.0).items_center();
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
        "games" => {
            let live = r.style == "live";
            let cell = |n: usize| r.cells.get(n).cloned().unwrap_or_default();
            let dot = el().size(8.0, 8.0).radius(4.0);
            let dot = if live { dot.fill(k.accent).shadow(0.0, 10.0, k.accent.alpha(0.8)).looping(LoopSpec::opacity(1.6, i as f64 * 0.13, 0.35, 1.0)) } else { dot.border(1.5, k.faint) };
            base.child(dot)
                .child(text(if live { "LIVE".to_string() } else { cell(1) }).font(MONO).color(if live { k.accent } else { k.faint }).w(118.0))
                .child(text(cell(2)).font(BODY).color(tone).grow(1.0).truncate())
                .child(text(r.detail.as_str()).font(SMALL).color(k.faint).truncate())
        }
        "home" | "settings" => {
            let cell = |n: usize| r.cells.get(n).cloned().unwrap_or_default();
            let (key, label, detail) = if v.mode == "home" { (cell(0), cell(1), cell(2)) } else { (String::new(), cell(0), cell(1)) };
            let mut base = base;
            if !key.is_empty() { base = base.child(keycap(&key, selected, k)) }
            base.child(text(label).font(BODY).color(tone).w(if v.mode == "home" { 150.0 } else { 260.0 }))
                .child(text(detail).font(SMALL).color(if v.mode == "settings" && selected { k.accent } else { k.faint }).truncate())
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
    let hints = [("space", "pause"), ("m", "mute"), ("esc", "browse"), ("o", "mpv"), ("⌘.", "stop")];
    let mut bottom = row().key("chrome-bottom").gap(14.0).items_center();
    for (key, what) in hints {
        bottom = bottom.child(row().gap(6.0).items_center().child(keycap(key, false, k)).child(text(what).font(SMALL).color(k.dim)));
    }
    let bottom = col().key("chrome-bottom-wrap").absolute(0.0, 0.0).w_full().h_full().justify_end().px(28.0).pb(22.0).child(bottom);
    frame.child(col().key("chrome").absolute(0.0, 0.0).w_full().h_full().opacity(if chrome { 1.0 } else { 0.0 }).motion(motion::SETTLE).child(top).child(bottom))
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
