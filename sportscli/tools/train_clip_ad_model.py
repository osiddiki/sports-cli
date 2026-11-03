"""Developer-only: train the ad and in-venue show detector on pretrained MobileCLIP2-S0 image features.

The image encoder already knows commercials, dance teams and crowds, so only two small
heads are fitted on labeled frames: one separates ads from everything that keeps sound,
the other has a class per label and mutes ``arena`` shows (dancers, fan contests, fan
cams, mascot acts). Playback mutes when either head says so. The encoder and both heads
export as one TFLite model that sportscli/ai_ad_worker.py runs unchanged.

Run with a Python that has torch, open_clip_torch, scikit-learn, litert-torch and Pillow:

    python -m sportscli.tools.train_clip_ad_model dataset.json --holdout game-a game-b

The manifest is the one train_ad_model.py reads. Features are cached per broadcast in
--cache, so refitting after a label change takes seconds. Recordings, features and
weights stay local; never commit them.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import time

from sportscli import ai_ad_mute, app
from sportscli.tools import evaluate_ads
from sportscli.tools.train_ad_model import report, split

MODEL, PRETRAINED, SIZE = 'MobileCLIP2-S0', 'dfndr2b', 256
# Labels that keep sound; ads and arena shows are muted. Promos, breaks and unknown time are left out.
KEEP = {'broadcast': 1, 'overlay': 1, 'studio': 1, 'bench': 1, 'arena': 0, 'ad': 0}
STUDIO_WEIGHT = 4.0  # studio desks resemble ad graphics; weighting them keeps the ad head off them


def load_encoder():
    import open_clip
    import torch
    model = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED)[0].eval()
    device = 'mps' if torch.backends.mps.is_available() else 'cpu'
    return model.to(device), device


def embed(model, device, images):
    import torch
    # The model's own preprocessing is identity normalization; pixels only need scaling to 0-1.
    pixels = torch.from_numpy(images).to(device).permute(0, 3, 1, 2).float() / 255
    with torch.no_grad():
        features = model.encode_image(pixels)
    return torch.nn.functional.normalize(features, dim=-1).cpu().numpy().astype('float32')


def video_frames(video, start, duration, every):
    """Yield whole frames squashed to SIZE x SIZE, so a corner score bug is never cropped."""
    import numpy as np
    command = ['ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(video)]
    if duration: command += ['-t', str(duration)]
    command += ['-vf', f'fps=1/{every},scale={SIZE}:{SIZE}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    try:
        while True:
            raw = process.stdout.read(SIZE * SIZE * 3)
            if len(raw) < SIZE * SIZE * 3: break
            yield np.frombuffer(raw, np.uint8).reshape(SIZE, SIZE, 3)
    finally:
        process.stdout.close(); process.wait()


def batched(frames, encoder, size=64):
    import numpy as np
    model, device = encoder
    batch, out = [], []
    for frame in frames:
        batch.append(frame)
        if len(batch) == size: out.append(embed(model, device, np.stack(batch))); batch = []
    if batch: out.append(embed(model, device, np.stack(batch)))
    return np.concatenate(out) if out else np.zeros((0, 512), 'float32')


def load_dataset(manifest, every, cache):
    """Return recordings with features, positions and labels; labels are read fresh, features from the cache."""
    import numpy as np
    from PIL import Image
    base = manifest.parent
    cache.mkdir(parents=True, exist_ok=True)
    encoder, recordings = None, []
    for item in json.loads(manifest.read_text(encoding='utf-8')).get('recordings', []):
        league = str(item.get('league', 'nba'))
        if item.get('frames'):
            folder = (base / item['frames']).resolve()
            tags = json.loads((base / item.get('tags', pathlib.Path(item['frames']) / 'tags.json')).read_text(encoding='utf-8'))
            if any(label not in evaluate_ads.LABELS for label in tags.values()): raise ValueError(f'Unknown label in {folder}')
            broadcast = str(item.get('broadcast') or folder.name)
            names = sorted(tags, key=lambda name: float(pathlib.Path(name).stem))
            positions, labels = [float(pathlib.Path(n).stem) for n in names], [tags[n] for n in names]
            def frames():
                for name in names:
                    with Image.open(folder / name) as image:
                        yield np.asarray(image.convert('RGB').resize((SIZE, SIZE), Image.BILINEAR))
        elif item.get('labels'):
            video = (base / item['video']).resolve()
            intervals = evaluate_ads.read_intervals(base / item['labels'])
            broadcast = str(item.get('broadcast') or video.stem)
            start = float(item.get('start', 0))
            frames = lambda: video_frames(video, start, item.get('duration'), every)
            positions = labels = None
        else:
            continue
        stored = cache / f'{broadcast}-{every:g}s.npz'
        if stored.is_file():
            features = np.load(stored)['features']
        else:
            encoder = encoder or load_encoder()
            began = time.monotonic()
            features = batched(frames(), encoder)
            np.savez(stored, features=features)
            print(f'{broadcast}: embedded {len(features)} frames in {time.monotonic() - began:.0f}s', flush=True)
        if positions is None:
            positions = [start + i * every for i in range(len(features))]
            labels = [evaluate_ads.label_at(intervals, p) for p in positions]
        recordings.append({'broadcast': broadcast, 'league': league, 'features': features,
                           'positions': positions, 'labels': labels})
    if not recordings: raise SystemExit('The manifest lists no labeled recordings.')
    return recordings


def fit(recordings):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    x = np.concatenate([r['features'] for r in recordings])
    labels = np.array([l for r in recordings for l in r['labels']])
    usable = np.isin(labels, list(KEEP))
    y = (labels[usable] != 'ad').astype(int)
    if not y.all() and y.any():
        weights = np.where(y == 0, (y == 1).sum() / (y == 0).sum(), 1.0) * np.where(labels[usable] == 'studio', STUDIO_WEIGHT, 1.0)
    else:
        raise SystemExit('Training needs labeled ad and coverage frames.')
    ad = LogisticRegression(max_iter=3000).fit(x[usable], y, sample_weight=weights)
    show = LogisticRegression(max_iter=3000, class_weight='balanced').fit(x[usable], labels[usable])
    return {'ad_w': ad.coef_[0].astype('float32'), 'ad_b': np.float32(ad.intercept_[0]),
            'show_w': show.coef_.astype('float32'), 'show_b': show.intercept_.astype('float32'),
            'keep': np.array([KEEP[c] for c in show.classes_], 'float32')}


def scores(heads, features):
    """The keep-sound score the exported model computes: the lower of the two heads."""
    import numpy as np
    ad = 1 / (1 + np.exp(-(features @ heads['ad_w'] + heads['ad_b'])))
    z = features @ heads['show_w'].T + heads['show_b']
    p = np.exp(z - z.max(1, keepdims=True)); p /= p.sum(1, keepdims=True)
    if p.shape[1] == 1: p = np.ones_like(p)  # a single fitted class carries no show information
    return np.minimum(ad, (p * heads['keep']).sum(1)).tolist()


def export(heads, output):
    import litert_torch
    import open_clip
    import torch
    clip = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED)[0].eval()

    class Detector(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.visual = clip.visual
            for name, value in heads.items(): self.register_buffer(name, torch.tensor(value))

        def forward(self, pixels):
            # ai_ad_worker feeds [1, SIZE, SIZE, 3] float pixels 0-255 of the whole resized frame.
            f = self.visual(pixels.permute(0, 3, 1, 2) / 255.0)
            f = f / f.norm(dim=-1, keepdim=True)
            ad = torch.sigmoid(f @ self.ad_w + self.ad_b)
            show = (torch.softmax(f @ self.show_w.T + self.show_b, dim=-1) * self.keep).sum(-1)
            return torch.minimum(ad, show).reshape(-1, 1)

    model = Detector().eval()
    sample = (torch.rand(1, SIZE, SIZE, 3) * 255,)
    converted = litert_torch.convert(model, sample)
    converted.export(str(output))
    with torch.no_grad(): expected = model(*sample).item()
    got = float(converted(*[s.numpy() for s in sample]).reshape(-1)[0])
    if abs(expected - got) > 1e-3: raise SystemExit(f'Converted model disagrees: {expected:.4f} vs {got:.4f}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('manifest', type=pathlib.Path, help='dataset manifest of local recordings')
    parser.add_argument('--output', type=pathlib.Path, default=pathlib.Path('ad-model.tflite'))
    parser.add_argument('--every', type=float, default=2.0, help='seconds between frames (playback checks every 2)')
    parser.add_argument('--cache', type=pathlib.Path, help='feature cache folder (default: features/ next to the manifest)')
    parser.add_argument('--holdout', nargs='*', default=None, help='broadcast names to evaluate on')
    parser.add_argument('--evaluate-only', action='store_true', help='report held-out results without exporting')
    parser.add_argument('--leagues', nargs='*', help='enable only these trained leagues (default: every trained league)')
    parser.add_argument('--install', action='store_true', help='make the result the active detector for --ai-ad-mute')
    args = parser.parse_args(argv)
    if not shutil.which('ffmpeg'): raise SystemExit('ffmpeg is required.')
    recordings = load_dataset(args.manifest, args.every, args.cache or args.manifest.parent / 'features')
    train_set, held = split(recordings, args.holdout)
    if args.install and not held: raise SystemExit('Installation requires a held-out broadcast for evaluation.')
    if not train_set: raise SystemExit('Every broadcast is held out; nothing is left to train on.')
    print(f'Fitting on {len(train_set)} broadcasts; evaluating on {len(held)}.', flush=True)
    heads = fit(train_set)
    metadata = {'model': f'{MODEL} ({PRETRAINED})', 'mutes': ['ad', 'arena'],
                # Channel recordings such as 'tv' add coverage and ads but are not leagues the app can enable.
                'leagues': sorted({r['league'] for r in train_set} & set(app.LEAGUES) & set(args.leagues or app.LEAGUES)),
                'input': [SIZE, SIZE],
                'broadcasts': sorted({r['broadcast'] for r in train_set}), 'holdout': sorted({r['broadcast'] for r in held})}
    if held:
        score = lambda r: scores(heads, r['features'])
        metadata['student'] = report(held, score, args.every)
        # Leagues differ in studio, bench and arena coverage, so report each one on its own.
        metadata['by_league'] = {league: report([r for r in held if r['league'] == league], score, args.every)
                                 for league in sorted({r['league'] for r in held})}
        print('Held-out mute counts (muted/samples):')
        for label, count in metadata['student'].items():
            if count['samples'] and label != 'unknown': print(f'  {label:10} {count["muted"]}/{count["samples"]}')
        for league, counts in metadata['by_league'].items():
            print(f'  {league}: ' + ', '.join(f'{label} {count["muted"]}/{count["samples"]}'
                                              for label, count in counts.items() if count['samples'] and label != 'unknown'))
    else:
        print('No held-out broadcast: this run cannot estimate accuracy.')
    if args.evaluate_only: return 0
    export(heads, args.output)
    metadata['size_bytes'] = args.output.stat().st_size
    print(f'Wrote {args.output} ({metadata["size_bytes"] / 1e6:.1f} MB).')
    args.output.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
    if args.install:
        root = ai_ad_mute.installation_dir()
        if not (root / 'venv').is_dir(): raise SystemExit('Install the detector runtime first: ./sports-cli --install-ad-model')
        shutil.copyfile(args.output, root / 'ad-model.tflite')
        (root / 'ad-model.json').write_text(json.dumps(metadata, indent=2) + '\n')
        print(f'Installed for {", ".join(metadata["leagues"])}. Remove {root / "ad-model.tflite"} to restore the original.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
