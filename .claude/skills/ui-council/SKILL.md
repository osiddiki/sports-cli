---
name: ui-council
description: Hill-climb the Sports CLI terminal app's look and usability with a council of models. Shoots every main screen and task flow as a beginner's Mac shows it (80 x 24, Terminal's white Basic profile), has Claude Opus, Claude Sonnet, GPT through the Codex CLI and Grok through Cursor's agent CLI score seven categories from 1 to 10, and tabulates the rounds. Use when asked to improve or judge the UI, after a change to screens, wording, footers or layout, or to check a UI change did not make things worse.
---

# UI council

This is the loop from Theo's "Agents Sucked At UI (Until Today)": real screenshots, reviewers from different model families, scores out of 10 per category, fixes, then again. Reviewers never see a target score.

## One round

1. Shoot the current tree. It needs network, since it uses live games and channels; shoot every version you compare in the same sitting.

   ```sh
   ~/.cache/sports-cli-verify/venv/bin/python .claude/skills/ui-council/scripts/shoot.py ROUND_DIR
   ```

   The venv is the one `verify-sports-cli` makes; the script installs Pillow into it when missing. It writes `NN-name.png` (what the person sees), `NN-name.txt` and `flows.md` (the keys between shots).

2. Review, in parallel:
   - GPT: `.claude/skills/ui-council/scripts/gpt_review.sh ROUND_DIR ROUND_DIR/review-gpt.md` (Codex CLI, read-only).
   - Grok: `.claude/skills/ui-council/scripts/grok_review.sh ROUND_DIR ROUND_DIR/review-grok.md` (Cursor's agent CLI in ask mode, `grok-4.7-high`; it needs `cursor agent login` once, which the owner does).
   - Claude Opus and Claude Sonnet: one background subagent each, with this brief: "Read `.claude/skills/ui-council/council.md` and follow it exactly. SHOTS is ROUND_DIR. View every one of the .png files with your Read tool, not a sample, and read flows.md. Judge only what the screenshots and flows show; do not read the app's source code. Save your complete reply, ending with the JSON object, to ROUND_DIR/review-MODEL.md; that is the only file you may write."

3. Tabulate: `.claude/skills/ui-council/scripts/score.py ROUND_DIR...` prints every reviewer's seven scores, minimum and mean per round.

4. Fix what two or more reviewers agree on, run `python3 -m unittest` and every walk in `walk-sports-cli`, and shoot the next round.

## Reading the numbers

- One reviewer scoring the same shots twice moves up to 1 point in a category and about 0.15 in its mean. Treat smaller changes as noise, and judge a round on every reviewer, not one. Compare a reviewer only with its own earlier rounds.
- Live data changes between rounds (games start and end, sources come and go), so a screen can look different for reasons that are not the change.
- A reviewer can ask for one thing in a round and its opposite in the next. Act on agreement across reviewers and rounds.
- Never put the target in `council.md`; reviewers who know it score toward it.

## When the screenshot looks wrong

Suspect the capture before the app. `shoot.py` adds what pyte lacks and curses uses: dim text (SGR 2), scroll up and down (`CSI S`, `CSI T`), underline drawing and a braille font for the spinner. Without scrolling, old lines stayed on screen and looked like the app overlapping text. Compare with `NN-name.txt`, and with a real Terminal window when in doubt.
