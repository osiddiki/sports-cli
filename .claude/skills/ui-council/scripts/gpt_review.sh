#!/bin/sh
# GPT's council review of one shot folder through the Codex CLI: gpt_review.sh SHOTS OUT.md
set -eu
shots=$(cd "$1" && pwd)
council=$(dirname "$0")/../council.md
images=''
for image in "$shots"/*.png; do images="$images -i $image"; done
prompt=$(sed "s|SHOTS|$shots|" "$council")
# shellcheck disable=SC2086
codex exec --skip-git-repo-check --ephemeral -s read-only -C "$shots" -c model_reasoning_effort=high \
  $images -o "$2" "$prompt" </dev/null >/dev/null 2>&1
tail -c 3000 "$2"
