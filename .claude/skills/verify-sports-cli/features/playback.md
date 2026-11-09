# Playback

Pressing Enter on a source resolves it (through the local relay when needed, with a backup source for live streams) and plays it in mpv; quitting mpv returns to the app.

## Sub-features

- `play-tv` a Live TV channel plays and returns.
- `play-game` a live game's source plays and returns.

## How to get to it (user POV)

- Enter on a source on the Streams screen.

## Driving it with drive.py

Preconditions: baseline; network without a proxy; mpv installed. The driver's mpv config plays headless for `--mpv-seconds` and exits, so the app's own play path runs end to end without a window.

- **TV channel.** `$D --mpv-seconds 8 --app-args="--tv" 'expect:ESPN +\d+' key:Enter 'expect:^ Streams' shot:sources key:Enter 'expect:Returned from mpv' shot:returned`. The status line reads `Returned from mpv.`, which the app prints only when mpv exits with status 0 after playing.
- **Game.** From a schedule with a live game: `$D --mpv-seconds 8 --app-args="--league nba" 'expect:LIVE' key:Enter 'expect:^ Streams' key:Enter 'expect:Returned from mpv' shot:returned`. Live games may also show `Backup:` in the status line when a second source merged.

## Gotchas

- `mpv exited with status N; feed hidden until refresh.` means the source failed; try the next source (`key:Down key:Enter`).
- A sandbox proxy makes playback fail for reasons unrelated to the app; prove playback from a normal terminal.
