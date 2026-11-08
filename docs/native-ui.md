# The native window

`native/` is Sports CLI as a Mac window, drawn with [Rune](https://github.com/anomalyco/rune), with the game playing inside it. Python still runs every screen and key: the window starts `sports-cli --web`, sends keys to `api/app/key` and draws `api/app/view`, as the OpenTUI front end does. This page is the design and why it looks the way it does.

## The idea: tuning a signal

The app's whole job is finding a clean signal in a noisy, unreliable world of streams, then getting out of the way. So the design borrows from instruments rather than from streaming services: a dark field, quiet type, and one source of light.

- **Night game.** A near-black field (`#0B0D10`), warm white text (`#ECE9E2`) and grey secondary text (`#7E848D`). On-air red (`#FF3B30`) marks live games and nothing else.
- **One accent per context.** Each league has its own colour (NBA ember, NFL turf, NHL ice, MLB clay, soccer violet, TV electric blue). Home, search, settings and help stay neutral. Besides on-air red, the accent is the only saturated thing on screen, so it always means "this is where you are".
- **League marks, not row numbers.** Home is tonight's games across leagues, and each row starts with its league's code (`NBA`, `NHL`, `MLS`) in its accent. A league's own lists carry the mark beside their title. `1` to `9` still open rows, but no list draws the numbers.
- **The beam.** The selection is a 3 pt bar of accent light with a soft wash spreading right. It springs between rows (0.25 s, no bounce) instead of jumping, so the eye follows it.
- **The signal ruler.** Every source carries 18 ticks. Lit ticks say how well it plays, tall ticks mark sharpness (1080p fills the ruler, 720p most of it), a sweep runs through it while it is checked, and a failed source lies flat with its reason in red. The selected source's ruler lights in the accent. The loading screen of the player is the same ruler, doubled: tuning in.
- **The pulse.** Live games breathe: a red dot that fades between 35% and 100% every 1.6 s, each row a little out of phase. Upcoming games show only their time. The header dot pulses only while the app works in the background.
- **The mark.** Three bars of a signal meter, the tallest in the accent.

## Motion

Quick and quiet. Everything settles in 0.25 to 0.3 s with no bounce; nothing moves on the first frame or idles when there is nothing to show.

- Going deeper slides the new screen in 14 pt from the right with a fade; coming back slides it from the left.
- Enter on a source fills the window with the video. Esc shrinks the same video into a corner tile (one shared element, `matched("player")`) and the list comes back while the game keeps playing. Tab brings it back full size.
- The player's title and keys fade out 2.6 s after your last key and return on any key.
- Prompts and the stream-link card rise from 96% scale over a dimmed field.

## Playing inside the window

Rune's own `Player` checks an asset's video tracks before playing, and a live HLS asset has none, so `native/src/video.rs` drives AVPlayer itself. An AVPlayerItemVideoOutput hands IOSurface frames to a pull thread, which pushes them into a Rune `NativeSurface`: the GPU reads the decoder's frames with no copy, and AVPlayer plays the sound. Request headers go with every playlist and segment request, so sources that need a Referer play directly, without the web proxy.

Broadcast SDR is tagged SMPTE 170M or BT.709, which share one transfer curve; Rune draws only BT.709 and sRGB, so frames are relabelled BT.709 before the push (HDR keeps its tag and is refused). Without this, every frame of the first channel tested was rejected and the picture stayed black.

`o` hands the stream to mpv, which keeps ad muting, stats and the relay's failover window. In-window video needs macOS; elsewhere the window says so and `o` opens mpv.

## Keys

The screens take the terminal app's keys unchanged; the footer lists them for each screen. The window adds:

| Where | Key | Does |
|---|---|---|
| Full player | Space | pause or resume |
| | m | mute |
| | Esc or Tab | shrink to the corner and keep browsing |
| | o | open in mpv instead |
| | Backspace or ⌘. | stop |
| Full replay | Left / Right | jump 10 seconds |
| | Shift Left / Shift Right | jump a minute |
| | [ / ] | previous or next period |
| | drag the bar | move through the replay |
| Browsing with a corner player | Tab | full size, except on Home, where Tab jumps between the menu and the games |
| | ⌘. | stop |
| Prompt | Enter / Esc | answer / cancel (an empty answer clears) |
| Stream link | ⌘C | copy |

## Staying in step with Python

One worker thread sends every request in the order you made it, and Python numbers each view. The window applies every event it receives, in order, and shows a view only when its number is newer. A player tells Python how it ended once: a failed stream hides its source once; one you stopped, replaced or handed to mpv is never hidden.

`verification/native/Client.tla` checks these promises over every order of keys, polls, background plays, failures and stops, and shows that the OpenTUI front end's shape (polls on their own connection) can apply two plays out of order. Run `verification/native/check.sh`; the `verify-native-client` skill explains the results.

## Running and proving it

```sh
cd native && cargo run --release
native/verify.sh      # headless: Tab to the menu, Live TV, play the second channel, corner player; needs network
```

Headless runs (`--snap`, `--script`, `--tree`) use Rune's virtual clock. `SPORTS_NATIVE_SYNC=1` holds that clock to the wall clock and runs the main run loop where AVPlayer delivers its callbacks, so scripted runs see real loading and real frames. `SPORTS_NATIVE_LOG=1` prints the player's frame counts.

## Not done yet

- The window has no app bundle or launcher; it runs from `native/`.
- Replays and in-window ad muting are untested in the window; use `o` for mpv when you need ad muting.
- The scripted proof picks the second Live TV channel by position, so it depends on the channel list.
