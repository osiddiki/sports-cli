"""Check a live HLS source and summarize recent checks without saving its URL."""
from __future__ import annotations

import ipaddress
import json
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request


def _permitted(url, local_port):
    """Allow public hosts and the loopback relay explicitly selected for this test."""
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            return False
        name = parts.hostname.lower().rstrip('.')
        if local_port is not None and parts.scheme == 'http' and name in ('localhost', '127.0.0.1'):
            return parts.port == local_port
        if name in ('localhost', 'metadata.google.internal') or name.endswith(('.localhost', '.local', '.internal')):
            return False
        try:
            return ipaddress.ip_address(name).is_global
        except ValueError:
            return '.' in name
    except ValueError:
        return False


class _Redirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, local_port):
        self.local_port = local_port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _permitted(newurl, self.local_port):
            raise ValueError('Private redirect rejected')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def inspect_stream(url, headers=None, *, quick=False):
    """Check one playlist and segment; optionally probe the segment for resolution."""
    parts = urllib.parse.urlsplit(url)
    local_port = parts.port if parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost') else None
    opener = urllib.request.build_opener(_Redirect(local_port))
    request_headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}
    request_headers.update(headers or {})

    def open_url(target, timeout):
        if not _permitted(target, local_port):
            raise ValueError('Private media URL rejected')
        return opener.open(urllib.request.Request(target, headers=request_headers), timeout=timeout)

    started = time.monotonic()
    current, resolution, probe_data, bandwidth, speed = url, None, None, None, None
    ffprobe = shutil.which('ffprobe')
    timeout = 4 if quick else 10
    for _ in range(3):
        with open_url(current, timeout) as res:
            data = res.read(1_000_001)
            current = res.geturl()
        if len(data) > 1_000_000:
            raise ValueError('Playlist too large')
        manifest = data.decode('utf8', 'replace')
        if not manifest.lstrip().startswith('#EXTM3U'):
            raise ValueError('Not an HLS playlist')
        lines = [line.strip() for line in manifest.splitlines() if line.strip()]
        variants = []
        for i, line in enumerate(lines):
            if line.startswith('#EXT-X-STREAM-INF:'):
                size = re.search(r'RESOLUTION=(\d+)x(\d+)', line, re.I)
                rate = re.search(r'(?<![-A-Z])BANDWIDTH=(\d+)', line, re.I)
                if i + 1 < len(lines) and not lines[i + 1].startswith('#'):
                    variants.append((int(size.group(2)) if size else 0, int(rate.group(1)) if rate else 0, lines[i + 1]))
        if variants:
            height, rate, child = max(variants)
            if height >= 144:
                resolution = height
            bandwidth = rate or None
            current = urllib.parse.urljoin(current, child)
            continue
        segments = [(i, line) for i, line in enumerate(lines) if not line.startswith('#')]
        if not segments:
            raise ValueError('No media segments available')
        index, name = segments[-2] if len(segments) > 1 else segments[-1]
        duration = next((float(m.group(1)) for line in reversed(lines[:index]) if line.startswith('#EXTINF:')
                         for m in [re.match(r'#EXTINF:\s*([\d.]+)', line)] if m), None)
        segment = urllib.parse.urljoin(current, name)
        with open_url(segment, timeout if quick else 12) as res:
            sample = res.read(512)
            # Players keep their connection open between segments, so time the transfer, not the connection setup.
            first_byte = time.monotonic()
            latency_ms = round((first_byte - started) * 1000)
            if not sample or sample.lstrip().lower().startswith((b'<html', b'<!doctype html')):
                raise ValueError('Media segment unavailable')
            if sample.startswith(b'\x89PNG\r\n\x1a\n') and b'tsDa' not in sample:
                raise ValueError('Unsupported image-wrapped media segment')
            # Probe only a bounded portion of the chosen segment. ffprobe cannot
            # follow URLs embedded in an untrusted playlist from this input.
            # A quick check reads less; the first keyframe's header is enough for the height,
            # and half a megabyte is enough to tell a fast server from one that will stall.
            probe_data = sample + res.read((512 if quick else 2048) * 1024 - len(sample))
            elapsed = time.monotonic() - first_byte
            length = res.headers.get('Content-Length')
            size = int(length) if length and length.isdigit() and int(length) > 0 else (bandwidth or 0) * (duration or 0) / 8
            if duration and size and elapsed > 0 and len(probe_data) >= min(size, 64 * 1024):
                # How many seconds of video arrive per second of download; below 1 the player falls behind.
                speed = round((len(probe_data) - len(sample)) / elapsed * duration / size, 1)
        break
    else:
        raise ValueError('Too many nested playlists')

    if ffprobe and probe_data and len(probe_data) > 512:
        command = [ffprobe, '-v', 'error', '-select_streams', 'v:0',
                   '-show_entries', 'stream=height', '-of', 'json', 'pipe:0']
        try:
            result = subprocess.run(command, input=probe_data, capture_output=True, timeout=12, check=False)
            if result.returncode == 0:
                streams = json.loads(result.stdout).get('streams', [])
                if streams and isinstance(streams[0].get('height'), int) and streams[0]['height'] >= 144:
                    resolution = streams[0]['height']
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return {'latency_ms': latency_ms, 'height': resolution, 'speed': speed}


def source_key(feed):
    if feed.metadata.get('provider'):
        return feed.source + ':' + str(feed.metadata['provider'])
    if feed.source == 'PPV':
        return 'PPV:' + feed.side + ':' + feed.label.split('|', 1)[0].strip()
    if feed.source == 'NBA Bite':
        return feed.source + ':' + ':'.join(part.strip() for part in feed.label.split('|')[:2])
    # Directory labels have provider name first; do not store expiring URLs or event data.
    return feed.source + ':' + feed.label.split('|', 1)[0].strip()


def summary(record):
    if not isinstance(record, dict):
        return ''
    checks = record.get('checks', [])
    if not isinstance(checks, list):
        return ''
    checks = [v for v in checks[-20:] if isinstance(v, bool)]
    if not checks:
        return ''
    ok, total = sum(checks), len(checks)
    if total < 3:
        grade = '?'
    else:
        rate = ok / total
        grade = 'A' if rate >= 0.9 else 'B' if rate >= 0.7 else 'C' if rate >= 0.5 else 'D'
    height = record.get('height')
    quality = f' {height}p' if isinstance(height, int) and height > 0 else ''
    return f'{grade} {ok}/{total}{quality}'


def score(record):
    checks = record.get('checks', []) if isinstance(record, dict) else []
    checks = [v for v in checks[-20:] if isinstance(v, bool)] if isinstance(checks, list) else []
    if not checks:
        return (-1.0, -1, -1)
    height = record.get('height') if isinstance(record.get('height'), int) else 0
    latency = record.get('latency_ms') if isinstance(record.get('latency_ms'), int) else 999999
    # A small prior prevents a single lucky check from outranking a longer record.
    return ((sum(checks) + 1) / (len(checks) + 2), height, -latency)
