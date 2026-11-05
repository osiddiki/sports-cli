---
name: verify-relay-failover
description: Formally check the live relay's backup-source failover (sportscli/relay.py Merged, sportscli/merge.py Timeline and realign) never replays or rewinds the game. Use before and after any change to segment planning, switching, realignment, skipping or the merged playlist.
---

# Verify relay failover

**Promise:** with a backup source, the player never gets a moment of the broadcast twice or an earlier one, and only jumps ahead past a segment that failed on every source. A break shows as a replayed or skipped few seconds right after a source switch.

**Tool:** TLA+ with TLC, because the question is whether bad orders of events exist (publishing, stalls, recoveries, switches, skips), which a model checker answers exhaustively. A conformance test ties the model to the real code.

## Does a change touch this?

Yes if it edits any of: `merge.Timeline` (`plan`, `commit`, `skip`, `needs_alignment`, `observe`), `merge.realign`, `merge.pts_seconds`, `merge.first_pts`, or `relay.Merged` (`pump`, `align`, `switch`, `steer`, `healthy`, `alternates`). Run `git diff 7362cbe -- sportscli/merge.py sportscli/relay.py` to see what changed since the last sync.

## Run

```sh
verification/relay/check.sh                      # model check, about 30 s; needs Java 11+
python3 -m unittest tests.test_relay_model -v    # the real code against the model's resume rule
```

`check.sh` prints one line per config and exits 1 when a verdict differs from the expected one:

| Config | Models | Expected |
|---|---|---|
| `pts.cfg` | sources share transport-stream timestamps | holds |
| `names.cfg` | shared segment names only | NoRepeat breaks (known finding) |
| `pdt.cfg` | shared program dates only | NoRepeat breaks (known finding) |
| `edge.cfg` | nothing shared | NoRepeat breaks (known finding) |
| `broken.cfg` | resuming at the last segment's own name | NoRepeat breaks (proves the check can fail) |

Read every result, pass or fail, with the pstack `/statechart` skill on `verification/relay/RelayFailover.tla` with that config and `verification/relay/view.mjs`: it gives each broken rule's shortest path in plain words, and a chart when it passes.

## Drill the real relay

```sh
python3 verification/relay/drill.py --mpv     # about a minute; needs ffmpeg and mpv
python3 verification/relay/drill.py --only "other encoder" --trace
```

It runs the real relay against two local live sources, breaks the primary mid-stream, and reports
seconds repeated, seconds skipped, the longest wait and mpv stalls. Expect zero repeats and skips
when the backup shares the primary's clock, and 1 to 5 s of repeat or skip with another encoder
(table in `docs/relay-failover.md`). A repeat with a shared clock, or any freeze, is a regression. With three sources the relay must keep the one that lines up (`Merged.best_backup`), so "other encoder listed first, same stream second" must show no repeat.

## Real bug or modeling error?

The model is in `verification/relay/RelayFailover.tla`; its header maps each action to the code. When a rule breaks, replay the path on the real code (as `tests/test_relay_model.py` does): build two playlists, commit on one, call `Timeline.plan()` on the other, and apply `merge.realign` when `needs_alignment` is true. If the real code does not repeat, the model is wrong: fix the model to match the code. Never weaken `NoRepeat`, `NoJump` or `OnlyPublished` to make a check pass.

## Known findings

`names`, `pdt` and `edge` break NoRepeat: when the new source has not yet published the matching segment, `Timeline.plan()` falls back to keeping the distance from the live edge instead of waiting, and replays one or two segments. Transport-stream realignment (`pts`) waits correctly, which covers backups from the same encoder. `tests/test_relay_model.py::test_known_findings_names_dates_and_edge_fall_back_and_repeat` records today's behavior; when a fix lands, flip those expectations in the test and in `check.sh` to `holds`.

## Coverage and sync

Covers `sportscli/merge.py` (Timeline, realign) and `sportscli/relay.py` (Merged.pump, align, switch, steer, healthy) as of commit **7362cbe** plus `steer`: leaving a source slower than real time maps to Fail, and returning to the chosen source from a same-clock backup is the Return action. Leaves out: segment downloads and retries other than skip, playlist parsing, encryption and init maps (the relay refuses those streams), more than two sources, and timing beyond segment order. Bounds: 6 segments, backup 1 segment behind. Move the sync commit forward only after confirming the model's actions still describe the code at the new commit. Diagrams and the summary for readers live in `docs/relay-failover.md`.
