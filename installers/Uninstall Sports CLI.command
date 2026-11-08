#!/bin/sh
# Double-click in Finder to remove Sports CLI. It lists what it removes and asks first.
clear
if [ -f "$HOME/.local/share/sports-cli/uninstall.sh" ]; then
  sh "$HOME/.local/share/sports-cli/uninstall.sh"
else
  curl -fsSL https://raw.githubusercontent.com/osiddiki/sports-cli/main/uninstall.sh | sh
fi
printf '\nPress Enter to close. '
read -r _
