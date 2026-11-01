"""Live TV channels: free direct streams from iptv-org plus DaddyLive's 24/7 list."""
from __future__ import annotations

import concurrent.futures
import html
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import providers

IPTV_US = 'https://iptv-org.github.io/iptv/countries/us.m3u'
# Free playlists listed by FMHY (rentry.co/IPTV-Playlists) that carry US news and network channels.
PLAYLISTS = [('iptv-org', IPTV_US),
             ('doms9', 'https://raw.githubusercontent.com/doms9/iptv/default/M3U8/TV.m3u8'),
             ('Aria', 'https://raw.githubusercontent.com/aria-tv/aria/main/aria%2B/united_states.m3u')]
DADDYLIVE = 'https://dlive.sx/24-7-channels.php'
MAX_PLAYLIST = 4_000_000
ADULT = re.compile(r'18\+|\bxxx\b|\badult\b|\bporn', re.I)

# Shown first, in this order. Each name lists the spellings providers use.
FEATURED = [
    ('CNN', ('cnn', 'cnn usa')),
    ('CNBC', ('cnbc', 'cnbc usa')),
    ('MS NOW', ('ms now', 'msnbc')),
    ('Fox News', ('fox news', 'fox news channel')),
    ('Fox Business', ('fox business', 'fox business network')),
    ('Bloomberg TV', ('bloomberg tv', 'bloomberg')),
    ('ABC', ('abc', 'abc usa')),
    ('CBS', ('cbs', 'cbs usa')),
    ('NBC', ('nbc', 'nbc usa')),
    ('FOX', ('fox', 'fox usa')),
    ('ABC News Live', ('abc news live', 'abc news live 1')),
    ('CBS News 24/7', ('cbs news 24/7', 'cbs news')),
    ('NBC News NOW', ('nbc news now',)),
    ('LiveNOW from FOX', ('livenow from fox',)),
    ('Fox Weather', ('fox weather', 'fox weather channel')),
    ('The Weather Channel', ('the weather channel', 'weather channel')),
    ('C-SPAN', ('c-span', 'cspan', 'c span 1')),
    ('ESPN', ('espn', 'espn usa')),
    ('ESPN2', ('espn2', 'espn 2', 'espn2 usa')),
    ('FS1', ('fs1', 'fox sports 1', 'fox sports 1 usa')),
    ('TNT', ('tnt', 'tnt usa')),
    ('NFL RedZone', ('nfl redzone', 'nfl redzone main', 'nfl red zone', 'redzone')),
    ('NFL Network', ('nfl network',)),
]


@dataclass(frozen=True)
class Source:
    provider: str
    label: str
    url: str
    kind: str                      # 'direct' plays as is; 'candidate' is a player page resolved on selection
    headers: tuple = ()            # (name, value) pairs required by the stream
    referer: str = ''


@dataclass
class Channel:
    name: str
    sources: list = field(default_factory=list)
    featured: bool = False


def key(name):
    """Normalize a provider's channel name for matching: drop quality, region tags and punctuation."""
    name = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', name.casefold())
    name = re.sub(r'\b(?:hd|fhd|sd|uhd|4k|east|feed)\b', ' ', name)
    return ' '.join(re.sub(r'[^a-z0-9/+&-]+', ' ', name).split())


def iptv_channels(text, provider='iptv-org'):
    """Parse an M3U playlist into direct sources, keeping per-stream referer and user agent."""
    out, info, options = [], None, {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXTINF:'):
            info, options = line, {}
        elif line.startswith('#EXTVLCOPT:') and '=' in line:
            name, value = line[11:].split('=', 1)
            options[name.strip().lower()] = value.strip()
        elif line and not line.startswith('#') and info:
            title = info.rsplit(',', 1)[-1].strip()
            # Aria marks streams its own checks found dead.
            dead = 'tvg-status="not-working"' in info
            if title and not dead and not ADULT.search(title) and providers.public_url(line, any_port=True):
                headers = []
                if options.get('http-referrer'): headers.append(('Referer', options['http-referrer']))
                if options.get('http-user-agent'): headers.append(('User-Agent', options['http-user-agent']))
                # Playlists' quality labels are often wrong; checks measure the real resolution.
                label = 'stream' + (' (may be US-only)' if 'geo-blocked' in title.casefold() else '')
                clean = re.sub(r'\s*\[[^\]]*\]|\s*\(\d{3,4}p\)', '', title).strip()
                out.append((clean, Source(provider, label, line, 'direct', tuple(headers))))
            info, options = None, {}
    return out


def daddylive_channels(page, base):
    """Parse the 24/7 list: each channel's stream page is resolved when it is selected."""
    out, seen = [], set()
    for href, text in re.findall(r'<a[^>]+href="([^"]*watch\.php\?id=(?:\d+))"[^>]*>(.*?)</a>', page, re.S | re.I):
        number = re.search(r'id=(\d+)', href).group(1)
        name = re.sub(r'\s*ID:\s*\d+\s*$', '', ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', text)).split()))
        if not name or number in seen or ADULT.search(name): continue
        seen.add(number)
        watch = urllib.parse.urljoin(base, href)
        stream = urllib.parse.urljoin(base, f'/stream/stream-{number}.php')
        if providers.public_url(stream):
            out.append((name, Source('DaddyLive', f'channel {number}', stream, 'candidate', referer=watch)))
    return out


def fetch_text(url, timeout=15):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_PLAYLIST + 1)
    if len(data) > MAX_PLAYLIST: raise ValueError('Playlist too large')
    return data.decode('utf-8', 'replace')


def directory():
    """Return (channels, errors); featured channels first, then everything else by name."""
    def playlist(name, url): return iptv_channels(fetch_text(url), name)
    def daddy():
        page, final = providers.fetch_html(DADDYLIVE, timeout=15, limit=3_000_000)
        return daddylive_channels(page, final)
    found, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(len(PLAYLISTS) + 1) as pool:
        jobs = [(name, pool.submit(playlist, name, url)) for name, url in PLAYLISTS] + [('DaddyLive', pool.submit(daddy))]
        for name, future in jobs:
            try: found.extend(future.result())
            except Exception as exc: errors.append(f'{name}: {type(exc).__name__}')  # noqa: BLE001
    return group(found), errors


def group(found):
    by_key = {}
    for name, source in found:
        channel = by_key.setdefault(key(name), Channel(name))
        if source not in channel.sources: channel.sources.append(source)
    featured = []
    for title, spellings in FEATURED:
        merged = Channel(title, featured=True)
        for spelling in spellings:
            for source in by_key.pop(key(spelling), Channel('')).sources:
                if source not in merged.sources: merged.sources.append(source)
        if merged.sources: featured.append(merged)
    for channel in featured:
        # Direct streams start faster; player pages are backups. Checks then order by measured resolution.
        channel.sources.sort(key=lambda source: source.kind != 'direct')
    rest = sorted(by_key.values(), key=lambda channel: channel.name.casefold())
    return featured + rest


def matches(channel, query):
    query = key(query)
    return not query or all(word in key(channel.name) for word in query.split())
