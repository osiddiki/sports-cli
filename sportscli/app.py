#!/usr/bin/env python3
"""Sports terminal stream picker with an optional native fuzzy matcher."""
from __future__ import annotations
import argparse
import curses
import concurrent.futures
import datetime as dt
import json
import os
import pathlib
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from . import providers, relay, grading, checks, replays, smart_search, players, ad_mute, ai_ad_mute, breaks, tv
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

UTC = dt.timezone.utc
SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
AUTO_CHECKS_PER_DIRECTORY, AUTO_CHECKS_PER_SCREEN = 80, 80
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36'
ESPN = 'https://site.api.espn.com/apis/site/v2/sports'
LEAGUES = {
    'nba': ('NBA', 'basketball/nba', 'basketball'),
    'wnba': ('WNBA', 'basketball/wnba', 'basketball'),
    'ncaam': ("NCAA men's basketball", 'basketball/mens-college-basketball', 'basketball'),
    'ncaaw': ("NCAA women's basketball", 'basketball/womens-college-basketball', 'basketball'),
    'nfl': ('NFL', 'football/nfl', 'football'),
    'ncaaf': ('NCAA football', 'football/college-football', 'football'),
    'mlb': ('MLB', 'baseball/mlb', 'baseball'),
    'nhl': ('NHL', 'hockey/nhl', 'hockey'),
    'mls': ('Soccer: MLS', 'soccer/usa.1', 'soccer'),
    'epl': ('Soccer: Premier League', 'soccer/eng.1', 'soccer'),
    'laliga': ('Soccer: La Liga', 'soccer/esp.1', 'soccer'),
    'bundesliga': ('Soccer: Bundesliga', 'soccer/ger.1', 'soccer'),
    'seriea': ('Soccer: Serie A', 'soccer/ita.1', 'soccer'),
    'ligue1': ('Soccer: Ligue 1', 'soccer/fra.1', 'soccer'),
    'ligamx': ('Soccer: Liga MX', 'soccer/mex.1', 'soccer'),
    'ucl': ('Soccer: Champions League', 'soccer/uefa.champions', 'soccer'),
}
PPV = 'https://api.ppv.st/api/streams'
REDZONE = 'redzone'  # the id of the NFL RedZone row on an NFL Sunday schedule
SF = 'https://streamfree.top/streams'
SF_ROOT = 'https://streamfree.top'
NTV = 'https://ntv.st'
RESOLVER = 'http://127.0.0.1:38765'
CONFIG = pathlib.Path(os.environ.get('XDG_CONFIG_HOME', pathlib.Path.home() / '.config')) / 'nba-tui' / 'config.json'
HOME_ITEMS = [('sports', '1', 'Live sports', "Today's games and their streams"),
              ('replays', '2', 'Replays', 'Full games from recent days'),
              ('tv', '3', 'Live TV', 'CNN, CNBC, MS NOW, Fox News and more'),
              ('search', '/', 'Search', 'Teams, players, games and channels'),
              ('settings', ',', 'Settings', 'Player, backup streams, ad breaks')]

HELP_LINES = [
    'EVERYWHERE     1 live sports   2 replays   3 live TV   / search   , settings   ? help   q quit',
    'MOVE           Up/Down or j/k: select    Enter, Right or l: open    Esc, Left, h or Backspace: back',
    'LISTS          f: filter this list    r: refresh    s: change sport (games and replays)',
    'SCHEDULE       [ and ]: previous or next day',
    'SOURCES        Enter: play    x: test    i: show media URL    o: provider    a: home or away feed',
    '               g: rank by checks    u: play a pasted media URL    v: show sources that failed checks',
    'SEARCH         Type a team, player, game or channel    Up/Down: select    Esc: back    Ctrl-X: quit',
    'SETTINGS       Player, cache, terminal video, backup source, ad breaks, add-on, PPV resolver',
    'AD MUTE        --ad-mute: marked replays    --ai-ad-mute: local NBA detector    --ai-ad-observe: preview',
    'START          --tv: live TV    --league nba: a schedule    --search: search',
    'While a stream plays, keys go to mpv: Space pause, m mute, f fullscreen, q back here.',
    'Finished games and explicitly offline sources are hidden from live lists.',
    'Keep this terminal open while playing a local relay stream.',
]


def request(url, *, data=None, headers=None, timeout=12):
    h = {'User-Agent': UA, 'Accept': 'application/json,*/*'}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError('Response too large')
    return json.loads(raw)


def load_config():
    try:
        x = json.loads(CONFIG.read_text())
        return x if isinstance(x, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(config):
    CONFIG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # Windows keeps files private to the user's profile and has no POSIX modes to set.
    if os.name != 'nt': os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(config, f, indent=2)


def parse_date(s):
    try:
        return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(UTC)
    except (AttributeError, ValueError):
        return None


def safe_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        return p.scheme in ('http', 'https') and bool(p.netloc) and not p.username and not p.password
    except (ValueError, TypeError):
        return False


def title_matches(title, game):
    def normalize(value):
        text = unicodedata.normalize('NFKD', str(value).casefold())
        text = ''.join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r'\bstate\b', 'st', text)
        return re.sub(r'[^a-z0-9]+', ' ', text).strip()

    text = normalize(title)
    def spans(names):
        found = []
        for name in names:
            name = normalize(name)
            variants = [name]
            if game.category == 'soccer':
                # ESPN sometimes includes FC/CF while a directory leaves it out.
                variants.append(re.sub(r'\b(?:fc|cf)\b', '', name).strip())
            for variant in variants:
                variant = ' '.join(variant.split())
                if len(variant) >= 3:
                    found.extend(m.span() for m in re.finditer(r'\b' + re.escape(variant) + r'\b', text))
        return found

    home = spans([game.home_nickname, *game.home_aliases])
    away = spans([game.away_nickname, *game.away_aliases])
    # Distinct occurrences prevent a shared location or college nickname from
    # matching both teams when only one is actually present.
    return any(a[1] <= b[0] or b[1] <= a[0] for a in home for b in away)


def feed_side(label):
    text = str(label).lower()
    if re.search(r'\bhome\b', text) and not re.search(r'\baway\b', text):
        return 'home'
    if re.search(r'\baway\b', text) and not re.search(r'\bhome\b', text):
        return 'away'
    return 'unknown'


@dataclass
class Game:
    id: str
    title: str
    start: dt.datetime
    home: str
    away: str
    home_nickname: str
    away_nickname: str
    broadcasts: list[str] = field(default_factory=list)
    league: str = 'nba'
    category: str = 'basketball'
    home_aliases: list[str] = field(default_factory=list)
    away_aliases: list[str] = field(default_factory=list)
    state: str = 'unknown'


@dataclass
class Feed:
    source: str
    label: str
    side: str = 'unknown'
    url: str = ''
    kind: str = 'web'  # Resolve sources on selection so short-lived links are fresh.
    headers: dict = field(default_factory=dict)
    note: str = ''
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FeedDisplay:
    name: str
    side: str
    quality: str
    check: str
    detail: str


@dataclass
class ReplayBundle:
    league: str
    title: str
    date: dt.date | None
    sources: list[replays.Replay]
    identity: tuple


@dataclass(frozen=True)
class ReplayBundleHit:
    item: ReplayBundle
    kind: str = 'bundle'

    @property
    def league(self): return self.item.league
    @property
    def title(self): return self.item.title
    @property
    def key(self): return ('bundle', *self.item.identity)


@dataclass(frozen=True)
class SearchSideHit:
    item: Game
    side: str
    count: int
    kind: str = 'side'

    @property
    def league(self): return self.item.league
    @property
    def title(self): return f'{self.side.title()} broadcasts'
    @property
    def key(self): return ('side', self.item.league, self.item.id, self.side)


def replay_identity(index, hit):
    """Group the same dated matchup across different replay directories."""
    title = re.split(r'\s+[-–—]\s+', hit.title, maxsplit=1)[0].strip()
    parts = re.split(r'\s+(?:vs\.?|versus)\s+', title, maxsplit=1, flags=re.I)
    if hit.date and len(parts) == 2:
        teams = []
        for part in parts:
            matches = [(team, score) for team, score in index.resolve(part) if team.league == hit.league]
            teams.append(matches[0][0].id if matches and matches[0][1] >= .9 else smart_search.normalize(part))
        return (hit.league, hit.date.isoformat(), *sorted(teams)), title
    return (hit.league, hit.item.url), title


def check_age(seconds):
    minutes = max(0, int(seconds // 60))
    return 'now' if minutes == 0 else f'{minutes}m' if minutes < 60 else f'{minutes // 60}h'


def smoothness(speed):
    """0 smooth, 1 unmeasured or enough, 2 likely to lag: video downloaded per second of download time."""
    return 1 if speed is None else 0 if speed >= 2 else 1 if speed >= 1.2 else 2


def describe_feed(feed, record=None, automatic=None):
    """Turn provider-specific labels into consistent, readable stream rows."""
    parts = [part.strip() for part in feed.label.split('|') if part.strip()]
    server = re.sub(r'\s*\((?:home|away)\)\s*$', '', parts[0] if parts else feed.label, flags=re.I)
    details, listed_quality = [], ''
    for part in parts[1:]:
        if re.fullmatch(r'\d{3,4}p|4k|hd|sd', part, re.I):
            listed_quality = part.upper() if part.lower() in ('hd','sd','4k') else part.lower()
        elif part.lower() == 'live':
            details.append('Provider reports live')
        elif part.lower() != 'status unknown':
            details.append(part)
    record = record if isinstance(record, dict) else {}
    height = record.get('height')
    quality = f'{height}p' if isinstance(height, int) and height >= 144 else f'~{listed_quality}' if listed_quality else '-'
    status = {'checking':'Checking', 'testing':'Testing', 'OK':'OK', '?':'Unverified', 'n/a':'Checked on play'}.get(
        automatic, automatic or ('Checked on play' if feed.kind in ('ppv', 'web') else 'Unverified'))
    if isinstance(automatic, str) and automatic.split(' ')[0] == 'OK' and isinstance(record.get('speed'), (int, float)):
        status = ('Smooth', 'OK', 'May lag')[smoothness(record['speed'])] + automatic[2:]
    grade = grading.summary(record)
    if grade:
        grade = re.sub(r'\s+\d+p$', '', grade)
        status += f' {grade}'
    if not details and feed.kind == 'candidate': details.append('Player resolves on selection')
    return FeedDisplay(f'{feed.source} / {server}', feed.side.upper() if feed.side != 'unknown' else '-',
                       quality, status, ' | '.join(details))


class DependencyError(RuntimeError):
    """The stream has not been checked because a local dependency is missing."""


def games_from_espn(data, now=None, league='nba'):
    result = []
    for e in data.get('events', []):
        start = parse_date(e.get('date'))
        comps = e.get('competitions') or []
        if not start or not comps:
            continue
        comp = comps[0]
        status = (e.get('status') or {}).get('type') or (comp.get('status') or {}).get('type') or {}
        # ESPN explicitly marks finished/canceled events; do not infer from
        # their start time because games can run long or be delayed.
        if (status.get('completed') is True or status.get('state') == 'post'
                or status.get('name') in ('STATUS_CANCELED', 'STATUS_CANCELLED')):
            continue
        teams = {c.get('homeAway'): c.get('team', {}) for c in comp.get('competitors', [])}
        home, away = teams.get('home', {}), teams.get('away', {})
        home_name, away_name = home.get('displayName', ''), away.get('displayName', '')
        if not home_name or not away_name:
            continue
        home_nick = home.get('name') or home.get('shortDisplayName') or home_name.split()[-1]
        away_nick = away.get('name') or away.get('shortDisplayName') or away_name.split()[-1]
        broadcasts = []
        for b in comp.get('broadcasts', []):
            market = b.get('market', '')
            if isinstance(market, dict): market = market.get('type', '')
            names = b.get('names', []) or [b.get('media', {}).get('shortName', '')]
            for channel in names:
                if channel:
                    label = channel + (f' ({market.capitalize()})' if market.lower() in ('home', 'away') else '')
                    if label not in broadcasts: broadcasts.append(label)
        def aliases(team):
            return [team.get(key, '') for key in ('displayName', 'location', 'shortDisplayName')]
        state = {'in':'live', 'pre':'upcoming'}.get(status.get('state'), 'unknown')
        result.append(Game(str(e.get('id', '')), e.get('name', f'{away_name} at {home_name}'), start, home_name, away_name, home_nick, away_nick, broadcasts, league, LEAGUES[league][2], aliases(home), aliases(away), state))
    return sorted(result, key=lambda g: g.start)


def default_day():
    try: return dt.datetime.now(ZoneInfo('America/New_York')).date()
    except Exception: return dt.datetime.now().date()


def get_games(day=None, league='nba'):
    day = day or default_day()
    path = LEAGUES[league][1]
    def scoreboard(date):
        return games_from_espn(request(f'{ESPN}/{path}/scoreboard?dates={date:%Y%m%d}&limit=300'), league=league)
    games = scoreboard(day)
    if day != default_day(): return games
    # Late West Coast games run past midnight Eastern while ESPN keeps them on the previous day.
    try: earlier = [game for game in scoreboard(day - dt.timedelta(days=1)) if game.state == 'live']
    except (OSError, ValueError): return games
    ids = {game.id for game in games}
    return sorted([game for game in earlier if game.id not in ids] + games, key=lambda game: game.start)


def get_team_games(team_id, league, today=None):
    """Fetch one relevant team's upcoming schedule, limited to the next month."""
    today = today or default_day()
    path = LEAGUES[league][1]
    ident = urllib.parse.quote(str(team_id), safe='')
    data = request(f'{ESPN}/{path}/teams/{ident}/schedule')
    games = games_from_espn(data, league=league)
    last_day = today + dt.timedelta(days=30)
    return [game for game in games
            if today <= game.start.astimezone(ZoneInfo('America/New_York')).date() <= last_day]


def search_replay_pages(spec, team, opponent='', date=None, canceled=lambda: False):
    """Read bounded archive pages for a team; stop early for a specific game."""
    found, seen = [], set()
    for page in range(2, 7):
        if canceled(): raise concurrent.futures.CancelledError()
        page_spec = replays.page_spec(spec, page)
        if not page_spec: break
        items = replays.listing(page_spec)
        for item in items:
            title = smart_search.normalize(item.title)
            if smart_search.normalize(team) not in title: continue
            if opponent and smart_search.normalize(opponent) not in title: continue
            if date and smart_search.replay_date(item.title) != date: continue
            if item.url not in seen:
                seen.add(item.url); found.append(item)
        if found and (opponent or date): break
    return found


def ppv_feeds(data, game, now=None):
    feeds = []
    now = now or dt.datetime.now(UTC)
    for group in data.get('streams', []):
        for event in group.get('streams', []):
            if not title_matches(event.get('name', ''), game):
                continue
            try:
                event_start = dt.datetime.fromtimestamp(int(event.get('starts_at', 0)), UTC)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            if abs((event_start - game.start).total_seconds()) > 6 * 3600:
                continue
            try:
                ends_at = int(event.get('ends_at') or 0)
            except (TypeError, ValueError, OverflowError):
                ends_at = 0
            if ends_at and ends_at <= now.timestamp() and not event.get('always_live'):
                continue
            for src in [event, *(event.get('substreams') or [])]:
                url = src.get('iframe', '')
                if not safe_url(url):
                    continue
                label = src.get('source_tag') or src.get('name') or 'Broadcast'
                feeds.append(Feed('PPV', label, feed_side(label), url, 'ppv', note='local resolver for mpv; browser available'))
    return feeds


def sf_feeds(data, game):
    out = []
    for event in data.get('streams', {}).get(game.category, []):
        if not title_matches(event.get('name', ''), game):
            continue
        try:
            t = dt.datetime.fromtimestamp(int(event.get('match_timestamp', 0)), UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if abs((t - game.start).total_seconds()) > 6 * 3600:
            continue
        key = event.get('stream_key', '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', key):
            continue
        sources = request(SF_ROOT + '/api/v1/sources/' + urllib.parse.quote(key))
        # Sources were plain URLs and are now {"quality", "url"} objects; accept both.
        link = lambda item: item.get('url') if isinstance(item, dict) else item
        live_sources = sources.get('live_sources')
        live = {link(item) for item in live_sources} if isinstance(live_sources, list) else None
        for source in sources.get('sources', [])[:20]:
            source = link(source)
            if not isinstance(source, str) or (live is not None and source not in live):
                continue
            parts = urllib.parse.urlsplit(source)
            variant = parts.path.rsplit('/', 1)[-1]
            match = re.fullmatch(re.escape(key) + r'(\d+p)(\d*)', variant)
            if not match or not parts.path.startswith('/embed/' + game.category + '/'):
                continue
            quality, number = match.groups()
            number = number or '1'
            embed = SF_ROOT + parts.path
            status = 'live' if live is not None else 'status unknown'
            out.append(Feed('StreamFree', f'Source {number} | {quality} | {status}', url=embed,
                            kind='sf', metadata={'key': key, 'quality': quality, 'variant': variant, 'category': game.category}))
    return out


def request_text(url, *, headers=None, timeout=12, limit=1_000_000):
    h = {'User-Agent': UA, 'Accept': '*/*'}
    if headers:
        h.update(headers)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as res:
        data = res.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Response too large')
    return data.decode('utf-8', 'replace')


def resolve_streamfree(feed, *, timeout=12):
    key = feed.metadata['key']
    quality = feed.metadata['quality']
    variant = feed.metadata['variant']
    category = feed.metadata.get('category', 'basketball')
    embed_headers = {'Referer': f'{SF_ROOT}/player/{category}/{key}', 'Origin': SF_ROOT}
    page = request_text(feed.url, headers=embed_headers, timeout=timeout)
    match = re.search(r'const\s+_0x\s*=\s*(\{.*?\})\s*;', page, re.S)
    if not match:
        raise ValueError('StreamFree player did not provide playlist tokens')
    tokens = json.loads(match.group(1)).get(quality)
    if not isinstance(tokens, dict) or not all(key in tokens for key in ('_t', '_e', '_n')):
        raise ValueError('StreamFree playlist tokens are missing')
    info = request(SF_ROOT + '/get-stream-key/' + urllib.parse.quote(key), headers=embed_headers, timeout=timeout)
    if info.get('is_external') and info.get('external_url'):
        url = info['external_url']
        if not safe_url(url):
            raise ValueError('Invalid external stream URL')
    else:
        server = info.get('server_name') or 'origin'
        route = 'live-externalcdn' if server == 'externalcdn' else 'live-cdn' if server != 'origin' else 'live'
        query = urllib.parse.urlencode({k: tokens[k] for k in ('_t', '_e', '_n')})
        url = f'{SF_ROOT}/{route}/{urllib.parse.quote(variant)}/index.m3u8?{query}'
    headers = {'Referer': feed.url, 'Origin': SF_ROOT, 'User-Agent': UA}
    return url, headers


def check_playlist(url, headers=None):
    manifest = request_text(url, headers=headers, timeout=10)
    if not manifest.lstrip().startswith('#EXTM3U'):
        raise ValueError('Provider did not return an HLS playlist')
    if not any(line.strip() and not line.lstrip().startswith('#') for line in manifest.splitlines()):
        raise ValueError('Playlist has no media yet')
    return manifest


def streamfree_wraps_segments(url, manifest, headers):
    """Detect StreamFree's PNG transport-stream chunks using a small segment sample."""
    if any(tag in manifest for tag in ('#EXT-X-STREAM-INF', '#EXT-X-MAP', '#EXT-X-KEY', '#EXT-X-BYTERANGE')):
        return False
    segments = [line.strip() for line in manifest.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if not segments: return False
    segment = urllib.parse.urljoin(url, segments[-2] if len(segments) > 1 else segments[-1])
    if not providers.public_url(segment): raise ValueError('Invalid StreamFree segment URL')
    req = urllib.request.Request(segment, headers=headers)
    with urllib.request.build_opener(providers.PublicRedirect()).open(req, timeout=8) as response:
        sample = response.read(256)
    if not sample.startswith(b'\x89PNG\r\n\x1a\n'): return False
    pos = 8
    while pos + 8 <= len(sample):
        size = int.from_bytes(sample[pos:pos+4], 'big')
        tag = sample[pos+4:pos+8]
        if tag == b'tsDa': return True
        if size > len(sample)-pos-12: break
        pos += size+12
    return False


def addon_base(manifest):
    manifest = manifest.strip().replace('stremio://', 'https://', 1)
    if not safe_url(manifest):
        raise ValueError('Enter a valid HTTP(S) manifest URL')
    parts = urllib.parse.urlsplit(manifest)
    if not parts.path.endswith('/manifest.json') or parts.query or parts.fragment:
        raise ValueError('The add-on URL must end in /manifest.json')
    return manifest[:-len('/manifest.json')]


def addon_feeds(manifest, game):
    base = addon_base(manifest)
    meta = request(manifest.replace('stremio://', 'https://', 1))
    catalogs = meta.get('catalogs', [])
    # Only query the selected sport and generic sports catalogs.
    pattern = re.escape(game.category) + r'|sports.*today|sports.*live|live.*sport'
    candidates = [c for c in catalogs if re.search(pattern, str(c.get('id', '')) + ' ' + str(c.get('name', '')), re.I)]
    if not candidates:
        candidates = [c for c in catalogs if c.get('type') in ('sport', 'tv')][:3]
    found = {}
    for cat in candidates[:4]:
        typ, ident = str(cat.get('type', '')), str(cat.get('id', ''))
        if not re.fullmatch(r'[\w.-]+', typ) or not re.fullmatch(r'[\w.-]+', ident):
            continue
        try:
            data = request(f'{base}/catalog/{typ}/{ident}.json')
        except (OSError, ValueError):
            continue
        for item in data.get('metas', []):
            if title_matches(item.get('name', ''), game):
                found[(typ, str(item.get('id', '')))] = item
    if len(found) != 1:
        if len(found) > 1:
            raise ValueError('Add-on returned more than one matching game; no match selected')
        return []
    (typ, ident), item = next(iter(found.items()))
    if not ident:
        return []
    url = f'{base}/stream/{typ}/{urllib.parse.quote(ident, safe=":")}.json'
    data = request(url, timeout=20)
    out = []
    for s in data.get('streams', []):
        label = ' '.join(str(s.get(k, '')).replace('\n', ' ') for k in ('name', 'title', 'description')).strip()
        if re.search(r'please reinstall|no streams|no sources|visit .*configure', label, re.I):
            continue
        url = s.get('url', '')
        hints = s.get('behaviorHints') or {}
        headers = (hints.get('proxyHeaders') or {}).get('request') or {}
        if not isinstance(headers, dict):
            headers = {}
        if safe_url(url):
            out.append(Feed('Add-on', label or 'Stream', feed_side(label), url, 'direct', headers, 'media link from add-on; availability not yet checked'))
    return out


def feed_tasks(game, config):
    tasks = [('StreamFree', lambda: sf_feeds(request(SF), game)),
             ('PPV', lambda: ppv_feeds(request(PPV), game)),
             ('NTV', lambda: ntv_feeds(game))]
    if game.league == 'nba':
        tasks.extend([('NBA Monster', lambda: monster_feeds(game)),
                      ('NBA Bite', lambda: directory_feeds(game))])
    if config.get('addon'):
        tasks.append(('Add-on', lambda: addon_feeds(config['addon'], game)))
    return tasks


def fetch_feeds(game, config):
    out, errors = [], []
    tasks = feed_tasks(game, config)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [(name, pool.submit(fn)) for name, fn in tasks]
        for name, future in futures:
            try:
                out.extend(future.result())
            except (OSError, ValueError, KeyError, TypeError) as e:
                errors.append(f'{name}: {type(e).__name__}')
    return out, errors


def channel_side(channel, game):
    explicit = feed_side(channel)
    if explicit != 'unknown': return explicit
    # A generic League Pass label may carry either side; do not guess.
    if 'league pass' in channel.lower(): return 'unknown'
    clean = lambda x: re.sub(r'[^a-z0-9]', '', x.lower())
    candidate = clean(channel)
    sides = set()
    for broadcast in game.broadcasts:
        side = feed_side(broadcast)
        name = clean(re.sub(r'\s*\((?:Home|Away)\)\s*$', '', broadcast, flags=re.I))
        if side != 'unknown' and len(name) >= 6 and name in candidate: sides.add(side)
    return next(iter(sides)) if len(sides) == 1 else 'unknown'


def directory_feeds(game):
    out = []
    for item in providers.nbabite_entries(game, title_matches):
        channel = item['channel'] or 'Unknown channel'
        label = f"{item['name']} | {channel} | {item['quality']} | {item['language']}"
        out.append(Feed('NBA Bite', label, channel_side(channel, game), item['url'], 'candidate',
                        note='directory listing; resolved when selected', metadata={'referer':providers.NBABITE+'/'}))
    return out


def monster_feeds(game):
    return [Feed('NBA Monster', item['name'], url=item['url'], kind='candidate',
                 note='directory listing; resolved when selected', metadata={'referer':providers.MONSTER+'/'})
            for item in providers.monster_entries(game)]


def ntv_feeds(game):
    def fetch_server(server):
        data = request(f'{NTV}/api/get-matches?server={server}&type=both',
                       headers={'Referer':NTV+'/'}, timeout=12)
        if not data.get('success'): raise ValueError('NTV directory unavailable')
        out = []
        for event in data.get('all', []):
            if not title_matches(event.get('title', ''), game): continue
            if event.get('live') is False or str(event.get('status', '')).lower() in ('upcoming', 'offline', 'ended', 'finished'):
                continue
            try: event_time = dt.datetime.fromtimestamp(int(event.get('date', 0))/1000, UTC)
            except (ValueError, TypeError, OverflowError, OSError): continue
            if abs((event_time-game.start).total_seconds()) > 6*3600: continue
            for source in event.get('sources', [])[:25]:
                url = source.get('url', '')
                if not providers.public_url(url): continue
                channel = source.get('channelName') or source.get('source') or 'Stream'
                label = f'{server.title()} | {channel}'
                out.append(Feed('NTV', label, channel_side(channel,game), url, 'candidate',
                                note='directory listing; resolved when selected',
                                metadata={'referer':NTV+'/', 'provider':server + '/' + str(source.get('source',''))
                                          + '/' + str(source.get('channelId') or channel)}))
        return out
    out = []
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch_server, server) for server in ('phoenix', 'falcon')]
        for future in futures:
            try: out.extend(future.result())
            except (OSError, ValueError, TypeError, KeyError) as exc: failures.append(exc)
    if len(failures) == len(futures): raise failures[0]
    seen = set()
    return [f for f in out if not (f.url in seen or seen.add(f.url))]


# Retry a failed HLS segment instead of skipping it, and refill a little more
# after an underrun so one slow segment does not cause repeated stutters.
HLS_OPTIONS = 'allowed_extensions=ALL,allowed_segment_extensions=ALL,extension_picky=0,seg_max_retry=3'
STALL_OPTIONS = ('cache-pause-wait=2', 'network-timeout=20')


def mpv_command(url, headers=None, *, cache=15, terminal_video=False):
    # Software decoding drops about 8 frames a second of 1080p60 on an M2 Air; hardware decoding drops none.
    cmd = ['mpv', '--no-ytdl', '--force-window=yes', '--title=Sports CLI', '--hwdec=auto-safe', '--demuxer-lavf-o=' + HLS_OPTIONS, '--cache=yes', f'--cache-secs={cache}', '--demuxer-max-bytes=256MiB', '--demuxer-readahead-secs=' + str(cache)]
    cmd.extend('--' + option for option in STALL_OPTIONS)
    if terminal_video:
        cmd.append('--vo=tct')
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9-]+', name) or '\n' in value or '\r' in value or ',' in value:
            continue
        cmd.append(f'--http-header-fields-append={name}: {value}')
    cmd.extend(['--', url])
    return cmd


def find_iina():
    if sys.platform != 'darwin': return None
    for candidate in (shutil.which('iina-cli'), shutil.which('iina'),
                      '/Applications/IINA.app/Contents/MacOS/iina-cli',
                      str(pathlib.Path.home() / 'Applications/IINA.app/Contents/MacOS/iina-cli')):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK): return candidate
    return None


def iina_command(executable, url, headers=None, *, cache=15):
    cmd = [executable, '--no-stdin', '--mpv-ytdl=no', '--mpv-force-media-title=Sports CLI',
           '--mpv-hwdec=auto-safe', '--mpv-cache=yes', f'--mpv-cache-secs={cache}', '--mpv-demuxer-max-bytes=256MiB',
           '--mpv-demuxer-lavf-o=' + HLS_OPTIONS, *('--mpv-' + option for option in STALL_OPTIONS)]
    fields = []
    for name, value in (headers or {}).items():
        if isinstance(name, str) and isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9-]+', name) and not any(c in value for c in '\r\n,'):
            fields.append(f'{name}: {value}')
    if fields: cmd.append('--mpv-http-header-fields=' + ','.join(fields))
    cmd.append(url)
    return cmd


def setup_hint(problem, option, otherwise, *, running=True):
    """How to fix a missing piece: the installer's command when it installed the app, else the manual message."""
    command = os.environ.get('SPORTS_CLI_COMMAND')
    if not command: return otherwise
    return f'{problem}. {"Quit and run" if running else "Run"}: {command} {option}'


def find_celluloid():
    return shutil.which('celluloid') if sys.platform.startswith('linux') else None


def celluloid_command(executable, url, headers=None, *, cache=15):
    # Use a separate instance so each stream's mpv options and headers apply to
    # its own window even when the user already has Celluloid running.
    cmd = [executable, '--no-existing-session', '--mpv-ytdl=no',
           '--mpv-force-media-title=Sports CLI', '--mpv-cache=yes',
           f'--mpv-cache-secs={cache}', '--mpv-demuxer-max-bytes=256MiB',
           '--mpv-demuxer-lavf-o=' + HLS_OPTIONS, *('--mpv-' + option for option in STALL_OPTIONS)]
    fields = []
    for name, value in (headers or {}).items():
        # Celluloid internally wraps option values in single quotes and parses
        # backslash escapes. Reject values that could change that parsing.
        if (isinstance(name, str) and isinstance(value, str) and
                re.fullmatch(r'[A-Za-z0-9-]+', name) and not any(c in value for c in "\r\n,'\\")):
            fields.append(f'{name}: {value}')
    if fields: cmd.append('--mpv-http-header-fields=' + ','.join(fields))
    cmd.append(url)
    return cmd


def quick_probe_feed(feed):
    """Resolve a source and sample one segment without using shared player state."""
    temporary_relay = None
    try:
        if feed.kind == 'sf':
            url, headers = resolve_streamfree(feed, timeout=4)
        elif feed.kind == 'direct':
            url, headers = feed.url, feed.headers
        elif feed.kind == 'candidate':
            found = providers.extract_player(feed.url, max_pages=3,
                                             referer=feed.metadata.get('referer'), timeout=4)
            if found['kind'] == 'relay':
                temporary_relay = relay.Relay()
                url = temporary_relay.add(found['url'], found['edge'], found['channel'], found['referer'])
                headers = {}
            elif found['kind'] == 'sf':
                source = Feed(feed.source, feed.label, feed.side, found['url'], 'sf',
                              metadata={key:found[key] for key in ('key','quality','variant','category')})
                url, headers = resolve_streamfree(source, timeout=4)
            elif found['kind'] == 'direct':
                ref = found['referer']; parts = urllib.parse.urlsplit(ref)
                url = found['url']
                headers = {'Referer':ref, 'Origin':parts.scheme+'://'+parts.netloc, 'User-Agent':UA}
            else:
                return None
        else:
            return None
        return grading.inspect_stream(url, headers, quick=True)
    finally:
        if temporary_relay: temporary_relay.close()


class App:
    def __init__(self, screen, config, *, ad_mute_enabled=False, ai_ad_mute_enabled=False, ai_ad_observe_enabled=False):
        self.scr, self.config = screen, config
        self.games, self.all_games, self.feeds = [], [], []
        self.replays = []
        self.search_index = None
        self.search_query = ''
        self.search_plan = None
        self.search_results = []
        self.search_position = 0
        self.search_touched = False
        self.search_return = None
        self.search_cache = {}
        self.search_needed = set()
        self.search_work = []
        self.search_inflight = set()
        self.search_queue = queue.Queue()
        self.search_jobs = queue.Queue()
        self.search_workers_started = False
        self.search_sources = {}
        self.search_due = None
        self.search_draw_signature = None
        self.player_directory = None
        self.player_key = None
        self.player_due = None
        self.player_cache = {}
        self.player_partial_key = None
        self.player_partial = []
        self.player_inflight = set()
        self.player_jobs = queue.Queue(maxsize=1)
        self.player_queue = queue.Queue()
        self.player_worker_started = False
        self.search_preview_key = None
        self.search_preview_generation = 0
        self.search_preview_sections = {}
        self.search_preview_order = []
        self.search_preview_pending = 0
        self.search_preview_queue = queue.Queue()
        self.search_preview_errors = []
        self.search_preview_adopt_generation = None
        self.search_preview_started = 0.0
        self.day = default_day()
        self.league = 'nba'
        self.league_index = 0
        self.league_cancel = None
        self.search = ''
        self.feed_search = ''
        self.replay_search = ''
        self.gi = self.fi = self.ri = 0
        self.mode = 'home'
        self.home_index = 0
        self.section = 'sports'           # which section the sport list serves: sports or replays
        self.settings_index = 0; self.settings_return = 'home'
        self.feed_context = 'live'
        self.feed_return = None
        self.current_replay = None
        self.channels, self.channel_errors, self.channel_filter = [], [], ''
        self.ci = 0; self.channels_loading = False; self.channels_loaded_at = 0
        self.channel_queue = queue.Queue(); self.current_channel = None; self.redzone_pending = False
        self.current_bundle = None
        self.bundle_index = 0
        self.help_return = 'games'
        self.help_scroll = 0
        self.live_color = 0
        self.accent_color = curses.A_BOLD
        self.loading_games = False
        self.schedule_generation = 0
        self.schedule_jobs = queue.Queue(maxsize=1)
        self.schedule_result_lock = threading.Lock()
        self.schedule_result = None
        self.schedule_workers_started = False
        self.schedule_workers = []
        self.schedule_stop = threading.Event()
        self.schedule_selected_key = None
        self.schedule_selected_id = None
        self.filter = 'all'
        self.provider_filter = 'all'
        self.message = 'Choose what to watch, or press / to search.'
        self.errors = []
        self.feed_queue = queue.Queue()
        self.feed_jobs = queue.Queue(maxsize=16)
        self.feed_workers = []
        self.feed_generation = 0
        self.feed_sections = {}
        self.feed_order = []
        self.feed_pending = 0
        self.feed_selection_touched = False
        self.hidden_feeds = set()
        self.auto_checks = {}
        self.auto_queue = queue.Queue()
        self.auto_jobs = queue.Queue(maxsize=AUTO_CHECKS_PER_SCREEN)
        self.auto_workers = []
        self.auto_generation = None; self.auto_started = 0; self.auto_heights = {}; self.auto_speeds = {}; self.show_failed = False
        self.check_cache = checks.CheckCache(); self.auto_checked_at = {}; self.recheck_generation = None
        self.manual_checks = set()
        self.manual_queue = queue.Queue()
        self.manual_status = {}
        self.action_serial = 0
        self.action_current = None
        self.action_inflight = set()
        self.action_queue = queue.Queue()
        self.replay_queue = queue.Queue()
        self.replay_generation = 0
        self.replay_sections = {}
        self.replay_order = []
        self.replay_pending = 0
        self.replay_errors = []
        self.replay_selection_touched = False
        self.helper = None
        self.relay = None
        self.resource_lock = threading.Lock()
        self.closed = threading.Event()
        self.gui_players = []
        self.resolver_owned = False
        self.cache = int(config.get('cache', 15)) if str(config.get('cache', 15)) in ('5','15','30') else 15
        self.terminal_video = bool(config.get('terminal_video', False))
        self.dual_stream = config.get('dual_stream', True) is not False
        self.ad_breaks = config.get('ad_breaks') if config.get('ad_breaks') in breaks.MODES else 'mute'
        self.stream_notes = {}
        self.record_dir = None
        self.ad_mute = ad_mute_enabled
        self.ai_ad_mute = ai_ad_mute_enabled
        self.ai_ad_observe = ai_ad_observe_enabled
        self.player = config.get('player', 'mpv') if config.get('player', 'mpv') in ('mpv', 'iina', 'celluloid') else 'mpv'
        self.rank_by_checks = False

    def put(self, y, x, s, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        try:
            self.scr.addnstr(y, x, str(s), max(0, w-x-1), attr)
        except curses.error:
            pass

    def read_key(self):
        try:
            raw = self.scr.get_wch() if hasattr(self.scr, 'get_wch') else self.scr.getch()
        except curses.error:
            return -1, None
        if isinstance(raw, str):
            return ord(raw), raw if raw.isprintable() else None
        return raw, chr(raw) if 32 <= raw <= 126 else None

    def visible_feeds(self):
        side = self.filter
        query = self.feed_search.casefold().strip()
        feeds = [f for f in self.feeds if id(f) not in self.hidden_feeds and (self.filter == 'all' or f.side == side)
                 and (self.provider_filter == 'all' or f.source == self.provider_filter)
                 and (not query or query in (f.source + ' ' + f.label).casefold())
                 and (self.show_failed or not self.failed_check(f))]
        if self.rank_by_checks:
            history = self.config.get('health') if isinstance(self.config.get('health'), dict) else {}
            def rank(feed):
                status = self.manual_status.get(id(feed), self.auto_checks.get(id(feed)))
                # Prefer a currently reachable segment, keep unchecked sources
                # available, and place explicit HTTP failures below them.
                reachability = 2 if status == 'OK' else 0 if isinstance(status, str) and status.startswith('HTTP ') else 1
                return (reachability, *grading.score(history.get(grading.source_key(feed), {})))
            feeds.sort(key=rank, reverse=True)
        else:
            # Working sources first, smooth before laggy, then by measured resolution; the rest keep directory order.
            def order(feed):
                status = self.manual_status.get(id(feed), self.auto_checks.get(id(feed)))
                group = 0 if status == 'OK' else 2 if self.failed_check(feed) else 1
                return (group, smoothness(self.auto_speeds.get(id(feed))), -self.auto_heights.get(id(feed), 0))
            feeds.sort(key=order)
        return feeds

    def failed_check(self, feed):
        status = self.manual_status.get(id(feed), self.auto_checks.get(id(feed)))
        return status == 'Failed' or isinstance(status, str) and status.startswith('HTTP ')

    def search_rows(self, width=None):
        """Build compact display rows, keeping headings out of keyboard selection."""
        hits = self.search_results
        compact = width is not None and width < 65
        if not hits: return []
        def player_text(hit):
            if compact:
                marker = '?' if not hit.item.verified else ''
                team = hit.item.team.short or hit.item.team.nickname or hit.item.team.display
                return f'{hit.title} · {hit.league.upper()}{marker} · {team}'
            roster = '' if hit.item.verified else '  ·  roster unverified'
            return f'{hit.title}  ·  {LEAGUES[hit.league][0]}  ·  {hit.item.team.display}{roster}'
        player_heading = ('PLAYERS  ·  ? roster unverified' if compact and
                          any(hit.kind == 'player' and not hit.item.verified for hit in hits) else 'PLAYERS')
        if hits[0].kind != 'team' or not self.search_plan.primary:
            rows = []
            for i, hit in enumerate(hits):
                if hit.kind == 'player':
                    if not any(row[2] == 'player-heading' for row in rows):
                        rows.append((None, player_heading, 'player-heading'))
                    rows.append((i, player_text(hit), ''))
                elif hit.kind == 'channel':
                    if not any(row[2] == 'channel-heading' for row in rows):
                        rows.append((None, 'TV CHANNELS', 'channel-heading'))
                    rows.append((i, f'   {hit.title}', ''))
                else:
                    rows.append((i, f'{hit.kind.upper()}  {hit.title}  ·  {LEAGUES[hit.league][0]}', ''))
            return rows
        rows = [(0, f'{LEAGUES[hits[0].league][0]}  {hits[0].title}', 'team')]
        live = [(i, hit) for i, hit in enumerate(hits) if hit.kind in ('live', 'side')]
        upcoming = [(i, hit) for i, hit in enumerate(hits) if hit.kind == 'upcoming']
        archived = [(i, hit) for i, hit in enumerate(hits) if hit.kind == 'bundle']
        other = [(i, hit) for i, hit in enumerate(hits) if i and hit.kind == 'team']
        matching_players = [(i, hit) for i, hit in enumerate(hits) if hit.kind == 'player']
        rows.append((None, '   LIVE', 'heading'))
        if not live: rows.append((None, '      —', 'empty'))
        for i, hit in live:
            if hit.kind == 'side':
                rows.append((i, f'       {hit.side.title()}  ·  {hit.count} source{"s" if hit.count != 1 else ""}', ''))
            else:
                title = f'{hit.item.away_nickname} at {hit.item.home_nickname}' if compact else hit.title
                rows.append((i, ('    ' if compact else '      ') + title, 'live'))
        if live and self.search_preview_pending:
            rows.append((None, '       Finding broadcasts…', 'empty'))
        rows.append((None, '   REPLAYS', 'heading'))
        if not archived: rows.append((None, '      —', 'empty'))
        for i, hit in archived:
            when = hit.item.date.strftime('%b %d') if hit.item.date else 'Date unknown'
            sources = f'  ·  {len(hit.item.sources)} sources' if len(hit.item.sources) > 1 else ''
            title = hit.title
            if compact:
                parts = re.split(r'\s+(?:vs\.?|versus)\s+', title, maxsplit=1, flags=re.I)
                if len(parts) == 2:
                    names = []
                    for part in parts:
                        matches = [(team, score) for team, score in self.search_index.resolve(part) if team.league == hit.league]
                        names.append((matches[0][0].nickname or matches[0][0].short or part)
                                     if matches and matches[0][1] >= .9 else part)
                    title = ' vs '.join(names)
            prefix = '   ' if compact else '      '
            rows.append((i, f'{prefix}{when}  {title}{sources if not compact else ""}', ''))
        if upcoming:
            rows.append((None, '   UPCOMING', 'heading'))
            for i, hit in upcoming:
                when = hit.item.start.astimezone().strftime('%b %d %I%p' if compact else '%a %b %d  %I:%M %p')
                title = f'{hit.item.away_nickname} at {hit.item.home_nickname}' if compact else hit.title
                rows.append((i, f'{"    " if compact else "      "}{when}  {title}', ''))
        if matching_players:
            rows.append((None, '   ' + player_heading, 'heading'))
            for i, hit in matching_players:
                rows.append((i, ('   ' if compact else '      ') + player_text(hit), ''))
        if other:
            rows.append((None, '   OTHER TEAMS', 'heading'))
            for i, hit in other:
                rows.append((i, f'      {LEAGUES[hit.league][0]}  {hit.title}', ''))
        channels = [(i, hit) for i, hit in enumerate(hits) if hit.kind == 'channel']
        if channels:
            rows.append((None, '   TV CHANNELS', 'heading'))
            for i, hit in channels: rows.append((i, f'      {hit.title}', ''))
        return rows

    def action_lines(self, actions):
        """This screen's actions wrapped to the terminal width, whole hints per line."""
        width = self.scr.getmaxyx()[1] - 2
        lines = []
        for hint in actions.split('   '):
            if lines and len(lines[-1]) + 3 + len(hint) <= width: lines[-1] += '   ' + hint
            else: lines.append(hint)
        return lines

    def footer(self, actions):
        """This screen's actions, then the keys that work on every screen."""
        h, w = self.scr.getmaxyx()
        lines = self.action_lines(actions)
        for y, line in enumerate(lines, h-3-len(lines)): self.put(y, 1, line)
        self.put(h-3, 1, '1 sports   2 replays   3 TV   / search   , settings   ? help   q quit' if w >= 72
                 else '1 2 3 sections   / search   , settings   q quit')

    def busy(self):
        """Whether background work the user is waiting on is still running."""
        return bool(self.loading_games or self.channels_loading or self.replay_pending or self.feed_pending
                    or self.action_inflight or self.search_inflight & self.search_needed)

    @staticmethod
    def spinner():
        # One frame per redraw tick; the UI redraws every 150 ms while it waits for keys.
        return SPINNER[int(time.monotonic() / .1) % len(SPINNER)]

    def render(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        self.put(0, 1, 'SPORTS CLI', self.accent_color)
        heading = {'home':'Home', 'settings':'Settings', 'search':'Search', 'leagues':'Live sports: choose a sport' if self.section == 'sports' else 'Replays: choose a sport', 'games':'Games', 'replays':'Replays', 'replay_sources':'Replay sites', 'channels':'Live TV', 'feeds':'Sources', 'help':'Help'}
        title = heading.get(self.mode, '')
        self.put(1, 1, title, curses.A_BOLD)
        if self.busy(): self.put(1, len(title) + 2, self.spinner(), self.accent_color)
        if h < 12 or w < 36:
            self.put(3, 1, 'Please enlarge your terminal to at least 36 x 12.')
            self.put(h-2, 1, 'Ctrl-X quit' if self.mode == 'search' else 'q quit')
            self.scr.refresh(); return
        if self.mode != 'search': self.search_draw_signature = None
        if self.mode == 'search':
            self.put(2, 1, 'Search: ' + (self.search_query or '') + '_', curses.A_BOLD)
            lines = self.search_rows(w); capacity = max(1, h-7)
            signature = (h, w, self.search_query, self.search_position, self.message, tuple(lines))
            if signature != self.search_draw_signature and hasattr(self.scr, 'redrawwin'):
                try: self.scr.redrawwin()
                except curses.error: pass
            self.search_draw_signature = signature
            cursor = next((i for i, (index, _, _) in enumerate(lines) if index == self.search_position), 0)
            start = max(0, cursor-capacity+1)
            for y, (index, text, style) in enumerate(lines[start:start+capacity], 3):
                selected = index is not None and index == self.search_position
                attr = (curses.A_REVERSE if selected else self.live_color if style == 'live' else
                        curses.A_BOLD if style == 'team' else curses.A_DIM if style in ('heading', 'player-heading', 'channel-heading', 'empty') else 0)
                self.put(y, 2, f'{">" if selected else " "} {text}', attr)
            if not self.search_results:
                waiting = bool(self.search_inflight & self.search_needed or self.player_due or self.player_key in self.player_inflight)
                text = 'Try: stephen curry  |  lakers today  |  mavs vs rockets' if not self.search_query else self.spinner() + ' Looking for matches' if waiting else 'Keep typing a player, team or league.'
                self.put(4, 2, text)
            self.put(h-4, 1, 'Type to search   Up/Down select   Enter open' if w >= 55 else 'Type   ↑↓ select   Enter open')
            self.put(h-3, 1, 'Esc back   Ctrl-U clear   Ctrl-X quit')
        elif self.mode == 'help':
            rows = max(1, h-6)
            for y, line in enumerate(HELP_LINES[self.help_scroll:self.help_scroll+rows], 3):
                self.put(y, 2, line)
            self.put(h-3, 1, 'j/k scroll   Esc back   q quit')
        elif self.mode == 'home':
            for y, (_, key, label, detail) in enumerate(HOME_ITEMS, 3):
                selected = y-3 == self.home_index
                line = f'{">" if selected else " "} {key}  {label:<12}  {detail}'
                self.put(y, 2, line, curses.A_REVERSE if selected else 0)
            self.footer('Enter open')
        elif self.mode == 'settings':
            for y, (label, value, _) in enumerate(self.settings_rows(), 3):
                selected = y-3 == self.settings_index
                self.put(y, 2, f'{">" if selected else " "} {label:<32} {value}', curses.A_REVERSE if selected else 0)
            self.footer('Enter change   Esc back')
        elif self.mode == 'leagues':
            keys = list(LEAGUES)
            rows = max(1, h-7)
            start = max(0, self.league_index-rows+1)
            for y, key in enumerate(keys[start:start+rows], 3):
                selected = start+y-3 == self.league_index
                self.put(y, 2, f'{">" if selected else " "} {LEAGUES[key][0]}', curses.A_REVERSE if selected else 0)
            self.footer('Enter choose   Esc back')
        elif self.mode == 'games':
            self.put(2, 1, f'{LEAGUES[self.league][0]}  |  {self.day:%a %b %d} (ET date)  |  search: {self.search or "all"}')
            actions = 'Enter sources   s sport   [ ] date   f filter   r refresh   Esc back'
            rows = max(1, h-6-len(self.action_lines(actions)))
            start = max(0, self.gi-rows+1)
            for y, game in enumerate(self.games[start:start+rows], 3):
                selected = start+y-3 == self.gi
                when = game.start.astimezone().strftime('%a %b %d  %I:%M %p %Z')
                status = {'live':'LIVE', 'upcoming':'UPCOMING', 'redzone-live':'LIVE', 'redzone':'CHANNEL'}.get(game.state, '?')
                if game.id == REDZONE: when = f'{"Live TV channel":<24}'
                attr = curses.A_REVERSE if selected else self.live_color if game.state in ('live', 'redzone-live') else 0
                self.put(y, 2, f'{">" if selected else " "} {status}  {when}  {game.title}', attr)
            if not self.games:
                self.put(4, 2, self.spinner() + ' Loading schedule' if self.loading_games else 'No upcoming or live games. Change date or search.')
            self.footer(actions)
        elif self.mode == 'replays':
            self.put(2, 1, f'{LEAGUES[self.league][0]}  |  recent replays  |  search: {self.replay_search or "all"}')
            actions = 'Enter sources   s sport   f search titles   r refresh   Esc back'
            rows = max(1, h-6-len(self.action_lines(actions)))
            start = max(0, self.ri-rows+1)
            for y, item in enumerate(self.replays[start:start+rows], 3):
                selected = start+y-3 == self.ri
                self.put(y, 2, f'{">" if selected else " "} [{item.source}] {item.title}', curses.A_REVERSE if selected else 0)
            if not self.replays:
                self.put(4, 2, self.spinner() + ' Searching replay catalogs' if self.replay_pending else 'No replays found. Try another search or league.')
            self.footer(actions)
        elif self.mode == 'channels':
            shown = self.visible_channels()
            self.put(2, 1, f'{len(shown)} channels  |  filter: {self.channel_filter or "all"}')
            actions = 'Enter sources   f filter   r refresh   Esc back'
            rows = max(1, h-6-len(self.action_lines(actions)))
            start = max(0, self.ci-rows+1)
            for y, channel in enumerate(shown[start:start+rows], 3):
                selected = start+y-3 == self.ci
                count = len(channel.sources)
                self.put(y, 2, f'{">" if selected else " "} {channel.name}  ({count} source{"s" if count != 1 else ""})',
                         curses.A_REVERSE if selected else curses.A_BOLD if channel.featured else 0)
            if not shown:
                self.put(4, 2, self.spinner() + ' Loading channels' if self.channels_loading else 'No channels match. Press f to change the filter.')
            self.footer(actions)
        elif self.mode == 'replay_sources':
            self.put(2, 1, self.current_bundle.title)
            actions = 'Enter sources   Esc back'
            rows = max(1, h-6-len(self.action_lines(actions)))
            start = max(0, self.bundle_index-rows+1)
            for y, item in enumerate(self.current_bundle.sources[start:start+rows], 3):
                selected = start+y-3 == self.bundle_index
                self.put(y, 2, f'{">" if selected else " "} {item.source}', curses.A_REVERSE if selected else 0)
            self.footer(actions)
        else:
            if self.feed_context == 'replay':
                self.put(2, 1, self.current_replay.title)
                self.put(3, 1, f'Replay sources from {self.current_replay.source}')
            elif self.feed_context == 'tv':
                self.put(2, 1, self.current_channel.name)
                self.put(3, 1, 'Live TV')
            else:
                game = self.games[self.gi]
                self.put(2, 1, f'{game.title}  |  {game.start.astimezone().strftime("%a %b %d %I:%M %p %Z")}')
                self.put(3, 1, 'TV: ' + (', '.join(game.broadcasts) or 'not listed'))
            fs = self.visible_feeds()
            ad_mode = 'AI NBA' if self.ai_ad_mute else 'AI preview' if self.ai_ad_observe else 'marked replays' if self.ad_mute else 'off'
            context = [self.player.upper()]
            if self.filter != 'all': context.append(f'side: {self.filter}')
            if self.provider_filter != 'all': context.append(f'source: {self.provider_filter}')
            if self.feed_search: context.append(f'search: {self.feed_search}')
            if self.cache != 15: context.append(f'cache: {self.cache}s')
            if ad_mode != 'off': context.append(f'ad mute: {ad_mode}')
            if self.ai_ad_mute and self.ad_breaks != 'mute': context.append('breaks: ' + breaks.LABELS[self.ad_breaks])
            if self.rank_by_checks: context.append('ranked')
            if not self.dual_stream and self.feed_context in ('live', 'tv'): context.append('backup: off')
            self.put(4, 1, '  ·  '.join(context), curses.A_DIM)
            self.fi = min(self.fi, max(0, len(fs)-1))
            compact = w < 65
            name_width = max(10, min(30, w-25)) if compact else max(14, min(50, w-40))
            columns = (f'{"SOURCE":<{name_width}}  {"SIDE":5} CHECK' if compact else
                       f'{"SOURCE":<{name_width}}  {"SIDE":5} {"QUALITY":7} CHECK')
            self.put(5, 4, columns, curses.A_DIM)
            # Only live games have home and away broadcasts to choose between.
            failed = sum(1 for f in self.feeds if id(f) not in self.hidden_feeds and self.failed_check(f))
            actions = ('Enter play   x test   i inspect   u paste URL   ' + ('a side   ' if self.feed_context == 'live' else '')
                       + 'o source   f filter   g rank   ' + ('s sport   ' if self.feed_context != 'tv' else '')
                       + (f'v hide {failed} failed   ' if self.show_failed and failed else f'v show {failed} failed   ' if failed else '')
                       + 'r refresh   Esc back')
            bottom = h-3-len(self.action_lines(actions))
            rows = max(1, (bottom-6)//2)
            start = max(0, self.fi-rows+1)
            for index, feed in enumerate(fs[start:start+rows]):
                y = 6 + index*2
                selected = start+index == self.fi
                history = self.config.get('health') if isinstance(self.config.get('health'), dict) else {}
                opening = self.action_current and self.action_current[1] == self.feed_generation and self.action_current[2] is feed
                status = (('Opening' if self.action_current[3] == 'play' else 'Inspecting') if opening else
                          'testing' if (self.feed_generation, id(feed)) in self.manual_checks else
                          self.manual_status.get(id(feed), self.auto_checks.get(id(feed))))
                if id(feed) in self.auto_checked_at and status == self.auto_checks.get(id(feed)) and id(feed) not in self.manual_status:
                    status = f'{status} {check_age(time.time() - self.auto_checked_at[id(feed)])}'
                record = dict(history.get(grading.source_key(feed), {}))
                if id(feed) in self.auto_heights: record['height'] = self.auto_heights[id(feed)]
                if id(feed) in self.auto_speeds: record['speed'] = self.auto_speeds[id(feed)]
                view = describe_feed(feed, record, status)
                short_name = view.name if len(view.name) <= name_width else view.name[:name_width-1] + '…'
                attr = curses.A_REVERSE if selected else 0
                row = (f'{short_name:<{name_width}}  {view.side:5} {view.check}' if compact else
                       f'{short_name:<{name_width}}  {view.side:5} {view.quality:7} {view.check}')
                self.put(y, 2, f'{">" if selected else " "} {row}', attr)
                if y+1 < bottom:
                    fallback = {'ppv':'Uses local resolver', 'web':'Browser-only; no media URL'}.get(feed.kind, 'Direct stream')
                    detail = view.detail or fallback
                    if feed.kind == 'web' and view.detail: detail = fallback + ' | ' + view.detail
                    if short_name != view.name: detail = view.name + ' | ' + detail
                    if compact and view.quality != '-': detail = view.quality + ' | ' + detail
                    self.put(y+1, 4, detail, attr if selected else curses.A_DIM)
            if not fs:
                self.put(6, 2, self.spinner() + ' Looking for sources' if self.feed_pending else
                         f'All {failed} sources failed their checks. Press v to show them or r to check again.' if failed and not self.show_failed else
                         'No sources available. Refresh near game time or change the filter.')
            self.footer(actions)
        self.put(h-1, 1, self.message, curses.A_DIM)
        self.scr.refresh()

    def refresh_games(self):
        key = (self.league, self.day)
        selected_id = None
        if self.schedule_selected_key in (None, key):
            selected_id = self.games[self.gi].id if self.games and self.gi < len(self.games) else None
            if selected_id is None and self.schedule_selected_key == key:
                selected_id = self.schedule_selected_id
        self.schedule_selected_key, self.schedule_selected_id = key, selected_id
        with self.schedule_result_lock:
            self.schedule_generation += 1
            generation = self.schedule_generation
            self.schedule_result = None
        self.all_games = []; self.games = []; self.gi = 0; self.loading_games = True
        self.message = 'Loading games for this date...'; self.render()
        while True:
            try: self.schedule_jobs.get_nowait()
            except queue.Empty: break
        self.schedule_jobs.put_nowait((generation, self.day, self.league))
        if not self.schedule_workers_started:
            self.schedule_workers_started = True
            for index in range(2):
                worker = threading.Thread(target=self._schedule_worker, name=f'schedule-{index}', daemon=True)
                self.schedule_workers.append(worker)
                worker.start()

    def _schedule_worker(self):
        while True:
            job = self.schedule_jobs.get()
            if job is None:
                # One sentinel wakes both workers even though the queue holds
                # at most one pending schedule request.
                try: self.schedule_jobs.put_nowait(None)
                except queue.Full: pass
                return
            if self.schedule_stop.is_set(): return
            generation, day, league = job
            try:
                games, error = get_games(day, league), None
            except Exception as exc:
                games, error = None, exc
            with self.schedule_result_lock:
                if not self.schedule_stop.is_set() and generation == self.schedule_generation:
                    self.schedule_result = (generation, day, league, games, error)

    def stop_schedule_workers(self):
        self.schedule_stop.set()
        with self.schedule_result_lock:
            self.schedule_generation += 1
            self.schedule_result = None
        while True:
            try: self.schedule_jobs.get_nowait()
            except queue.Empty: break
        try: self.schedule_jobs.put_nowait(None)
        except queue.Full: pass

    def poll_games(self):
        if self.mode != 'games':
            return
        with self.schedule_result_lock:
            result = self.schedule_result
            self.schedule_result = None
        if result is None:
            return
        generation, day, league, games, error = result
        if generation != self.schedule_generation or day != self.day or league != self.league:
            return
        self.loading_games = False
        if error is not None:
            self.all_games = []; self.games = []; self.gi = 0
            self.message = f'Schedule unavailable: {type(error).__name__}. Press r to retry.'
            return
        self.all_games = games
        self.apply_search()
        if self.schedule_selected_id:
            self.gi = next((i for i, game in enumerate(self.games) if game.id == self.schedule_selected_id), 0)
        self.message = f'{len(self.games)} games shown. Select one.'

    def apply_search(self):
        query = self.search.lower()
        self.games = [g for g in self.all_games if query in (g.title + ' ' + g.home + ' ' + g.away).lower()]
        redzone = self.redzone_row()
        if redzone and query in redzone.title.lower(): self.games.insert(0, redzone)
        self.gi = min(self.gi, max(0, len(self.games)-1))

    def redzone_row(self):
        """NFL RedZone, the live TV channel, leads an NFL Sunday schedule."""
        if self.league != 'nfl' or self.day.weekday() != 6 or not self.all_games: return None
        # Load channels ahead so Enter opens RedZone's sources at once.
        if not self.channels and not self.channels_loading: self.refresh_channels()
        live = any(game.state == 'live' for game in self.all_games)
        return Game(REDZONE, 'NFL RedZone: every touchdown from every Sunday game', min(g.start for g in self.all_games),
                    '', '', '', '', ['NFL RedZone'], 'nfl', 'football', state='redzone-live' if live else 'redzone')

    def open_redzone(self):
        channel = next((c for c in self.channels if c.name == 'NFL RedZone'), None)
        if channel:
            self.current_channel = channel; self.open_feeds('tv', 'games')
        elif self.channels_loading:
            self.redzone_pending = True; self.message = 'Loading NFL RedZone sources...'
        else:
            self.message = 'NFL RedZone has no sources right now. Press r to refresh.'

    def update_global_search(self):
        # Search covers live TV too; load the channel list once, in the background.
        if not self.channels and not self.channels_loading: self.refresh_channels()
        if self.search_index is None:
            self.search_index = smart_search.default_index()
        if self.player_directory is None:
            self.player_directory = players.Directory(self.search_index, LEAGUES)
        today = default_day()
        query = smart_search.parse_query(self.search_query, today)
        self.search_plan = smart_search.make_plan(self.search_index, query)
        query = self.search_plan.query
        fragment = query.fragments[0] if query.fragments else ''
        player_key = (fragment, query.league) if len(fragment) >= 3 else None
        if player_key != self.player_key:
            self.player_partial_key, self.player_partial = None, []
        self.player_key = player_key
        cached_player = self.player_cache.get(self.player_key)
        self.player_due = (time.monotonic() + .35 if self.player_key and
                           (not cached_player or cached_player[0] <= time.monotonic()) else None)
        work, sources = [], {}
        day = query.date or today
        if query.intent != 'replay' and day >= today:
            work.extend(('games', league, day) for league in self.search_plan.leagues)
        if query.date is None and query.intent != 'replay' and query.fragments:
            choices = self.search_index.resolve(query.fragments[0])
            for league in self.search_plan.leagues:
                team = next((team for team, _ in choices if team.league == league), None)
                if team: work.append(('team-games', league, team.id, today))
        if query.intent != 'live':
            for league in self.search_plan.leagues:
                for spec in replays.catalogs(league):
                    key = ('replays', spec, '')
                    sources.setdefault(key, []).append(league)
                    if key not in work: work.append(key)
            # A specific matchup/date can require searching beyond the recent catalog.
            exact_team = bool(query.fragments and any(score >= .99 for _, score in self.search_index.resolve(query.fragments[0])))
            if self.search_plan.primary and (query.date or len(query.fragments) > 1 or exact_team):
                term = smart_search.archive_search_term(self.search_plan.primary)
                for league in self.search_plan.leagues:
                    for spec in replays.catalogs(league):
                        host = urllib.parse.urlsplit(spec[1]).hostname
                        if host not in ('basketball-video.com','nfl-video.com','fullmatchshows.com','watchreplay.net'):
                            continue
                        key = ('replays', spec, term)
                        sources.setdefault(key, []).append(league)
                        if key not in work: work.append(key)
                        if host == 'basketball-video.com' and league == 'nba':
                            opponent = ''
                            if len(query.fragments) > 1:
                                choices = self.search_index.resolve(query.fragments[1])
                                other = next((team for team, _ in choices if team.league == league), None)
                                if other: opponent = smart_search.archive_search_term(other)
                            deep = ('replay-pages', spec, term, opponent, query.date)
                            sources.setdefault(deep, []).append(league)
                            if deep not in work: work.append(deep)
        self.search_sources = sources
        self.search_work = work
        self.search_needed = set(work)
        self.search_due = time.monotonic() + .2 if work else None
        self.search_position = 0; self.search_touched = False
        self.rebuild_global_results()

    def start_search_preview(self, game):
        key = (game.league, game.id)
        if key == self.search_preview_key and time.monotonic()-self.search_preview_started < 60: return
        self.search_preview_generation += 1
        generation = self.search_preview_generation
        self.search_preview_key = key
        self.search_preview_started = time.monotonic()
        self.search_preview_sections = {}
        self.search_preview_errors = []
        self.search_preview_adopt_generation = None
        tasks = feed_tasks(game, dict(self.config))
        self.search_preview_order = [name for name, _ in tasks]
        self.search_preview_pending = len(tasks)
        for name, operation in tasks:
            def worker(name=name, operation=operation):
                try: self.search_preview_queue.put((generation, name, operation(), None))
                except Exception as exc: self.search_preview_queue.put((generation, name, [], type(exc).__name__))
            threading.Thread(target=worker, daemon=True).start()

    def poll_search_preview(self):
        changed = False
        while True:
            try: generation, name, feeds, error = self.search_preview_queue.get_nowait()
            except queue.Empty: break
            if generation != self.search_preview_generation: continue
            self.search_preview_sections[name] = feeds
            self.search_preview_pending = max(0, self.search_preview_pending-1)
            if error: self.search_preview_errors.append(f'{name}: {error}')
            if (self.search_preview_adopt_generation == self.feed_generation and self.feed_context == 'live'):
                self.feed_queue.put((self.feed_generation, name, feeds, error))
            changed = True
        if changed and self.mode == 'search': self.rebuild_global_results()

    def grouped_search_results(self, hits):
        primary = self.search_plan.primary
        if not primary or not self.search_plan.query.fragments:
            if self.mode == 'search' and self.search_preview_key is not None:
                self.search_preview_generation += 1
                self.search_preview_key = None; self.search_preview_sections = {}; self.search_preview_pending = 0
            return hits
        team = smart_search.Hit('team', primary.league, primary.display, primary, 1.0)
        games = [hit for hit in hits if hit.league == primary.league and hit.kind in ('live', 'upcoming')]
        live = [hit for hit in games if hit.kind == 'live']
        upcoming = sorted((hit for hit in games if hit.kind == 'upcoming'), key=lambda hit: hit.item.start)
        exact = any(item.league == primary.league and item.id == primary.id and score >= .99
                    for item, score in self.search_index.resolve(self.search_plan.query.fragments[0]))
        if self.mode == 'search':
            if live and exact:
                self.start_search_preview(live[0].item)
            elif self.search_preview_key is not None:
                self.search_preview_generation += 1
                self.search_preview_key = None; self.search_preview_sections = {}; self.search_preview_pending = 0
        result = [team]
        for hit in live:
            result.append(hit)
            if self.search_preview_key == (hit.league, hit.item.id):
                preview = [feed for name in self.search_preview_order for feed in self.search_preview_sections.get(name, [])]
                for side in ('home', 'away'):
                    count = sum(feed.side == side and feed.kind != 'web' for feed in preview)
                    if count: result.append(SearchSideHit(hit.item, side, count))
        bundles = {}
        for hit in hits:
            if hit.kind != 'replay' or hit.league != primary.league: continue
            identity, title = replay_identity(self.search_index, hit)
            if identity not in bundles:
                bundles[identity] = ReplayBundle(hit.league, title, hit.date, [], identity)
            if all(item.url != hit.item.url for item in bundles[identity].sources):
                bundles[identity].sources.append(hit.item)
        ordered = sorted(bundles.values(), key=lambda bundle: bundle.date or dt.date.min, reverse=True)
        limit = 10
        result.extend(ReplayBundleHit(bundle) for bundle in ordered[:limit])
        result.extend(upcoming[:2] if len(self.search_plan.query.fragments) == 1 else upcoming)
        result.extend(hit for hit in hits if hit.kind == 'team' and
                      (hit.item.league, hit.item.id) != (primary.league, primary.id))
        return result

    def channel_hits(self, limit=6):
        """Live TV channels whose names match the query, main news and networks first."""
        query = self.search_query.strip()
        if len(query) < 2: return []
        matches = [channel for channel in self.channels if tv.matches(channel, query)]
        matches.sort(key=lambda channel: not channel.featured)
        return [smart_search.Hit('channel', '', channel.name, channel) for channel in matches[:limit]]

    def rebuild_global_results(self):
        selected = (self.search_results[self.search_position].key if self.search_touched
                    and self.search_position < len(self.search_results) else None)
        games, archived = [], []
        for key in self.search_work:
            cached = self.search_cache.get(key)
            if not cached or cached[2]: continue
            if key[0] in ('games', 'team-games'): games.extend(cached[1])
            else:
                archived.extend((league, item) for league in self.search_sources.get(key, ()) for item in cached[1])
        ranked = smart_search.rank(self.search_index, self.search_plan, games, archived, default_day())
        grouped = self.grouped_search_results(ranked)
        cached_players = self.player_cache.get(self.player_key)
        matches = (self.player_partial if self.player_key and self.player_partial_key == self.player_key else
                   cached_players[1] if cached_players and not cached_players[2] else [])
        player_hits = [smart_search.Hit('player', player.team.league, player.name, player, 1.0)
                       for player in matches]
        team_matches = self.search_index.resolve(self.search_plan.query.fragments[0]) if self.search_plan.query.fragments else ()
        strong_team = bool(team_matches and team_matches[0][1] >= .9)
        if player_hits and not strong_team:
            # A fuzzy team match such as León for "lebron" must not bury a
            # directly matching roster player.
            self.search_results = player_hits + [hit for hit in ranked if hit.kind == 'team'][:3]
        else:
            self.search_results = grouped + player_hits
        channels = self.channel_hits()
        named = channels and tv.key(channels[0].title).startswith(tv.key(self.search_query))
        # A query that names a channel (cnbc, fox news) leads with channels unless it clearly names a team.
        self.search_results = channels + self.search_results if named and not strong_team else self.search_results + channels
        keys = [item.key for item in self.search_results]
        if selected in keys:
            self.search_position = keys.index(selected)
        elif not self.search_touched:
            self.search_position = 0
        else:
            self.search_position = min(self.search_position, max(0, len(keys)-1))
        if self.mode != 'search': return
        pending = sum(1 for key in self.search_needed
                      if key in self.search_inflight or key not in self.search_cache)
        count = sum(item.kind not in ('team', 'side', 'player') for item in self.search_results)
        player_count = sum(item.kind == 'player' for item in self.search_results)
        player_pending = bool(self.player_key and (self.player_due is not None or self.player_key in self.player_inflight))
        unavailable = sum(1 for key in self.search_needed if key in self.search_cache and self.search_cache[key][2])
        if not self.search_query:
            self.message = 'Start typing a player, team, matchup, or league.'
        elif pending or player_pending:
            self.message = (f'{player_count} players · checking...' if player_count and not count else
                            f'{count} games and replays found | searching...')
        elif count:
            self.message = f'{count} games and replays found. Choose one or keep typing.'
            if unavailable: self.message += f' {unavailable} sources unavailable; Ctrl-R retries.'
        elif player_count:
            self.message = f'{player_count} players · Enter opens team.'
        elif cached_players and cached_players[2] and not strong_team:
            self.message = 'Player search unavailable. Ctrl-R retries.'
        elif self.search_plan.leagues:
            self.message = (f'No matches; {unavailable} sources unavailable. Ctrl-R retries.' if unavailable else
                            'No matching games or replays found. Try another team or date.')
        else:
            self.message = 'Keep typing a player, team, matchup, or league.'

    def start_player_search(self):
        if self.mode != 'search' or self.player_due is None or time.monotonic() < self.player_due: return
        key = self.player_key
        self.player_due = None
        if not key or key in self.player_inflight: return
        while True:
            try:
                stale = self.player_jobs.get_nowait()
                self.player_inflight.discard(stale)
            except queue.Empty: break
        self.player_inflight.add(key)
        self.player_jobs.put_nowait(key)
        if not self.player_worker_started:
            self.player_worker_started = True
            threading.Thread(target=self.player_search_worker, name='player-search', daemon=True).start()

    def player_search_worker(self):
        while True:
            key = self.player_jobs.get()
            if key is None: return
            canceled = lambda: self.closed.is_set() or self.mode != 'search' or key != self.player_key
            try:
                def publish(value):
                    if not canceled(): self.player_queue.put((key, value, None, False, True))
                value = self.player_directory.search(key[0], key[1], request, canceled, publish)
                self.player_queue.put((key, value, None, False, False))
            except concurrent.futures.CancelledError:
                self.player_queue.put((key, [], None, True, False))
            except Exception as exc:
                self.player_queue.put((key, [], type(exc).__name__, False, False))

    def poll_player_search(self):
        changed = False
        while True:
            try: key, value, error, canceled, partial = self.player_queue.get_nowait()
            except queue.Empty: break
            if partial:
                if key == self.player_key and self.mode == 'search':
                    self.player_partial_key, self.player_partial = key, value
                    changed = True
                continue
            self.player_inflight.discard(key)
            if key == self.player_partial_key:
                self.player_partial_key, self.player_partial = None, []
            if not canceled:
                ttl = 20 if error else 30 if any(not player.verified for player in value) else 300
                self.player_cache[key] = (time.monotonic() + ttl, value, error)
                while len(self.player_cache) > 96: self.player_cache.pop(next(iter(self.player_cache)))
            elif key == self.player_key and not self.closed.is_set():
                self.player_due = time.monotonic()
            if key == self.player_key: changed = True
        if changed: self.rebuild_global_results()
        return changed

    def start_global_search(self):
        if self.mode != 'search': return
        if self.search_due is None or time.monotonic() < self.search_due: return
        self.search_due = None
        if not self.search_workers_started:
            self.search_workers_started = True
            for _ in range(3):
                threading.Thread(target=self.global_search_worker, daemon=True).start()
        now = time.monotonic()
        for key in self.search_work:
            cached = self.search_cache.get(key)
            if key in self.search_inflight or (cached and cached[0] > now): continue
            self.search_inflight.add(key)
            self.search_jobs.put(key)
        self.rebuild_global_results()

    def global_search_worker(self):
        while True:
            key = self.search_jobs.get()
            if key is None: return
            if key not in self.search_needed:
                self.search_queue.put((key, None, None, True)); continue
            try:
                if key[0] == 'games': value = get_games(key[2], key[1])
                elif key[0] == 'team-games': value = get_team_games(key[2], key[1], key[3])
                elif key[0] == 'replay-pages':
                    value = search_replay_pages(key[1], key[2], key[3], key[4], lambda:key not in self.search_needed)
                else: value = replays.listing(key[1], key[2])
                self.search_queue.put((key, value, None, False))
            except concurrent.futures.CancelledError:
                self.search_queue.put((key, None, None, True))
            except Exception as exc:
                self.search_queue.put((key, [], type(exc).__name__, False))

    def poll_global_search(self):
        changed = False
        while True:
            try: key, value, error, canceled = self.search_queue.get_nowait()
            except queue.Empty: break
            self.search_inflight.discard(key)
            if not canceled:
                ttl = 20 if error else 60 if key[0] in ('games', 'team-games') else 300
                self.search_cache[key] = (time.monotonic()+ttl, value, error)
                while len(self.search_cache) > 96:
                    self.search_cache.pop(next(iter(self.search_cache)))
            elif key in self.search_needed:
                self.search_due = time.monotonic()
            changed = True
        if changed: self.rebuild_global_results()
        return changed

    def open_global_result(self):
        if not self.search_results: return
        hit = self.search_results[self.search_position]
        if hit.kind == 'player':
            query = self.search_plan.query
            fragments = list(query.fragments)
            fragments[0] = hit.item.team.display
            text = ' vs '.join(fragments)
            if query.league: text = query.league + ' ' + text
            if query.date: text += ' ' + query.date.isoformat()
            if query.intent == 'replay': text += ' replay'
            elif query.intent == 'live': text += ' live'
            self.search_query = text
            self.update_global_search()
            return
        if hit.kind == 'team':
            primary = self.search_plan.primary
            if primary and (hit.item.league, hit.item.id) == (primary.league, primary.id):
                target = next((i for i, item in enumerate(self.search_results)
                               if item.kind in ('live', 'upcoming', 'bundle')), None)
                if target is not None:
                    self.search_position = target; self.search_touched = True
                    self.message = 'Choose a game or replay.'
                    return
            self.search_query = hit.item.display
            self.update_global_search()
            return
        if hit.kind == 'channel':
            self.current_channel = hit.item; self.search_return = None
            self.open_feeds('tv', 'search')
            return
        if hit.kind == 'bundle':
            self.current_bundle = hit.item; self.bundle_index = 0; self.mode = 'replay_sources'
            self.message = 'Choose a replay source.'
            return
        self.league = hit.league
        self.search_return = None
        self.feed_return = 'search'
        self.filter = hit.side if hit.kind == 'side' else 'all'
        self.provider_filter = 'all'; self.feed_search = ''
        if hit.kind == 'replay':
            self.current_replay = hit.item; self.feed_context = 'replay'
        else:
            self.games = [hit.item]; self.all_games = [hit.item]; self.gi = 0
            self.day = getattr(hit, 'date', None) or self.day; self.feed_context = 'live'
        self.mode = 'feeds'
        if self.feed_context == 'live' and self.search_preview_key == (hit.league, hit.item.id):
            self.adopt_search_preview()
        else:
            self.refresh_feeds()

    def adopt_search_preview(self):
        """Reuse listings already fetched for the selected live game."""
        self.feed_generation += 1
        generation = self.feed_generation
        self.feed_sections = dict(self.search_preview_sections)
        self.feed_order = list(self.search_preview_order)
        self.feeds = [feed for name in self.feed_order for feed in self.feed_sections.get(name, [])]
        self.errors = list(self.search_preview_errors)
        self.feed_pending = self.search_preview_pending
        self.fi = 0; self.feed_selection_touched = False
        self.hidden_feeds.clear(); self.auto_checks.clear(); self.manual_status.clear(); self.auto_heights.clear(); self.auto_speeds.clear()
        self.auto_checked_at.clear()
        self.search_preview_adopt_generation = generation
        for feeds in self.feed_sections.values(): self.start_auto_checks(generation, feeds)
        self.message = f'{len(self.feeds)} choices found'
        if self.feed_pending: self.message += f' | checking {self.feed_pending} sources...'
        elif self.errors: self.message += ' | ' + ', '.join(self.errors)

    def refresh_replays(self):
        self.replay_generation += 1
        generation = self.replay_generation
        specs = replays.catalogs(self.league)
        self.replays = []; self.ri = 0; self.replay_sections = {}; self.replay_errors = []
        self.replay_order = [spec[0] for spec in specs]
        self.replay_pending = len(specs); self.replay_selection_touched = False
        self.message = 'Searching replay catalogs...' if specs else 'No replay catalog configured for this league.'
        for spec in specs:
            def worker(spec=spec, query=self.replay_search):
                try: items = replays.listing(spec, query) if query else replays.recent(spec)
                except Exception as exc: self.replay_queue.put((generation, spec[0], [], type(exc).__name__)); return
                if query:
                    # Archive site searches return only recent games; older ones sit on later pages.
                    try: items += search_replay_pages(spec, query, canceled=lambda: generation != self.replay_generation)
                    except Exception: pass
                    unique = {item.url: item for item in items}
                    items = sorted(unique.values(), key=lambda item: smart_search.replay_date(item.title) or dt.date.min, reverse=True)
                self.replay_queue.put((generation, spec[0], items, None))
            threading.Thread(target=worker, daemon=True).start()

    def poll_replays(self):
        active = (self.mode == 'replays' or (self.mode == 'feeds' and self.feed_context == 'replay')
                  or (self.mode == 'leagues' and self.section == 'replays')
                  or (self.mode == 'search' and (self.search_return == 'replays'
                      or (self.search_return == 'feeds' and self.feed_context == 'replay')))
                  or (self.mode == 'help' and (self.help_return == 'replays'
                      or (self.help_return == 'feeds' and self.feed_context == 'replay')
                      or (self.help_return == 'leagues' and self.section == 'replays'))))
        changed = False
        while True:
            try: generation, name, items, error = self.replay_queue.get_nowait()
            except queue.Empty: break
            if generation != self.replay_generation or not active: continue
            selected = self.replays[self.ri] if self.replay_selection_touched and self.ri < len(self.replays) else None
            self.replay_sections[name] = items
            self.replay_pending = max(0, self.replay_pending-1)
            # Newest first across archives; titles without a date keep their archive's order at the end.
            self.replays = sorted((item for source in self.replay_order for item in self.replay_sections.get(source, [])),
                                  key=lambda item: smart_search.replay_date(item.title) or dt.date.min, reverse=True)
            self.ri = self.replays.index(selected) if selected in self.replays else min(self.ri, max(0, len(self.replays)-1))
            if error: self.replay_errors.append(f'{name}: {error}')
            changed = True
        if changed and self.mode == 'replays':
            self.message = f'{len(self.replays)} replays found'
            if self.replay_pending: self.message += f' | checking {self.replay_pending} catalogs...'
            elif self.replay_errors: self.message += ' | ' + ', '.join(self.replay_errors)
        return changed

    def visible_channels(self):
        return [channel for channel in self.channels if tv.matches(channel, self.channel_filter)]

    def refresh_channels(self):
        if self.channels_loading: return
        self.channels_loading = True; self.message = 'Loading live TV channels...'
        def worker():
            try: result = tv.directory()
            except Exception as exc: result = ([], [type(exc).__name__])
            self.channel_queue.put(result)
        threading.Thread(target=worker, daemon=True).start()

    def channel_summary(self):
        if not self.channel_filter: return f'{len(self.channels)} channels.'
        return f'{len(self.visible_channels())} of {len(self.channels)} channels match "{self.channel_filter}". f changes the filter.'

    def poll_channels(self):
        try: channels, errors = self.channel_queue.get_nowait()
        except queue.Empty: return False
        self.channels_loading = False
        if channels or not self.channels:
            self.channels, self.channel_errors = channels, errors; self.channels_loaded_at = time.monotonic()
        self.ci = min(self.ci, max(0, len(self.visible_channels())-1))
        if self.redzone_pending:
            self.redzone_pending = False
            if self.mode == 'games' and self.games and self.games[self.gi].id == REDZONE: self.open_redzone()
        elif self.mode == 'channels':
            self.message = self.channel_summary() + (' | ' + ', '.join(errors) if errors else '')
        elif self.mode == 'search' and self.search_plan is not None:
            self.rebuild_global_results()
        return True

    def refresh_feeds(self, recheck=False):
        self.feed_generation += 1
        generation = self.feed_generation
        if recheck: self.recheck_generation = generation
        if self.feed_context == 'tv':
            channel = self.current_channel
            def channel_sources():
                return [Feed(source.provider, source.label, url=source.url, kind=source.kind, headers=dict(source.headers),
                             note='resolved when selected' if source.kind == 'candidate' else '',
                             metadata={'referer': source.referer} if source.referer else {})
                        for source in channel.sources]
            tasks = [('Channel sources', channel_sources)]
        elif self.feed_context == 'replay':
            item = self.current_replay
            def replay_servers():
                return [Feed(item.source, server.label, url=server.url, kind='candidate',
                             note='replay server; resolved when selected',
                             metadata={'referer':server.referer, 'provider':urllib.parse.urlsplit(server.url).hostname or ''})
                        for server in replays.servers(item)]
            tasks = [('Replay servers', replay_servers)]
        else:
            tasks = feed_tasks(self.games[self.gi], dict(self.config))
        self.feeds = []; self.errors = []; self.fi = 0
        self.hidden_feeds.clear()
        self.auto_checks.clear(); self.auto_heights.clear(); self.auto_speeds.clear(); self.auto_checked_at.clear()
        self.manual_status.clear()
        self.feed_sections = {}; self.feed_order = [name for name, _ in tasks]
        self.feed_pending = len(tasks); self.feed_selection_touched = False
        self.message = 'Checking sources...' if tasks else 'No sources available.'
        while True:
            try: self.feed_jobs.get_nowait()
            except queue.Empty: break
        for name, operation in tasks:
            try: self.feed_jobs.put_nowait((generation, name, operation))
            except queue.Full: self.feed_queue.put((generation, name, [], 'Busy'))
        if tasks and not self.feed_workers:
            for index in range(6):
                worker = threading.Thread(target=self._feed_worker, name=f'source-load-{index}', daemon=True)
                self.feed_workers.append(worker); worker.start()

    def _feed_worker(self):
        while True:
            job = self.feed_jobs.get()
            if job is None or self.closed.is_set(): return
            generation, name, operation = job
            if generation != self.feed_generation: continue
            try: feeds, error = operation(), None
            except Exception as exc: feeds, error = [], type(exc).__name__
            if not self.closed.is_set(): self.feed_queue.put((generation, name, feeds, error))

    def poll_feeds(self):
        active = (self.mode == 'feeds' or (self.mode == 'leagues' and self.league_cancel == 'feeds')
                  or (self.mode == 'search' and self.search_return == 'feeds')
                  or (self.mode == 'help' and (self.help_return == 'feeds'
                      or (self.help_return == 'leagues' and self.league_cancel == 'feeds'))))
        changed = False
        while True:
            try: generation, name, feeds, error = self.feed_queue.get_nowait()
            except queue.Empty: break
            if generation != self.feed_generation or not active: continue
            visible = self.visible_feeds()
            selected = visible[self.fi] if self.feed_selection_touched and self.fi < len(visible) else None
            self.feed_sections[name] = feeds
            self.feed_pending = max(0, self.feed_pending-1)
            if error: self.errors.append(f'{name}: {error}')
            self.feeds = [feed for source in self.feed_order for feed in self.feed_sections.get(source, [])]
            self.start_auto_checks(generation, feeds)
            visible = self.visible_feeds()
            selected_index = next((index for index, item in enumerate(visible) if item is selected), None)
            if selected_index is not None:
                self.fi = selected_index
            else:
                self.fi = 0 if selected is None else min(self.fi, max(0, len(visible)-1))
            changed = True
        if changed and self.mode == 'feeds':
            self.message = f'{len(self.feeds)} choices found'
            if self.feed_pending: self.message += f' | checking {self.feed_pending} sources...'
            elif self.errors: self.message += ' | ' + ', '.join(self.errors)
        return changed

    def start_auto_checks(self, generation, feeds):
        if self.closed.is_set() or generation != self.feed_generation: return
        if generation != self.auto_generation:
            self.auto_generation = generation; self.auto_started = 0
            while True:
                try: self.auto_jobs.get_nowait()
                except queue.Empty: break
        unchecked = []
        for feed in feeds:
            entry = None if generation == self.recheck_generation else self.check_cache.get(feed)
            if entry is None: unchecked.append(feed); continue
            self.auto_checks[id(feed)] = entry.status
            self.auto_checked_at[id(feed)] = entry.checked_at
            if entry.height is not None: self.auto_heights[id(feed)] = entry.height
            if entry.speed is not None: self.auto_speeds[id(feed)] = entry.speed
        # Check every source the app can sample, so dead ones drop out and the smooth ones rise.
        room = max(0, AUTO_CHECKS_PER_SCREEN - self.auto_started)
        chosen = [feed for feed in unchecked if feed.kind in ('sf', 'direct', 'candidate')][:min(AUTO_CHECKS_PER_DIRECTORY, room)]
        self.auto_started += len(chosen)
        for feed in chosen:
            self.auto_checks[id(feed)] = 'checking'
            try: self.auto_jobs.put_nowait((generation, feed))
            except queue.Full: self.auto_checks[id(feed)] = '?'
        if not self.auto_workers:
            for index in range(6):
                worker = threading.Thread(target=self._auto_check_worker, name=f'source-check-{index}', daemon=True)
                self.auto_workers.append(worker); worker.start()

    def _auto_check_worker(self):
        while True:
            job = self.auto_jobs.get()
            if job is None or self.closed.is_set(): return
            generation, feed = job
            if generation != self.feed_generation: continue
            height = speed = None
            try:
                result = quick_probe_feed(feed)
                status = 'OK' if result else 'n/a'
                if isinstance(result, dict): height, speed = result.get('height'), result.get('speed')
            except urllib.error.HTTPError as exc:
                status = f'HTTP {exc.code}'
            except Exception:
                status = 'Failed'
            if not self.closed.is_set(): self.auto_queue.put((generation, feed, status, height, speed))

    def poll_auto_checks(self):
        changed = False
        before = self.visible_feeds()
        # A source the user picked stays selected as results reorder the list; otherwise the top one is.
        chosen = self.feed_selection_touched or self.fi > 0
        selected = before[self.fi] if chosen and self.fi < len(before) else None
        old_index = self.fi
        while True:
            try: generation, feed, status, *measured = self.auto_queue.get_nowait()
            except queue.Empty: break
            if generation != self.feed_generation or id(feed) not in self.auto_checks: continue
            self.auto_checks[id(feed)] = status
            self.auto_checked_at.pop(id(feed), None)
            height, speed = (list(measured) + [None, None])[:2]
            if isinstance(height, int): self.auto_heights[id(feed)] = height
            if isinstance(speed, (int, float)): self.auto_speeds[id(feed)] = speed
            self.check_cache.put(feed, status, height, speed)
            changed = True
        if changed:
            self.check_cache.save()
            visible = self.visible_feeds()
            selected_index = next((index for index, item in enumerate(visible) if item is selected), None)
            self.fi = (selected_index if selected_index is not None else
                       min(old_index, max(0, len(visible)-1)) if chosen else 0)
        return changed

    def prompt(self, label, initial=''):
        # A normal terminal prompt accepts long manifest URLs and lets the user paste.
        curses.def_prog_mode()
        curses.endwin()
        try:
            return input('\n' + label + ' ').strip()
        except EOFError:
            return ''
        finally:
            curses.reset_prog_mode()
            self.scr.clear()
            self.scr.refresh()

    def configure_addon(self):
        value = self.prompt('Add-on manifest URL (blank clears):')
        if value:
            try: addon_base(value)
            except ValueError as e: self.message = str(e); return
            self.config['addon'] = value
        else:
            self.config.pop('addon', None)
        save_config(self.config)
        self.message = 'Add-on saved.' if value else 'Add-on removed.'
        if self.mode == 'feeds': self.refresh_feeds()

    def configure_resolver(self):
        value = self.prompt('Resolver folder (blank clears):')
        if value:
            path = pathlib.Path(value).expanduser().resolve()
            if not (path / 'src/server.js').is_file():
                self.message = 'Folder must contain src/server.js'; return
            self.config['resolver_dir'] = str(path)
        else:
            self.config.pop('resolver_dir', None)
        save_config(self.config)
        self.message = 'Resolver folder saved.' if value else 'Resolver folder cleared.'

    def helper_running(self):
        try:
            with socket.create_connection(('127.0.0.1', 38765), timeout=0.3): return True
        except OSError:
            return False

    def ensure_helper(self):
        # A manual test and playback can resolve sources concurrently. Start
        # at most one helper and never start a new one after the app closes.
        with self.resource_lock:
            if self.closed.is_set(): raise DependencyError('The app is closing.')
            if self.helper_running(): return
            if self.helper is None or self.helper.poll() is not None:
                directory = self.config.get('resolver_dir') or os.environ.get('NBA_RESOLVER_DIR', '') or str(pathlib.Path(__file__).resolve().parent.parent / 'resolver')
                if not directory:
                    raise DependencyError('Set up the resolver and press d to select its folder; see README.')
                entry = pathlib.Path(directory).expanduser() / 'src/server.js'
                if not entry.is_file() or not (entry.parent.parent / 'node_modules').is_dir() or not shutil.which('node'):
                    raise DependencyError(setup_hint('PPV sources need a one-time setup', '--setup-ppv',
                                                     'Resolver, its dependencies or Node.js missing; see README.'))
                env = os.environ.copy(); env.update({'HOST':'127.0.0.1', 'PORT':'38765', 'NODE_USE_ENV_PROXY':'1'})
                self.helper = subprocess.Popen(['node', str(entry.resolve())], cwd=str(entry.parent.parent), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.resolver_owned = True
            helper = self.helper
        for _ in range(35):
            if self.closed.is_set(): raise DependencyError('The app is closing.')
            if helper.poll() is not None: raise DependencyError('Resolver failed to start; run npm install in its folder.')
            if self.helper_running(): return
            self.closed.wait(.1)
        raise DependencyError('Resolver did not start on localhost:38765.')

    def get_relay(self):
        with self.resource_lock:
            if self.closed.is_set(): raise DependencyError('The app is closing.')
            if self.relay is None: self.relay = relay.Relay()
            return self.relay

    def hide_feed(self, feed):
        self.hidden_feeds.add(id(feed))
        self.fi = min(self.fi, max(0, len(self.visible_feeds()) - 1))

    def resolve_feed(self, feed):
        url, headers = feed.url, feed.headers
        if feed.kind == 'candidate':
            found = providers.extract_player(feed.url, referer=feed.metadata.get('referer'))
            if found['kind'] == 'sf':
                feed = Feed(feed.source, feed.label, feed.side, found['url'], 'sf',
                            metadata={k:found[k] for k in ('key','quality','variant','category')})
            elif found['kind'] == 'ppv':
                feed = Feed(feed.source, feed.label, feed.side, found['url'], 'ppv')
            elif found['kind'] == 'relay':
                url = self.get_relay().add(found['url'],found['edge'],found['channel'],found['referer'])
                check_playlist(url)
                return url, {}
            else:
                referer = found['referer']
                p = urllib.parse.urlsplit(referer)
                headers = {'Referer':referer, 'Origin':p.scheme+'://'+p.netloc, 'User-Agent':UA}
                url = found['url']; check_playlist(url, headers)
                return url, headers
            url, headers = feed.url, feed.headers
        if feed.kind == 'sf':
            for attempt in range(2):
                try:
                    url, headers = resolve_streamfree(feed)
                    manifest = check_playlist(url, headers)
                    wrapped = streamfree_wraps_segments(url, manifest, headers)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code != 403 or attempt: raise
            if wrapped:
                url = self.get_relay().add_streamfree(url, headers['Referer'],
                                                      lambda: resolve_streamfree(feed), manifest)
                check_playlist(url)
                return url, {}
        if feed.kind == 'ppv':
            self.ensure_helper()
            data = request(RESOLVER + '/api/embed', data={'iframe':feed.url}, timeout=35)
            url = data.get('proxiedUrl', '')
            if not data.get('ok') or not safe_url(url):
                raise RuntimeError(data.get('error', 'No playable URL returned'))
            u = urllib.parse.urlsplit(url)
            if u.hostname not in ('localhost','127.0.0.1') or u.port != 38765:
                raise RuntimeError('Unexpected resolver URL')
            headers = {}
            check_playlist(url)
        return url, headers

    def player_ready(self, feed):
        if feed.kind == 'web':
            self.message = 'This add-on supplied a browser-only source.'; return False
        if (self.ai_ad_mute or self.ai_ad_observe) and self.player != 'mpv':
            self.message = 'AI ad detection requires mpv. Press p to switch players.'; return False
        if (self.ai_ad_mute or self.ai_ad_observe) and not ai_ad_mute.installed():
            self.message = setup_hint('AI detector not installed', '--setup-ads', 'AI detector not installed. Run ./sports-cli --install-ad-model.'); return False
        iina = find_iina() if self.player == 'iina' else None
        celluloid = find_celluloid() if self.player == 'celluloid' else None
        if self.player == 'iina' and not iina:
            self.message = 'IINA not found. Install it or press p to switch to mpv.'; return False
        if self.player == 'celluloid' and not celluloid:
            self.message = 'Celluloid not found. Install it or press p to switch to mpv.'; return False
        if self.player == 'mpv' and not shutil.which('mpv'):
            self.message = setup_hint('mpv is not installed', '--setup', 'mpv is not installed or not on PATH.'); return False
        return True

    def play(self, feed, *, resolved=None):
        if not self.player_ready(feed): return
        iina = find_iina() if self.player == 'iina' else None
        celluloid = find_celluloid() if self.player == 'celluloid' else None
        if resolved is None:
            self.message = 'Resolving selected stream...'; self.render()
            try:
                url, headers = self.resolve_feed(feed)
            except (OSError, ValueError, KeyError, RuntimeError) as e:
                if not isinstance(e, DependencyError):
                    self.hide_feed(feed)
                    self.message = f'Stream unavailable: {e}. Hidden until refresh.'
                else:
                    self.message = str(e)
                return
        else:
            url, headers = resolved
        if self.player == 'iina':
            try:
                result = subprocess.run(iina_command(iina, url, headers, cache=self.cache),
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=10, check=False)
                self.message = ('Opened IINA. Keep this terminal open for local streams.' if result.returncode == 0
                                else f'IINA could not start (status {result.returncode}).')
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.message = f'Could not start IINA: {type(exc).__name__}'
            return
        if self.player == 'celluloid':
            try:
                process = subprocess.Popen(celluloid_command(celluloid, url, headers, cache=self.cache),
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=True)
                self.gui_players.append(process)
                self.message = 'Launched Celluloid. Keep this terminal open for local streams.'
            except OSError as exc:
                self.message = f'Could not start Celluloid: {type(exc).__name__}'
            return
        intervals, ad_note = [], ''
        if self.ad_mute:
            if self.feed_context == 'replay':
                self.message = 'Checking replay for explicit ad markers...'; self.render()
                try:
                    intervals = ad_mute.marked_replay_intervals(url, headers)
                except (OSError, ValueError, TypeError):
                    pass
                if not intervals: ad_note = ' No marked ads found; ad mute was inactive.'
            else:
                ad_note = ' Ad mute is available only for marked replays.'
        ai_requested = self.ai_ad_mute or self.ai_ad_observe
        leagues = ai_ad_mute.supported_leagues() if ai_requested else set()
        ai_active = ai_requested and self.feed_context != 'tv' and self.league in leagues
        if ai_requested and self.feed_context == 'tv':
            ad_note = ' AI ad detection is for sports broadcasts and stays off for TV channels.'
        elif ai_requested and not ai_active:
            ad_note = (' AI ad mute is currently limited to NBA broadcasts.' if leagues == {'nba'}
                       else ' The installed ad model was not trained on this league.')
        temporary, monitor, preview = None, None, None
        command = mpv_command(url, headers, cache=self.cache, terminal_video=self.terminal_video)
        if self.record_dir:
            # mpv writes the stream it receives, so nothing is downloaded twice.
            name = re.sub(r'[^A-Za-z0-9]+', '-', f'{self.league}-{feed.source}-{dt.datetime.now():%Y%m%d-%H%M%S}').strip('-')
            command.insert(1, '--stream-record=' + str(self.record_dir / (name + '.ts')))
            ad_note += f' Recording to {self.record_dir}.'
        if intervals or ai_active:
            try:
                # A short /tmp path keeps the socket under the Unix path-length limit.
                temporary = tempfile.TemporaryDirectory(prefix='sports-mpv-', dir=None if os.name == 'nt' else '/tmp')
                socket_path = ad_mute.ipc_path(temporary.name)
                on_break, preview = self.break_handler() if ai_active and self.ai_ad_mute else (None, None)
                monitor = (ai_ad_mute.Monitor(socket_path, temporary.name, mute=self.ai_ad_mute, on_break=on_break)
                           if ai_active else ad_mute.Monitor(socket_path, intervals))
                monitor.start()
                command.insert(1, '--input-ipc-server=' + socket_path)
                if ai_active and '--hwdec=auto-safe' in command:
                    # The detector's screenshots fail on frames left on the GPU once the stream's timestamps jump;
                    # copying decoded frames back keeps hardware decoding and lets software screenshots read them.
                    command[command.index('--hwdec=auto-safe')] = '--hwdec=auto-copy-safe'
                    command.insert(1, '--screenshot-sw=yes')
            except OSError:
                if preview: preview.close(); preview = None
                if temporary: temporary.cleanup(); temporary = None
                ad_note = ' Ad mute could not start.'
        ad_note = self.stream_notes.pop(url, '') + ad_note
        self.message = 'Starting mpv. Press q in mpv to return.' + ad_note; self.render()
        curses.def_prog_mode(); curses.endwin()
        try:
            result = subprocess.run(command, check=False)
            if result.returncode == 0:
                self.message = 'Returned from mpv.' + ad_note
            else:
                self.hide_feed(feed)
                self.message = f'mpv exited with status {result.returncode}; feed hidden until refresh.'
        except OSError as e:
            self.message = f'Could not start mpv: {e}'
        finally:
            if monitor: monitor.stop()
            if preview: preview.close()
            if temporary: temporary.cleanup()
            curses.reset_prog_mode(); self.scr.refresh()

    def break_handler(self):
        """Return (on_break, preview) for the chosen ad-break mode; preview may be None."""
        if self.ad_breaks == 'mute': return None, None
        live = self.feed_context == 'live' and self.games and self.gi < len(self.games)
        title = self.games[self.gi].title if live else 'Back when the game returns'
        other = (breaks.other_live_game(self.games, self.games[self.gi].id)
                 if live and self.ad_breaks == 'corner' and not self.terminal_video else None)
        corner = self.ad_breaks == 'corner' and not self.terminal_video
        preview = None
        if other:
            def resolve():
                feeds, _ = fetch_feeds(other, dict(self.config))
                for feed in [item for item in feeds if item.kind not in ('web', 'ppv')][:4]:
                    try: return self.resolve_feed(feed)
                    except Exception: continue
                return None
            preview = breaks.CornerPreview(other.title, resolve,
                                           lambda url, headers: breaks.corner_command(mpv_command(url, headers, cache=self.cache)))
            preview.prepare()
        detail = (f'{other.title} is in the corner' if other else
                  'No other live game to preview' if corner else '')
        def on_break(active, command):
            if active:
                breaks.show_cover(command, title, detail)
                if preview: preview.show()
            else:
                if preview: preview.hide()
                breaks.hide_cover(command)
        return on_break, preview

    def begin_feed_action(self, feed, action):
        """Resolve in the background; player and terminal operations stay on the UI thread."""
        if action not in ('play', 'inspect'): raise ValueError('Unknown source action')
        if action == 'play' and not self.player_ready(feed): return
        if action == 'inspect' and feed.kind == 'web':
            self.message = 'This source has no playable media URL.'; return
        current = self.action_current
        if current and current[1] == self.feed_generation and current[2] is feed and current[3] == action:
            self.message = 'This source is already being resolved.'; return
        if len(self.action_inflight) >= 2:
            self.message = 'Two sources are resolving; try again when one finishes.'; return
        self.action_serial += 1
        serial, generation = self.action_serial, self.feed_generation
        self.action_current = (serial, generation, feed, action)
        self.action_inflight.add(serial)
        self.message = f'{"Opening" if action == "play" else "Inspecting"} {feed.source}; Esc cancels.'
        backups = self.backup_candidates(feed) if action == 'play' else []
        def worker():
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(backups)) if backups else None
            # Resolve backups alongside the selected source; never let them delay it for long.
            pending = [(pool.submit(self.resolve_feed, backup), backup) for backup in backups] if pool else []
            try:
                url, headers = self.resolve_feed(feed)
                if pending: url, headers = self.merge_backup(url, headers, pending)
                result, error = (url, headers), None
            except Exception as exc:
                result, error = None, exc
            finally:
                if pool: pool.shutdown(wait=False, cancel_futures=True)
            if not self.closed.is_set(): self.action_queue.put((serial, generation, feed, action, result, error))
        threading.Thread(target=worker, daemon=True).start()

    def backup_candidates(self, feed):
        """Other live sources for the same broadcast side, reachable ones first."""
        if not self.dual_stream or self.feed_context not in ('live', 'tv') or feed.kind in ('web', 'ppv'): return []
        def status(item): return self.manual_status.get(id(item), self.auto_checks.get(id(item)))
        # A home and an away feed have different commentary, so never splice between them.
        options = [item for item in self.feeds if item is not feed and id(item) not in self.hidden_feeds
                   and item.kind not in ('web', 'ppv') and item.url != feed.url
                   and (feed.side == 'unknown' or item.side in (feed.side, 'unknown'))]
        options.sort(key=lambda item: (status(item) != 'OK', item.side != feed.side,
                                       isinstance(status(item), str) and status(item).startswith('HTTP ')))
        return options[:2]

    def merge_backup(self, url, headers, pending, budget=10):
        """Serve the selected source with the first backup that resolves in time."""
        sources, label = [(url, headers)], None
        try:
            futures = {future: backup for future, backup in pending}
            for future in concurrent.futures.as_completed(futures, timeout=budget):
                try: sources.append(future.result()); label = futures[future].source; break
                except Exception: continue
        except concurrent.futures.TimeoutError:
            pass
        if len(sources) == 1: return url, headers
        try: merged, usable = self.get_relay().add_merged(sources)
        except (OSError, ValueError, DependencyError): return url, headers
        if not merged or usable < 2: return url, headers
        self.stream_notes[merged] = f' Backup: {label}.'
        return merged, {}

    def show_stream_url(self, url, headers):
        curses.def_prog_mode(); curses.endwin()
        try:
            print('\nMedia URL (may expire; localhost links need this app open):\n'+url)
            for name, value in headers.items(): print(name+': '+value)
            try: input('\nPress Enter to return...')
            except EOFError: pass
        finally:
            curses.reset_prog_mode(); self.scr.clear(); self.scr.refresh()
        self.message = 'Stream link shown.'

    def cancel_feed_action_if_changed(self):
        current = self.action_current
        if not current: return
        _, generation, feed, _ = current
        visible = self.visible_feeds() if self.mode == 'feeds' else []
        if (generation != self.feed_generation or self.mode != 'feeds' or
                self.fi >= len(visible) or visible[self.fi] is not feed):
            self.action_current = None
            if self.mode == 'feeds' and generation == self.feed_generation:
                self.message = 'Source changed; previous request canceled.'

    def poll_feed_actions(self):
        while True:
            try: serial, generation, feed, action, result, error = self.action_queue.get_nowait()
            except queue.Empty: break
            self.action_inflight.discard(serial)
            current = self.action_current
            if not current or current[0] != serial: continue
            self.action_current = None
            if self.closed.is_set() or generation != self.feed_generation or self.mode != 'feeds': continue
            visible = self.visible_feeds()
            if self.fi >= len(visible) or visible[self.fi] is not feed:
                self.message = 'Source changed; previous request canceled.'
                continue
            if error:
                if action == 'play' and not isinstance(error, DependencyError):
                    self.hide_feed(feed)
                    self.message = f'Stream unavailable: {error}. Hidden until refresh.'
                else:
                    self.message = str(error) if isinstance(error, DependencyError) else f'Stream unavailable: {error}'
            elif action == 'play':
                self.play(feed, resolved=result)
            else:
                self.show_stream_url(*result)

    def test_feed(self, feed):
        if feed.kind == 'web':
            self.message = 'This source has no playable media URL.'; return
        generation = self.feed_generation
        token = (generation, id(feed))
        if token in self.manual_checks:
            self.message = 'This source is already being tested.'; return
        if len(self.manual_checks) >= 2:
            self.message = 'Two source tests are running; try again when one finishes.'; return
        self.manual_checks.add(token)
        self.message = f'Testing {feed.source}; you can keep browsing.'
        def worker():
            try:
                url, headers = self.resolve_feed(feed)
                if self.closed.is_set(): return
                result, error = grading.inspect_stream(url, headers), None
            except Exception as exc:
                result, error = {}, exc
            self.manual_queue.put((generation, feed, result, error))
        threading.Thread(target=worker, daemon=True).start()

    def poll_manual_checks(self):
        changed = False
        while True:
            try: generation, feed, result, error = self.manual_queue.get_nowait()
            except queue.Empty: break
            self.manual_checks.discard((generation, id(feed)))
            if generation != self.feed_generation or not any(item is feed for item in self.feeds):
                continue
            changed = True
            if isinstance(error, DependencyError):
                if self.mode == 'feeds': self.message = str(error)
                continue
            before = self.visible_feeds()
            selected = before[self.fi] if self.fi < len(before) else None
            old_index = self.fi
            history = self.config.get('health')
            if not isinstance(history, dict): history = {}; self.config['health'] = history
            key = grading.source_key(feed)
            record = history.get(key)
            if not isinstance(record, dict): record = {}; history[key] = record
            checks = record.get('checks') if isinstance(record.get('checks'), list) else []
            record['checks'] = (checks + [error is None])[-20:]
            if error is None:
                record['latency_ms'] = result['latency_ms']
                self.manual_status[id(feed)] = 'OK'
            if result.get('height'): record['height'] = result['height']
            save_config(self.config)
            outcome = 'OK' if error is None else f'HTTP {error.code}' if isinstance(error, urllib.error.HTTPError) else None
            if outcome: self.check_cache.put(feed, outcome, result.get('height')); self.check_cache.save()
            if error: self.hidden_feeds.add(id(feed))
            visible = self.visible_feeds()
            selected_index = next((index for index, item in enumerate(visible) if item is selected), None)
            self.fi = selected_index if selected_index is not None else min(old_index, max(0, len(visible)-1))
            label = grading.summary(record)
            if self.mode == 'feeds':
                if error:
                    self.message = f'Test failed ({str(error) or type(error).__name__}); hidden until refresh. | {label}'
                else:
                    self.message = f'Segment reachable in {record["latency_ms"]} ms | {label}'
        return changed

    def close(self):
        self.closed.set()
        self.stop_schedule_workers()
        if self.player_worker_started:
            while True:
                try: self.player_jobs.get_nowait()
                except queue.Empty: break
            self.player_jobs.put_nowait(None)
        while True:
            try: self.feed_jobs.get_nowait()
            except queue.Empty: break
        for _ in self.feed_workers: self.feed_jobs.put_nowait(None)
        while True:
            try: self.auto_jobs.get_nowait()
            except queue.Empty: break
        for _ in self.auto_workers: self.auto_jobs.put_nowait(None)
        if self.search_workers_started:
            self.search_needed.clear()
            while True:
                try: self.search_jobs.get_nowait()
                except queue.Empty: break
            for _ in range(3): self.search_jobs.put(None)
        with self.resource_lock:
            local_relay, self.relay = self.relay, None
            helper = self.helper if self.resolver_owned else None
            self.resolver_owned = False
        if local_relay: local_relay.close()
        if helper and helper.poll() is None:
            helper.terminate()
            try: helper.wait(timeout=3)
            except subprocess.TimeoutExpired: helper.kill(); helper.wait()

    def poll_gui_players(self):
        running = []
        for process in self.gui_players:
            status = process.poll()
            if status is None: running.append(process)
            elif status: self.message = f'Celluloid exited with status {status}.'
        self.gui_players = running

    # Keys that mean the same thing on every screen except search, where they type.
    SECTIONS = {ord('1'): 'sports', ord('2'): 'replays', ord('3'): 'tv'}
    BACK_KEYS = (27, curses.KEY_BACKSPACE, 127, 8, curses.KEY_LEFT, ord('h'))
    OPEN_KEYS = (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT, ord('l'))
    # What each screen asks of the user when they arrive back on it.
    ARRIVAL = {'home': 'Choose what to watch, or press / to search.', 'leagues': 'Choose a sport.',
               'games': 'Select a game.', 'replays': 'Select a replay.', 'channels': 'Select a channel.',
               'replay_sources': 'Choose a replay site.', 'feeds': 'Choose a source.',
               'settings': 'Enter changes the selected setting.', 'search': 'Type to search.'}

    def arrive(self, mode):
        self.mode = mode; self.message = self.ARRIVAL.get(mode, '')

    def handle_key(self, key, typed):
        """Apply one key press to the current screen; return False to quit."""
        if self.mode == 'search': return self.search_key(key, typed)
        if key == ord('q'): return False
        if self.mode == 'help':
            if key in (ord('?'), *self.BACK_KEYS): self.arrive(self.help_return); return True
            elif key in (curses.KEY_UP, ord('k')): self.help_scroll = max(0, self.help_scroll-1); return True
            elif key in (curses.KEY_DOWN, ord('j')): self.help_scroll = min(len(HELP_LINES)-1, self.help_scroll+1); return True
            elif key not in (*self.SECTIONS, ord('/'), ord(',')): self.unused_key(typed); return True
            if key == ord(',') and self.help_return == 'settings': self.arrive('settings'); return True
            self.mode = self.help_return  # the global keys leave help as if from the screen beneath it
        if key == ord('?'):
            self.help_return = self.mode; self.help_scroll = 0; self.mode = 'help'; self.message = 'Controls and setup.'
        elif key in self.SECTIONS: self.open_section(self.SECTIONS[key])
        elif key == ord('/'): self.open_search()
        elif key == ord(','): self.open_settings()
        elif key == ord('s') and (self.mode in ('games', 'replays') or self.mode == 'feeds' and self.feed_context != 'tv'): self.pick_sport()
        elif key in (curses.KEY_UP, ord('k')): self.move(-1)
        elif key in (curses.KEY_DOWN, ord('j')): self.move(1)
        elif key in self.OPEN_KEYS: self.open_selected()
        elif key in self.BACK_KEYS: self.go_back()
        elif key == ord('r') and self.mode in ('games', 'replays', 'channels', 'feeds'):
            {'games': self.refresh_games, 'replays': self.refresh_replays,
             'channels': self.refresh_channels, 'feeds': lambda: self.refresh_feeds(recheck=True)}[self.mode]()
        elif key == ord('f') and self.mode in ('games', 'replays', 'channels', 'feeds'): self.filter_list()
        elif self.mode == 'games' and key in (ord('['), ord(']')):
            self.day += dt.timedelta(days=-1 if key == ord('[') else 1); self.gi = 0; self.refresh_games()
        elif self.mode == 'feeds': self.feed_key(key, typed)
        else: self.unused_key(typed)
        return True

    def unused_key(self, typed):
        if typed and typed.strip(): self.message = f'{typed} does nothing here. Press ? for keys.'

    def search_key(self, key, typed):
        if key in (17, 24): return False  # Ctrl-Q or Ctrl-X
        if key in (curses.KEY_UP, 16):
            self.search_position = max(0, self.search_position-1); self.search_touched = True
        elif key in (curses.KEY_DOWN, 14):
            self.search_position = min(max(0, len(self.search_results)-1), self.search_position+1); self.search_touched = True
        elif key in (10, 13, curses.KEY_ENTER):
            self.open_global_result()
        elif key == 27:
            self.arrive(self.search_return or 'home'); self.search_return = None
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.search_query = self.search_query[:-1]; self.update_global_search()
        elif key == 21:  # Ctrl-U
            self.search_query = ''; self.update_global_search()
        elif key == 18:  # Ctrl-R
            for work in self.search_needed: self.search_cache.pop(work, None)
            self.player_cache.pop(self.player_key, None)
            if self.player_directory: self.player_directory.clear()
            self.player_due = time.monotonic() if self.player_key else None
            self.search_due = time.monotonic(); self.rebuild_global_results()
        elif typed:
            self.search_query += typed; self.update_global_search()
        return True

    def leave_feeds(self):
        if self.mode == 'feeds': self.feed_generation += 1; self.feed_pending = 0

    def open_section(self, section):
        """Home's three sections, reachable from every screen with 1, 2 and 3."""
        self.leave_feeds()
        if section == 'tv':
            self.mode = 'channels'; self.ci = min(self.ci, max(0, len(self.visible_channels())-1))
            # Channel lists change slowly; reuse one for ten minutes.
            if not self.channels or time.monotonic() - self.channels_loaded_at > 600: self.refresh_channels()
            else: self.message = self.channel_summary()
            return
        if self.section == section and (self.mode in ('games', 'replays') or self.mode == 'feeds' and self.feed_context != 'tv'):
            # The key for the section you are in changes its sport; Esc comes back here.
            self.pick_sport(); return
        self.section = section; self.league_cancel = None
        self.league_index = list(LEAGUES).index(self.league)
        self.mode = 'leagues'; self.message = 'Choose a sport for live games.' if section == 'sports' else 'Choose a sport for replays.'

    def open_search(self):
        self.leave_feeds()
        self.search_return = self.mode; self.mode = 'search'; self.update_global_search()

    def open_settings(self):
        if self.mode == 'settings': self.go_back(); return
        self.settings_return = self.mode; self.mode = 'settings'; self.message = 'Enter changes the selected setting.'

    def pick_sport(self):
        """Change sport without leaving the current section; Esc returns to where it opened."""
        self.league_cancel = self.mode
        self.section = 'replays' if self.mode == 'replays' or self.feed_context == 'replay' else 'sports'
        self.league_index = list(LEAGUES).index(self.league); self.mode = 'leagues'
        self.message = 'Choose a sport.'

    def move(self, step):
        size = {'home': len(HOME_ITEMS), 'leagues': len(LEAGUES), 'games': len(self.games), 'replays': len(self.replays),
                'channels': len(self.visible_channels()), 'settings': len(self.settings_rows()),
                'replay_sources': len(self.current_bundle.sources) if self.current_bundle else 0,
                'feeds': len(self.visible_feeds())}.get(self.mode, 0)
        field = {'home': 'home_index', 'leagues': 'league_index', 'games': 'gi', 'replays': 'ri', 'channels': 'ci',
                 'settings': 'settings_index', 'replay_sources': 'bundle_index', 'feeds': 'fi'}.get(self.mode)
        if not field: return
        setattr(self, field, min(max(0, size-1), max(0, getattr(self, field) + step)))
        if self.mode == 'replays': self.replay_selection_touched = True
        if self.mode == 'feeds': self.feed_selection_touched = True

    def open_selected(self):
        if self.mode == 'home':
            action = HOME_ITEMS[self.home_index][0]
            if action == 'search': self.open_search()
            elif action == 'settings': self.open_settings()
            else: self.open_section(action)
        elif self.mode == 'leagues':
            self.league = list(LEAGUES)[self.league_index]
            if self.league_cancel == 'feeds': self.leave_feeds()
            self.league_cancel = None; self.search = ''; self.gi = 0
            if self.section == 'replays': self.mode = 'replays'; self.replay_search = ''; self.refresh_replays()
            else: self.mode = 'games'; self.refresh_games()
        elif self.mode == 'games' and self.games:
            if self.games[self.gi].id == REDZONE: self.open_redzone()
            else: self.open_feeds('live', None)
        elif self.mode == 'replays' and self.replays:
            self.current_replay = self.replays[self.ri]; self.replay_selection_touched = True
            self.open_feeds('replay', None)
        elif self.mode == 'channels' and self.visible_channels():
            self.current_channel = self.visible_channels()[self.ci]
            self.open_feeds('tv', 'channels')
        elif self.mode == 'replay_sources' and self.current_bundle.sources:
            self.current_replay = self.current_bundle.sources[self.bundle_index]
            self.league = self.current_bundle.league
            self.open_feeds('replay', 'replay_sources')
        elif self.mode == 'settings':
            self.settings_rows()[self.settings_index][2]()
        elif self.mode == 'feeds' and self.visible_feeds():
            self.feed_selection_touched = True; self.begin_feed_action(self.visible_feeds()[self.fi], 'play')

    def open_feeds(self, context, origin):
        self.feed_context = context; self.feed_return = origin; self.mode = 'feeds'
        self.filter = 'all'; self.provider_filter = 'all'; self.feed_search = ''; self.refresh_feeds()

    def go_back(self):
        """Up one level: sources -> list -> sport -> home."""
        if self.mode == 'feeds':
            self.leave_feeds()
            origin = self.feed_return or {'replay': 'replays', 'tv': 'channels'}.get(self.feed_context, 'games')
            self.feed_return = None; self.arrive(origin)
            if origin == 'search': self.rebuild_global_results()
        elif self.mode == 'leagues':
            # A sport picker opened from a list returns there; the section's own picker goes home.
            back, self.league_cancel = self.league_cancel or 'home', None
            self.arrive(back)
        elif self.mode in ('games', 'replays'):
            if self.mode == 'replays': self.replay_generation += 1; self.replay_pending = 0
            self.section = 'sports' if self.mode == 'games' else 'replays'
            self.league_index = list(LEAGUES).index(self.league); self.league_cancel = None; self.arrive('leagues')
        elif self.mode == 'replay_sources':
            self.arrive('search'); self.rebuild_global_results()
        elif self.mode == 'settings':
            self.arrive(self.settings_return or 'home')
        elif self.mode == 'channels':
            self.arrive('home')
        else:
            self.message = 'This is the start. Press q to quit.'

    def filter_list(self):
        if self.mode == 'channels':
            self.channel_filter = self.prompt('Filter channels (blank shows all):', self.channel_filter).strip()
            self.ci = 0; self.message = f'Filter: {self.channel_filter}.' if self.channel_filter else 'Showing all channels.'
        elif self.mode == 'games':
            self.search = self.prompt('Filter games by team (blank shows all):').strip()
            self.apply_search(); self.message = f'{len(self.games)} games shown.'
        elif self.mode == 'replays':
            self.replay_search = self.prompt('Search replay titles (blank shows recent):').strip()
            self.refresh_replays()
        elif self.mode == 'feeds':
            self.feed_search = self.prompt('Filter sources (blank shows all):')
            self.fi = 0; self.feed_selection_touched = True
            self.message = f'{len(self.visible_feeds())} matching choices.'

    def feed_key(self, key, typed=None):
        visible = self.visible_feeds()
        selected = visible[self.fi] if self.fi < len(visible) else None
        if key == ord('x') and selected:
            self.feed_selection_touched = True; self.test_feed(selected)
        elif key == ord('i') and selected:
            self.feed_selection_touched = True; self.begin_feed_action(selected, 'inspect')
        elif key == ord('a') and self.feed_context == 'live':
            filters = ['all', 'home', 'away', 'unknown']
            self.filter = filters[(filters.index(self.filter)+1) % len(filters)]; self.fi = 0; self.feed_selection_touched = True
            self.message = f'Broadcast side: {self.filter}.'
        elif key == ord('v') and (self.show_failed or any(self.failed_check(f) for f in self.feeds)):
            self.show_failed = not self.show_failed; self.fi = 0; self.feed_selection_touched = True
            self.message = 'Showing sources that failed their checks.' if self.show_failed else 'Hiding sources that failed their checks.'
        elif key == ord('o'):
            names = ['all'] + list(dict.fromkeys(f.source for f in self.feeds if id(f) not in self.hidden_feeds))
            self.provider_filter = names[(names.index(self.provider_filter)+1) % len(names)] if self.provider_filter in names else 'all'
            self.fi = 0; self.feed_selection_touched = True; self.message = f'Source: {self.provider_filter}.'
        elif key == ord('g'):
            self.rank_by_checks = not self.rank_by_checks
            visible = self.visible_feeds()
            self.fi = next((index for index, item in enumerate(visible) if item is selected), 0)
            self.feed_selection_touched = True
            self.message = 'Ranked by reachability and your checks.' if self.rank_by_checks else 'Directory order.'
        elif key == ord('u'):
            value = self.prompt('Direct media URL:')
            if safe_url(value): self.play(Feed('Manual', 'Pasted URL', url=value, kind='direct'))
            else: self.message = 'Enter a valid HTTP(S) media URL.'
        elif key not in (ord('x'), ord('i')): self.unused_key(typed)

    def settings_rows(self):
        """(label, current value, change) for the settings screen."""
        return [
            ('Player', self.player.upper(), self.cycle_player),
            ('Cache', f'{self.cache} seconds', self.cycle_cache),
            ('Video in the terminal', 'on' if self.terminal_video else 'off', self.toggle_terminal_video),
            ('Backup source for live streams', 'on' if self.dual_stream else 'off', self.toggle_backup),
            ('During detected ads', breaks.LABELS[self.ad_breaks] + ('' if self.ai_ad_mute else ' (needs --ai-ad-mute)'), self.cycle_ad_breaks),
            ('Sports add-on', 'set' if self.config.get('addon') else 'none', self.configure_addon),
            ('PPV resolver folder', self.config.get('resolver_dir') or 'default', self.configure_resolver),
        ]

    def cycle_player(self):
        if self.player != 'mpv': self.player = 'mpv'
        elif sys.platform == 'darwin' and find_iina(): self.player = 'iina'
        elif sys.platform.startswith('linux') and find_celluloid(): self.player = 'celluloid'
        else:
            self.message = ('IINA not found. Install it with: brew install --cask iina' if sys.platform == 'darwin' else
                            'Celluloid not found. Install it with your package manager.' if sys.platform.startswith('linux')
                            else 'No alternate player configured on this platform.')
            return
        if self.player != 'mpv' and self.terminal_video: self.terminal_video = False; self.config['terminal_video'] = False
        self.config['player'] = self.player; save_config(self.config); self.message = f'Player: {self.player.upper()}.'

    def cycle_cache(self):
        self.cache = {5:15, 15:30, 30:5}[self.cache]; self.config['cache'] = self.cache; save_config(self.config)
        self.message = f'Cache: {self.cache} seconds.'

    def toggle_terminal_video(self):
        if self.player != 'mpv': self.message = 'Terminal video needs mpv.'; return
        self.terminal_video = not self.terminal_video; self.config['terminal_video'] = self.terminal_video; save_config(self.config)
        self.message = 'Terminal video enabled.' if self.terminal_video else 'Window video enabled.'

    def toggle_backup(self):
        self.dual_stream = not self.dual_stream; self.config['dual_stream'] = self.dual_stream; save_config(self.config)
        self.message = ('Live playback adds a backup source when one resolves.' if self.dual_stream
                        else 'Backup source off; live streams play directly.')

    def cycle_ad_breaks(self):
        self.ad_breaks = breaks.MODES[(breaks.MODES.index(self.ad_breaks)+1) % len(breaks.MODES)]
        self.config['ad_breaks'] = self.ad_breaks; save_config(self.config)
        self.message = 'During detected ads: ' + breaks.LABELS[self.ad_breaks] + '.'
        if not self.ai_ad_mute: self.message += ' Applies when run with --ai-ad-mute.'

    def run(self):
        try:
            curses.curs_set(0)
            if curses.has_colors() and not os.environ.get('NO_COLOR'):
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                self.live_color = curses.color_pair(1) | curses.A_BOLD
                curses.init_pair(2, 105 if curses.COLORS >= 256 else curses.COLOR_MAGENTA, -1)
                self.accent_color = curses.color_pair(2) | curses.A_BOLD
        except curses.error: pass
        self.scr.keypad(True)
        # Avoid terminal-specific insert/delete optimizations that can leave
        # stale characters when asynchronous search sections change length.
        if hasattr(self.scr, 'idlok'): self.scr.idlok(False)
        if hasattr(self.scr, 'idcok'): self.scr.idcok(False)
        self.scr.timeout(150)
        if self.mode == 'games': self.refresh_games()
        try:
            while True:
                self.poll_games(); self.poll_global_search(); self.poll_player_search(); self.start_global_search(); self.start_player_search(); self.poll_search_preview()
                self.poll_replays(); self.poll_channels(); self.poll_feeds(); self.poll_auto_checks(); self.poll_manual_checks(); self.cancel_feed_action_if_changed(); self.poll_feed_actions(); self.poll_gui_players(); self.render(); key, typed = self.read_key()
                if key == -1: continue
                if not self.handle_key(key, typed): break
        finally:
            self.close()


def main():
    parser = argparse.ArgumentParser(description='Browse sports games and choose streams.')
    parser.add_argument('--list', action='store_true', help='Print upcoming games and feed listings without a terminal UI')
    parser.add_argument('--date', help='Schedule date in YYYY-MM-DD (Eastern time)')
    parser.add_argument('--league', choices=LEAGUES, help='Open a league directly (default: choose in the app; nba for --list)')
    parser.add_argument('--search', action='store_true', help='Open global search directly')
    parser.add_argument('--tv', action='store_true', help='Open live TV channels (CNN, CNBC, MS NOW, Fox News...) directly')
    parser.add_argument('--addon', help='Stremio sports add-on manifest URL for this run')
    parser.add_argument('--resolver-dir', help='Local ppv-hls-proxy checkout for this run')
    mute = parser.add_mutually_exclusive_group()
    mute.add_argument('--ad-mute', action='store_true', help='Mute explicitly marked replay ad breaks in mpv for this run')
    mute.add_argument('--ai-ad-mute', action='store_true', help='Use the optional experimental local NBA ad detector in mpv')
    mute.add_argument('--ai-ad-observe', action='store_true', help='Show NBA ad predictions without changing audio')
    parser.add_argument('--install-ad-model', action='store_true', help='Explicitly download and install the optional local NBA ad detector')
    parser.add_argument('--record', metavar='DIR', type=pathlib.Path,
                        help='Save what mpv plays to DIR for this run, e.g. to label ad-detector training footage')
    args = parser.parse_args(); config = load_config()
    if args.record:
        try: args.record.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f'Cannot use recording folder: {exc}', file=sys.stderr); return 2
    if args.install_ad_model: return ai_ad_mute.install()
    if (args.ai_ad_mute or args.ai_ad_observe) and not ai_ad_mute.installed():
        print(setup_hint('AI ad detector not installed', '--setup-ads', 'AI ad detector not installed. Run ./sports-cli --install-ad-model first.',
                         running=False), file=sys.stderr)
        return 2
    if args.addon: config['addon'] = args.addon
    if args.resolver_dir: config['resolver_dir'] = args.resolver_dir
    if args.list and args.tv:
        channels, errors = tv.directory()
        for channel in channels:
            if channel.featured or len(channels) <= 50:
                print(f'{channel.name}: ' + ', '.join(f'{source.provider} {source.label}' for source in channel.sources))
        print(f'\n{len(channels)} channels in all. Run ./sports-cli --tv to browse and play them.')
        for error in errors: print('Source error:', error, file=sys.stderr)
        return 0 if channels else 1
    if args.list:
        try:
            league = args.league or 'nba'
            day = dt.date.fromisoformat(args.date) if args.date else default_day()
            games = get_games(day, league)
            if not games: print(f'No live or upcoming {LEAGUES[league][0]} games found for that date.'); return 0
            for i, game in enumerate(games, 1):
                print(f'{i:2}. {game.start.astimezone():%Y-%m-%d %H:%M %Z}  {game.title}')
            g = next((game for game in games if game.state == 'live'), games[0])
            print('\nStreams for:', g.title)
            print('TV:', ', '.join(g.broadcasts) or 'not listed')
            feeds, errors = fetch_feeds(g, config)
            for f in feeds:
                print(f'[{f.side.upper():7}] {f.source}: {f.label} ({f.kind})')
            for e in errors: print('Source error:', e, file=sys.stderr)
            if not feeds: print('No feeds found yet. Try again closer to tipoff.')
            return 0
        except Exception as e:
            print(f'Could not load schedule: {type(e).__name__}: {e}', file=sys.stderr); return 1
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('An interactive terminal is required; use --list for plain output.', file=sys.stderr); return 2
    try:
        def run_ui(screen):
            app = App(screen, config, ad_mute_enabled=args.ad_mute, ai_ad_mute_enabled=args.ai_ad_mute,
                      ai_ad_observe_enabled=args.ai_ad_observe)
            app.record_dir = args.record
            if args.search:
                app.mode = 'search'; app.search_return = 'home'
                app.message = 'Start typing a team, matchup, or league.'
            if args.league:
                app.league = args.league
                app.league_index = list(LEAGUES).index(args.league)
                app.section = 'sports'; app.mode = 'games'
            if args.date: app.day = dt.date.fromisoformat(args.date)
            if args.tv: app.open_section('tv')
            app.run()
        curses.wrapper(run_ui)
    except KeyboardInterrupt:
        pass
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
