---
name: verify-native-client
description: Formally check the native Rune window's sync with the Python app and its player lifecycle (native/src/main.rs, native/src/server.rs, sportscli/remote.py Remote.view) never shows an older screen, never applies a play out of order, and reports each player's end once. Use before and after any change to how the native window sends requests, applies views or events, or starts, replaces, fails or stops a player; and to prove the in-window video still plays (native/verify.sh).
---

# Verify the native client

## What is promised

- **ScreenNeverGoesBack.** The window never replaces a screen with an older one. Harm: the selection jumps back after a key press, or a screen you left flashes back.
- **EventsInOrder.** Play, prompt and quit events from Python apply once each, in the order Python made them. Harm: two plays close together leave you watching the older choice.
- **ExitOnce.** Python hears each player's ending once. Harm: a source is hidden twice, or a source you stopped or replaced gets hidden as failed.

`verification/native/Client.tla` models the window and Python as two processes joined by connections. Bounds: 3 key presses, 2 polls, 3 plays, one or two connections.

## Does my change touch it?

Yes when the diff changes any of these:

| Model step | Code |
|---|---|
| `YouPressAKey`, `TheTimerPolls` | `native/src/main.rs` `on_key`, `poll` (a poll only when `inflight == 0`) |
| `Answer` (one connection, in order) | `native/src/server.rs` `Server::start` worker thread and `Server::send` |
| `Receive` | `native/src/main.rs` `State::apply` (events first, then the view only if `seq` is newer) |
| `ASourceResolvesInTheBackground` | `sportscli/app.py` `poll_feed_actions` calling `play`, `sportscli/remote.py` `Headless.play` |
| `TheStreamFails`, `YouStopThePlayer` | `native/src/main.rs` the `Failed` branch of `poll`, `State::stop`, `State::open_in_mpv`, the replace branch of `apply` |
| seq numbering | `sportscli/remote.py` `Remote.view` (events drained and seq assigned under one lock) |

## Run it

```sh
verification/native/check.sh
```

Every line must say `ok`. The configs and their expected verdicts:

| Config | Expected | Meaning |
|---|---|---|
| `code` | holds | the window as built |
| `noguard` | holds | one connection keeps order even without the seq check |
| `twochannels` | EventsInOrder | polls on their own connection, the OpenTUI front end's shape |
| `twochannels-noguard` | ScreenNeverGoesBack | and with no seq check, an older screen comes back |
| `doubleexit` | ExitOnce | a stop that ignores "already reported" |

Read any result, pass or fail, with the `/statechart` skill on `Client.tla` and `view.mjs`. It gives the shortest path to a broken rule in plain words. `chart.cfg` is small enough to draw; `chart.mmd` is the current chart.

Then prove the video path on the real app (needs network, plays a live TV channel):

```sh
native/verify.sh
```

It must end `ok` with frames pushed at or above 50 and none rejected. It fails if frames stop reaching the window, as when Rune refuses a frame's colour tags.

## Bug or modeling error?

- If `code` breaks, replay the path against the real window: send the same keys in a `native/scripts/*.json` script under `SPORTS_NATIVE_SYNC=1 SPORTS_NATIVE_LOG=1` and look for the screen or exit the path predicts. If the code does not do it, the model is wrong; fix the model to match the code before trusting a pass.
- If a variant stops breaking, the model lost the step that breaks it. `ASourceResolvesInTheBackground` is what lets `twochannels` break: without plays made outside a key's own answer, no reply carries events out of order.

## Changing it on purpose

Change the model in the same commit as the code, keep each rule, and add a config that breaks on the old behaviour. Never weaken a rule to make `code` pass.

## Coverage record

Files: `native/src/main.rs`, `native/src/server.rs`, `sportscli/remote.py` (`Remote.view`, `Remote.key`, `Headless.play`). Last confirmed to describe the code at the commit that added this skill (`git log -1 --format=%h -- .claude/skills/verify-native-client`). Move this record forward only after rechecking that table against the code at the new commit.

Not covered: what Python does inside a key (the screen statechart has its own test, `tests/test_screens.py`); AVPlayer's behaviour; the relay (`verify-relay-failover`).
