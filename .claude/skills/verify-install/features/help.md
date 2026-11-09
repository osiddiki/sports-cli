# Help after a failed install

## Sub-features

- Any failure writes `~/sports-cli-help.txt`: a plain request, the failed step, what the installer said, the system, tools already present, and the last 40 lines printed.
- The home folder path is replaced by `~` in that message.
- The message is copied to the clipboard (`pbcopy`, `wl-copy`, `xclip` or `xsel`); without one, the screen says where the file is.
- The screen names chatgpt.com, claude.ai and gemini.google.com. The installer calls no AI service itself.
- Ctrl-C is not treated as a failure.

## How to get to it (user POV)

The install stops. The last lines say "Stuck? An AI assistant can walk you through it, for free." They open one of the three sites and paste.

## Driving it with drive.sh

`drive.sh install help` gives the installer a file that is not an archive. Proof: the screen names the three sites; the saved message contains "Step that failed: Downloading Sports CLI"; the stand-in clipboard holds the same bytes; the message does not contain the home path; the earlier good install still has its `sports` command.

## Gotchas

- The failing run installs to a second folder (`SPORTS_CLI_HOME`), so it cannot damage the good install being checked.
- Only one failure is driven. A failure inside Homebrew or a package manager takes the same path but is not reproduced here.
