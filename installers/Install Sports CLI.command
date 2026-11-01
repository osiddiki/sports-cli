#!/bin/sh
# Double-click in Finder to install or update Sports CLI, then open it.
cd "$HOME" || exit 1
clear
if curl -fsSL https://raw.githubusercontent.com/osiddiki/sports-cli/main/install.sh | sh; then
  printf '\nStarting Sports CLI. Next time, open "Sports CLI" from Applications, Launchpad or Spotlight.\n\n'
  exec "$HOME/.local/bin/sports"
fi
printf '\nSomething went wrong; the message above says what. Press Enter to close. '
read -r _
