# Screens

The app opens on Home: live sports, replays, live TV, search and settings. Keys mean the
same thing on every screen:

| Key | Does |
|---|---|
| `1` to `9` | Open that row of the list (a sport, a game, a channel, a source) |
| `/` | Search teams, players, games and channels |
| `,` | Settings |
| Enter, → or `l` | Open or play |
| Esc, ←, `h` or Backspace | Back up one level |
| `s` | Change sport (schedules, replays and their sources) |
| `f` | Filter the current list |
| `r` | Refresh the current list |
| `?` · `q` | Help · quit |
| `a` | Home or away feed (live game sources) |

Pressing `?` or `,` on its own screen closes it. Esc leads back to Home, where `1`, `2` and `3` open live sports, replays and live TV.
Any other key that does nothing on a screen says so on the status line.

The OpenTUI front end (`opentui/`) and the native window (`native/`) drive a headless copy of this same app (`sportscli/remote.py`), so it follows the chart without a second implementation.

The statechart lives in `sportscli/screens.py`. `tests/test_screens.py` explores every
reachable screen state with every key (offline, with sample games, replays and channels)
and checks that:

- every move between screens is one the chart allows, and every chart move happens;
- Esc, repeated, always leads back to Home;
- help and search return to the screen they were opened from;
- `/`, `1`, `2`, `3` and `,` lead to the same place from every screen, and `s` only ever changes sport;
- `q` (Ctrl-X in search) quits from every screen;
- every key shown in a screen's footer does something there, in every league.

Change the chart in the same commit as the key handling in `App.handle_key`. The diagram
leaves out the global keys except from Home, since every screen has them.

```mermaid
stateDiagram-v2
    [*] --> home
    home --> channels: Enter
    home --> leagues: Enter
    home --> search: Enter
    home --> settings: Enter
    home --> home: Esc
    home --> search: /
    home --> settings: ,
    home --> help: ?
    home --> [*]: q
    leagues --> section_: Enter
    leagues --> back_: Esc
    leagues --> home: Esc
    games --> feeds: Enter
    games --> leagues: Esc
    games --> leagues: s
    replays --> feeds: Enter
    replays --> leagues: Esc
    replays --> leagues: s
    channels --> feeds: Enter
    channels --> home: Esc
    replay_sources --> feeds: Enter
    replay_sources --> search: Esc
    feeds --> origin_: Esc
    feeds --> leagues: s
    settings --> back_: Esc
    settings --> back_: ,
    search --> feeds: Enter
    search --> replay_sources: Enter
    search --> back_: Esc
    search --> home: Esc
    search --> [*]: Ctrl-X
    help --> back_: Esc
    help --> back_: ?
    help --> [*]: q
    note right of back_: the screen that opened this one
    note right of origin_: the list the sources came from
    note right of section_: games or replays
```
