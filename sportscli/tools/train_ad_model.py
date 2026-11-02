"""Developer-only: train a small multi-sport ad classifier from labeled local recordings.

Run with a Python that has TensorFlow 2.16 and Pillow, from the repository root:

    python -m sportscli.tools.train_ad_model dataset.json --output ad-model.tflite

The dataset manifest lists local recordings and interval labels in the format
described in docs/ad-detector-testing.md:

    {"recordings": [{"video": "game1.mp4", "labels": "game1.json", "league": "nba"},
                    {"video": "game2.mkv", "labels": "game2.json", "league": "nfl",
                     "broadcast": "nfl-2025-wk3", "start": 300, "duration": 1800},
                    {"frames": "replay1/", "tags": "replay1/tags.json", "league": "epl"}]}

A ``frames`` entry is a folder of stills from label_frames (or any images named
by their position in seconds) with a tags.json mapping file names to labels.

Whole broadcasts are held out for evaluation, never individual frames. When the
original NBA model is installed it supplies soft labels for unlabeled time, so a
partly labeled recording still helps. Recordings, frames and weights stay local.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

from sportscli import ai_ad_mute, app
from sportscli.tools import evaluate_ads

TEACHER_SIZE = (200, 320)        # the original model's input, height x width
STUDENT_SIZE = (144, 256)
TARGETS = {'broadcast': 1.0, 'studio': 1.0, 'bench': 1.0, 'overlay': 1.0, 'ad': 0.0}
COVERAGE = ('broadcast', 'studio', 'bench', 'overlay')


def frames(video, start, duration, every, size):
    """Decode evenly spaced RGB frames with ffmpeg; nothing is written to disk."""
    import numpy as np
    height, width = size
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(start), '-i', str(video)]
    if duration: command += ['-t', str(duration)]
    command += ['-vf', f'fps=1/{every},scale={width}:{height}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    data = subprocess.run(command, capture_output=True, check=True).stdout
    count = len(data) // (height * width * 3)
    images = np.frombuffer(data[:count * height * width * 3], dtype=np.uint8).reshape(count, height, width, 3)
    return images, [start + i * every for i in range(count)]


def still_frames(folder, tags, size):
    """Load tagged stills named by position, such as 000123.0.jpg, at the given size."""
    import numpy as np
    from PIL import Image
    height, width = size
    images, positions, labels = [], [], []
    for name, label in sorted(tags.items(), key=lambda item: float(pathlib.Path(item[0]).stem)):
        with Image.open(folder / name) as image:
            images.append(np.asarray(image.convert('RGB').resize((width, height), Image.BILINEAR)))
        positions.append(float(pathlib.Path(name).stem)); labels.append(label)
    return np.stack(images), positions, labels


def load_teacher(model):
    """Score frames with the original NBA model to soft-label unlabeled time."""
    import tensorflow as tf
    interpreter = tf.lite.Interpreter(model_path=str(model), num_threads=4)
    interpreter.allocate_tensors()
    source, target = interpreter.get_input_details()[0], interpreter.get_output_details()[0]
    def score(image):
        interpreter.set_tensor(source['index'], image[None].astype('float32'))
        interpreter.invoke()
        return float(interpreter.get_tensor(target['index'])[0][0])
    return score


def load_dataset(manifest, every, teacher, promo, teacher_weight):
    import numpy as np
    from PIL import Image
    base = manifest.parent
    document = json.loads(manifest.read_text(encoding='utf-8'))
    recordings = []
    # Without a teacher, decode straight to the student size: hours of video at teacher size do not fit in memory.
    size = TEACHER_SIZE if teacher else STUDENT_SIZE
    for item in document.get('recordings', []):
        league = str(item.get('league', 'nba'))
        if item.get('frames'):
            folder = (base / item['frames']).resolve()
            tags = json.loads((base / item.get('tags', pathlib.Path(item['frames']) / 'tags.json')).read_text(encoding='utf-8'))
            if any(label not in evaluate_ads.LABELS for label in tags.values()): raise ValueError(f'Unknown label in {folder}')
            broadcast = str(item.get('broadcast') or folder.name)
            images, positions, tagged = still_frames(folder, tags, size)
        else:
            video = (base / item['video']).resolve()
            intervals = evaluate_ads.read_intervals(base / item['labels']) if item.get('labels') else []
            broadcast = str(item.get('broadcast') or video.stem)
            images, positions = frames(video, float(item.get('start', 0)), item.get('duration'), every, size)
            tagged = [evaluate_ads.label_at(intervals, position) for position in positions]
        targets, weights, labels, soft = [], [], [], []
        for image, position, label in zip(images, positions, tagged):
            teacher_score = teacher(image) if teacher else None
            soft.append(teacher_score)
            if label == 'promo' and promo != 'ignore': label_target = TARGETS[promo]
            else: label_target = TARGETS.get(label)
            if label_target is not None: targets.append(label_target); weights.append(1.0)
            elif teacher_score is not None and label == 'unknown' and teacher_weight > 0:
                targets.append(teacher_score); weights.append(teacher_weight)
            else: targets.append(0.5); weights.append(0.0)
            labels.append(label)
        small = images if size == STUDENT_SIZE else np.stack(
            [np.asarray(Image.fromarray(image).resize(STUDENT_SIZE[::-1], Image.BILINEAR)) for image in images])
        recordings.append({'broadcast': broadcast, 'league': league, 'images': small,
                           'targets': np.array(targets, 'float32'), 'weights': np.array(weights, 'float32'),
                           'labels': labels, 'positions': positions, 'teacher': soft})
        print(f'{broadcast} ({league}): {len(small)} frames, '
              f'{sum(1 for l in labels if l == "ad")} ad / {sum(1 for l in labels if l in COVERAGE)} coverage labeled',
              flush=True)
    if not recordings: raise SystemExit('The manifest lists no recordings.')
    return recordings


def split(recordings, holdout):
    names = sorted({r['broadcast'] for r in recordings})
    if holdout:
        held = set(holdout)
        unknown = held - set(names)
        if unknown: raise ValueError("Unknown held-out broadcasts: " + ", ".join(sorted(unknown)))
    elif len(names) < 2: held = set()
    else: held = set(names[::5] if len(names) >= 5 else names[-1:])
    return [r for r in recordings if r['broadcast'] not in held], [r for r in recordings if r['broadcast'] in held]


def build(alpha):
    import tensorflow as tf
    backbone = tf.keras.applications.MobileNetV3Small(
        input_shape=(*STUDENT_SIZE, 3), include_top=False, weights='imagenet', alpha=alpha,
        minimalistic=(alpha == 1.0), include_preprocessing=True, pooling='avg')
    inputs = tf.keras.Input((*STUDENT_SIZE, 3))
    features = backbone(inputs)
    outputs = tf.keras.layers.Dense(1, activation='sigmoid')(tf.keras.layers.Dropout(.2)(features))
    return tf.keras.Model(inputs, outputs), backbone


def train(train_set, epochs, alpha, batch):
    import numpy as np
    import tensorflow as tf
    # Unlabeled frames without teacher scores carry no weight; leaving them out saves memory.
    keep = [r['weights'] > 0 for r in train_set]
    images = np.concatenate([r['images'][k] for r, k in zip(train_set, keep)])
    targets = np.concatenate([r['targets'][k] for r, k in zip(train_set, keep)])
    weights = np.concatenate([r['weights'][k] for r, k in zip(train_set, keep)])
    # Hours of footage fill the memory of a 16 GB machine; keep only the combined copy.
    for r in train_set: del r['images']
    hard = weights >= 1
    ads, coverage = int(np.sum(hard & (targets < .5))), int(np.sum(hard & (targets >= .5)))
    if not ads or not coverage: raise SystemExit('Training needs labeled ad and coverage frames.')
    # Balance labeled classes so a mostly-coverage dataset still learns ads.
    weights = np.where(hard & (targets < .5), weights * (ads + coverage) / (2 * ads),
                       np.where(hard, weights * (ads + coverage) / (2 * coverage), weights)).astype('float32')
    def augment(image, target, weight):
        image = tf.image.random_flip_left_right(image)
        image = tf.image.random_brightness(tf.cast(image, tf.float32), 25)
        image = tf.image.random_contrast(image, .8, 1.2)
        image = tf.image.resize(tf.image.random_crop(image, (int(STUDENT_SIZE[0] * .9), int(STUDENT_SIZE[1] * .9), 3)), STUDENT_SIZE)
        return tf.clip_by_value(image, 0, 255), target, weight
    # Feed frames by index so TensorFlow does not hold another copy of every image.
    def frame(index):
        return images[index], targets[index], weights[index]
    def load(index):
        image, target, weight = tf.numpy_function(frame, [index], (tf.uint8, tf.float32, tf.float32))
        image.set_shape((*STUDENT_SIZE, 3)); target.set_shape(()); weight.set_shape(())
        return image, target, weight
    data = (tf.data.Dataset.range(len(images)).shuffle(len(images), seed=7)
            .map(load, num_parallel_calls=tf.data.AUTOTUNE)
            .map(augment, num_parallel_calls=tf.data.AUTOTUNE).batch(batch).prefetch(tf.data.AUTOTUNE))
    model, backbone = build(alpha)
    frozen = max(1, epochs // 3)
    backbone.trainable = False
    model.compile(tf.keras.optimizers.Adam(1e-3), 'binary_crossentropy', weighted_metrics=['accuracy'])
    model.fit(data, epochs=frozen, verbose=2)
    backbone.trainable = True
    model.compile(tf.keras.optimizers.Adam(1e-4), 'binary_crossentropy', weighted_metrics=['accuracy'])
    model.fit(data, epochs=epochs - frozen, verbose=2)
    return model, images


def export(model, sample, output):
    import numpy as np
    import tensorflow as tf
    # TF 2.16 cannot int8-convert a Keras 3 MobileNetV3 directly; a SavedModel converts cleanly.
    saved = tempfile.mkdtemp(prefix='ad-model-')
    try:
        model.export(saved)
        converter = tf.lite.TFLiteConverter.from_saved_model(saved)
        converter.optimizations = [tf.lite.Optimize.DEFAULT]
        picks = np.random.default_rng(3).choice(len(sample), size=min(200, len(sample)), replace=False)
        converter.representative_dataset = lambda: ([sample[i:i + 1].astype('float32')] for i in picks)
        converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
        converter.inference_input_type = converter.inference_output_type = tf.uint8
        output.write_bytes(converter.convert())
    finally:
        shutil.rmtree(saved, ignore_errors=True)


def scorer(model):
    import numpy as np
    import tensorflow as tf
    interpreter = tf.lite.Interpreter(model_path=str(model), num_threads=2)
    interpreter.allocate_tensors()
    source, target = interpreter.get_input_details()[0], interpreter.get_output_details()[0]
    in_scale, in_zero = source['quantization']; out_scale, out_zero = target['quantization']
    def score(image):
        pixels = np.clip(np.round(image.astype('float32') / in_scale + in_zero), 0, 255).astype(np.uint8)[None]
        interpreter.set_tensor(source['index'], pixels); interpreter.invoke()
        return float((float(interpreter.get_tensor(target['index']).reshape(-1)[0]) - out_zero) * out_scale)
    return score


def report(recordings, scores_for, every):
    """Mute counts by label, replaying each held-out broadcast through the playback decision."""
    totals = {label: {'samples': 0, 'muted': 0} for label in evaluate_ads.LABELS}
    for recording in recordings:
        scores = scores_for(recording)
        intervals = [(p, p + every, l) for p, l in zip(recording['positions'], recording['labels'])]
        result = evaluate_ads.evaluate(scores, intervals, recording['positions'][0] if recording['positions'] else 0,
                                       every, lambda s: min(1.0, max(0.0, s)), positions=recording['positions'])
        for label, count in result['counts'].items():
            totals[label]['samples'] += count['samples']; totals[label]['muted'] += count['muted']
    return totals


def benchmark(model, runs=50):
    import numpy as np
    score = scorer(model)
    image = np.random.default_rng(1).integers(0, 255, (*STUDENT_SIZE, 3), dtype=np.uint8)
    for _ in range(5): score(image)
    started = time.perf_counter()
    for _ in range(runs): score(image)
    return (time.perf_counter() - started) / runs * 1000


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('manifest', type=pathlib.Path, help='dataset manifest of local recordings')
    parser.add_argument('--output', type=pathlib.Path, default=pathlib.Path('ad-model.tflite'))
    parser.add_argument('--every', type=float, default=1.0, help='seconds between training frames')
    parser.add_argument('--epochs', type=int, default=9)
    parser.add_argument('--alpha', type=float, default=1.0, choices=(0.75, 1.0), help='MobileNetV3 width')
    parser.add_argument('--batch', type=int, default=32)
    parser.add_argument('--holdout', nargs='*', default=None, help='broadcast names to evaluate on')
    parser.add_argument('--promo', choices=('ignore', 'ad', 'broadcast'), default='ignore',
                        help='how to train on network promos (default: leave them out)')
    parser.add_argument('--teacher-weight', type=float, default=0.0,
                        help='explicitly opt into original-model soft labels; default 0 uses human labels only')
    parser.add_argument('--install', action='store_true',
                        help='make the result the active detector for --ai-ad-mute')
    args = parser.parse_args(argv)
    if not shutil.which('ffmpeg'): raise SystemExit('ffmpeg is required.')
    _, original = ai_ad_mute.installation_paths()
    original = original.with_name('nba-ads-v1.tflite')
    teacher = load_teacher(original) if original.is_file() and args.teacher_weight > 0 else None
    if teacher is None: print('Teacher disabled or unavailable; training uses labeled frames only.')
    recordings = load_dataset(args.manifest, args.every, teacher, args.promo, args.teacher_weight)
    train_set, held = split(recordings, args.holdout)
    if args.install and not held: raise SystemExit('Installation requires a held-out broadcast for evaluation.')
    if not train_set: raise SystemExit('Every broadcast is held out; nothing is left to train on.')
    print(f'Training on {len(train_set)} broadcasts; evaluating on {len(held)}.', flush=True)
    model, sample = train(train_set, args.epochs, args.alpha, args.batch)
    export(model, sample, args.output)
    size, latency = args.output.stat().st_size, benchmark(args.output)
    print(f'Wrote {args.output} ({size / 1e6:.2f} MB, {latency:.1f} ms per frame on this machine).')
    # Channel recordings such as 'tv' add coverage and ads but are not leagues the app can enable.
    metadata = {'leagues': sorted({r['league'] for r in train_set} & set(app.LEAGUES)), 'input': list(STUDENT_SIZE),
                'broadcasts': sorted({r['broadcast'] for r in train_set}), 'holdout': sorted({r['broadcast'] for r in held}),
                'size_bytes': size, 'ms_per_frame': round(latency, 1)}
    if held:
        student = scorer(args.output)
        for r in held: r['student'] = [student(i) for i in r['images']]
        student_scores = lambda r: r['student']
        metadata['student'] = report(held, student_scores, args.every)
        if teacher and all(s is not None for r in held for s in r['teacher']):
            metadata['original'] = report(held, lambda r: r['teacher'], args.every)
        # Leagues differ in studio and bench coverage, so report each one on its own.
        metadata['by_league'] = {league: report([r for r in held if r['league'] == league], student_scores, args.every)
                                 for league in sorted({r['league'] for r in held})}
        print('Held-out mute counts (muted/samples):')
        for label in evaluate_ads.LABELS[:-1]:
            row = [f'{name} {metadata[name][label]["muted"]}/{metadata[name][label]["samples"]}'
                   for name in ('student', 'original') if name in metadata]
            print(f'  {label:10} ' + '   '.join(row))
        for league, counts in metadata['by_league'].items():
            print(f'  {league}: ' + ', '.join(f'{label} {count["muted"]}/{count["samples"]}'
                                              for label, count in counts.items() if count['samples']))
    else:
        print('No held-out broadcast: this run cannot estimate accuracy.')
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
