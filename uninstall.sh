#!/bin/sh
# Remove Sports CLI from a Mac or Linux computer: the app, the sports command, its launchers,
# settings, source-check memory and the optional ad detector. mpv, Python and Homebrew stay,
# because other apps may use them.
#   sports --uninstall          asks first
#   sh uninstall.sh --yes       does not ask
set -eu

APP=${SPORTS_CLI_HOME:-$HOME/.local/share/sports-cli}
BIN=$HOME/.local/bin
YES=0
for arg in "$@"; do
  case $arg in
    --yes) YES=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

data=${XDG_DATA_HOME:-$HOME/.local/share}
targets="$APP
$BIN/sports
$HOME/Applications/Sports CLI.app
$data/applications/sports-cli.desktop
${XDG_CONFIG_HOME:-$HOME/.config}/nba-tui
${XDG_CACHE_HOME:-$HOME/.cache}/sports-live
$data/sports-live"

found=''
old_ifs=$IFS; IFS='
'
for target in $targets; do
  if [ -e "$target" ]; then found="$found$target
"; fi
done
IFS=$old_ifs

if [ -z "$found" ]; then
  echo 'Sports CLI is not installed on this computer.'
  exit 0
fi

printf '\nThis removes Sports CLI and its settings:\n%s' "$found" | sed 's/^\//  \//'
echo 'mpv and Python stay, since other apps may use them.'
if [ "$YES" = 0 ]; then
  if [ -r /dev/tty ] && { : </dev/tty; } 2>/dev/null; then
    printf 'Press Enter to remove it, or close this window to keep it. '
    read -r _ </dev/tty || exit 1
  else
    echo 'Run again with --yes to remove it.'; exit 1
  fi
fi

IFS='
'
for target in $found; do rm -rf "$target"; done
IFS=$old_ifs

# The installer added this folder to PATH in these files; take its two lines back out.
line='export PATH="$HOME/.local/bin:$PATH"'
for profile in "$HOME/.zprofile" "$HOME/.bash_profile" "$HOME/.profile"; do
  [ -f "$profile" ] && grep -qsF '# Added by the Sports CLI installer' "$profile" || continue
  awk -v line="$line" '
    $0 == "# Added by the Sports CLI installer" { held = 1; next }
    held && $0 == line { held = 0; next }
    held { print "# Added by the Sports CLI installer"; held = 0 }
    { print }
  ' "$profile" > "$profile.sports-cli-tmp" && cat "$profile.sports-cli-tmp" > "$profile"
  rm -f "$profile.sports-cli-tmp"
done

printf '\nSports CLI is removed. To install it again, use the installer from the README.\n'
