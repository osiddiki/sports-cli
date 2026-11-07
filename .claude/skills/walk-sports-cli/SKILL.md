---
name: walk-sports-cli
description: Rerun scripted screen walks of the Sports CLI terminal app (./sports-cli) and keep a ledger of every step, recording which screen it was on, which key or click it sent, and what showed up, with a pass, fail or no-effect verdict. Use after any change to screens, keys, footers, status text or clicking, to recheck the same walk before and after a change, or when asked whether a screen still works. Clicks are recorded, not fixed.
---

# Walk Sports CLI

A walk is a file of steps. `scripts/walk.py` plays each walk in a fresh app in a pseudo-terminal and writes a ledger. The script decides every verdict, so the agent running it only reads the ledger. That makes a walk cheap to rerun on a small model.

This skill checks screens and keys. For playback through mpv, prompts and Settings toggles, use the recipes in `.claude/skills/verify-sports-cli/features/`. `tests/test_screens.py` already checks the screen chart offline. A walk checks the real screens.

## Run

1. From the repo root, run `.claude/skills/verify-sports-cli/scripts/doctor.sh`. If it prints a proxy line, run only the offline walks and report the network walks as not provable in this shell.
2. Run the walks:

```sh
W=.claude/skills/walk-sports-cli
$W/scripts/walk.py $W/walks/home.walk $W/walks/global-keys.walk $W/walks/clicks.walk   # offline
$W/scripts/walk.py $W/walks/*.walk                                                    # all, needs network
```

The first run creates the same `pyte` venv as `verify-sports-cli`. If `python3 -m venv` fails, install the `python3-venv` package. Each walk gets its own settings folder and headless mpv, and the script ends only the app it started.

3. Read `summary.md` in the evidence folder it prints (`--out DIR`, default `~/.cache/sports-cli-verify/walks/<time>/`). For each walk with a fail or a no effect, open its `ledger.md`.

## Hand the walk to a cheap model

A screen walk needs no judgment. Spawn a subagent on the cheapest fast model you have, for example `composer-2.5-fast`, with this brief:

> In `<repo>`, run `.claude/skills/verify-sports-cli/scripts/doctor.sh`, then `.claude/skills/walk-sports-cli/scripts/walk.py <walk files>`. If the doctor prints a proxy line, run only the offline walks. Do not edit any file. Reply with the doctor's output, the evidence folder, `summary.md` verbatim, and every ledger row whose verdict is `fail` or `no effect`, verbatim, with its shot path.

Read the rows it returns yourself before you report them.

## What a pass records

Each `ledger.md` starts with the commit, the exact rerun command, the app arguments, and which mouse modes the app had on. Then it has one row per step:

| Column | Holds |
|---|---|
| Screen | the heading before the step |
| Action | the key, text or click sent; a click also gives its row and column and whether the terminal sent anything |
| Expected | the step's regex |
| Showed | the heading, the selected row and the status line after the step |
| Verdict | `pass`, `fail`, `no effect`, `seen` or `not run` |
| Shot | `NN.txt`, the full screen after the step |

`verdicts.txt` lists step, verdict and action. Compare two runs with `diff OLD/home/verdicts.txt NEW/home/verdicts.txt`.

Report the evidence folder and its counts. For any step you cite, give the screen, the action and what showed, quoted from the ledger.

## How a failure shows

- `fail` means the expected text never showed within 20 s, or it was already on screen before the step. The second case means the step proved nothing, and **Showed** says so.
- The walk stops at its first failure. Later steps are `not run`, and the script exits 1.
- `NN.txt` holds the screen the app was on at that moment.

Report each failure as the walk, step, expected text, what showed, and the shot path. Do not fix the app in this skill. If the change under test meant to alter that screen, update the walk in the same change. Otherwise the app broke, so report it.

## Clicks

Clicking is not built. The app never turns on mouse reporting, so a real terminal sends no click. `walk.py` sends a click only when the app has turned on a mouse mode (1000, 1002, 1003 or 1006). Otherwise it sends nothing and records why.

A click that leaves the heading and the selected row unchanged gets `no effect`. The walk continues, and the exit status stays 0. `walks/clicks.walk` gives each click the screen a working click would open. Today every click there reads `no effect`. Record that result and leave the app alone. Once clicking is built, the same walk passes or fails with no edits.

## Write a walk

Run `scripts/walk.py --help` for the step grammar (`args`, `start`, `key`, `type`, `click`, `wait`, `=> REGEX`).

- Use one file per user path, in `walks/`, starting from the app's first screen.
- Give every key and click an expectation the screen did not show before the step. Anchor headings with `^ Heading`. Every screen line starts with a space, so `^ Settings` matches the Settings heading and not the Home row named Settings.
- Match text the app renders: headings, the selected row (`> WNBA`) and status messages. Never match on row numbers alone, or on dates and counts that change daily.
- Put `# Needs network.` on the first line of any walk that loads schedules, channels or replays.
- When you find the same problem twice by hand, add a walk line that catches it.

## Why it works this way

These rules come from Lauren Tan's (@poteto) posts about building tibo.dog with pstack. X refused the API call with `client-not-enrolled`, so they were read on the public mirror [tibopoteto.lol/people/poteto](https://tibopoteto.lol/people/poteto).

- "bots play the game headless to tune each difficulty against target survival rates" (Nov 1). So walks run headless against fixed expectations.
- "the simulation is fully deterministic, so every run replays exactly from its inputs" (Nov 1). So the walk file is the input, and each ledger names its commit and rerun command.
- "pushing stuff up into determinism - much more affordable and reproducible than spinning up agent swarms for everything" (Nov 7). So the script decides verdicts and a cheap model only runs it.
