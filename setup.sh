#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
commit=0f91ece96dc33cfda83948264948452bc988bb54
python=${PYTHON:-python3}
# Use the first Node.js on PATH that starts: a Homebrew upgrade can leave one that cannot load its libraries.
node_dir= node_major= node_seen=
old_ifs=$IFS; IFS=:
for dir in $PATH; do
  [ -n "$dir" ] && [ -x "$dir/node" ] || continue
  node_seen=1
  if major=$("$dir/node" -p "Number(process.versions.node.split('.')[0])" 2>/dev/null); then
    node_dir=$dir node_major=$major; break
  fi
done
IFS=$old_ifs
if [ "${SPORTS_CLI_SKIP_RESOLVER:-0}" = 1 ]; then
  echo 'Skipping the PPV resolver; everything else works without it.'
elif [ -z "$node_seen" ] || { [ -n "$node_dir" ] && ! PATH="$node_dir:$PATH" command -v npm >/dev/null 2>&1; }; then
  echo 'Node.js (20+) and npm not found; skipping the PPV resolver. Everything else works without it.' >&2
elif [ -z "$node_dir" ]; then
  echo 'Node.js is installed but does not start; skipping the PPV resolver. Everything else works without it.' >&2
elif [ "$node_major" -lt 20 ]; then
  echo 'Node.js is older than 20; skipping the PPV resolver. Install Node.js 20 or newer for PPV sources.' >&2
else
  if [ ! -f resolver/src/server.js ]; then
    if [ -e resolver ]; then echo 'resolver exists but is not the expected checkout.' >&2; exit 1; fi
    git clone https://github.com/Lunatic16/ppv-hls-proxy.git resolver
    git -C resolver checkout --detach "$commit"
  fi
  PATH="$node_dir:$PATH" npm install --prefix resolver --ignore-scripts --no-audit --no-fund
fi
if command -v "$python" >/dev/null 2>&1 && "$python" -m pip --version >/dev/null 2>&1; then
  if ! "$python" -m pip install --quiet --disable-pip-version-check --no-cache-dir --upgrade --target .deps -r sportscli/requirements.txt; then
    echo 'Optional UI or search packages could not be installed; built-in fallbacks remain available.' >&2
  fi
else
  echo 'Python pip not found; built-in UI and search remain available.' >&2
fi
if [ "${SPORTS_CLI_INSTALLER:-0}" = 1 ]; then
  :  # install.sh reports how to start the app
elif command -v mpv >/dev/null 2>&1; then
  echo 'Ready: ./sports-cli'
elif [ "$(uname -s)" = Darwin ] && { command -v iina-cli >/dev/null 2>&1 || command -v iina >/dev/null 2>&1 || [ -x /Applications/IINA.app/Contents/MacOS/iina-cli ] || [ -x "$HOME/Applications/IINA.app/Contents/MacOS/iina-cli" ]; }; then
  echo 'Ready: ./sports-cli (press p in the stream list to select IINA)'
elif [ "$(uname -s)" = Linux ] && command -v celluloid >/dev/null 2>&1; then
  echo 'Ready: ./sports-cli (press p in the stream list to select Celluloid)'
else
  echo 'Install mpv, IINA on macOS, or Celluloid on Linux, then run ./sports-cli'
fi
