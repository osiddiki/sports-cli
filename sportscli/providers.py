"""Discover event listings and resolve selected public player pages without a browser."""
from __future__ import annotations
import html
import ipaddress
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36'
NBABITE = 'https://nbabite.st'
MONSTER = 'https://nbamonster.com'
SF_ROOT = 'https://streamfree.top'
ROXIE = 'https://roxiestreams.su'
ROXIE_PAGES = {'nba':'nba', 'nhl':'nhl', 'mlb':'mlb', 'nfl':'nfl', 'ncaaf':'nfl', 'mls':'soccer', 'epl':'soccer',
               'laliga':'soccer', 'bundesliga':'soccer', 'seriea':'soccer', 'ligue1':'soccer', 'ligamx':'soccer', 'ucl':'soccer'}


def public_url(url, any_port=False):
    """A public http(s) URL. Pages must use the standard ports; media streams on a public
    host may use any port (free playlists often serve on 8080 and the like)."""
    try:
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password: return False
        name = u.hostname.lower().rstrip('.')
        if name in ('localhost','metadata.google.internal') or name.endswith(('.localhost','.local','.internal')): return False
        if not any_port and u.port is not None and u.port not in (80, 443): return False
        try:
            if not ipaddress.ip_address(name).is_global: return False
        except ValueError:
            if '.' not in name: return False
        return True
    except ValueError: return False


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_url(newurl): raise ValueError('private redirect rejected')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_html(url, referer=None, limit=1_500_000, timeout=15):
    if not public_url(url): raise ValueError('invalid public URL')
    headers = {'User-Agent':UA, 'Accept':'text/html,application/xhtml+xml,*/*'}
    if referer: headers['Referer'] = referer
    opener = urllib.request.build_opener(PublicRedirect())
    with opener.open(urllib.request.Request(url,headers=headers),timeout=timeout) as r:
        data = r.read(limit+1); final = r.url
    if len(data) > limit: raise ValueError('page too large')
    return data.decode('utf8','replace'), final


def vidara_stream(page, player_url, *, timeout=15):
    """Use the replay player's own JSON endpoint when its page advertises it."""
    parts = urllib.parse.urlsplit(player_url)
    match = re.fullmatch(r'/e/([A-Za-z0-9_-]+)', parts.path)
    if not match or '/api/stream' not in page or 'streaming_url' not in page:
        return None
    api = urllib.parse.urljoin(player_url, '/api/stream')
    if not public_url(api):
        raise ValueError('invalid player API URL')
    origin = f'{parts.scheme}://{parts.netloc}'
    payload = json.dumps({'filecode':match.group(1), 'device':'web', 'codecs':[]}).encode()
    req = urllib.request.Request(api, data=payload, headers={'User-Agent':UA,
        'Content-Type':'application/json', 'Accept':'application/json', 'Referer':player_url, 'Origin':origin})
    with urllib.request.build_opener(PublicRedirect()).open(req, timeout=timeout) as response:
        raw = response.read(500_001)
    if len(raw) > 500_000: raise ValueError('player API response too large')
    media = json.loads(raw).get('streaming_url', '')
    if not public_url(media): raise ValueError('player did not return a public media URL')
    return {'kind':'direct', 'url':media, 'referer':player_url}


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.card = None; self.cards = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'a' and 'card' in a.get('class','').split():
            self.card = {'href':a.get('href',''), 'text':'', 'start':''}
        elif self.card is not None and a.get('data-cd'):
            self.card['start'] = a['data-cd']
    def handle_data(self, data):
        if self.card is not None: self.card['text'] += ' ' + data
    def handle_endtag(self, tag):
        if tag == 'a' and self.card is not None:
            self.cards.append(self.card); self.card = None


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.row = None; self.cell = None; self.rows = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'tr': self.row = []
        elif tag == 'td' and self.row is not None:
            self.cell = {'class':a.get('class',''), 'text':'', 'href':''}
        elif tag == 'a' and self.cell is not None and not self.cell['href']:
            self.cell['href'] = a.get('href','')
    def handle_data(self, data):
        if self.cell is not None: self.cell['text'] += data
    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.row.append(self.cell); self.cell = None
        elif tag == 'tr' and self.row is not None:
            if any('who' in x['class'].split() for x in self.row): self.rows.append(self.row)
            self.row = None


def nbabite_entries(game, title_matches):
    page, _ = fetch_html(NBABITE+'/')
    cards = CardParser(); cards.feed(page)
    found=[]
    for card in cards.cards:
        try: delta=abs(int(card['start'])-game.start.timestamp())
        except (TypeError, ValueError): continue
        if delta <= 6*3600 and title_matches(card['text'],game): found.append((delta,card['href']))
    if not found: return []
    _, path = min(found)
    event_url = urllib.parse.urljoin(NBABITE+'/',path)
    if urllib.parse.urlsplit(event_url).hostname != 'nbabite.st': return []
    page, _ = fetch_html(event_url, NBABITE+'/')
    parser=TableParser(); parser.feed(page)
    entries=[]
    for row in parser.rows[:60]:
        if len(row)<7: continue
        provider=' '.join(row[1]['text'].split())
        url=row[1]['href']; quality=' '.join(row[3]['text'].split()); language=' '.join(row[4]['text'].split())
        channel=' '.join(row[6]['text'].split())
        if not provider or not public_url(url): continue
        # Some directory entries point at yesterday's different matchup.
        path_words=urllib.parse.unquote(urllib.parse.urlsplit(url).path).replace('-',' ')
        if re.search(r'\bvs\b',path_words,re.I) and not title_matches(path_words,game): continue
        entries.append({'name':provider,'quality':quality,'language':language,'channel':channel,'url':url})
    return entries


def roxie_entries(game, title_matches, *, max_domains=2):
    """Plain HLS from RoxieStreams: its league page lists events, each event page names the
    playlist (`getRandomStream('nba6.m3u8','admin2')`) and a text file lists the hosts serving it."""
    section = ROXIE_PAGES.get(game.league)
    if not section: return []
    page, _ = fetch_html(f'{ROXIE}/{section}', timeout=8)
    links = re.findall(r'<a\s+href="(/[a-z]+-streams-\d+)"[^>]*>\s*([^<]+?)\s*</a>', page)
    path = next((href for href, title in links if title_matches(html.unescape(title), game)), None)
    if not path: return []
    event, _ = fetch_html(ROXIE+path, ROXIE+'/', timeout=8)
    streams = re.findall(r'getRandomStream\(\s*\'([\w.-]+\.m3u8)\'(?:\s*,\s*\'(\w+)\')?\s*\)\)"[^>]*>\s*([^<]*?)\s*</button>', event)
    if not streams: return []
    default = re.search(r"var\s+subdomain\s*=\s*'(\w+)'", event)
    names = re.search(r"fetch\('(domains[\w.-]*\.txt)'\)", event)
    listing, _ = fetch_html(f'{ROXIE}/{names.group(1) if names else "domainsz77.txt"}', ROXIE+'/', limit=20_000, timeout=8)
    hosts = [h for h in listing.split() if re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', h)][:max_domains]
    entries = []
    for number, (playlist, sub, label) in enumerate(streams[:6], 1):
        sub = sub or (default.group(1) if default else 'admin2')
        for mirror, host in enumerate(hosts, 1):
            url = f'https://{sub}.{host}/{playlist}'
            if not public_url(url): continue
            name = label or f'Stream {number}'
            entries.append({'name':name if mirror == 1 else f'{name} | mirror {mirror}', 'url':url})
    return entries


def monster_entries(game):
    page, _ = fetch_html(MONSTER+'/')
    pattern=r'href=["\'](/matches/[^"\']*?-'+re.escape(game.id)+r')["\']'
    match=re.search(pattern,page,re.I)
    if not match: return []
    event_url=urllib.parse.urljoin(MONSTER,match.group(1))
    page, _=fetch_html(event_url,MONSTER+'/')
    pattern=r'<iframe\s+[^>]*src=["\'](https://piratecat\.store/smartplayer\.php\?[^"\']+)["\']'
    urls=list(dict.fromkeys(html.unescape(u) for u in re.findall(pattern,page,re.I)))
    return [{'name':f'Server {i}', 'url':u} for i,u in enumerate(urls[:20],1)]


class EmbedParser(HTMLParser):
    def __init__(self): super().__init__(); self.urls=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag in ('iframe','video','source'):
            u=a.get('src') or a.get('data-src')
            if u: self.urls.append(u)
        if a.get('data-embed-url'): self.urls.append(a['data-embed-url'])
        if tag == 'script' and a.get('src','').startswith('https://watchfa.st/player-js/player.js'):
            ident = a.get('data-id','')
            if re.fullmatch(r'\d{1,3}',ident): self.urls.append('https://watchfa.st/player-js/frame.php?id='+ident)


def extract_player(start_url, max_pages=8, referer=None, *, timeout=15):
    """Follow player iframes and return a supported media source. No scripts are executed."""
    queue=[(start_url, referer or NBABITE+'/')]; seen=set(); errors=[]
    while queue and len(seen)<max_pages:
        url, referer=queue.pop(0)
        if url in seen or not public_url(url): continue
        seen.add(url)
        host=urllib.parse.urlsplit(url).hostname or ''
        path=urllib.parse.urlsplit(url).path
        sf_match=re.fullmatch(r'/embed/(basketball|football|soccer|baseball|hockey)/([^/]+)',path)
        if host in ('streamfree.top','strmfree.st') and sf_match:
            category,variant=sf_match.groups()
            match=re.fullmatch(r'(.+?)(\d{3,4}p)(\d*)',variant)
            if match:
                key,quality,_=match.groups()
                return {'kind':'sf','url':SF_ROOT+'/embed/'+category+'/'+variant,'key':key,'quality':quality,'variant':variant,'category':category}
        if host in ('taifood-blog.asia','embedindia.st') and '/embed/' in url:
            return {'kind':'ppv','url':url}
        try: page, final = fetch_html(url,referer,timeout=timeout)
        except (OSError,ValueError) as exc:
            errors.append(type(exc).__name__); continue
        try:
            replay = vidara_stream(page, final, timeout=timeout)
            if replay: return replay
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(type(exc).__name__)
        # This player's playlists expire quickly and segments are wrapped in images.
        src=re.search(r'\bconst\s+SRC\s*=\s*["\']([^"\']+)["\']',page)
        edge=re.search(r'\bconst\s+EDGE\s*=\s*["\']([^"\']+)["\']',page)
        channel=re.search(r'\bconst\s+CHANNEL\s*=\s*["\']([\w-]+)["\']',page)
        if src and edge and channel and public_url(src.group(1)) and public_url(edge.group(1)) and '/api/playlist-token' in page:
            return {'kind':'relay','url':src.group(1),'edge':edge.group(1),'channel':channel.group(1),'referer':final}
        cleaned=html.unescape(page.replace('\\/','/'))
        # JSON player options escape '&' as \u0026; keep the query intact.
        cleaned=re.sub(r'\\u00([0-9a-fA-F]{2})',lambda m: chr(int(m.group(1),16)),cleaned)
        for m in re.finditer(r'unescape\(["\']([^"\']+)["\']\)',cleaned):
            cleaned += '\n'+urllib.parse.unquote(m.group(1))
        for m in re.finditer(r'(?:(?:https?:)?//)[a-zA-Z0-9][^\s"\'<>\\]*?\.m3u8(?:\?[^\s"\'<>\\]*)?',cleaned,re.I):
            candidate=urllib.parse.urljoin(final,m.group(0))
            if public_url(candidate):
                return {'kind':'direct','url':candidate,'referer':final}
        parser=EmbedParser(); parser.feed(page)
        for child in parser.urls[:12]:
            target=urllib.parse.urljoin(final, html.unescape(child))
            if public_url(target) and urllib.parse.urlsplit(target).hostname not in ('www.youtube.com','youtube.com'):
                queue.append((target,final))
    detail = ', '.join(sorted(set(errors))) if errors else 'unsupported or unavailable player'
    raise ValueError('Could not extract a playable stream ('+detail+')')
