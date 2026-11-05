# OpenTUI prototype

A prototype of the games screen built with [OpenTUI](https://opentui.com) on Bun. The Python app still does all the work: on start this launches `sports-cli --web` on port 8765 (or a free port when 8765 is taken) and draws the screen from that server's JSON API (`api/leagues`, `api/games`, `api/channels`, `api/sources`, `api/play`). It stops the server on quit.

```sh
brew install oven-sh/bun/bun
cd opentui && bun install
bun index.ts
```

Keys: ←/→ league (the last tab is TV), ↑/↓ move, Enter opens a game or channel and then plays a source in mpv, Tab switches panels, Esc returns to the list, r refreshes, p shows the phone link as a QR code (←/→ for the Wi-Fi link), q quits.

The phone page is on while the prototype runs. The header shows its Tailscale link when Tailscale is running, otherwise the Wi-Fi link; it stays the same between runs because the token is saved and the port is 8765 unless that port is busy. The link is the password, so share it only with people you trust.

mpv starts with the app's own options (cache, segment retries, stall timeout, hardware decoding). The Now playing panel reads resolution, frame rate, decoder, cache, dropped frames and bitrate from mpv's IPC socket, and flags a stall after 10 seconds without progress. Quitting also closes the mpv it started.

Not covered yet: search, replays, settings, ad modes and player choice. Type-check with `bunx tsc --noEmit -p .`.
