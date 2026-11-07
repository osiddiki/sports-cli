"""Where a replay can be moved to.

A timeline is the length of one finished playlist, the period marks that length
or its cues support, and the playhead a seek lands on. It never carries a score.
Chapter names are period labels (Q1, 1st Half, OT). A cue whose name is not one
of those is ignored, so a scoreboard title cannot show up on the bar.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse
import urllib.request

from . import grading

STEP = 10.0
MINUTE = 60.0
# Even slices of the file are only honest once each period is at least this long.
MIN_PERIOD = 10 * 60
# A cue-bounded stretch shorter than this is an ad fragment, not a period.
MIN_SPAN = 60.0

PERIODS = {
    'basketball': ('Q1', 'Q2', 'Q3', 'Q4'),
    'football': ('Q1', 'Q2', 'Q3', 'Q4'),
    'hockey': ('P1', 'P2', 'P3'),
    'soccer': ('1st Half', '2nd Half'),
}
# Extra cue-bounded stretches after the regulation ones. Not guessed from length.
OVERTIME = ('OT', '2OT', '3OT')
NAMED = {
    'q1': 'Q1', 'q2': 'Q2', 'q3': 'Q3', 'q4': 'Q4',
    'ot': 'OT', 'ot1': 'OT', 'ot2': '2OT', '2ot': '2OT',
    'p1': 'P1', 'p2': 'P2', 'p3': 'P3',
    'half': 'Half', '1st half': '1st Half', '2nd half': '2nd Half',
    'first half': '1st Half', 'second half': '2nd Half',
}
# mpv keeps its own seek keys. These two match the window's period jumps.
CHAPTER_BINDS = '[ add chapter -1\n] add chapter 1\n'
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', re.I)


class Chapter:
    def __init__(self, name, start):
        self.name, self.start = name, float(start)

    def as_dict(self):
        return {'name': self.name, 'start': self.start}


class Timeline:
    def __init__(self, duration, seekable, chapters):
        self.duration = duration
        self.seekable = bool(seekable) and duration is not None and duration > 0
        self.chapters = tuple(chapters)

    def as_dict(self):
        return {
            'duration': self.duration,
            'seekable': self.seekable,
            'chapters': [chapter.as_dict() for chapter in self.chapters],
        }


def _stamp(value):
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    return dt.datetime.fromisoformat(text)


def _attrs(line):
    body = line.split(':', 1)[1] if ':' in line else ''
    found = {}
    for key, value in _ATTR.findall(body):
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        found[key.upper()] = value
    return found


def _period_name(value):
    return NAMED.get(' '.join(str(value).split()).casefold())


def from_playlist(manifest, sport):
    """Read one media playlist. A master playlist, or one that never ends, has no timeline."""
    if not manifest or not str(manifest).lstrip().startswith('#EXTM3U'):
        return Timeline(None, False, ())
    if '#EXT-X-STREAM-INF' in manifest:
        return Timeline(None, False, ())
    position, span_start = 0.0, 0.0
    in_ad, ended = False, False
    spans, named = [], []
    anchor = None
    for raw in str(manifest).splitlines():
        line = raw.strip()
        if line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
            try:
                anchor = (position, _stamp(line.split(':', 1)[1]))
            except ValueError:
                anchor = None
        elif line.startswith('#EXT-X-DATERANGE:') and anchor is not None:
            attrs = _attrs(line)
            name = _period_name(attrs.get('TITLE', ''))
            if name and 'START-DATE' in attrs:
                try:
                    start = anchor[0] + (_stamp(attrs['START-DATE']) - anchor[1]).total_seconds()
                except ValueError:
                    start = None
                if start is not None:
                    named.append(Chapter(name, start))
        elif line.startswith('#EXT-X-CUE-OUT') and not in_ad:
            if position > span_start:
                spans.append((span_start, position))
            in_ad = True
        elif line.startswith('#EXT-X-CUE-IN') and in_ad:
            in_ad = False
            span_start = position
        elif line == '#EXT-X-ENDLIST':
            ended = True
        elif line.startswith('#EXTINF:'):
            try:
                position += max(0.0, float(line[8:].split(',', 1)[0]))
            except ValueError:
                return Timeline(None, False, ())
    if not in_ad and position > span_start:
        spans.append((span_start, position))
    if not ended or position <= 0:
        return Timeline(None, False, ())
    chapters = _chapters(position, spans, named, PERIODS.get(sport))
    return Timeline(position, True, chapters)


def _chapters(duration, spans, named, periods):
    named = [chapter for chapter in named if 0 <= chapter.start < duration]
    if named:
        named.sort(key=lambda chapter: chapter.start)
        kept = []
        for chapter in named:
            if kept and abs(kept[-1].start - chapter.start) < 0.5:
                continue
            kept.append(chapter)
        return tuple(kept)
    usable = [span for span in spans if span[1] - span[0] >= MIN_SPAN]
    if periods and len(periods) <= len(usable) <= len(periods) + len(OVERTIME):
        labels = periods + OVERTIME
        return tuple(Chapter(label, start) for label, (start, _) in zip(labels, usable))
    if not periods or duration < MIN_PERIOD * len(periods):
        return ()
    count = len(periods)
    return tuple(Chapter(label, duration * index / count) for index, label in enumerate(periods))


def chapter_at(timeline, playhead):
    name = None
    for chapter in timeline.chapters:
        if chapter.start <= playhead + 0.001:
            name = chapter.name
        else:
            break
    return name


def move(timeline, playhead, command):
    """`chapter` steps the index by one, same as mpv `add chapter`, so the middle of a period goes to the previous period."""
    playhead = float(playhead or 0)
    if not timeline.seekable or timeline.duration is None:
        sent = {'seek': playhead, 'chapter': None}
        return playhead, None, sent
    duration = timeline.duration
    if 'to' in command:
        landing = float(command['to'])
    elif 'chapter' in command:
        landing = _chapter_landing(timeline, playhead, int(command['chapter']))
    else:
        landing = playhead + float(command.get('by', 0))
    landing = min(duration, max(0.0, landing))
    name = chapter_at(timeline, landing)
    return landing, name, {'seek': landing, 'chapter': name}


def _chapter_landing(timeline, playhead, direction):
    if not timeline.chapters or direction == 0:
        return playhead
    current = 0
    for index, chapter in enumerate(timeline.chapters):
        if chapter.start <= playhead + 0.001:
            current = index
    nxt = current + (1 if direction > 0 else -1)
    if nxt < 0 or nxt >= len(timeline.chapters):
        return playhead
    return timeline.chapters[nxt].start


def ffmetadata(timeline):
    lines = [';FFMETADATA1']
    chapters = timeline.chapters
    end_of_file = round(float(timeline.duration) * 1000)
    for index, chapter in enumerate(chapters):
        start = round(chapter.start * 1000)
        end = round(chapters[index + 1].start * 1000) if index + 1 < len(chapters) else end_of_file
        if end <= start:
            end = start + 1
        lines.extend(('[CHAPTER]', 'TIMEBASE=1/1000', f'START={start}', f'END={end}', f'title={chapter.name}'))
    return '\n'.join(lines) + '\n'


def player_args(chapters_path, binds_path):
    """mpv argv that adds period jumps without replacing mpv's seek keys."""
    path = str(binds_path).replace('\\', '/')
    if re.search(r'\s', path):
        path = '"' + path.replace('"', '') + '"'
    return [f'--chapters-file={chapters_path}', f'--input-commands=load-input-conf {path}']


def media_playlist(url, headers=None):
    parsed = urllib.parse.urlsplit(url)
    local = parsed.hostname in ('127.0.0.1', 'localhost') and parsed.scheme == 'http'
    local_port = parsed.port if local else None
    opener = urllib.request.build_opener(grading._Redirect(local_port))
    request_headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}
    request_headers.update(headers or {})
    current = url
    for _ in range(3):
        if not grading._permitted(current, local_port):
            raise ValueError('Invalid playlist URL')
        request = urllib.request.Request(current, headers=request_headers)
        with opener.open(request, timeout=5) as response:
            raw = response.read(1_000_001)
            current = response.geturl()
        if len(raw) > 1_000_000:
            raise ValueError('Playlist too large')
        manifest = raw.decode('utf-8', 'replace')
        if not manifest.lstrip().startswith('#EXTM3U'):
            raise ValueError('Not an HLS playlist')
        if '#EXT-X-STREAM-INF' not in manifest:
            return manifest
        lines = [line.strip() for line in manifest.splitlines() if line.strip()]
        variants = []
        for index, line in enumerate(lines):
            if line.startswith('#EXT-X-STREAM-INF:') and index + 1 < len(lines) and not lines[index + 1].startswith('#'):
                match = re.search(r'RESOLUTION=\d+x(\d+)', line, re.I)
                variants.append((int(match.group(1)) if match else 0, lines[index + 1]))
        if not variants:
            raise ValueError('Playlist has no variant')
        current = urllib.parse.urljoin(current, max(variants)[1])
    raise ValueError('Playlist did not resolve')
