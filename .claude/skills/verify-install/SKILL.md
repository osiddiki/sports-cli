---
name: verify-install
description: Drive Sports CLI's installer the way a new user does (install.sh, the sports launcher, the failed-install help message, sports --uninstall, the double-click files) in a throwaway home folder and save evidence. Use to prove a change to install.sh, uninstall.sh, setup.sh, installers/ or .gitattributes works, before publishing installers, or when asked whether install or uninstall still works.
---

# Verify the installer

The surface is a shell installer that someone with no terminal experience runs once. `scripts/drive.sh` runs it as that person would, in an empty home folder, and checks what they would see and what lands on disk. For the app's own screens use `verify-sports-cli`; for the screen walks, `walk-sports-cli`.

## Launch

There is no server. One command builds an archive of the working tree (tracked files, committed or not), makes a throwaway home, and drives every feature:

```sh
.claude/skills/verify-install/scripts/drive.sh                 # all features, about 30 s
.claude/skills/verify-install/scripts/drive.sh help uninstall  # only these (uninstall needs install first)
```

It prints one `pass` or `FAIL` line per check and exits 1 on any failure. A new file must be `git add`ed to be in the archive.

## Doctor

Run these before trusting a failure:

- `command -v mpv python3 curl tar git script` must find all six. Without mpv the installer tries Homebrew, which the throwaway home cannot use.
- `git status --short` shows what the archive will contain beyond the last commit.
- The `run` and `wrappers` features need network. Behind a proxy, run `install help uninstall` and report the other two as not provable here.

## Drive

`drive.sh` sets `HOME`, `TMPDIR` and a minimal `PATH`, so nothing reads or writes the real home folder. It puts a stand-in `pbcopy` first on `PATH`, so the real clipboard is never replaced. It passes `SPORTS_CLI_ARCHIVE` (a local archive instead of GitHub) and `SPORTS_CLI_SKIP_RESOLVER=1`. The uninstaller's "Press Enter" prompt is answered through `script`, which gives it a terminal. Each feature's steps and proof are in `features/`.

Not driven here: `sports --update` (it downloads `main` from GitHub, not the working tree; after a push, run it on a real install), Homebrew and Linux package installs (they need a password), and `install.ps1`, `uninstall.ps1` and the `.bat` files beyond their line endings (they need Windows).

## Evidence

Each run writes `~/.cache/sports-cli-verify/install/<time>/NN-feature.txt`: the screen of each step, plus the help message as saved. The throwaway path shows as `~TMP`. A proof is the `pass` lines together with those files. Quote the screen text for any step you cite. The checks look at side effects (files present or gone, the profile's contents, the stand-in clipboard), not only at what was printed.

## Cleanup

The script removes its throwaway home and archive when it exits, on success or failure. It starts no process that outlives it. Evidence stays in `~/.cache/sports-cli-verify/install/`.

## Helpers

`scripts/drive.sh [feature...]` is the only helper. Set `VERIFY_INSTALL_OUT=DIR` to choose the evidence folder.
