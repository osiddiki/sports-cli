# Phone page (`--web`)

The page `sports --web` serves for phones: Sports tab with league chips and games, a game's sources, TV tab with channel search, and in-browser playback through the token proxy. Every path sits under `/<token>/`; the page fetches relative paths (`api/...`, `p/...`), so the link must resolve under that folder. A link typed without the slash after the token (`…:8765/<token>#/`) gets a 301 to `/<token>/`.

## Sub-features

- `web-home` Sports tab renders the same games `api/games` returns.
- `web-leagues` every league chip renders its games (or "No live or upcoming games today.").
- `web-game` a game's Sources list, with background checks settling.
- `web-play` Play on a source; the `<video>` time advances (hls.js on desktop, native HLS on iPhone).
- `web-tv` TV tab lists channels; search filters them; a channel's sources and Play.
- `web-no-slash` the link without the trailing slash still renders games and channels.

## How to get to it (user POV)

- Run `sports --web` (or `./sports-cli --web`) and open a printed link on a phone on the same Wi-Fi or tailnet.

## Driving it with a browser

Preconditions: baseline; network without a proxy. iPhone WebKit is required: iPhone Safari is how this page is used, and Chromium alone missed the blank-page bug.

```sh
S=.claude/skills/verify-sports-cli/scripts
$S/web_drive.py                                   # its own local server, iPhone WebKit + desktop Chromium
$S/web_drive.py --url-file ~/sports-cli-web-link.txt   # a server already running (the phone's link)
$S/web_drive.py --leagues nba,ncaam --no-play     # quicker
```

The first run makes `~/.cache/sports-cli-verify/web-venv` with Playwright and its WebKit and Chromium. On Linux WebKit needs system libraries once: `~/.cache/sports-cli-verify/web-venv/bin/playwright install-deps webkit` (asks for sudo).

Pass criteria (the script decides): Home and each league show as many rows as `api/games` returns; Sources lists rows; Play makes the video advance on some source among the first three; TV lists channels and search filters them; the no-slash link renders games; and no console error, uncaught page error or 4xx/5xx from the page's own requests (stream-proxy `p/` misses, `api/play` 502s for a source that will not resolve, and Chromium's `/favicon.ico` are not counted).

Evidence: `summary.txt`, `problems.txt` and `ENGINE-NN-label.png/.txt` per step in `~/.cache/sports-cli-verify/web/<time>/`. The token is replaced with `<token>` in everything written.

## Gotchas

- Playwright's Linux WebKit has no HLS decoder; `play-*` there reads `SKIP` with "cannot play HLS". Playback is proven on desktop; prove iPhone playback on a real phone when it matters.
- Never paste the link into a report: it is the password. Use `--url-file`.
- Live data: late at night only a few leagues have games; `game` reads `SKIP` when none do.
