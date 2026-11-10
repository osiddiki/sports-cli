#!/bin/sh
# Run every check that covers what changed, before a commit lands.
#   scripts/gate.sh            changes since the last commit (staged, unstaged, untracked)
#   scripts/gate.sh REV        changes since REV, for example origin/main
# Prints one PASS or FAIL line per check and a log folder; exits 1 if any check failed.
cd "$(dirname "$0")/.." || exit 1
base=${1:-HEAD}
LOG=${GATE_LOG:-$HOME/.cache/sports-cli-verify/gate/$(date +%Y%m%d-%H%M%S)}
mkdir -p "$LOG"
changed=$( { git diff --name-only "$base"; git ls-files --others --exclude-standard; } | sort -u)
touches() { echo "$changed" | grep -Eq "$1"; }
failed=0
run() {  # run NAME COMMAND...
  name=$1; shift
  if "$@" > "$LOG/$name.log" 2>&1; then echo "PASS $name"; else echo "FAIL $name (see $LOG/$name.log)"; failed=1; fi
}

run unit-tests python3 -m unittest
run compile sh -c 'python3 -m py_compile sports-cli sportscli/*.py sportscli/tools/*.py && sh -n setup.sh install.sh uninstall.sh installers/*.command installers/publish.sh'
if touches '^sportscli/(app|remote|screens|replays|tv|web)\.py|^\.claude/skills/(walk|verify)-sports-cli/'; then
  run screen-walks .claude/skills/walk-sports-cli/scripts/walk.py --out "$LOG/walks" .claude/skills/walk-sports-cli/walks/clicks.walk \
    .claude/skills/walk-sports-cli/walks/global-keys.walk .claude/skills/walk-sports-cli/walks/home.walk \
    .claude/skills/walk-sports-cli/walks/live-tv.walk .claude/skills/walk-sports-cli/walks/replays.walk \
    .claude/skills/walk-sports-cli/walks/schedule.walk
  run app-recipes env RUN="$LOG/recipes" .claude/skills/verify-sports-cli/scripts/run_all.sh
fi
if touches '^(install|uninstall|setup)\.(sh|ps1)$|^installers/|^\.gitattributes$|^\.claude/skills/verify-install/'; then
  run install env VERIFY_INSTALL_OUT="$LOG/install" .claude/skills/verify-install/scripts/drive.sh
fi
if touches '^sportscli/(relay|merge)\.py|^verification/relay/'; then
  run relay-models verification/relay/check.sh
  run relay-conformance python3 -m unittest tests.test_relay_model tests.test_backup_choice_model
fi
if touches '^native/|^sportscli/remote\.py|^verification/native/'; then
  run native-model env TLA2TOOLS="$HOME/.cache/tla/tla2tools.jar" verification/native/check.sh
  run native-build sh -c 'cd native && PATH="$HOME/.cargo/bin:$PATH" cargo test --release'
fi
echo "logs: $LOG"
exit $failed
