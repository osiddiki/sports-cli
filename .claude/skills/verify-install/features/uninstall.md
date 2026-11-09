# Uninstall and the double-click files

## Sub-features

- `sports --uninstall` lists what it will remove and waits for Enter; closing the window keeps everything.
- It removes the app, the `sports` command, the Applications or app-menu launcher, settings, the check cache and the ad detector.
- It takes its own PATH line out of the shell profile and leaves the rest of the profile alone.
- mpv, Python and Homebrew stay.
- The `.command` files are executable shell; the `.bat` files use CRLF; all four are on the `installers` GitHub release.

## How to get to it (user POV)

Type `sports --uninstall`, or download and double-click Uninstall Sports CLI from the README.

## Driving it with drive.sh

`drive.sh install uninstall wrappers`. Proof: the screen shows "This removes Sports CLI" and then "Sports CLI is removed"; each listed path is gone; a neighbouring app's settings file is still there; the profile keeps `export KEEP=1` and loses the installer's line; each release asset answers.

## Gotchas

- `uninstall` needs a terminal for its prompt, which `script` supplies. With no terminal and no `--yes` it removes nothing, by design.
- After changing a file in `installers/`, rerun `installers/publish.sh`; `wrappers` checks only that the assets exist, not that they match the working tree.
