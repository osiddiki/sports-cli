"""Evaluate the optional local ad detector against a labeled video.

This developer tool reads a local recording and an interval manifest; it never
downloads media or sends frames elsewhere. Run ``python -m
sportscli.tools.evaluate_ads --help`` for the manifest format and options.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import select
import shutil
import statistics
import subprocess
import tempfile
import time

from sportscli import ai_ad_mute


# studio and bench are program coverage kept apart so their false mutes can be measured.
LABELS = ('ad', 'broadcast', 'studio', 'bench', 'arena', 'promo', 'break', 'overlay', 'unknown')


def read_intervals(path):
    """Read non-overlapping labeled intervals; unlabeled gaps stay unknown."""
    document = json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    if not isinstance(document, dict) or not isinstance(document.get('intervals'), list):
        raise ValueError('Manifest must be an object with an intervals list')
    intervals = []
    for item in document['intervals']:
        if not isinstance(item, dict):
            raise ValueError('Each interval must be an object')
        start, end, label = item.get('start'), item.get('end'), item.get('label')
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
               for x in (start, end)):
            raise ValueError('Interval start and end must be finite seconds')
        if start < 0 or end <= start or label not in LABELS:
            raise ValueError('Each interval needs start < end and a valid label')
        intervals.append((float(start), float(end), label))
    intervals.sort()
    if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
        raise ValueError('Intervals must not overlap')
    return intervals


def label_at(intervals, position):
    for start, end, label in intervals:
        if start <= position < end:
            return label
        if start > position:
            break
    return 'unknown'


def evaluate(frames, intervals, start, every, score_frame, decision=None, positions=None):
    """Evaluate chronological samples using the same state machine as playback."""
    decision = decision or ai_ad_mute.Decision()
    counts = {label: {'samples': 0, 'muted': 0} for label in LABELS}
    samples, durations = [], []
    for index, frame in enumerate(frames):
        position = positions[index] if positions is not None else start + index * every
        before = time.monotonic()
        score = score_frame(frame)
        durations.append((time.monotonic() - before) * 1000)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('Worker returned an invalid score')
        muted = bool(decision.update(score))
        label = label_at(intervals, position)
        counts[label]['samples'] += 1
        counts[label]['muted'] += int(muted)
        samples.append({'time': round(position, 3), 'label': label, 'score': score, 'muted': muted})
    return {'counts': counts, 'samples': samples,
            'median_inference_ms': round(statistics.median(durations), 1) if durations else None}


class Worker:
    """Use the existing line protocol; model scores represent broadcast evidence."""
    def __init__(self, python, worker, model):
        self.process = subprocess.Popen([str(python), '-u', str(worker), str(model)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)
        try:
            if not self.read(60).get('ready'):
                raise ValueError('Detector could not load the model')
        except Exception:
            self.close()
            raise

    def read(self, timeout):
        if not select.select([self.process.stdout], [], [], timeout)[0]:
            raise ValueError('Detector did not respond in time')
        line = self.process.stdout.readline()
        if not line:
            raise ValueError('Detector exited unexpectedly')
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError('Detector returned an invalid response')
        return value

    def score(self, frame):
        self.process.stdin.write(json.dumps({'image': str(frame)}) + '\n')
        self.process.stdin.flush()
        return self.read(30).get('score')

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        if self.process.stdout:
            self.process.stdout.close()


def main(argv=None):
    python, model = ai_ad_mute.installation_paths()
    parser = argparse.ArgumentParser(
        description='Measure ad detection on a local labeled video. No frames are uploaded.',
        epilog=('Manifest JSON: {"intervals": [{"start": 0, "end": 15, "label": "broadcast"}, '
                '{"start": 15, "end": 45, "label": "ad"}]}. Times are seconds from the start '
                'of the video. Labels are ad, broadcast, studio, bench, promo, break, overlay, or unknown; gaps are unknown.'))
    parser.add_argument('video', type=pathlib.Path, help='local recording, never a URL')
    parser.add_argument('manifest', type=pathlib.Path, help='JSON intervals labeled independently')
    parser.add_argument('--start', type=float, default=0, help='starting position in seconds')
    parser.add_argument('--duration', type=float, required=True, help='seconds to evaluate (maximum 1800)')
    parser.add_argument('--every', type=float, default=2, help='sample interval in seconds (default: 2)')
    parser.add_argument('--python', type=pathlib.Path, default=python, help='detector Python')
    parser.add_argument('--worker', type=pathlib.Path, default=ai_ad_mute.WORKER, help='detector worker')
    parser.add_argument('--model', type=pathlib.Path, default=model, help='detector model')
    parser.add_argument('--output', type=pathlib.Path, help='optional JSON report')
    args = parser.parse_args(argv)
    if not all(math.isfinite(x) for x in (args.start, args.duration, args.every)):
        parser.error('Times must be finite')
    if args.start < 0 or not 0 < args.duration <= 1800 or not .25 <= args.every <= 60:
        parser.error('Use start >= 0, duration > 0 and <= 1800, every between 0.25 and 60')
    if args.duration / args.every > 1800:
        parser.error('Limit the evaluation to at most 1800 samples')
    if not args.video.is_file() or not args.manifest.is_file():
        parser.error('Video and manifest must be local files')
    if not all(path.is_file() for path in (args.python, args.worker, args.model)):
        parser.error('Detector is not installed; set --python, --worker and --model or run ./sports-cli --install-ad-model')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        parser.error('ffmpeg is required to sample the local recording')
    try:
        intervals = read_intervals(args.manifest)
        with tempfile.TemporaryDirectory(prefix='sports-ad-eval-') as directory:
            frame_pattern = str(pathlib.Path(directory) / 'frame-%06d.jpg')
            select_filter = (f'trim=end={args.duration:.9f},'
                             f'select=gte(t\\,selected_n*{args.every:.9f}),showinfo')
            command = [ffmpeg, '-hide_banner', '-v', 'info', '-nostdin', '-ss', str(args.start),
                       '-i', str(args.video), '-t', str(args.duration), '-an', '-vf', select_filter,
                       '-fps_mode', 'passthrough', '-q:v', '4', frame_pattern]
            decoded = subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     text=True, timeout=max(60, args.duration * 3))
            frames = sorted(pathlib.Path(directory).glob('frame-*.jpg'))
            if not frames:
                raise ValueError('No frames were sampled')
            times = [(int(n), float(t)) for n, t in re.findall(
                r'showinfo[^\n]*?\bn:\s*(\d+)\s+pts:\s*-?\d+\s+pts_time:([+\-0-9.eE]+)', decoded.stderr)]
            if len(times) != len(frames) or any(n != i or not math.isfinite(t) for i, (n, t) in enumerate(times)):
                raise ValueError('Could not determine the actual timestamp of every sampled frame')
            positions = [args.start + t for _, t in times]
            worker = Worker(args.python, args.worker, args.model)
            try:
                report = evaluate(frames, intervals, args.start, args.every, worker.score, positions=positions)
            finally:
                worker.close()
        report.update({'video': args.video.name, 'start': args.start, 'duration': args.duration,
                       'sample_interval': args.every})
        print('Samples every', args.every, 'seconds; each label is counted separately.')
        for label in LABELS:
            count = report['counts'][label]
            print(f"{label:9} {count['muted']:4} muted / {count['samples']:4} samples")
        print('Median frame processing:', report['median_inference_ms'], 'ms')
        if args.output:
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Ad evaluation failed: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
