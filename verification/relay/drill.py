"""End-to-end drill of live backup failover: real relay, real segments, optional real mpv.

Encodes a test stream once, serves it as two live HLS sources on localhost, merges them with
sportscli.relay (the code the app uses), breaks the primary part way through, and reports what a
player would have seen: seconds of game played twice, seconds skipped, and the longest freeze.

    python3 verification/relay/drill.py                  # every scenario, about a minute
    python3 verification/relay/drill.py --only encoder   # scenarios whose name contains "encoder"
    python3 verification/relay/drill.py --mpv            # also play each merged stream in headless mpv

Needs ffmpeg (and mpv with --mpv). Segment contents are known from how they were cut, so repeats and
gaps are measured in seconds of game time, not guessed from names or timestamps.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.server
import json
import math
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from sportscli import relay  # noqa: E402

SEGMENT = 2.0
CONTENT_SECONDS = 90
WINDOW = 5
HEAD_START = 8


def encode(directory, *, skip=0.0, ts_offset=0, bitrate='600k'):
    """Cut the test pattern into 2 s transport-stream segments; return [(file, content_start, duration)]."""
    directory.mkdir(parents=True, exist_ok=True)
    command = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25',
               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', str(CONTENT_SECONDS + skip)]
    if skip: command += ['-ss', str(skip)]
    command += ['-c:v', 'libx264', '-preset', 'ultrafast', '-b:v', bitrate, '-g', '50', '-keyint_min', '50',
                '-sc_threshold', '0', '-c:a', 'aac', '-b:a', '64k', '-output_ts_offset', str(ts_offset), '-f', 'hls',
                '-hls_time', str(SEGMENT), '-hls_list_size', '0', '-hls_segment_type', 'mpegts',
                '-hls_flags', 'independent_segments', '-hls_segment_filename', str(directory / 'seg%03d.ts'),
                str(directory / 'index.m3u8')]
    subprocess.run(command, check=True)
    durations = [float(x) for x in re.findall(r'#EXTINF:([\d.]+)', (directory / 'index.m3u8').read_text())]
    start, out = skip, []
    for index, duration in enumerate(durations):
        out.append((directory / f'seg{index:03d}.ts', start, duration)); start += duration
    return out


class Feed:
    """One live source: publishes a segment every SEGMENT seconds, `delay` segments behind the real broadcast."""
    def __init__(self, segments, names, delay, started):
        self.segments, self.names, self.delay, self.started = segments, names, delay, started
        self.fault, self.fault_at, self.frozen_at = None, None, None
        self.by_name = {name: path for name, (path, _, _) in zip(names, segments)}
        feed = self
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_GET(self):
                status, body = feed.respond(self.path)
                self.send_response(status); self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'application/octet-stream'); self.end_headers(); self.wfile.write(body)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}/live.m3u8'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def published(self, now):
        if self.frozen_at is not None and now >= self.frozen_at: now = self.frozen_at
        return max(0, min(len(self.segments), int((now - self.started) // SEGMENT) + 1 - self.delay))

    def respond(self, path):
        now = time.monotonic()
        if self.fault == 'down' and self.fault_at is not None and now >= self.fault_at: return 503, b'down'
        if path.endswith('.m3u8'):
            count = self.published(now); first = max(0, count - WINDOW)
            lines = ['#EXTM3U', '#EXT-X-VERSION:3', f'#EXT-X-TARGETDURATION:{math.ceil(SEGMENT)}', f'#EXT-X-MEDIA-SEQUENCE:{first}']
            for index in range(first, count): lines += [f'#EXTINF:{self.segments[index][2]:.3f},', self.names[index]]
            return 200, ('\n'.join(lines) + '\n').encode()
        name = path.rsplit('/', 1)[-1]
        if name not in self.by_name: return 404, b'missing'
        if self.fault == 'segments' and self.fault_at is not None and now >= self.fault_at: return 404, b'gone'
        return 200, self.by_name[name].read_bytes()

    def break_at(self, mode, when):
        self.fault, self.fault_at = mode, when
        if mode == 'frozen':
            self.frozen_at = when


class Scenario:
    def __init__(self, name, backup, fault, delay=1):
        self.name, self.backup, self.fault, self.delay = name, backup, fault, delay


SCENARIOS = [
    Scenario('same stream, same names, primary down', 'same', 'down'),
    Scenario('same stream, renamed segments, primary down', 'renamed', 'down'),
    Scenario('other encoder, primary down', 'encoder', 'down'),
    Scenario('same stream, primary frozen', 'same', 'frozen'),
    Scenario('same stream, primary segments vanish', 'same', 'segments'),
    Scenario('other encoder, primary frozen', 'encoder', 'frozen'),
    Scenario('other encoder, backup 3 segments behind, primary down', 'encoder', 'down', delay=3),
    Scenario('other encoder listed first, same stream second, primary down', ['encoder', 'same'], 'down'),
]


def play_in_mpv(url, seconds):
    """Play the merged stream in headless mpv and report cache stalls and decoder errors."""
    directory = tempfile.mkdtemp(prefix='drill-mpv-')
    sock = os.path.join(directory, 'ipc')
    log = open(os.path.join(directory, 'log'), 'w')
    process = subprocess.Popen(['mpv', '--vo=null', '--ao=null', '--no-config', '--msg-level=all=warn', '--cache-pause-wait=2',
                                f'--input-ipc-server={sock}', url], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    stalls, stalled_at, stall_time, position, lock = 0, None, 0.0, 0.0, threading.Lock()
    opened = time.monotonic()
    def watch():
        nonlocal stalls, stalled_at, stall_time, position
        connection = None
        for _ in range(100):
            try:
                connection = socket.socket(socket.AF_UNIX); connection.connect(sock); break
            except OSError: time.sleep(.1)
        if connection is None: return
        for index, name in enumerate(('paused-for-cache', 'time-pos'), 1):
            connection.sendall(json.dumps({'command': ['observe_property', index, name]}).encode() + b'\n')
        buffered = b''
        while True:
            try: chunk = connection.recv(4096)
            except OSError: return
            if not chunk: return
            buffered += chunk; *lines, buffered = buffered.split(b'\n')
            for line in lines:
                try: event = json.loads(line)
                except ValueError: continue
                if event.get('event') != 'property-change': continue
                with lock:
                    if event['name'] == 'time-pos' and isinstance(event.get('data'), (int, float)): position = event['data']
                    if event['name'] == 'paused-for-cache' and 'data' in event:
                        # The first seconds are buffering before playback, not a stall.
                        if event['data'] and stalled_at is None and time.monotonic() - opened > 6: stalls += 1; stalled_at = time.monotonic()
                        elif not event['data'] and stalled_at is not None: stall_time += time.monotonic() - stalled_at; stalled_at = None
    threading.Thread(target=watch, daemon=True).start()
    time.sleep(seconds)
    with lock:
        if stalled_at is not None: stall_time += time.monotonic() - stalled_at
        result = {'played': round(position, 1), 'stalls': stalls, 'stalled_seconds': round(stall_time, 1)}
    process.terminate()
    try: process.wait(5)
    except subprocess.TimeoutExpired: process.kill()
    log.close()
    text = open(os.path.join(directory, 'log')).read()
    result['decoder_errors'] = len(re.findall(r'(?i)(invalid|error while decoding|corrupt|non monoton|truncated)', text))
    result['decoder_lines'] = [line.strip()[:110] for line in text.splitlines() if re.search(r'(?i)(invalid|error while decoding|corrupt|non monoton|truncated)', line)][:4]
    shutil.rmtree(directory, ignore_errors=True)
    return result


def run(scenario, media, seconds, fault_at, with_mpv, trace=False):
    started = time.monotonic() - HEAD_START * SEGMENT
    primary = Feed(media['a'], [p.name for p, _, _ in media['a']], 0, started)
    backups = []
    for index, kind in enumerate([scenario.backup] if isinstance(scenario.backup, str) else scenario.backup):
        if kind == 'same':
            backups.append(Feed(media['a'], [p.name for p, _, _ in media['a']], scenario.delay, started))
        elif kind == 'renamed':
            backups.append(Feed(media['a'], [f'other-{p.name}' for p, _, _ in media['a']], scenario.delay, started))
        else:
            backups.append(Feed(media['b'], [f'b{index}-{p.name}' for p, _, _ in media['b']], scenario.delay, started))
    known = {}
    for label, feed in (('primary', primary), *(('backup', feed) for feed in backups)):
        for path, start, duration in feed.segments:
            known.setdefault(hashlib.sha1(path.read_bytes()).hexdigest(), (label, start, duration))
    server = relay.Relay()
    merged_url, usable = server.add_merged([(feed.url, {}) for feed in (primary, *backups)])
    if not merged_url: return {'scenario': scenario.name, 'error': f'could not merge ({usable} usable source)'}
    session = next(iter(server.merged.values()))
    begin = time.monotonic()
    primary.break_at(scenario.fault, begin + fault_at)
    mpv_result = {}
    mpv_thread = None
    if with_mpv:
        mpv_thread = threading.Thread(target=lambda: mpv_result.update(play_in_mpv(merged_url, seconds)), daemon=True)
        mpv_thread.start()
    seen, order, arrivals = set(), [], []
    while time.monotonic() - begin < seconds:
        try: text = urllib.request.urlopen(merged_url, timeout=25).read().decode()
        except OSError: time.sleep(.5); continue
        for line in text.splitlines():
            if line.startswith('http') and line not in seen:
                seen.add(line)
                try: data = urllib.request.urlopen(line, timeout=25).read()
                except OSError: continue
                arrivals.append(time.monotonic() - begin)
                order.append(known.get(hashlib.sha1(data).hexdigest(), ('unknown', 0.0, 0.0)))
        time.sleep(.5)
    if mpv_thread: mpv_thread.join(seconds + 10)
    repeated = skipped = 0.0
    for (_, s1, d1), (_, s2, _) in zip(order, order[1:]):
        end = s1 + d1
        repeated += max(0.0, end - s2); skipped += max(0.0, s2 - end)
    after = [a for a in arrivals if a >= fault_at]
    freeze = max((b - a for a, b in zip([fault_at] + after, after)), default=seconds - fault_at)
    result = {'scenario': scenario.name, 'segments': len(order), 'repeated_s': round(repeated, 1), 'skipped_s': round(skipped, 1),
              'longest_wait_after_fault_s': round(freeze, 1), 'switches': session.switches, 
              'unknown_segments': sum(1 for label, _, _ in order if label == 'unknown')}
    if trace: result['output'] = [f'{when:5.1f}s {label} content {start:5.1f}-{start + duration:5.1f}' for when, (label, start, duration) in zip(arrivals, order)]
    if with_mpv: result['mpv'] = mpv_result
    for feed in (primary, *backups): feed.server.shutdown()
    server.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--only', default='', help='run scenarios whose name contains this text')
    parser.add_argument('--seconds', type=int, default=50, help='how long each scenario runs (default 50)')
    parser.add_argument('--fault-at', type=int, default=20, help='seconds before the primary breaks (default 20)')
    parser.add_argument('--mpv', action='store_true', help='also play each merged stream in headless mpv')
    parser.add_argument('--trace', action='store_true', help='list every segment the player was offered')
    parser.add_argument('--json', action='store_true', help='print results as JSON')
    args = parser.parse_args()
    for tool in ('ffmpeg',) + (('mpv',) if args.mpv else ()):
        if not shutil.which(tool): sys.exit(f'{tool} is required')
    chosen = [s for s in SCENARIOS if args.only.lower() in s.name.lower()]
    work = pathlib.Path(tempfile.mkdtemp(prefix='relay-drill-'))
    try:
        print('Encoding the test stream...', file=sys.stderr)
        media = {'a': encode(work / 'a'), 'b': encode(work / 'b', skip=0.8, ts_offset=5000, bitrate='450k')}
        with concurrent.futures.ThreadPoolExecutor(len(chosen)) as pool:
            results = list(pool.map(lambda s: run(s, media, args.seconds, args.fault_at, args.mpv, args.trace), chosen))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if args.json: print(json.dumps(results, indent=2)); return
    for item in results:
        print(f"\n{item['scenario']}")
        for key, value in item.items():
            if key == 'output': print('  output:'); [print('    ' + line) for line in value]
            elif key != 'scenario': print(f'  {key}: {value}')


if __name__ == '__main__':
    main()
