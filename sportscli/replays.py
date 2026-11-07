"""Browse replay catalogs and resolve their listed servers on demand."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import re
import urllib.parse
from dataclasses import dataclass, replace
from html.parser import HTMLParser

from . import providers, smart_search


@dataclass(frozen=True)
class Replay:
    source: str
    title: str
    url: str


@dataclass(frozen=True)
class ReplayServer:
    label: str
    url: str
    referer: str


@dataclass(frozen=True)
class ReplayGame:
    """One list row. identity is the league, the day, and the two teams. sources are the sites that carry that game."""
    identity: tuple
    league: str
    title: str
    date: dt.date | None
    sources: tuple[Replay, ...]


@dataclass(frozen=True)
class ReplayLine:
    """A day header, or one game row. game is None on a header."""
    text: str
    detail: str = ''
    game: ReplayGame | None = None


_DAYS = ('MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY')
_MONTHS = ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC')
_MONTH = (r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|'
          r'aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?')
_DATE_RE = re.compile(
    rf'(?<!\d)\d{{4}}-\d{{1,2}}-\d{{1,2}}(?!\d)'
    rf'|(?<!\d)\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?(?!\d)'
    rf'|\b(?:{_MONTH})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b'
    rf'|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH})\.?(?:,?\s+\d{{4}})?\b',
    re.I)
_SCORE_RE = re.compile(r'\b\d{1,3}\s*[-–—]\s*\d{1,3}\b')
_VS_RE = re.compile(r'\s+(?:vs\.?|versus)\s+', re.I)
_SPLIT_RE = re.compile(r'\s+[-–—|]\s+')
_NOISE = (
    'full game replay', 'full match replay', 'nba regular season', 'wnba regular season',
    'nfl regular season', 'regular season', 'college football', 'college basketball',
    'premier league', 'champions league', 'full match', 'serie a', 'la liga', 'ligue 1',
    'liga mx', 'bundesliga', 'basketball replays', 'basketball video', 'nfl video',
    'full match shows', 'fullmatchshows', 'watchreplay', 'watch replay',
    'ncaa', 'wnba', 'nba', 'nfl', 'mlb', 'nhl', 'mls', 'replay',
)
_NOISE_RE = re.compile(r'\b(?:' + '|'.join(re.escape(phrase) for phrase in _NOISE) + r')\b', re.I)


def matchup_title(title):
    """Team names only. The day, the league phrase, the site, and a score belong elsewhere."""
    text = re.sub(r'^\[[^\]]+\]\s*', '', ' '.join(str(title).split()))
    pieces = _SPLIT_RE.split(text)
    head = next((piece for piece in pieces if _VS_RE.search(piece)), text)
    cleaned = ' '.join(_SCORE_RE.sub(' ', _DATE_RE.sub(' ', _NOISE_RE.sub(' ', head))).split())
    parts = _VS_RE.split(cleaned, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return f'{parts[0]} vs {parts[1]}'
    return cleaned or ' '.join(text.split())


def _identity(league, title, day, url):
    parts = _VS_RE.split(title, maxsplit=1)
    if day is not None and len(parts) == 2 and parts[0] and parts[1]:
        keys = tuple(sorted(smart_search.normalize(part) for part in parts))
        if keys[0] and keys[1] and keys[0] != keys[1]:
            return (league, day.isoformat(), *keys)
    return (league, url)


def games(listings, league):
    """One ReplayGame per matchup on a day. Later listings of that game join its sources."""
    grouped, index = [], {}
    for item in listings:
        day = smart_search.replay_date(item.title)
        title = matchup_title(item.title)
        ident = _identity(league, title, day, item.url)
        slot = index.get(ident)
        if slot is None:
            index[ident] = len(grouped)
            grouped.append(ReplayGame(ident, league, title, day, (item,)))
            continue
        current = grouped[slot]
        if any(source.url == item.url for source in current.sources):
            continue
        grouped[slot] = replace(current, sources=current.sources + (item,))
    return grouped


def day_heading(day):
    if day is None:
        return 'DATE UNKNOWN'
    return f'{_DAYS[day.weekday()]}, {_MONTHS[day.month - 1]} {day.day}'


def lines(items):
    """Day headers, then one row per game. A list with no dates has no header."""
    rows, shown = [], object()
    dated = any(game.date for game in items)
    for game in items:
        if dated and game.date != shown:
            rows.append(ReplayLine(day_heading(game.date)))
            shown = game.date
        count = len(game.sources)
        rows.append(ReplayLine(game.title, '1 site' if count == 1 else f'{count} sites', game))
    return rows


def listings_of(item):
    """Catalog pages behind a row. A game has one per site. A single listing is itself."""
    return item.sources if isinstance(item, ReplayGame) else (item,)


def source_heading(item):
    pages = listings_of(item)
    if len(pages) == 1:
        return f'Replay sources from {pages[0].source}'
    return f'{len(pages)} sites'


def catalogs(league):
    now = dt.datetime.now(dt.timezone.utc)
    season = now.year if now.month >= 10 else now.year - 1
    basketball = ('Basketball Video', f'https://basketball-video.com/{season}-{str(season+1)[-2:]}', 'nba')
    if league == 'nba':
        return [('Basketball Replays', 'https://basketballreplays.net/', 'nba'), basketball]
    if league == 'wnba':
        return [('Basketball Video', 'https://basketball-video.com/wnba-video-fulll', 'wnba')]
    if league in ('ncaam', 'ncaaw'):
        category = 'college_basketball_w' if league == 'ncaaw' else 'college_basketball_m'
        return [('Basketball Video', 'https://basketball-video.com/college-basketball', category)]
    if league == 'nfl': return [('NFL Video', 'https://nfl-video.com/', 'nfl')]
    if league == 'ncaaf': return [('NFL Video', 'https://nfl-video.com/cfb/', 'college_football')]
    if league in ('mlb', 'nhl'):
        return [('WatchReplay', f'https://watchreplay.net/category/{league}/', league)]
    if league in ('mls','epl','laliga','bundesliga','seriea','ligue1','ligamx','ucl'):
        return [('FullMatchShows', 'https://fullmatchshows.com/', 'soccer')]
    return []


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.current = None; self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = [dict(attrs).get('href', ''), '']

    def handle_data(self, data):
        if self.current is not None: self.current[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.links.append(self.current); self.current = None


def listing(spec, query=''):
    name, base, category = spec
    parts = urllib.parse.urlsplit(base)
    origin = f'{parts.scheme}://{parts.netloc}'
    search_url = None
    if query and parts.hostname in ('basketball-video.com', 'nfl-video.com'):
        search_url = origin + '/search/?' + urllib.parse.urlencode({'q':query})
    elif query and parts.hostname in ('fullmatchshows.com', 'watchreplay.net'):
        search_url = origin + '/?' + urllib.parse.urlencode({'s':query})
    try:
        page, _ = providers.fetch_html(search_url or base, timeout=12)
    except (OSError, ValueError):
        if not search_url: raise
        # Some sites disable search to automated clients; recent listings remain useful.
        page, _ = providers.fetch_html(base, timeout=12)
    parser = _Links(); parser.feed(page)
    results, seen = [], set()
    for href, title in parser.links:
        title = ' '.join(title.split())
        if not re.search(r'\bvs\b|\bversus\b', title, re.I): continue
        if category == 'nba' and ('wnba' in title.lower() or not re.search(r'\bnba\b', title, re.I)): continue
        if category == 'wnba' and not re.search(r'\bwnba\b', title, re.I): continue
        if category.startswith('college_basketball'):
            if not re.search(r'\b(?:college|ncaa)\b', title, re.I): continue
            women = bool(re.search(r"\b(?:women|women's|womens)\b", title, re.I))
            if women != (category == 'college_basketball_w'): continue
        if category == 'nfl' and not re.search(r'\bnfl\b', title, re.I): continue
        if category == 'college_football' and not re.search(r'\b(?:college|ncaa)\b', title, re.I): continue
        if query and query.casefold() not in title.casefold(): continue
        url = urllib.parse.urljoin(base, href)
        if not providers.public_url(url) or urllib.parse.urlsplit(url).hostname != parts.hostname: continue
        if url in seen: continue
        seen.add(url); results.append(Replay(name, title, url))
        if len(results) == 80: break
    return results


PAGED_HOSTS = ('basketballreplays.net', 'basketball-video.com', 'nfl-video.com')
RECENT_PAGES = 4  # about a week of NBA games across both archives


def page_spec(spec, number):
    """The catalog's Nth listing page, or None when the site has no numbered pages."""
    name, base, category = spec
    if number == 1: return spec
    if urllib.parse.urlsplit(base).hostname not in PAGED_HOSTS: return None
    separator = '&' if urllib.parse.urlsplit(base).query else '?'
    return (name, f'{base}{separator}page{number}', category)


def recent(spec, pages=RECENT_PAGES):
    """The first listing pages fetched together; one page holds only a night or two of games."""
    specs = [page for page in (page_spec(spec, n) for n in range(1, pages+1)) if page]
    with concurrent.futures.ThreadPoolExecutor(len(specs)) as pool:
        futures = [pool.submit(listing, page) for page in specs]
        first = futures[0].result()  # the first page's failure is the catalog's failure
        later = []
        for future in futures[1:]:
            try: later += future.result()
            except (OSError, ValueError): pass
    seen = set()
    return [item for item in first + later if not (item.url in seen or seen.add(item.url))]


class _Servers(HTMLParser):
    def __init__(self, page_url):
        super().__init__(); self.page_url = page_url; self.servers = []; self.heading = ''; self.anchor = None

    def add(self, label, target):
        url = urllib.parse.urljoin(self.page_url, target or '')
        if not providers.public_url(url): return
        hostname = urllib.parse.urlsplit(url).hostname or ''
        if hostname in ('youtube.com', 'www.youtube.com'): return
        self.servers.append(ReplayServer(f'{label or "Server"} | {hostname}', url, self.page_url))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'iframe' and attrs.get('src'):
            self.add(self.heading or f'Server {len(self.servers)+1}', attrs['src'])
        if tag == 'a' and set((attrs.get('class') or '').split()) & {'myButton','su-button'}:
            self.anchor = [attrs.get('href',''), '']

    def handle_data(self, data):
        text = ' '.join(data.split())
        if re.search(r'\bserver\s*#?\s*\d+', text, re.I): self.heading = text[:70]
        if self.anchor is not None: self.anchor[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor is not None:
            target, text = self.anchor; text = ' '.join(text.split())
            self.add(self.heading if text.lower() == 'watch' else text, target)
            self.anchor = None


def servers(item):
    page, final = providers.fetch_html(item.url, timeout=15)
    parser = _Servers(final); parser.feed(page)
    seen = set()
    return [server for server in parser.servers[:40]
            if not (server.url in seen or seen.add(server.url))]
