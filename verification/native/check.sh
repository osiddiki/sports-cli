#!/bin/sh
# Model-check Client.tla under each config and compare with the expected verdicts.
# Usage: verification/native/check.sh   (needs Java 11+; downloads TLC 1.7.4 once, checksum-verified)
cd "$(dirname "$0")" || exit 1
JAR=${TLA2TOOLS:-$HOME/.cache/tla/tla2tools.jar}
SUM=936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88
if [ ! -f "$JAR" ]; then
  mkdir -p "$(dirname "$JAR")"
  curl -sL --max-time 120 -o "$JAR" https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar || exit 1
fi
[ "$(shasum -a 256 "$JAR" | cut -d' ' -f1)" = "$SUM" ] || { echo "tla2tools.jar checksum mismatch: $JAR"; exit 1; }
status=0
# config:expected   "holds" or the first rule TLC reports broken
#   code                 the window as built: one connection, seq check, stop checks "reported"
#   noguard              one connection keeps order even without the seq check
#   twochannels          polls on their own connection (the OpenTUI shape) reorder plays
#   twochannels-noguard  ...and without the seq check an older screen comes back
#   doubleexit           a stop that ignores "reported" tells the app twice
for case in code:holds noguard:holds twochannels:EventsInOrder twochannels-noguard:ScreenNeverGoesBack doubleexit:ExitOnce; do
  config=${case%%:*}; expected=${case#*:}
  out=$(java -XX:+UseParallelGC -cp "$JAR" tlc2.TLC -workers 1 -config "$config.cfg" Client.tla 2>&1)
  if echo "$out" | grep -q "No error has been found"; then got=holds
  else got=$(echo "$out" | sed -n 's/^Error: Invariant \([A-Za-z]*\) is violated.*/\1/p' | head -1); got=${got:-error}; fi
  if [ "$got" = "$expected" ]; then echo "ok   $config: $got"; else echo "FAIL $config: expected $expected, got $got"; status=1; fi
done
exit $status
