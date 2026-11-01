#!/bin/sh
# Upload the double-click installers to the "installers" GitHub release, whose download links README uses.
# The Mac file goes in a zip, which keeps it executable after download.
set -eu
cd "$(dirname "$0")"
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
ditto -c -k --keepParent 'Install Sports CLI.command' "$out/Sports-CLI-Mac.zip"
cp 'Install Sports CLI.bat' "$out/Install-Sports-CLI.bat"
gh release view installers >/dev/null 2>&1 ||
  gh release create installers --title 'Double-click installers' \
    --notes 'Download, then double-click. Each installs or updates Sports CLI from main and opens it. See README for first-run security prompts.'
gh release upload installers --clobber "$out/Sports-CLI-Mac.zip" "$out/Install-Sports-CLI.bat"
