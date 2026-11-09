# Navigation

Home, the three sections (live sports, replays, live TV), search, settings and help, with keys that mean the same thing on every screen. `tests/test_screens.py` proves every key against `sportscli/screens.py` offline; this proves the same paths on the real screen with live data.

## Sub-features

- `nav-home` the app opens on Home; `1`, `2`, `3`, `/` and `,` open sections from anywhere.
- `nav-back` Esc goes up one level and, repeated, reaches Home.
- `nav-sport` `s` changes sport on a schedule, replay list or their sources.
- `nav-help` `?` opens help; Esc returns to the same screen.
- `nav-search` `/` opens search from anywhere; Esc returns.
- `nav-date` `[` and `]` change the schedule date.
- `nav-keys` `1` inside live sports changes sport and Esc comes back; `h`/`l` go back and open; `,` opens and closes settings; an unused key says so.

## How to get to it (user POV)

- Start `./sports-cli`; Home is the first screen. `./sports-cli --league nba` opens a schedule directly.

## Driving it with drive.py

Preconditions: baseline.

- **Home to a schedule and back.** `$D 'expect:LIVE|LATER|No games' shot:home key:Up key:Right key:Enter 'expect:By sport: choose a sport' key:Enter 'expect:NBA  ·' shot:schedule key:Esc 'expect:choose a sport' key:Esc 'expect:LIVE|LATER|No games' shot:back-home`.
- **Change sport.** `$D --app-args="--league nba" 'expect:NBA  ·' key:s 'expect:choose a sport' key:Down key:Enter 'expect:WNBA  ·' shot:wnba`.
- **Help round trip.** `$D --app-args="--league nba" 'expect:NBA  ·' 'key:?' 'expect:Controls and setup' shot:help key:Esc 'expect:NBA  ·' shot:back`.
- **Search round trip.** `$D 'expect:LIVE|LATER|No games' key:/ 'expect:Find:' type:lakers shot:search key:Esc 'expect:LIVE|LATER|No games' shot:back`.
- **Date.** `$D --app-args="--league nba" 'expect:NBA  ·' shot:today 'key:]' 'expect:NBA  ·' shot:tomorrow`.

## Gotchas

- In search, letters type into the query, including `q` and `?`; Ctrl-X quits there.
- The schedule shows `Loading schedule` with a spinner first; expect the league line, not the game rows.
