# Agent and developer setup

This repository is a Python terminal app for discovering and playing sports streams and replays, on macOS, Linux and Windows. Use Python 3.9 or newer. The app itself uses the standard library, plus `windows-curses` and `tzdata` on Windows; mpv is the default player. IINA is an optional macOS player and Celluloid is an optional Linux player. The PPV resolver is a separate Node.js 20+ checkout installed by `./setup.sh` into the ignored `resolver/` directory.

## Setting it up for someone who doesn't use a terminal

When a person asks you to install, run, update or fix Sports CLI for them, assume they have never used a terminal, GitHub or Git. They need no GitHub account. Do the work yourself, explain each step in one plain sentence, and leave them with the app open.

1. **Explain, then ask once.** Tell them what will be installed and why, from the list in README's Install section: Sports CLI, plus whichever of mpv (the video player), Python and, on a Mac, Homebrew are missing. Check with `command -v mpv python3 brew`. Ask before installing. Their yes lets you pass `--yes` (`-Yes` on Windows).
2. **Install.** On macOS or Linux, run `curl -fsSL https://raw.githubusercontent.com/osiddiki/sports-cli/main/install.sh | sh -s -- --yes`. On Windows, run `irm https://raw.githubusercontent.com/osiddiki/sports-cli/main/install.ps1 | iex` in PowerShell. Add PPV sources (`--ppv`) or the ad detector (`--ads`) only when they ask for them.
3. **Passwords are theirs to type.** Homebrew and Linux package managers ask for the computer's password. Never ask for it, type it or store it. If your shell can't show them the prompt, open a Terminal window running the same command so they can type it there. On a Mac, use `osascript -e 'tell application "Terminal" to do script "<command>"'`. Tell them nothing appears on screen while they type a password, and that this is normal.
4. **Check it worked.** Run `~/.local/bin/sports --list --tv`, or `sports --list --tv` once the command's folder is on `PATH`. It should print channels. If it reports something missing, run the command it names, such as `sports --setup`.
5. **Open the app for them.** The app is a full-screen program that needs a real terminal window, so you can't run it inside your own tool calls. On a Mac, run `osascript -e 'tell application "Terminal" to do script "sports"'`. On Linux, open their terminal app with `sports`. On Windows, start `wt sports` or a new PowerShell window. Then teach them three things: arrow keys and Enter to choose, Esc to go back, and `q` to quit. `?` shows the rest.
6. **Later requests.** "Update it" means `sports --update`; their settings stay. "Video doesn't play" means checking `sports --setup`, then trying another source on the Sources screen. "Remove it" means deleting `~/.local/share/sports-cli` and `~/.local/bin/sports` (`%LOCALAPPDATA%\sports-cli` on Windows). Confirm before you delete.

Keep it this simple for them. Don't clone with Git, edit files, or run the developer checks below unless they ask to change the app.

## Development

Run the local checks from the repository root:

```sh
python3 -m unittest -v
python3 -m py_compile sports-cli sportscli/*.py sportscli/tools/*.py
sh -n setup.sh install.sh
```

To prove a change in the real app, use the project skill `.claude/skills/verify-sports-cli` (a pseudo-terminal driver with a feature map); `scripts/run_all.sh` drives every mapped feature and needs a network without a sandbox proxy. Run `./sports-cli` in a terminal, or `./sports-cli --list --league nba` for non-interactive output. Live provider tests depend on current games, upstream availability, and network access; keep deterministic fixtures in the unit tests and report which real-player or live checks were possible.

## Code map

- `install.sh` (macOS, Linux) and `install.ps1` (Windows, through winget): the one-command installers for non-developers. They install only missing pieces (Homebrew on a Mac, Python, mpv; Node.js only with `--ppv`/`-Ppv`; a detector-compatible Python and the ad model only with `--ads`/`-Ads`), unpack the app without Git, and write the `sports` launcher, which sets `SPORTS_CLI_COMMAND` so missing-piece messages name `sports --setup`, `--setup-ppv` or `--setup-ads`. Windows has no `setup.sh`; `install.ps1` does its work itself.
- Windows differences live at the boundary: `ad_mute.ipc_path` and `ad_mute.Connection` (named pipe instead of a Unix socket), `ai_ad_mute.venv_python` (`Scripts\python.exe`) and its `py` launcher search, and `save_config` (no POSIX modes). Keep new mpv IPC on `ad_mute.connect`.
- `sports-cli`: the only user-facing executable; `sportscli/app.py`: schedules, sources, screens, asynchronous discovery, player launch and configuration.
- `sportscli/smart_search.py`: local query parsing, team suggestions, and result ranking. `sportscli/data/teams.json` is the bundled ESPN team-name index; refresh it with `python3 -m sportscli.tools.update_teams` when appropriate.
- `sportscli/screens.py`: the screen statechart. `App.handle_key` implements it and `tests/test_screens.py` explores every screen state with every key against it; update the chart and `docs/screens.md` with any navigation change.
- `verification/relay/`: TLA+ model of the backup-source failover with `check.sh`; the project skill `verify-relay-failover` says when and how to run it, and `docs/relay-failover.md` shows the verdicts and charts.
- `sportscli/players.py`: lazy player-name search and bounded roster verification; keep this separate from team search so slow rosters cannot block the UI.
- `sportscli/progress.py`: optional Rich-backed download progress with a standard-library fallback; no model is downloaded by default.
- `sportscli/providers.py`: bounded public-page discovery and supported embedded player resolution.
- `sportscli/tv.py`: live TV channel directory from free M3U playlists (iptv-org, doms9, TheAriaTV; direct streams with their required headers, on any port of a public host) and DaddyLive's 24/7 list (player pages resolved on selection), merged by channel name with news and major networks first and adult channels left out.
- `sportscli/replays.py`: replay catalog listings, search, and per-replay server discovery.
- `sportscli/relay.py`: session-only local HLS relay for tokenized, image-wrapped streams, with pooled keep-alive connections and merged live sessions.
- `sportscli/merge.py`: playlist parsing and the verified output timeline used to fail over between equivalent live sources by segment name, program date, transport-stream timestamp, or live-edge distance.
- `sportscli/grading.py`: on-demand playlist and segment checks, local grades and ranking.
- `sportscli/checks.py`: two-hour on-disk memory of source check results in the user cache directory, keyed by a hash so no URL or token is stored.
- `sportscli/ad_mute.py`: optional mpv muting for complete replay playlists with explicit ad cues.
- `sportscli/ai_ad_mute.py`, `sportscli/ai_ad_worker.py`: separately installed, optional local NBA-only frame classifier and mpv muting control. Model weights and Python packages live outside the checkout.
- `sportscli/breaks.py`: what to show during a detected break: a break-screen OSD overlay (mpv leaves OSD out of the detector's video screenshots) or another live game in a separate, always-on-top corner mpv window resolved ahead of time.
- `sportscli/tools/export_ad_model.py`: optional developer-only export of the upstream MIT-licensed model using TensorFlow 2.16.2 and Keras 3.3.3; no model files are committed.
- `sportscli/tools/train_ad_model.py`, `sportscli/tools/label_frames.py`: optional developer-only training of a small int8 multi-sport model from labeled local recordings, and frame sampling/tag conversion for labeling. Never commit recordings, frames, tags or weights.
- `sportscli/tools/record_games.py`: optional developer-only unattended recording of chosen live games at 384x216 for training; it resolves sources through `App` without a screen and must clean up with `App.close()`.
- `sportscli/tools/evaluate_ads.py`: offline evaluation against manually labeled local video; see `docs/ad-detector-testing.md` for labels, data splits, and limits.
- `tests/`: offline tests for discovery, search, playback, relay, player state and UI behavior.

Preserve the lazy workflow: global search uses a local team index and fetches at most three relevant leagues after a brief typing debounce. A separate bounded worker looks up matching players and checks at most five rosters per query, with short-lived caching; clearly label unavailable roster checks. It may preview sources for one identified live game and look up replay servers only after a replay and provider are selected. Keep UI work responsive while directories load, retain the user's selection, and discard stale results when navigating away. Live schedules hide games ESPN marks completed; explicitly offline listings and sources that fail during a session are hidden until refresh. Replay archives intentionally include finished games.

Ad mute requires an explicit run flag: `--ad-mute` uses cues from marked replays, `--ai-ad-observe` previews local NBA predictions without muting, and `--ai-ad-mute` enables experimental muting only for NBA in mpv. Model installation must occur only when `--install-ad-model` is explicitly requested; never commit weights or add the runtime to default setup. Preserve the user's own mute state, delete temporary frames, and do not transmit them. Real-broadcast tests have found false mutes during studio and bench footage; do not claim reliable live-ad detection.

Up to six supported options from each directory, and eight per screen, receive bounded background playlist and segment checks, four at a time. Those quick checks do not persist grades or hide a source after a transient failure; explicit tests and playback failures still hide it until refresh. Schedule loads, manual checks, and stream resolution run in bounded background workers. Ignore stale results after navigation or refresh, preserve the user's current source when ranking changes, and start players or terminal prompts only on the UI thread.

Live playback adds a backup source unless the user turned it off with `b`. The relay lists only segments it has already downloaded; keep failover inside the relay, never mix home and away feeds, and fall back to direct playback when no backup resolves or a playlist uses encryption, `EXT-X-MAP`, or separate audio renditions. `sportscli/__init__.py` points Python at the system certificate bundle when a Python.org build has none.

Provider URLs and playlist tokens can expire. Resolve them at selection time, do not check them into the repository, and do not save them in source-check history. Preserve required HTTP headers and keep the local relay alive while a graphical player is using it. Providers can return unsupported players or change without notice; handle failures in the UI and avoid claiming every listing works.

`README.md` is the user-facing setup and controls guide. Update it when changing keys, player choices, sources, or dependencies. Keep local settings in the existing owner-only config file and avoid committing credentials, cookies, local configuration, or captured stream URLs.
