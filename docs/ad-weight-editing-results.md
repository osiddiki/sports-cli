# Ad detector: trained heads versus heads written or edited by reasoning

This experiment asks whether the weights of the ad detector's two small
heads are better learned by training, or written and edited directly by
an LLM (Claude, in this session) from what it reasons each weight should
compute. It was run on 5 November 2026. Two Liquid vision-language models
were tried as well, as a secondary question; those results are in a
separate section at the end.

Inspiration: "Sparks of Weight-Space Programming"
(rikiyat.github.io/blog/weight-space-programming), where a language
model read and wrote network weights directly. Their best result came
from a targeted edit, and editing could not add an ability the model
lacked. Our results match that pattern, with one exception (edit E1 below).

## Setup

Everything except the heads was fixed:

- **Encoder:** the frozen MobileCLIP2-S0 (dfndr2b) image encoder from
  `sportscli/tools/train_clip_ad_model.py`, fed the whole frame squashed to
  256x256, one frame every 2 seconds. Each head reads its 512-number
  feature.
- **Heads:** the shipped model computes `min(ad head, show head)` as the
  keep-sound score:
  - The ad head is one logistic unit, 512 weights plus a bias.
  - The show head is a softmax over six label rows (ad, arena, bench,
    broadcast, overlay, studio), each 512 weights plus a bias. Its score is
    the probability of the keep classes.
  - Every arm below produces numbers in this same format, so any of them
    exports to the same TFLite file.
- **Playback decision:** each held-out broadcast is replayed in order
  through the app's own decision (`ai_ad_mute.Decision`: mute after two
  scores at or below 0.2, unmute at 0.8). The metrics count what playback
  would have muted, not single frames.
- **Held-out games:** three folds, split by broadcast, so no frame from a
  held-out game is ever used to train, write or edit:
  - **Fold A:** NHL Jets at Panthers, college football Notre Dame at Navy,
    EPL Manchester United at Chelsea, NFL Network, NBA Rockets at
    Mavericks.
  - **Fold B:** college football Wisconsin at Iowa and Bears at Knights,
    NHL Blackhawks at Bruins, ESPN (SportsCenter and college football),
    NBA Knicks at 76ers.
  - **Fold C:** NFL Ravens at Bills, NBA Celtics at Magic on NBC Sports
    Boston.
  - The tables pool all three folds: 5,605 ad samples, 771 arena-show
    samples, 1,000 studio samples, 1,721 bench samples and 12.7 hours of
    game coverage.
- **Labels:** the v6 hand labels (`docs/ad-detector-testing.md`). `arena`
  means in-venue shows: dancers, mascot acts, fan contests, fan cams and
  halftime acts. Crowd shots, the anthem and huddles are `bench` and keep
  sound.
- **Metrics:**
  - **Ads muted:** share of ad samples muted.
  - **Arena shows muted:** share of arena samples muted.
  - **False-mute starts per game hour:** mutes that begin during game
    coverage (`broadcast` or `overlay`), per hour of game coverage. A mute
    that starts in an ad and runs a few seconds into the game is not
    counted here.
  - **Game time muted:** share of game samples muted, for any reason.
  - **Studio and bench muted:** share of those samples muted. AGENTS.md
    records false mutes on studio and bench footage.

## Result in one table

All three folds pooled:

| Arm | Ads muted | Arena shows muted | False-mute starts per game hour | Game time muted | Studio muted | Bench muted |
|---|---|---|---|---|---|---|
| 1 Trained (shipped: studio weight 4) | 95.7% | 44.7% | 3.4 | 1.08% | 3.7% | 2.6% |
| 1 Trained, studio weight 8 | 95.9% | 44.7% | 3.3 | 1.05% | 2.9% | 2.7% |
| 1 Trained, studio weight 16 | 95.5% | 44.9% | 3.3 | 1.00% | 2.4% | 2.7% |
| 2 Written from text, 0 batches seen | 44.5% | 61.0% | 20.1 | 5.88% | 0.0% | 20.4% |
| 2 Written, 10 batches | 60.9% | 31.9% | 1.9 | 0.38% | 0.0% | 5.7% |
| 2 Written, 25 batches | 71.7% | 23.6% | 2.5 | 0.51% | 0.6% | 4.8% |
| 2 Written, 50 batches | 72.1% | 29.8% | 2.7 | 0.61% | 0.6% | 8.7% |
| 2 Reference: class means of the same 50 batches (computed, not reasoned) | 81.6% | 37.7% | 6.7 | 1.32% | 3.4% | 7.0% |
| **3b Edit E1: studio concept into the ad head (513 weights)** | 94.3% | 43.1% | 3.5 | 1.00% | **0.7%** | 2.4% |
| 3b Control for E1: ad bias only, same training recall (1 weight) | 95.4% | 44.1% | 3.3 | 1.00% | 3.1% | 2.6% |
| 3b Edit E2b: arena bias +0.5 (1 weight) | 95.8% | 60.6% | 4.7 | 1.49% | 3.7% | 3.9% |
| 3b Edits E1 + E2b (514 weights) | 94.5% | 58.9% | 4.8 | 1.40% | 0.7% | 3.8% |
| 3a Data-derived edit: 4 ad-head weights zeroed | 94.2% | 42.0% | 3.3 | 0.91% | 1.2% | 2.2% |
| 3a Control: bias shift, same training recall | 93.6% | 42.5% | 3.1 | 0.80% | 1.5% | 2.3% |
| 3a Data-derived edit: 16 weights zeroed | 91.5% | 41.6% | 2.2 | 0.57% | 0.5% | 2.2% |
| 3a Control: bias shift, same training recall | 91.3% | 41.0% | 2.4 | 0.56% | 0.5% | 2.2% |
| 3a Data-derived edit: rank-1 removal (512 weights) | 91.1% | 41.0% | 2.1 | 0.54% | 0.5% | 2.3% |

Raw counts and the per-league tables are at the end of this page.

## What we learned

1. **Training wins at building the heads.** Heads written from text
   prompts reached at most 72% of ads, against 96% for the trained
   heads:
   - Most of the written heads' gain came from the first 10 batches. After
     that, edits traded one error for another.
   - Plain class means of the same 1,600 frames mute more ads (82%), with
     twice the false mutes. So the written heads are limited by what text
     prompts can express, not only by the frame budget.
   - The written heads did have fewer game false mutes (1.9 to 2.7 starts
     per game hour against 3.4) and almost no studio mutes. They are
     cautious because they never become confident about ads.
2. **One reasoned edit beat training at its own target.** Edit E1 adds
   the text direction for "studio desk / news anchor / studio video wall"
   to the trained ad head's keep side:
   - Held-out studio false mutes fell from 3.7% to 0.7%.
   - The two training-side fixes managed 2.9% (studio weight 8) and 2.4%
     (weight 16). The bias-only control at the same training ad recall
     stayed at 3.1%.
   - The cost is 1.4 points of ads overall: ESPN 90 to 86%, NHL 90 to 86%.
     It also cuts NHL arena shows from 38 to 23% of 39 samples, which the
     ad head had been muting.
   - Why it can beat training: the held-out studio shows (college-football
     studio, SportsCenter) look little like the few labeled studio frames
     in training. The text direction carries what the encoder learned
     about studios in general, and no amount of reweighting the training
     frames supplies that.
3. **Most edits only moved the threshold.** Every data-derived edit (3a)
   and every arena edit matched a one-number bias shift with the same
   training recall, within noise. Adding arena concepts (light shows, fan
   cams, jumbotron games, mascot acts) to the arena row gained no more
   arena frames per false mute than raising its bias. The trained row
   already points at those concepts; it only sits at a cautious threshold.
4. **Arena shows are a trade the user should choose.** Raising the arena
   bias by 0.5, one weight, mutes 61% of arena shows instead of 45%. It
   also adds 1.3 false-mute starts per game hour, all of them NBA (7.2 to
   10.8), where pregame intros, court light shows and jumbotron graphics
   are labeled as game coverage.

## Recommendation

- **Ship E1** on top of the trained heads, after a check on new studio
  footage. It is the only change here that beat training on held-out
  games, and it costs about 1.4 points of ad recall.
  - **Also needed before shipping:** NHL ad recall, the ESPN ad loss and
    NHL arena shows need a look first.
  - **How to ship it:** apply it in `train_clip_ad_model.py` after
    `fit()`, so every retrain gets the same edit, rather than as a
    hand-patched file.
- **Arena bias +0.5 is the user's call:** more arena shows muted, more
  false mutes on NBA broadcasts. Leave it off by default.
- **Keep training for the heads themselves.** Writing heads from text is
  not competitive for ads.
- **Data-derived direction removal (3a)** adds nothing over a threshold
  change. Don't ship it.
- None of this makes live-ad detection reliable. False mutes remain on
  NBA game coverage (3 to 7 starts per game hour).

## Arm 2: how the written heads were written

**Rules:**
- No optimizer and no fitting.
- I chose prompts, a scale and per-class biases. Each class row is the
  scale (100, the CLIP logit scale) times the normalized mean text
  embedding of its prompts.
- The ad head was switched off (constant keep), so the show head decides.
- That is 3,078 numbers in the show head, all written.

**Evidence I could see:**
- Fixed batches of 32 frames, stratified over the six labels, taken only
  from broadcasts in no held-out fold. Budget K always means the same
  first K batches.
- For each batch: per-label counts of frames that would be muted, which
  class won, and the mean similarity to each class's text.
- From the second round on, a contact sheet of every misclassified frame
  in the new batches.

**Rounds:**
- **0 batches:**
  - Prompts written from the label definitions, before seeing any frame:
    6 ad, 6 arena, 5 bench, 5 broadcast, 2 overlay, 3 studio.
  - On held-out games it muted 20 game segments per hour. The arena
    prompts mention courts and games, so game frames drifted to arena.
- **10 batches:**
  - Readings:
    - Ad frames sat only 0.013 closer to ad text than to overlay text,
      while overlay frames sat 0.076 further.
    - Studio frames matched studio text no better than overlay text.
    - Broadcast frames went to arena 19 of 53 times.
  - Edits:
    - Rewrote the arena prompts to describe non-game things (matching
      outfits, a costume, an empty court, a fan for the camera).
    - Added six ad prompts for kinds of ads seen (products on plain
      backgrounds, family insurance scenes, scenic car shots, trailers,
      betting apps, people with phones) and two studio prompts (suits on a
      set, a host at a video wall).
    - Set arena bias to −1, which cost arena frames, then −0.5.
    - Set ad bias to +2, chosen from the ad and overlay margins above.
  - Effect: broadcast false mutes on these frames fell from 14 to 1.
- **25 batches:**
  - The error sheet showed:
    - Missed arena: pom-pom dance teams, animated jumbotron races, logo
      animations, game-show graphics, drumlines.
    - False arena: anthem singers.
    - Missed ads: German Bundesliga ads, and black frames that no prompt
      can fix.
  - First try, all of these as prompts: total errors on the 800 frames
    rose from 142 to 160. Averaging more prompts blurs a class, and the
    animation prompts pulled in broadcast overlays.
  - Second try, one row per prompt with −log(n) per class: 170 errors.
  - Kept: two anthem prompts in bench; German, betting, toy and mobile-game
    ad prompts; pom-pom dancers and drumline in arena; ad bias 2.5. That
    gave 137 errors.
  - An ad bias of 3 gave 132 errors but more studio mutes, so I kept 2.5.
- **50 batches:**
  - New misses: court light shows, jumbotron graphics, fan cams, acted
    lifestyle ads.
  - Prompts for these lowered arena recall again.
  - A sweep of arena bias (−0.5 to +0.5) left total errors at 256 to 271
    on the 1,600 frames: written heads had plateaued.
  - Kept the 25-batch prompts with arena bias 0.

## Arm 3b: reasoned edits of the trained heads

**What I looked at:**
- Each fold's trained heads.
- The cosine between each weight row and text embeddings of 28 named
  concepts (`commercial`, `phone number`, `studio desk`, `anchor`,
  `bench`, `crowd`, `cheerleaders`, `fan cam`, `light show`, and others).
- Pool-frame evidence: training frames shared by every fold, never
  held-out frames.

**Readings, the same in all three folds:**
- **Ad head, keep side:** points toward the score bug (+0.12), jumbotron
  game (+0.11), bench (+0.11), wide game shots (+0.10) and news anchor
  (+0.10). It points away from acted home scenes (−0.07 to −0.08), phone
  numbers (−0.04) and product shots (−0.03).
  - Studio desk is weak on the keep side (+0.07 to +0.09). In fold A it is
    not in the top six.
  - So a studio frame is kept mostly because it is not an acted home
    scene, which is fragile for studio sets the head has not seen.
- **Show head, arena row** (relative to the mean row): toward cheerleaders
  (+0.19 to +0.22), dancers (+0.14), mascot (+0.13), crowd (+0.12),
  jumbotron game and fan cam (+0.11).
  - Its bias is −1.0 to −2.0, the second lowest after studio.
  - So it knows the concepts but sits at a cautious threshold, and "crowd"
    is close to it, which risks bench false mutes.
- **Show head, studio row:** toward anchor, desk and video wall, as
  expected.
- **Show head, ad row:** toward acted home scenes, phone numbers and
  product shots, mirroring the ad head.

**Edits** (amounts chosen by reasoning from similarity gaps, then checked
on pool frames; every "add" holds the reference frames' average logit
fixed by moving the bias):

| Edit | What it writes | Weights | Pool-frame evidence (fold A head) | Decision |
|---|---|---|---|---|
| E1 | ad head += 30 × text("studio desk", "news anchor", "host at video wall"); reference: ad frames | 513 | Studio frames sit about 0.07 closer to that text than ads, so about +2 logits for studio and 0 for the average ad. Studio median logit +4.9 → +6.4; ad frames muted 439 → 431 of 464; broadcast unchanged | Kept |
| E1b | the same at 20 | 513 | Ad frames 439 → 434; smaller studio gain | Not used |
| E2 | arena bias +1.0 | 1 | Arena 358 → 450, but broadcast 82 → 152, bench 31 → 58 | Rejected: too many game mutes |
| E2b | arena bias +0.5 | 1 | Arena 358 → 411; broadcast 82 → 105, bench 31 → 44 | Kept as an option |
| E3 | arena row += 30 × text(light show, fan cam, jumbotron game, halftime show); reference: broadcast and bench | 513 | Arena 358 → 416, broadcast 82 → 208 | Rejected: light shows and fans overlap game coverage |
| E3b | the same with halftime show, jumbotron game and mascot only | 513 | Arena 358 → 415, broadcast 82 → 139 | Rejected: worse than E2b per false mute |
| E4 | arena row −= 0.5 × its crowd component; reference: bench | 513 | Arena 358 → 346, bench 31 → 30 | Rejected: no effect |

Held-out results for E1, E2b and both are in the main table. For E1, the
control is a bias shift of +0.3 on the ad head, which gives the same
pool-frame ad mutes (431). E1 cut held-out studio false mutes to a
quarter of the control's; the control left them almost unchanged.

**How fair this is:** the targets, studio false mutes and low arena recall,
were chosen from failure modes `docs/ad-detector-testing.md` already
recorded for these folds. The amounts and accept or reject decisions used
pool frames only.

## Arm 3a: data-derived edits (not reasoning)

Arm 3a is kept for comparison: a computed direction with a grid-searched
strength, the kind of closed-form fit the reasoning arm should not be.

- **Direction:** among each fold's training studio, bench and overlay
  frames, the mean of those the ad head called ad-like minus the mean of
  those it kept.
- **Edit:** remove that direction from the ad head (512 weights, with bias
  compensation), or zero the 4 or 16 weights that push those frames toward
  "ad" the most.
- **Selection:** the strength rule (at most 1% of training ad recall) chose
  no edit in any fold, so I report fixed sizes.
- **Result:** each size equals a bias shift with the same training recall.

Two mistakes along the way:
- The first version had no bias compensation and raised false mutes
  everywhere.
- Its selection rule had no "no edit" option.

## Training-side comparison

To check whether training could fix studio as well as E1 did, the heads
were refit with studio frames weighted 8 and 16 times instead of 4. Held-out
studio false mutes went from 3.7% to 2.9% and 2.4%, against 0.7% for E1.

## Secondary: Liquid vision-language models

These runs are kept apart from the main comparison. Both Liquid models
were run on this Mac (M2, 16 GB) through `mlx-vlm` 0.7.12 with 8-bit MLX
builds. The auto-mode classifier refused installing vLLM on the 2x 5070 Ti
PC over SSH, so the teacher ran on single frames, not video clips. The
recordings are stored at 384x216, so both models saw upscaled
low-resolution frames. Full-resolution screenshots might do better.

**L1, LFM2.5-VL-450M as the runtime, zero-shot:**
- **Prompt:** each frame gets a fixed prompt; the keep-sound score is the
  probability of the answer "game" against "ad" and "show". I chose that
  wording on 64 labeled training-pool frames: one-word answers made 14
  errors, lettered options 18. Lettered options leaned heavily toward the
  arena option.
- **Scope:** only fold C, to keep within time, since scoring ran at about
  0.6 s per frame including decoding.

| Fold C (NFL Ravens at Bills, NBA Celtics at Magic) | Ads muted | False-mute starts per game hour | Game time muted | Studio muted | Bench muted |
|---|---|---|---|---|---|
| Trained heads (arm 1) | 96.1% (835/869) | 0.6 | 0.1% (3/2777) | 0/49 | 1/139 |
| LFM2.5-VL-450M zero-shot | 83.0% (721/869) | 1.9 | 0.2% (6/2777) | 16/49 | 2/139 |

**Cost on this Mac**, measured while a headless mpv decoded a 1080p H.264
clip and the app's screenshot path took frames:

| | Median per frame | 95th percentile | Install size | Runtime dependencies |
|---|---|---|---|---|
| Today's TFLite model (`ai_ad_worker.py`, CPU, 2 threads) | 131 ms | 136 ms | 44 MB model + 109 MB venv | ai-edge-litert, numpy, Pillow |
| LFM2.5-VL-450M, frame shrunk to 512x288 first (GPU) | 417 ms | 511 ms | 551 MB model | mlx 170 MB, mlx-vlm 39 MB, transformers 119 MB, torch 631 MB (pulled in by mlx-vlm) |
| LFM2.5-VL-450M, full 1080p screenshot | 3,713 ms | 3,921 ms | same | same |

- The vendor's under-250 ms per image figure was not reached on an M2
  under 1080p decoding.
- At full resolution the model reads many more image tokens, so frames
  must be shrunk first.
- The 450M model also needs a prompt, a tokenizer and an Apple-only
  runtime. The TFLite worker runs on all three operating systems.

**L2, LFM3-VL-2B as a labeling teacher:**
- **Setup:** five-way labels (ad, show, game, studio, bench), thinking
  turned off with an empty think block, about 1.5 to 1.8 s per frame.
- **Check on 117 labeled training-pool frames, balanced across labels:**
  - It agreed with the hand labels on 41%, and on mute versus keep on 68%.
  - It called most studio, bench, ad and arena frames "game".
  - Keeping only its confident answers did not help: 35 of 71 at
    confidence 0.9.
  - With thinking on, 3 of 12 agreed, at 2.7 s per frame.
- **Labeling run:** 30 minutes each of three games in no fold, a frame
  every 6 seconds, 900 frames: NBA Raptors at Lakers, NFL Chiefs at
  Broncos, NHL Oilers at Rangers.
  - Its labels: 83% game, 13% ad, 5% studio, 2% arena, 2 frames bench.
- **Hand check of 60 random teacher labels** (mostly game play, which is
  easy):
  - 47 agree on the fine label (78%), and 55 on mute versus keep (92%).
  - The mute or keep errors: a pregame intro and a crowd frame called
    arena, a matchup graphic called ad, a BetMGM ad called studio, and a
    cinematic ad called game.
- **Refit:** arm 1's heads refit on hand labels plus these 900 teacher
  labels change little (row L2 in the tables): ads 95.7 → 96.0%, game
  false-mute starts 3.4 → 3.9 per hour, studio 3.7 → 4.0%.

**Liquid verdict:** neither model should ship. The 450M model is less
accurate and about 3 times slower than the trained TFLite model, with a
far larger install. The 2B teacher is not reliable enough on single
low-resolution frames to label training data. If the teacher is tried
again, the next steps are 8-second video clips at full resolution on the
PC.


## Per-league results (all folds pooled)

### All held-out games, with counts

| Arm | Ads muted | Arena shows muted | False-mute starts per game hour | Game time muted | Studio muted | Bench muted |
|---|---|---|---|---|---|---|
| 1 Trained (shipped: studio weight 4) | 95.7% (5365/5605) | 44.7% (345/771) | 3.4 | 1.08% | 3.7% (37/1000) | 2.6% (44/1721) |
| 1 Trained, studio weight 8 | 95.9% (5373/5605) | 44.7% (345/771) | 3.3 | 1.05% | 2.9% (29/1000) | 2.7% (46/1721) |
| 1 Trained, studio weight 16 | 95.5% (5350/5605) | 44.9% (346/771) | 3.3 | 1.00% | 2.4% (24/1000) | 2.7% (46/1721) |
| 2 Written, 0 batches | 44.5% (2492/5605) | 61.0% (470/771) | 20.1 | 5.88% | 0.0% (0/1000) | 20.4% (351/1721) |
| 2 Written, 10 batches | 60.9% (3415/5605) | 31.9% (246/771) | 1.9 | 0.38% | 0.0% (0/1000) | 5.7% (98/1721) |
| 2 Written, 25 batches | 71.7% (4019/5605) | 23.6% (182/771) | 2.5 | 0.51% | 0.6% (6/1000) | 4.8% (83/1721) |
| 2 Written, 50 batches | 72.1% (4042/5605) | 29.8% (230/771) | 2.7 | 0.61% | 0.6% (6/1000) | 8.7% (149/1721) |
| 2 ref: class means of the 50 batches (not reasoning) | 81.6% (4576/5605) | 37.7% (291/771) | 6.7 | 1.32% | 3.4% (34/1000) | 7.0% (120/1721) |
| 3b Reasoned edit E1: studio concept into ad head (513 weights) | 94.3% (5288/5605) | 43.1% (332/771) | 3.5 | 1.00% | 0.7% (7/1000) | 2.4% (42/1721) |
| 3b control for E1: ad bias shift, same train recall (1 weight) | 95.4% (5347/5605) | 44.1% (340/771) | 3.3 | 1.00% | 3.1% (31/1000) | 2.6% (44/1721) |
| 3b Reasoned edit E2b: arena bias +0.5 (1 weight) | 95.8% (5372/5605) | 60.6% (467/771) | 4.7 | 1.49% | 3.7% (37/1000) | 3.9% (67/1721) |
| 3b E1 + E2b (514 weights) | 94.5% (5299/5605) | 58.9% (454/771) | 4.8 | 1.40% | 0.7% (7/1000) | 3.8% (66/1721) |
| 3a Data-derived: 4 weights zeroed | 94.2% (5278/5605) | 42.0% (324/771) | 3.3 | 0.91% | 1.2% (12/1000) | 2.2% (38/1721) |
| 3a control: bias shift, same train recall | 93.6% (5246/5605) | 42.5% (328/771) | 3.1 | 0.80% | 1.5% (15/1000) | 2.3% (40/1721) |
| 3a Data-derived: 16 weights zeroed | 91.5% (5126/5605) | 41.6% (321/771) | 2.2 | 0.57% | 0.5% (5/1000) | 2.2% (38/1721) |
| 3a control: bias shift, same train recall | 91.3% (5118/5605) | 41.0% (316/771) | 2.4 | 0.56% | 0.5% (5/1000) | 2.2% (38/1721) |
| 3a Data-derived: rank-1 removal (512 weights) | 91.1% (5106/5605) | 41.0% (316/771) | 2.1 | 0.54% | 0.5% (5/1000) | 2.3% (39/1721) |
| L2 Trained + LFM3-VL-2B teacher labels (secondary) | 96.0% (5383/5605) | 44.0% (339/771) | 3.9 | 1.22% | 4.0% (40/1000) | 2.8% (49/1721) |

Per league, rounded percentages:

### NBA

| Arm | Ads muted | Arena shows muted | False-mute starts per game hour | Game time muted | Studio muted | Bench muted |
|---|---|---|---|---|---|---|
| 1 Trained (shipped: studio weight 4) | 94% | 47% | 7.2 | 2% | 0% | 3% |
| 1 Trained, studio weight 8 | 94% | 47% | 7.2 | 2% | 0% | 3% |
| 1 Trained, studio weight 16 | 94% | 47% | 7.2 | 2% | 0% | 3% |
| 2 Written, 0 batches | 46% | 66% | 64.9 | 19% | 0% | 38% |
| 2 Written, 10 batches | 59% | 34% | 4.4 | 1% | 0% | 10% |
| 2 Written, 25 batches | 70% | 25% | 5.3 | 1% | 0% | 9% |
| 2 Written, 50 batches | 70% | 32% | 5.8 | 2% | 0% | 16% |
| 2 ref: class means of the 50 batches (not reasoning) | 84% | 41% | 17.2 | 4% | 0% | 12% |
| 3b Reasoned edit E1: studio concept into ad head (513 weights) | 94% | 46% | 7.2 | 2% | 0% | 3% |
| 3b control for E1: ad bias shift, same train recall (1 weight) | 94% | 47% | 6.9 | 2% | 0% | 3% |
| 3b Reasoned edit E2b: arena bias +0.5 (1 weight) | 94% | 65% | 10.8 | 4% | 0% | 6% |
| 3b E1 + E2b (514 weights) | 94% | 64% | 10.8 | 3% | 0% | 6% |
| 3a Data-derived: 4 weights zeroed | 94% | 46% | 6.9 | 2% | 0% | 3% |
| 3a control: bias shift, same train recall | 94% | 46% | 6.7 | 2% | 0% | 3% |
| 3a Data-derived: 16 weights zeroed | 94% | 45% | 5.0 | 1% | 0% | 3% |
| 3a control: bias shift, same train recall | 94% | 44% | 4.7 | 1% | 0% | 3% |
| 3a Data-derived: rank-1 removal (512 weights) | 94% | 44% | 4.7 | 1% | 0% | 3% |
| L2 Trained + LFM3-VL-2B teacher labels (secondary) | 94% | 46% | 8.0 | 3% | 0% | 4% |

### NFL

| Arm | Ads muted | Arena shows muted | False-mute starts per game hour | Game time muted | Studio muted | Bench muted |
|---|---|---|---|---|---|---|
| 1 Trained (shipped: studio weight 4) | 97% | – | 0.0 | 0% | 0% | 1% |
| 1 Trained, studio weight 8 | 97% | – | 0.0 | 0% | 0% | 1% |
| 1 Trained, studio weight 16 | 97% | – | 0.0 | 0% | 0% | 1% |
| 2 Written, 0 batches | 39% | – | 0.0 | 0% | 0% | 0% |
| 2 Written, 10 batches | 52% | – | 0.0 | 0% | 0% | 0% |
| 2 Written, 25 batches | 68% | – | 0.0 | 0% | 0% | 0% |
| 2 Written, 50 batches | 69% | – | 0.0 | 0% | 0% | 0% |
| 2 ref: class means of the 50 batches (not reasoning) | 78% | – | 2.3 | 0% | 2% | 1% |
| 3b Reasoned edit E1: studio concept into ad head (513 weights) | 95% | – | 0.0 | 0% | 0% | 0% |
| 3b control for E1: ad bias shift, same train recall (1 weight) | 97% | – | 0.0 | 0% | 0% | 1% |
| 3b Reasoned edit E2b: arena bias +0.5 (1 weight) | 97% | – | 0.0 | 0% | 0% | 1% |
| 3b E1 + E2b (514 weights) | 95% | – | 0.0 | 0% | 0% | 0% |
| 3a Data-derived: 4 weights zeroed | 94% | – | 0.0 | 0% | 0% | 0% |
| 3a control: bias shift, same train recall | 93% | – | 0.0 | 0% | 0% | 0% |
| 3a Data-derived: 16 weights zeroed | 89% | – | 0.0 | 0% | 0% | 0% |
| 3a control: bias shift, same train recall | 89% | – | 0.0 | 0% | 0% | 0% |
| 3a Data-derived: rank-1 removal (512 weights) | 89% | – | 0.0 | 0% | 0% | 0% |
| L2 Trained + LFM3-VL-2B teacher labels (secondary) | 97% | – | 0.8 | 0% | 0% | 1% |

### College football

| Arm | Ads muted | Arena shows muted | False-mute starts per game hour | Game time muted | Studio muted | Bench muted |
|---|---|---|---|---|---|---|
| 1 Trained (shipped: studio weight 4) | 97% | 5% | 2.0 | 0% | 6% | 3% |
| 1 Trained, studio weight 8 | 97% | 5% | 1.8 | 0% | 4% | 3% |
| 1 Trained, studio weight 16 | 97% | 5% | 1.8 | 0% | 3% | 3% |
| 2 Written, 0 batches | 45% | 11% | 2.0 | 0% | 0% | 3% |
| 2 Written, 10 batches | 64% | 8% | 1.0 | 0% | 0% | 3% |
| 2 Written, 25 batches | 73% | 3% | 1.8 | 0% | 1% | 2% |
| 2 Written, 50 batches | 74% | 3% | 1.8 | 0% | 1% | 2% |
| 2 ref: class means of the 50 batches (not reasoning) | 85% | 3% | 2.3 | 0% | 2% | 2% |
| 3b Reasoned edit E1: studio concept into ad head (513 weights) | 97% | 5% | 2.3 | 0% | 1% | 3% |
| 3b control for E1: ad bias shift, same train recall (1 weight) | 97% | 5% | 1.8 | 0% | 5% | 3% |
| 3b Reasoned edit E2b: arena bias +0.5 (1 weight) | 97% | 8% | 2.0 | 0% | 6% | 4% |
| 3b E1 + E2b (514 weights) | 97% | 8% | 2.3 | 0% | 1% | 4% |
| 3a Data-derived: 4 weights zeroed | 96% | 0% | 1.5 | 0% | 2% | 3% |
| 3a control: bias shift, same train recall | 96% | 0% | 1.5 | 0% | 3% | 3% |
| 3a Data-derived: 16 weights zeroed | 95% | 0% | 1.0 | 0% | 1% | 3% |
| 3a control: bias shift, same train recall | 95% | 0% | 1.3 | 0% | 1% | 3% |
| 3a Data-derived: rank-1 removal (512 weights) | 95% | 0% | 1.0 | 0% | 1% | 3% |
| L2 Trained + LFM3-VL-2B teacher labels (secondary) | 97% | 5% | 2.3 | 0% | 6% | 3% |

### NHL

| Arm | Ads muted | Arena shows muted | False-mute starts per game hour | Game time muted | Studio muted | Bench muted |
|---|---|---|---|---|---|---|
| 1 Trained (shipped: studio weight 4) | 90% | 38% | 5.4 | 2% | 0% | 0% |
| 1 Trained, studio weight 8 | 90% | 38% | 4.7 | 2% | 0% | 0% |
| 1 Trained, studio weight 16 | 89% | 38% | 5.4 | 1% | 0% | 0% |
| 2 Written, 0 batches | 49% | 23% | 2.7 | 1% | 0% | 0% |
| 2 Written, 10 batches | 60% | 13% | 2.0 | 0% | 0% | 0% |
| 2 Written, 25 batches | 71% | 13% | 3.4 | 1% | 0% | 0% |
| 2 Written, 50 batches | 71% | 23% | 3.4 | 1% | 0% | 0% |
| 2 ref: class means of the 50 batches (not reasoning) | 70% | 8% | 5.4 | 1% | 0% | 3% |
| 3b Reasoned edit E1: studio concept into ad head (513 weights) | 86% | 23% | 5.4 | 1% | 0% | 0% |
| 3b control for E1: ad bias shift, same train recall (1 weight) | 89% | 38% | 6.1 | 1% | 0% | 0% |
| 3b Reasoned edit E2b: arena bias +0.5 (1 weight) | 90% | 38% | 7.4 | 2% | 0% | 0% |
| 3b E1 + E2b (514 weights) | 87% | 23% | 7.4 | 2% | 0% | 0% |
| 3a Data-derived: 4 weights zeroed | 87% | 21% | 6.1 | 1% | 0% | 0% |
| 3a control: bias shift, same train recall | 85% | 23% | 5.4 | 1% | 0% | 0% |
| 3a Data-derived: 16 weights zeroed | 82% | 21% | 3.4 | 1% | 0% | 0% |
| 3a control: bias shift, same train recall | 82% | 21% | 4.7 | 1% | 0% | 0% |
| 3a Data-derived: rank-1 removal (512 weights) | 81% | 21% | 3.4 | 1% | 0% | 0% |
| L2 Trained + LFM3-VL-2B teacher labels (secondary) | 91% | 38% | 6.1 | 2% | 0% | 0% |

### EPL

| Arm | Ads muted | Arena shows muted | False-mute starts per game hour | Game time muted | Studio muted | Bench muted |
|---|---|---|---|---|---|---|
| 1 Trained (shipped: studio weight 4) | 99% | – | 0.0 | 0% | 0% | 2% |
| 1 Trained, studio weight 8 | 99% | – | 0.0 | 0% | 0% | 2% |
| 1 Trained, studio weight 16 | 98% | – | 0.0 | 0% | 0% | 2% |
| 2 Written, 0 batches | 54% | – | 0.6 | 0% | 0% | 0% |
| 2 Written, 10 batches | 78% | – | 0.0 | 0% | 0% | 0% |
| 2 Written, 25 batches | 84% | – | 0.0 | 0% | 0% | 0% |
| 2 Written, 50 batches | 84% | – | 0.0 | 0% | 0% | 0% |
| 2 ref: class means of the 50 batches (not reasoning) | 91% | – | 0.0 | 0% | 0% | 0% |
| 3b Reasoned edit E1: studio concept into ad head (513 weights) | 98% | – | 0.0 | 0% | 0% | 2% |
| 3b control for E1: ad bias shift, same train recall (1 weight) | 98% | – | 0.0 | 0% | 0% | 2% |
| 3b Reasoned edit E2b: arena bias +0.5 (1 weight) | 100% | – | 0.0 | 0% | 0% | 2% |
| 3b E1 + E2b (514 weights) | 98% | – | 0.0 | 0% | 0% | 2% |
| 3a Data-derived: 4 weights zeroed | 98% | – | 0.0 | 0% | 0% | 2% |
| 3a control: bias shift, same train recall | 98% | – | 0.0 | 0% | 0% | 2% |
| 3a Data-derived: 16 weights zeroed | 96% | – | 0.0 | 0% | 0% | 2% |
| 3a control: bias shift, same train recall | 96% | – | 0.0 | 0% | 0% | 2% |
| 3a Data-derived: rank-1 removal (512 weights) | 97% | – | 0.0 | 0% | 0% | 2% |
| L2 Trained + LFM3-VL-2B teacher labels (secondary) | 98% | – | 0.0 | 0% | 0% | 2% |

### ESPN channel (SportsCenter and college football, fold B)

| Arm | Ads muted | Arena shows muted | False-mute starts per game hour | Game time muted | Studio muted | Bench muted |
|---|---|---|---|---|---|---|
| 1 Trained (shipped: studio weight 4) | 90% | – | 0.0 | 1% | 4% | 0% |
| 1 Trained, studio weight 8 | 90% | – | 0.0 | 1% | 4% | 0% |
| 1 Trained, studio weight 16 | 87% | – | 0.0 | 1% | 4% | 0% |
| 2 Written, 0 batches | 26% | – | 0.0 | 0% | 0% | 0% |
| 2 Written, 10 batches | 29% | – | 0.0 | 0% | 0% | 0% |
| 2 Written, 25 batches | 50% | – | 0.0 | 0% | 0% | 0% |
| 2 Written, 50 batches | 50% | – | 0.0 | 0% | 0% | 0% |
| 2 ref: class means of the 50 batches (not reasoning) | 81% | – | 0.0 | 0% | 16% | 0% |
| 3b Reasoned edit E1: studio concept into ad head (513 weights) | 86% | – | 0.0 | 1% | 0% | 0% |
| 3b control for E1: ad bias shift, same train recall (1 weight) | 87% | – | 0.0 | 1% | 4% | 0% |
| 3b Reasoned edit E2b: arena bias +0.5 (1 weight) | 90% | – | 0.0 | 1% | 4% | 0% |
| 3b E1 + E2b (514 weights) | 86% | – | 0.0 | 1% | 0% | 0% |
| 3a Data-derived: 4 weights zeroed | 87% | – | 0.0 | 1% | 0% | 0% |
| 3a control: bias shift, same train recall | 87% | – | 0.0 | 1% | 0% | 0% |
| 3a Data-derived: 16 weights zeroed | 77% | – | 0.0 | 1% | 0% | 0% |
| 3a control: bias shift, same train recall | 80% | – | 0.0 | 1% | 0% | 0% |
| 3a Data-derived: rank-1 removal (512 weights) | 73% | – | 0.0 | 1% | 0% | 0% |
| L2 Trained + LFM3-VL-2B teacher labels (secondary) | 91% | – | 0.0 | 1% | 4% | 0% |

## Reproducing

The experiment scripts live outside the repository with the data, in
`~/ad-data/scripts`. Results are in `~/ad-data/arms`.
- `arms_lib.py`: folds and metrics.
- `arm1.py`: trained heads.
- `arm2.py`: written heads, with the configs in `arms/written-b*.json`.
- `arm3.py`, `arm3_fixed.py`, `arm3_control.py`: data-derived edits.
- `arm3b_inspect.py`, `arm3b.py`: reasoned edits, with the edit files in
  `arms/e*.json`.
- `arm1_studio.py`: the studio-weight refits.
- `arms_table.py`: the tables.

No recordings, frames, features or weights are in the repository.
