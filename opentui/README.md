# OpenTUI prototype

A prototype of the games screen built with [OpenTUI](https://opentui.com) on Bun. The Python app still does all the work: on start this launches `sports-cli --web --web-host 127.0.0.1` on a free port and draws the screen from that server's JSON API (`api/leagues`, `api/games`, `api/channels`, `api/sources`, `api/play`). It stops the server on quit.

```sh
brew install oven-sh/bun/bun
cd opentui && bun install
bun index.ts
```

Keys: ←/→ league (the last tab is TV), ↑/↓ move, Enter opens a game or channel and then plays a source in mpv, Tab switches panels, Esc returns to the list, r refreshes, q quits.

Not covered yet: search, replays, settings, ad modes and player choice. Type-check with `bunx tsc --noEmit -p .`.
