#!/bin/sh
# Grok's council review of one shot folder through Cursor's agent CLI (signed in), read-only: grok_review.sh SHOTS OUT.md
set -eu
shots=$(cd "$1" && pwd)
council=$(dirname "$0")/../council.md
cursor=${CURSOR_CLI:-/Applications/Cursor.app/Contents/Resources/app/bin/cursor}
prompt=$(sed "s|SHOTS|$shots|" "$council")
# Run from the repo: Cursor's agent prints nothing when started in a folder it does not treat as a workspace.
cd "$(dirname "$0")/../../../.." || exit 1
TERM=xterm "$cursor" agent -p --mode ask --model "${GROK_MODEL:-grok-4.7-high}" --output-format text \
  "$prompt Open every .png in $shots as an image, not only the .txt files." </dev/null > "$2" 2>"$2.err"
[ -s "$2" ] || { echo "Grok wrote nothing; see $2.err" >&2; exit 1; }
tail -c 3000 "$2"
