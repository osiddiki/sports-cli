#!/bin/sh
# Drive the native window headless like a person: Live TV, the second channel, its first
# source, play it, then Esc to the corner player. Passes when real video frames reached the
# window and the snapshots show them. Needs network: it plays a live TV channel.
# Usage: native/verify.sh [out-dir]
cd "$(dirname "$0")" || exit 1
OUT=${1:-${TMPDIR:-/tmp}/sports-native-verify}
mkdir -p "$OUT"
PATH=$HOME/.cargo/bin:$PATH cargo build --release --quiet || exit 1
LOG="$OUT/run.log"
SPORTS_NATIVE_SYNC=1 SPORTS_NATIVE_LOG=1 ./target/release/sports-native \
  --script scripts/tv-play-mini.json --frames 1500 --every 120 --out "$OUT/frames" --scale 1 >"$LOG" 2>&1
frames=$(sed -n 's/^video .*phase=Playing frames=\([0-9]*\).*/\1/p' "$LOG" | tail -1)
rejected=$(grep -c "frame rejected" "$LOG")
echo "frames pushed: ${frames:-0}   frames rejected: $rejected   snapshots: $OUT/frames"
[ "${frames:-0}" -ge 50 ] && [ "$rejected" -eq 0 ] || { echo "FAIL: no steady video (see $LOG)"; exit 1; }
echo "ok"
