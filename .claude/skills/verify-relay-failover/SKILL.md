---
name: verify-relay-failover
description: Formally check the live relay's backup-source failover (sportscli/relay.py Merged, sportscli/merge.py Timeline and realign) never replays or rewinds the game, and that a stream with several backups starts on time with the backup that lines up. Use before and after any change to segment planning, switching, realignment, skipping, the merged playlist, or how backups are loaded and chosen at the start (Merged.prepare, best_backup).
---

# Verify relay failover

**Promise:** with a backup source, the player never gets a moment of the broadcast twice or an earlier one, and only jumps ahead past a segment that failed on every source. A break shows as a replayed or skipped few seconds right after a source switch.

**Tool:** TLA+ with TLC, because the question is whether bad orders of events exist (publishing, stalls, recoveries, switches, skips), which a model checker answers exhaustively. A conformance test ties the model to the real code.

## Does a change touch this?

Yes if it edits any of: `merge.Timeline` (`plan`, `commit`, `skip`, `needs_alignment`, `observe`), `merge.realign`, `merge.pts_seconds`, `merge.first_pts`, or `relay.Merged` (`pump`, `align`, `switch`, `steer`, `healthy`, `alternates`). Those belong to `RelayFailover.tla`. Yes too if it edits `Merged.prepare`, `best_backup`, `shares_timeline`, `clock`, `PREPARE_WAIT` or `SAME_CLOCK`: those belong to `BackupChoice.tla`. Run `git diff 7362cbe -- sportscli/merge.py sportscli/relay.py` to see what changed since the last sync.

## Run

```sh
verification/relay/check.sh                      # model check, about 30 s; needs Java 11+
python3 -m unittest tests.test_relay_model -v    # the real code against the model's resume rule
python3 -m unittest tests.test_backup_choice_model -v   # the real chooser against BackupChoice, 1,884 cases, 2 s
```

`check.sh` prints one line per config and exits 1 when a verdict differs from the expected one:

| Config | Models | Expected |
|---|---|---|
| `pts.cfg` | sources share transport-stream timestamps | holds |
| `names.cfg` | shared segment names only | NoRepeat breaks (known finding) |
| `pdt.cfg` | shared program dates only | NoRepeat breaks (known finding) |
| `edge.cfg` | nothing shared | NoRepeat breaks (known finding) |
| `broken.cfg` | resuming at the last segment's own name | NoRepeat breaks (proves the check can fail) |
| `choice.cfg` | `BackupChoice`: three backups of every kind, loading in every order around the deadline | holds |
| `waitforall.cfg` | `BackupChoice` with the old start, which waited for every playlist | StartsOnTime breaks (proves the check can fail) |
| `firstready.cfg` | `BackupChoice` keeping the first loaded backup without comparing | KeepsTheOneThatLinesUp breaks (proves the check can fail) |

**Start promise (`BackupChoice.tla`):** the primary must load; a backup that has not answered by `Merged.PREPARE_WAIT` never delays a ready primary; the backup kept had loaded in time, and is the first offered that lines up with the primary, else the first that loaded. A break shows as a slow start, or as replayed or skipped seconds at the first failover because a backup from another encoder was kept over one that lined up.

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

`tests/test_backup_choice_model.py` runs the real `Merged.prepare()` for every mix of one to three backups (each of the model's six kinds, loaded or failed) and requires the kept backup to equal the model's `Best(Ready)`, with each segment downloaded at most once and none when playlists settle it. It does not use real time: the deadline itself is covered by `tests/test_merge.py::test_a_backup_whose_playlist_is_slow_does_not_hold_up_playback`.

## Known findings

`names`, `pdt` and `edge` break NoRepeat: when the new source has not yet published the matching segment, `Timeline.plan()` falls back to keeping the distance from the live edge instead of waiting, and replays one or two segments. Transport-stream realignment (`pts`) waits correctly, which covers backups from the same encoder. `tests/test_relay_model.py::test_known_findings_names_dates_and_edge_fall_back_and_repeat` records today's behavior; when a fix lands, flip those expectations in the test and in `check.sh` to `holds`.

## Coverage and sync

`BackupChoice.tla` covers `Merged.prepare`, `best_backup`, `shares_timeline` and `clock` as of commit **b597346**. Bounds: three backups, a deadline of two ticks. It leaves out the time the comparison's segment downloads take (each has its own 15 s timeout, outside the deadline), playlist parsing, and everything after the stream starts.

`RelayFailover.tla` covers `sportscli/merge.py` (Timeline, realign) and `sportscli/relay.py` (Merged.pump, align, switch, steer, healthy) as of commit **7362cbe** plus `steer`: leaving a source slower than real time maps to Fail, and returning to the chosen source from a same-clock backup is the Return action. Leaves out: segment downloads and retries other than skip, playlist parsing, encryption and init maps (the relay refuses those streams), more than two sources, and timing beyond segment order. Bounds: 6 segments, backup 1 segment behind. Move the sync commit forward only after confirming the model's actions still describe the code at the new commit. Diagrams and the summary for readers live in `docs/relay-failover.md`.
