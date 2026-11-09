#!/bin/sh
# Drive the installer the way a new user does, in a throwaway home folder, and keep the evidence.
#   drive.sh [feature...]     features: install run help uninstall wrappers (default: all, in that order)
# It installs the working tree as it is now (tracked files, committed or not), never touches the real
# home folder or clipboard, and removes the throwaway home when it ends. Evidence: OUT/NN-feature.txt.
set -u
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
OUT=${VERIFY_INSTALL_OUT:-$HOME/.cache/sports-cli-verify/install/$(date +%Y%m%d-%H%M%S)}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/sports-cli-verify-install.XXXXXX")
FAKE=$WORK/home
mkdir -p "$OUT" "$FAKE" "$WORK/bin"
trap 'rm -rf "$WORK"' EXIT
# A stand-in clipboard, so the run never replaces what the person has copied.
printf '#!/bin/sh\ncat > "%s/clipboard.txt"\n' "$WORK" > "$WORK/bin/pbcopy"; chmod +x "$WORK/bin/pbcopy"
cd "$REPO" || exit 1
tree=$(git stash create); tree=${tree:-HEAD}
git archive --worktree-attributes --format=tar.gz --prefix=sports-cli/ "$tree" -o "$WORK/app.tar.gz" || exit 1
printf 'not an archive\n' > "$WORK/bad.tar.gz"

as_user() { env -i PATH="$WORK/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin" HOME="$FAKE" TERM=xterm USER="${USER:-user}" TMPDIR="$WORK" SPORTS_CLI_SKIP_RESOLVER=1 "$@"; }
failed=0; number=0
check() { # check NAME COMMAND...: pass when the command succeeds
  name=$1; shift
  if "$@" >/dev/null 2>&1; then echo "  pass  $name"; else echo "  FAIL  $name"; failed=1; fi
}
# A pipeline runs in a subshell, so name the evidence file first, then pipe into keep.
shot() { number=$((number + 1)); file=$OUT/$(printf '%02d' "$number")-$1.txt; }
keep() { sed "s|$WORK|~TMP|g" > "$file"; }

install() {
  echo 'install: the one-command installer into an empty home'
  shot install
  as_user SPORTS_CLI_ARCHIVE="$WORK/app.tar.gz" sh "$REPO/install.sh" --yes 2>&1 | keep
  check 'installer says it is ready' grep -q 'Sports CLI is ready' "$file"
  check 'the sports command exists' test -x "$FAKE/.local/bin/sports"
  check 'the app is unpacked' test -f "$FAKE/.local/share/sports-cli/sports-cli"
  check 'experimental front ends are left out' sh -c "! ls '$FAKE/.local/share/sports-cli' | grep -Eq '^(native|opentui|verification)$'"
  if [ "$(uname -s)" = Darwin ]; then check 'the Applications launcher exists' test -d "$FAKE/Applications/Sports CLI.app"; fi
  check 'no help message after a good install' test ! -e "$FAKE/sports-cli-help.txt"
  check 'the install log is cleaned up' sh -c "! ls '$WORK'/sports-cli-install.* 2>/dev/null | grep -q ."
}

run() {
  echo 'run: the installed command lists TV channels (needs network)'
  shot run
  as_user "$FAKE/.local/bin/sports" --list --tv 2>&1 | keep
  check 'it prints channels with sources' grep -Eq '^(ESPN|CNN): ' "$file"
}

help() {
  echo 'help: a broken download ends with a message to paste into an AI assistant'
  rm -f "$FAKE/sports-cli-help.txt" "$WORK/clipboard.txt"
  shot help-screen
  as_user SPORTS_CLI_ARCHIVE="$WORK/bad.tar.gz" SPORTS_CLI_HOME="$WORK/elsewhere/app" sh "$REPO/install.sh" --yes 2>&1 | keep
  check 'the screen says where to paste' grep -q 'chatgpt.com, claude.ai or gemini.google.com' "$file"
  shot help-message; keep < "$FAKE/sports-cli-help.txt"
  check 'the message names the failed step' grep -q 'Step that failed: Downloading Sports CLI' "$FAKE/sports-cli-help.txt"
  check 'the message is what was copied' cmp -s "$FAKE/sports-cli-help.txt" "$WORK/clipboard.txt"
  check 'the home folder path is not in it' sh -c "! grep -q '$FAKE' '$FAKE/sports-cli-help.txt'"
  check 'a failed install leaves a working one alone' test -x "$FAKE/.local/bin/sports"
}

uninstall() {
  echo 'uninstall: sports --uninstall lists what it removes, waits for Enter, and leaves nothing'
  mkdir -p "$FAKE/.config/nba-tui" "$FAKE/.cache/sports-live" "$FAKE/.config/other-app"
  echo '{}' > "$FAKE/.config/nba-tui/config.json"; echo keep > "$FAKE/.config/other-app/settings"
  echo 'export KEEP=1' >> "$FAKE/.zprofile"
  shot uninstall
  (sleep 2; printf '\r'; sleep 3) | as_user script -q /dev/null "$FAKE/.local/bin/sports" --uninstall 2>&1 | tr -d '\r' | keep
  check 'it listed what it removes' grep -q 'This removes Sports CLI' "$file"
  check 'it said it is removed' grep -q 'Sports CLI is removed' "$file"
  for gone in .local/share/sports-cli .local/bin/sports 'Applications/Sports CLI.app' .config/nba-tui .cache/sports-live; do
    check "removed: $gone" test ! -e "$FAKE/$gone"
  done
  check 'another app is untouched' test -f "$FAKE/.config/other-app/settings"
  check 'its PATH line is gone, the rest of the profile stays' sh -c "! grep -q 'Sports CLI installer' '$FAKE/.zprofile' && grep -q 'KEEP=1' '$FAKE/.zprofile'"
}

wrappers() {
  echo 'wrappers: the double-click files are valid and published'
  for file_name in 'Install Sports CLI.command' 'Uninstall Sports CLI.command'; do
    check "valid shell: $file_name" sh -n "$REPO/installers/$file_name"
    check "executable: $file_name" test -x "$REPO/installers/$file_name"
  done
  for bat in 'Install Sports CLI.bat' 'Uninstall Sports CLI.bat'; do
    check "CRLF line endings: $bat" sh -c "! tr -d '\r' < '$REPO/installers/$bat' | cmp -s - '$REPO/installers/$bat'"
  done
  for asset in Sports-CLI-Mac.zip Install-Sports-CLI.bat Uninstall-Sports-CLI-Mac.zip Uninstall-Sports-CLI.bat; do
    check "published: $asset" curl -fsSLI -o /dev/null "https://github.com/osiddiki/sports-cli/releases/download/installers/$asset"
  done
}

[ $# -gt 0 ] || set -- install run help uninstall wrappers
for feature in "$@"; do
  case $feature in install|run|help|uninstall|wrappers) "$feature" ;; *) echo "unknown feature: $feature"; failed=1 ;; esac
done
echo "evidence: $OUT"
exit $failed
