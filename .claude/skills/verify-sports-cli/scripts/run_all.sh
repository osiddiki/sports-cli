#!/bin/sh
# Run every recipe in features/ once and write a pass/fail summary next to the evidence.
# Usage: .claude/skills/verify-sports-cli/scripts/run_all.sh   (from anywhere; needs network without a proxy)
cd "$(dirname "$0")/../../../.." || exit 1
D=.claude/skills/verify-sports-cli/scripts/drive.py
RUN=${RUN:-$HOME/.cache/sports-cli-verify/runs/all-$(date +%Y%m%d-%H%M%S)}
mkdir -p "$RUN"
SUMMARY="$RUN/summary.txt"
.claude/skills/verify-sports-cli/scripts/doctor.sh > "$RUN/doctor.txt" 2>&1
pass=0; fail=0
check() {  # check ID [drive.py arguments...]
  id=$1; shift
  if "$D" --out "$RUN/$id" "$@" > "$RUN/$id.log" 2>&1; then
    echo "PASS $id" | tee -a "$SUMMARY"; pass=$((pass+1))
  else
    echo "FAIL $id (see $RUN/$id.log)" | tee -a "$SUMMARY"; fail=$((fail+1))
  fi
}
shell_check() {  # shell_check ID command...
  id=$1; shift
  if "$@" > "$RUN/$id.out" 2> "$RUN/$id.err"; then
    echo "PASS $id" | tee -a "$SUMMARY"; pass=$((pass+1))
  else
    echo "FAIL $id (exit $?; see $RUN/$id.err)" | tee -a "$SUMMARY"; fail=$((fail+1))
  fi
}

H='expect:LIVE|LATER|No games'
check nav-home "$H" 'expect:Tonight +By sport +Replays' shot:home key:Up 'expect:Tonight.s games, live ones first' key:Right "expect:One sport's games" key:Enter 'expect:By sport: choose a sport' key:Enter 'expect:NBA  ·' shot:schedule key:Esc 'expect:choose a sport' key:Esc "expect:One sport's games" shot:back-home
check nav-help --app-args="--league nba" 'expect:NBA  ·' 'key:?' 'expect:Controls and setup' shot:help key:Esc 'expect:NBA  ·' shot:back
check nav-search "$H" key:/ 'expect:Find:' type:lakers 'expect:Find: lakers' shot:search key:Esc 'expect:^ Tonight' shot:back
check nav-date --app-args="--league nba" 'expect:NBA  ·' shot:today 'key:]' 'expect:NBA  ·' shot:tomorrow
check nav-sport --app-args="--league nba" 'expect:NBA  ·' key:s 'expect:choose a sport' key:Down key:Enter 'expect:WNBA  ·' shot:wnba
check tv-open "$H" key:4 'expect:ESPN +\d+ streams?' shot:channels key:Esc 'expect:Sports and news channels' shot:back
check tv-start --app-args="--tv" 'expect:ESPN +\d+ streams?' shot:start
check tv-filter --app-args="--tv" 'expect:ESPN +\d+' key:f 'expect:Filter channels' type:weather key:Enter 'expect:match "weather"' shot:filtered
check tv-search "$H" key:/ type:cnbc 'expect:TV CHANNELS' shot:results key:Enter 'expect:^ Streams' shot:sources key:Esc 'expect:Find: cnbc'
check tv-sources --app-args="--tv" 'expect:ESPN +\d+' key:Down key:Enter 'expect:ESPN2' 'expect:^ Streams' wait:8 'expect:Stream 1' shot:sources
check settings "$H" key:, 'expect:Backup stream for live games' shot:settings key:Down key:Down key:Down key:Enter 'expect:Backup source off' key:Down key:Down key:Down key:Enter 'expect:When an ad is detected: break screen' key:Up key:Up key:Up key:Up key:Up key:Enter 'expect:Buffer before playing: 30 seconds' shot:changed key:Esc "$H" key:4 'expect:ESPN +\d+' key:Enter 'expect:backup off' 'expect:buffer 30s' shot:sources
check play-tv --mpv-seconds 8 --app-args="--tv" 'expect:ESPN +\d+' key:Enter 'expect:^ Streams' 'expect:Stream 1' shot:sources key:Enter 'expect:Returned from mpv' shot:returned
check nav-keys --app-args="--league nba" 'expect:NBA  ·' key:s 'expect:choose a sport' key:Esc 'expect:NBA  ·' key:h 'expect:By sport: choose a sport' key:l 'expect:NBA  ·' key:, 'expect:Backup stream' key:, 'expect:NBA  ·' key:z 'expect:z does nothing here' shot:keys
check rep-list "$H" key:3 'expect:Replays: choose a sport' key:Enter 'expect:replays found' shot:replays key:Enter 'expect:Replay sources from|^ \d+ sources$' 'expect:^ Streams' shot:sources key:Esc 'expect:^ Replays$' shot:back
shell_check list-games python3 sports-cli --list --league nba
shell_check list-tv python3 sports-cli --list --tv

echo "$pass passed, $fail failed. Evidence: $RUN" | tee -a "$SUMMARY"
[ "$fail" -eq 0 ]
