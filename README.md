# Sports CLI

Find a game or replay in your terminal, choose a source, and play it in mpv, IINA, or Celluloid. Search suggestions appear as you type; the app loads only the relevant schedules and replay catalogs, then finds streams for the game you select. Its compact browsing flow takes inspiration from [ani-cli](https://github.com/pystardust/ani-cli).

Live schedules cover NBA, WNBA, NFL, NCAA football, men's and women's NCAA basketball, MLB, NHL, and soccer: MLS, Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Liga MX, and UEFA Champions League. Search includes replays from supported archives.

## Install

### Double-click install

No typing needed:

- **Mac:** download [Sports-CLI-Mac.zip](https://github.com/osiddiki/sports-cli/releases/download/installers/Sports-CLI-Mac.zip), open it, and double-click **Install Sports CLI**. The first time, macOS says it can't verify the file, because it isn't from the App Store. Click **Done**, open **System Settings → Privacy & Security**, scroll down, click **Open Anyway** next to "Install Sports CLI", then confirm.
- **Windows:** download [Install-Sports-CLI.bat](https://github.com/osiddiki/sports-cli/releases/download/installers/Install-Sports-CLI.bat) and double-click it. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.

A window opens, lists what it will install, and waits for you to press Enter. It may ask for your computer's password; nothing appears while you type it, which is normal. When it finishes, Sports CLI opens. From then on, open **Sports CLI** from Applications, Launchpad or Spotlight on a Mac, or from the Start menu on Windows. Double-clicking the installer again updates it.

### Install with one command

On a **Mac or Linux** computer, open **Terminal**, paste this line, and press Enter:

```sh
curl -fsSL https://raw.githubusercontent.com/osiddiki/sports-cli/main/install.sh | sh
```

On **Windows** 10 or 11, open **PowerShell** from the Start menu, paste this line, and press Enter:

```powershell
irm https://raw.githubusercontent.com/osiddiki/sports-cli/main/install.ps1 | iex
```

Then open **Sports CLI** from Applications (Mac), your app menu (Linux) or the Start menu (Windows), or open a new terminal window and type `sports`. On Ubuntu, if Terminal says `curl: command not found`, run `sudo apt install curl` first. On Windows, [Windows Terminal](https://aka.ms/terminal) shows the app best; it comes with Windows 11.

**Prefer not to use a terminal?** Ask an AI coding assistant that can run commands on your computer (Claude Code, Codex, Cursor and others) to *"install Sports CLI from github.com/osiddiki/sports-cli and open it for me"*. The repository's `AGENTS.md` tells it how: it explains what it will install, asks before installing, leaves any password for you to type, and opens the app in a window for you.

The installer lists what it will add and waits for Enter before changing anything. It installs only what is missing:

- **mpv**, the video player.
- **Python** 3.9 or newer, if the computer has none.
- **Homebrew**, the standard Mac package installer, on a Mac without it. It asks for your Mac password.
- On Linux, mpv and Python come from your package manager, so it may ask for your password.
- On Windows, Python and mpv come from winget, the package installer built into Windows. Without winget, install "App Installer" from the Microsoft Store first.

On a Mac or Linux the app goes in `~/.local/share/sports-cli` and the `sports` command in `~/.local/bin`; on Windows both go in `%LOCALAPPDATA%\sports-cli`. The installer adds the command's folder to your `PATH` if needed. It never installs Node.js or the ad detector unless you ask for them.

| Command | Does |
| --- | --- |
| `sports` | Start the app |
| `sports --update` | Download the latest version; your settings stay |
| `sports --setup` | Install anything missing again, such as mpv |
| `sports --setup-ppv` | Add PPV sources: installs Node.js and the PPV resolver |
| `sports --setup-ads` | Add the local ad detector, including a Python it supports |

If something is missing while you use the app, the status line names the command that fixes it.

### Manual setup

For development, or to choose each piece yourself, you need Python 3.9+ with `curses`, Git, and a player. Node.js 20+ and npm are needed only for the optional PPV resolver.

**macOS:** `brew install mpv node`, and optionally `brew install --cask iina` for [IINA](https://iina.io/), a native graphical player.

**Ubuntu / Debian:** `sudo apt install git python3 mpv nodejs npm`. Check `node --version`; if it is below 20, install a newer version from [Node.js](https://nodejs.org/). For a graphical player, optionally install [Celluloid](https://celluloid-player.github.io/) with `sudo apt install celluloid`. Other Linux distributions can use their own package manager.

**Then:**

```sh
git clone https://github.com/osiddiki/sports-cli.git
cd sports-cli
./setup.sh
./sports-cli
```

The setup script installs a pinned copy of the [PPV HLS resolver](https://github.com/Lunatic16/ppv-hls-proxy) in `resolver/` when Node.js 20+ is available, and, when pip is available, [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) for faster typo matching and [Rich](https://github.com/Textualize/rich) for download progress in `.deps/`. You can run `./sports-cli` without setup; built-in search and progress displays still work, and PPV sources need the resolver. To update a checkout later, run `git pull` and then `./sports-cli`; rerun `./setup.sh` to install or update the optional packages.

## First run

1. The app opens on Home. Choose Live sports, Replays or Live TV, or press `/` to search across everything. Use `./sports-cli --search` to open directly into search.
2. Try `stephen curry`, `warriors`, `mavs rockets`, `lakers vs blazers oct 27`, or `tamu vs alabama`. Select a player to see their team's games and replays. Team results are grouped with live broadcasts, up to ten recent replays, and upcoming games. Matching team suggestions follow below. The app checks a relevant team's upcoming schedule and, for specific NBA matchups, searches a few older replay pages in the background.
3. Use the arrow keys and `Enter` to open a game, a home or away broadcast list, or a replay. Home and away choices appear when a directory identifies those sides; open the live game to see all sources, including unlabeled ones. Replay listings for the same game are grouped; choose a provider, then a server. In search, `Ctrl-U` clears the query, `Ctrl-R` refreshes, and `Esc` goes back.
4. Select a stream and press `Enter` to play. Resolution runs in the background while you browse; going back or choosing another stream cancels the pending launch. The stream list shows its provider, broadcast side, listed or measured quality, and availability check. Press `o` to filter by provider, `a` by home or away broadcast, or `f` to filter sources by name. Press `x` to test a source, `v` to show sources that failed their checks, and `r` to refresh.
5. On macOS, switch between mpv and IINA in Settings (`,`); on Linux, between mpv and Celluloid. The graphical player must be installed first.
6. To browse archives, press `2` (or choose Replays on Home) and pick a sport. Press `s` in a game, replay or stream list to choose a different sport.

Press `?` for the in-app controls and `Ctrl-F` to return to global search from another screen. `Esc` goes back. `Ctrl-X` quits from search; `q` quits from the other lists. The schedule date is in Eastern time; game times are displayed in your machine's local timezone. Completed games are omitted from live schedules but may appear as replays.

Search uses a small bundled team index and local fuzzy matching; it does not download or run an AI model. RapidFuzz accelerates typo matching when installed; the standard-library fallback works without it. For player names of at least three characters, the app also searches ESPN in the background and checks a small number of matching team rosters. Suggestions appear while checks continue and are marked **roster unverified** until confirmed; the label remains if a roster cannot be checked. Selecting a player opens their listed team's results; a replay is a team game and does not guarantee the player appeared in it. Player lookup needs an internet connection and depends on ESPN's coverage and roster data.

Schedule searches query at most three relevant leagues. The app fetches broadcast listings only for an identified live game and looks up replay servers only after selection. Results depend on what schedules and archives provide.

When using mpv, close its window or press `q` in mpv to return to the picker. IINA and Celluloid open separate windows, leaving the picker usable. **Keep the terminal app open while playing a local relay stream.**

## Controls

The app opens on **Home**: live sports, replays, live TV, search and settings. Every key means the same thing on every screen, and each screen's footer lists the keys that work there.

| Key | Action |
| --- | --- |
| `1` `2` `3` | Live sports, replays, live TV, from anywhere (inside sports or replays, `1` or `2` changes sport) |
| `/` | Search teams, players, games, replays and TV channels |
| `,` | Open or close Settings: player, cache, terminal video, backup source, ad breaks, add-on, PPV resolver |
| `↑` `↓` (or `j` `k`) | Move |
| `Enter`, `→` or `l` | Open or play |
| `Esc`, `←`, `h` or `Backspace` | Back up one level; repeated, it reaches Home |
| `s` | Change sport, on a schedule, a replay list or their sources |
| `f` | Filter the current list (games, replays, channels, sources) |
| `r` | Refresh the current list |
| `[` `]` | Previous or next day on a schedule |
| `x`, `i` | On sources: test the selected source, or show its media URL |
| `o`, `a`, `g` | On sources: cycle provider, home or away feed (live games), rank by your checks |
| `u` | On sources: play a pasted media URL |
| `?`, `q` | Help or quit |

A key that does nothing on the current screen says so on the status line. In search, letters type into the query: `Esc` goes back, `Ctrl-U` clears and `Ctrl-X` quits. While a stream plays, keys go to mpv (Space pause, `m` mute, `q` back to the app).

`./sports-cli --tv` starts on live TV, `./sports-cli --league ncaaf` opens college football directly, and `./sports-cli --league nba --date 2026-10-31` picks a schedule date. `./sports-cli --list --league wnba` prints a plain-text schedule and the first game's streams; `./sports-cli --list --tv` prints the main channels. Run `./sports-cli --help` for all league codes.

## Sources and options

### Live directories

| Source | Coverage and behavior |
| --- | --- |
| StreamFree | Multiple sports; sources marked not live are omitted and fresh playlist URLs are resolved on selection. |
| PPV | Multiple sports and broadcast labels; uses the local PPV resolver. |
| NTV | Multiple sports and event-specific servers; some channels require the app's local relay. |
| NBA Bite, NBA Monster | Additional NBA directories; individual embedded players are resolved when selected. |
| Sports add-on | Optional compatible Stremio manifest configured with `a`; results depend on the add-on. |

A dash for broadcast side or quality means the directory did not provide it. A `~` before quality, such as `~1080p`, means the directory reported that value; a value without `~` came from a source check. Labels can be wrong and directories can list the same stream more than once. Sources that do not report live status may still be unavailable.

While choices load, the app checks every source it can sample in the background, six at a time. It downloads part of a media segment and compares the download speed with the stream's bitrate. `Smooth` means the server delivers video at least twice as fast as it plays, `OK` means fast enough, and `May lag` means it may fall behind. Sources whose check fails, such as a page with no playable stream or an `HTTP 403`, are hidden: `v` shows or hides them. On a live game in testing, none of the failed sources worked when checked again alone. The list puts working sources first, smooth ones before ones that may lag, then higher resolutions. PPV sources show `Checked on play` because they are resolved only when selected. Results appear without waiting for these checks. The app remembers each check for 2 hours, so reopening a game or channel keeps its order without checking again; a remembered result shows its age, such as `OK 40m`, and `r` on the sources screen checks everything again.

If resolution, a test, or mpv playback fails, the source is hidden for the current list. Press `r` to fetch the list again. A missing local dependency is reported without hiding the source. Some players use short-lived tokens or wrapped video segments; the session-only relay on `127.0.0.1` refreshes tokens and supplies normal HLS to the player. Its URLs stop working when the app exits.

### Live TV

Press `3` from anywhere, choose Live TV on Home, or start with `./sports-cli --tv`. `./sports-cli --list --tv` prints the main channels and their sources. News, the major networks and sports channels (ESPN, FS1, TNT, NFL RedZone, NFL Network) are listed first; NFL RedZone also leads the NFL schedule on Sundays, followed by about 2,000 other channels. Press `f` to filter by name, or find a channel from `/` search, then Enter to see its sources. They come from free playlists listed by FMHY ([iptv-org](https://github.com/iptv-org/iptv), [doms9](https://github.com/doms9/iptv) and [TheAriaTV](https://github.com/aria-tv/aria)) and from DaddyLive's 24/7 channel pages, which are resolved through the local relay when selected. Opening a channel checks its sources in the background; the QUALITY column then shows the measured resolution, and the smoothest, highest-resolution working source moves to the top. Some free streams work only from the US. Channels play like games, including the backup source; AI ad detection stays off for TV.

### Backup sources

When you play a live game, the app also resolves up to two other sources for the same broadcast side and serves the first one that works, together with your choice, through the local relay on `127.0.0.1`. The relay downloads each segment before handing it to the player. If your source freezes, stops listing new segments, or fails a segment, playback continues from the backup without the player seeing an error. When both sources re-host the same upstream stream, the relay resumes at the same segment or timestamp, so the switch is seamless. Independently encoded sources can show a short picture change, and if their clocks are unrelated mpv's time display jumps at the switch.

The backup is used only for live HLS sources that are not encrypted or split into separate audio playlists; PPV and browser-only sources are not used as backups. Home and away feeds are never mixed, because their commentary differs. If no backup resolves within about 10 seconds, the stream plays directly as before. Turn this off in Settings (`,`); the setting is saved. A backup doubles playlist requests but not video downloads, which come from one source at a time.

### Replays

The replay view uses Basketball Replays and Basketball Video for basketball, NFL Video for football, FullMatchShows for soccer, and WatchReplay for MLB and NHL. It fetches listings only for the selected league and looks up servers only after you select a replay. Soccer replay listings can include multiple competitions. Search depends on each directory; when a site blocks search, the app filters its recent listings instead. Coverage and playable server support vary, and a full match may be listed as separate parts. More replay indexes are listed by [FMHY](https://fmhy.net/video#sports-replays).

### Source checks and ranking

Press `t` to resolve the selected source and read part of one segment from your connection. The test runs in the background, so you can keep browsing; up to two manual tests can run at once. The app shows video height when it can determine it from the playlist or an optional `ffprobe` installation. This is a point-in-time check, not a sustained playback or buffering test.

The last 20 manual `t` checks are stored per provider, server, or channel; background checks do not affect grades. After at least three tests, A, B, C, and D correspond to success rates of at least 90%, 70%, 50%, and below 50%; fewer tests show `?`. The adjacent count shows the number of successful and total checks. Press `g` to rank sources: those with a recently reachable segment come first, followed by unchecked choices and then explicit HTTP failures. Previous manual results break ties within those groups. Your selection stays on the same source as new checks arrive.

### Ad mute

To enable ad mute for one run, start with `./sports-cli --ad-mute`. Normal launches leave it off. It currently works only in mpv for complete replay HLS playlists with explicit `EXT-X-CUE-OUT` / `EXT-X-CUE-IN` ad markers. The app mutes at marked breaks and restores sound afterward if it was responsible for muting. It does not download a model, send frames to a service, or run AI.

Most live streams tested do not include these markers, and this setting does not yet detect their commercials. A replay without markers, IINA, and Celluloid also play without automatic muting. Manual mute controls remain available in the player.

For an **experimental local AI detector for NBA broadcasts**, install it separately, then opt in for each run:

```sh
sports --setup-ads       # one-time optional setup
sports --ai-ad-observe   # show predictions, keep audio unchanged
sports --ai-ad-mute      # enable for this run
```

`sports --setup-ads` installs a Python the conversion tools support when the computer has none (Python 3.12 from Homebrew, your Linux package manager or winget), then runs the detector setup. From a Git checkout, run `./sports-cli --install-ad-model` instead. If setup is interrupted, running it again starts over cleanly.

The install downloads the original 150 MB model and conversion packages, verifies its checksum, and quantizes it locally. The model download shows byte progress; the later install and conversion steps show an activity indicator because their total time is unknown. Allow about 1.5 GB of temporary disk space during installation. It removes the source model and conversion environment afterward, leaving the 22 MB model and an isolated runtime in `${XDG_DATA_HOME:-~/.local/share}/sports-live/ad-mute/` (on Windows, `%USERPROFILE%\.local\share\sports-live\ad-mute`). On Linux and Apple Silicon, the finished installation takes roughly 200 MB, depending on Python and platform. Intel Macs use a larger TensorFlow runtime. Setup needs Python 3.9–3.12 (3.10–3.12 on Windows) with `venv` and pip. If the app runs on a newer Python, it searches for a compatible version. The normal launcher never installs the model automatically, and the Git checkout contains no model weights.

This detector samples video frames locally in mpv and sends nothing to an AI service. It was trained for NBA video and is disabled for other leagues. Try `--ai-ad-observe` first: it shows predictions without muting. In tests with real NBA broadcasts and commercials, the detector also classified some studio and bench coverage as ads. Automatic muting is experimental and may silence game coverage; use mpv's `m` key to override it; unmuting also ends the break screen and corner preview until coverage returns. The model is a quantized version of the MIT-licensed [NBA-redzone-ML model](https://huggingface.co/plt3/NBA-redzone-ML), and the project describes its original training and evaluation [here](https://github.com/plt3/NBA-redzone-ML).

Choose what happens during a detected break in Settings (`,`). **Mute only** is the default. **Break screen** also covers the picture with the matchup until coverage returns; the detector keeps watching the game underneath. **Other game in a corner** adds a small, always-on-top mpv window with another live game from the same schedule, with its sound on, and closes it when your game returns. The other game's stream is found in the background when playback starts, so the first break may begin before it is ready. If no other game is live, you get the break screen only. Press `q` in the corner window to close it for that break.

To collect footage for your own detector, start with `./sports-cli --record ~/ad-data/recordings`; mpv saves each stream you play there, including commercials. Recordings stay on your machine and must never be committed. For developers comparing detectors against labeled local recordings, or training a small multi-sport model, see [ad detector testing](docs/ad-detector-testing.md). An installed trained model enables AI detection for the leagues it was trained on.

### Add-ons and resolver

Set a compatible sports Stremio add-on in Settings (`,`). For example, [Sports Streams](https://sports.highfly.to/configure) offers a manifest after its one-time setup. The app queries the selected sport and generic sports catalogs for direct URLs. Pass a manifest for one run with `./sports-cli --addon 'https://…/manifest.json'`.

The PPV helper runs only when needed on `127.0.0.1:38765` and is closed on exit if the app started it. To use a different checkout, set it in Settings (`,`) or pass `--resolver-dir /path/to/ppv-hls-proxy`; run `npm install` in that checkout first. Settings are saved with owner-only permissions in `${XDG_CONFIG_HOME:-~/.config}/nba-tui/config.json`. Source-check history does not store media URLs or tokens. The app does not decrypt DRM-protected streams.

Streams and archives change frequently. Try another source or increase the cache with `c` if playback stalls. For development and agent setup, see [AGENTS.md](AGENTS.md).
