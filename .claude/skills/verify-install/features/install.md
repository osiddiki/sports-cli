# Install and first run

## Sub-features

- The installer lists what it will add, installs only what is missing, unpacks the app and writes the `sports` command.
- On a Mac it adds `Sports CLI.app` to the user's Applications folder; on Linux, a `.desktop` entry.
- The download leaves out `native/`, `opentui/`, `verification/`, `.claude/` and `.github/`.
- A good install leaves no help message and no log behind.

## How to get to it (user POV)

Paste `curl -fsSL https://raw.githubusercontent.com/osiddiki/sports-cli/main/install.sh | sh` into Terminal, or double-click Install Sports CLI. Then open Sports CLI from Applications or type `sports`.

## Driving it with drive.sh

`drive.sh install run`. Proof: "Sports CLI is ready" on screen; `~/.local/bin/sports` is executable; the app folder has `sports-cli` and none of the experimental folders; `sports --list --tv` prints lines such as `ESPN: ...`.

## Gotchas

- `run` needs network, and the channel list depends on public playlists being up.
- The archive holds tracked files only. An untracked new file is missing until `git add`.
- A Node.js that cannot start is treated as absent; the installer says so in one line and skips the PPV add-on.
