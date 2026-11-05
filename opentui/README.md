# OpenTUI front end

The same app as `./sports-cli`, drawn with [OpenTUI](https://opentui.com) on Bun. The Python app does all the work. On start this launches `sports-cli --web` on port 8765 (or a free port when 8765 is taken). The server runs a headless copy of the terminal app (`sportscli/remote.py`) with the same screens and keys, so the statechart in `sportscli/screens.py` and `docs/screens.md` applies unchanged. This front end sends each key press to it (`api/app/key`) and draws the screen it describes (`api/app/view`). It stops the server on quit.

```sh
brew install oven-sh/bun/bun
cd opentui && bun install
bun index.ts
```

Keys are the app's: `1` `2` `3` for live sports, replays and TV, `/` search, `,` settings, `?` help, `q` quit, arrows or `j` `k` to move, Enter or → to open, Esc or ← to go back, and on every list `f` filter, `r` refresh, `s` sport, `[` `]` day, and on sources `x` `i` `o` `a` `g` `u` `v`. Each screen's footer lists the keys that work there. Prompts (`f`, `u`, the add-on and resolver settings) open as a box over the screen instead of leaving full-screen mode.

Differences from the terminal app:
- `p` shows the phone link as a QR code (←/→ for the other link). In search it types a `p`.
- mpv runs beside this screen, so you can keep browsing, search or open another game while it plays. Starting another source replaces it.
- `i` shows the media URL in a box over the screen, and any key closes it.

The phone page is on while this runs. The header shows its Tailscale link when Tailscale is running, otherwise the Wi-Fi link. It stays the same between runs because the token is saved and the port is 8765 unless that port is busy. The link is the password, so share it only with people you trust.

mpv starts with the app's own options (cache, segment retries, stall timeout, hardware decoding). The Now playing panel reads resolution, frame rate, decoder, cache, dropped frames and bitrate from mpv's IPC socket, and flags a stall after 10 seconds without progress. If mpv exits on its own with an error, the app hides that source until refresh, as the terminal app does. Quitting also closes the mpv it started.

Type-check with `bunx tsc --noEmit -p .`.
