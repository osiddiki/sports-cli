# Sports CLI verification map

This folder is the maintained list of user-facing features and how to prove each one in the real app. Read this index, run the doctor, then follow the feature file.

## Baseline preconditions

- Run from the repo root. `scripts/doctor.sh` reports the commit and no blocker.
- `D=.claude/skills/verify-sports-cli/scripts/drive.py` is the driver; every recipe below uses `$D`.
- Network features need a shell without a sandbox proxy (the doctor says `network: no proxy`). In a sandbox, prove only the offline parts and report the rest as not provable there.
- Live listings depend on the time of day: games and sources appear near start time. Pick a live or upcoming game that exists now.

## Driving conventions

- Start each recipe from a fresh driver run; each run has its own settings and ends its own app.
- Match on text the app renders (headings, footers, status messages), never on row numbers alone.
- Take a `shot:` before and after every action you are proving.

## Proof and skip reporting

- Report the evidence folder the driver prints and the shots that show the action and its result.
- A feature with several entry points is verified only for the entry points you drove; say which.
- Report an unreachable path with the exact steps and what was missing (no live game, no source, proxy).

## Features

| ID | File | What it proves |
|---|---|---|
| navigation | [navigation.md](navigation.md) | Home, sections, sport changes, help, search, and back keys |
| live-tv | [live-tv.md](live-tv.md) | Channel list, filter, search, channel sources, `--tv` |
| sources-settings | [sources-settings.md](sources-settings.md) | Streams screen, checks, and the Settings screen |
| playback | [playback.md](playback.md) | Real resolve-and-play through mpv for a TV channel and a game |
| replays | [replays.md](replays.md) | Replay archive, sources |
| cli-list | [cli-list.md](cli-list.md) | `--list` and `--list --tv` plain output |
