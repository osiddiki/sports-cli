"""Local, session-scoped HLS relay for players that serve image-wrapped segments."""
from __future__ import annotations
import concurrent.futures
import gzip
import hashlib
import http.client
import http.server
import io
import json
import secrets
import sys
import socket
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.error
import zlib

from . import merge

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36'
MAX_SEGMENT = 24 * 1024 * 1024


class _Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # Players drop kept-alive connections when they quit; that is not an error worth printing.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)): return
        super().handle_error(request, client_address)


class _Pool:
    """Keep-alive HTTP connections, so each segment skips a fresh TCP and TLS handshake."""
    def __init__(self, per_host=4):
        self.idle = {}; self.lock = threading.Lock(); self.per_host = per_host
        self.context = None

    def _context(self):
        if self.context is None:
            with self.lock:
                if self.context is None:
                    self.context = ssl.create_default_context()
        return self.context

    def _take(self, key):
        with self.lock:
            connections = self.idle.get(key)
            return connections.pop() if connections else None

    def _give(self, key, connection):
        with self.lock:
            connections = self.idle.setdefault(key, [])
            if len(connections) < self.per_host: connections.append(connection); return
        connection.close()

    def request(self, url, headers, timeout, limit):
        for _ in range(6):
            parts = urllib.parse.urlsplit(url)
            if parts.scheme not in ('http', 'https') or not parts.hostname: raise ValueError('unsupported URL')
            key = (parts.scheme, parts.hostname, parts.port)
            target = (parts.path or '/') + ('?' + parts.query if parts.query else '')
            fresh = False
            while True:
                connection = None if fresh else self._take(key)
                if connection is None:
                    fresh = True
                    if parts.scheme == 'https':
                        connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=timeout, context=self._context())
                    else:
                        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
                else:
                    connection.timeout = timeout
                    if connection.sock: connection.sock.settimeout(timeout)
                try:
                    connection.request('GET', target, headers=headers)
                    response = connection.getresponse()
                    data = response.read(limit + 1)
                except (http.client.HTTPException, OSError) as exc:
                    connection.close()
                    # An idle keep-alive connection may have been closed by the server.
                    if not fresh: fresh = True; continue
                    if isinstance(exc, (TimeoutError, socket.timeout)): raise TimeoutError(str(exc)) from exc
                    raise urllib.error.URLError(exc) from exc
                break
            if len(data) > limit or response.will_close: connection.close()
            else: self._give(key, connection)
            location = response.getheader('Location')
            if response.status in (301, 302, 303, 307, 308) and location:
                url = urllib.parse.urljoin(url, location); continue
            if response.status >= 400:
                raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, io.BytesIO(data))
            return data
        raise urllib.error.URLError('too many redirects')

    def close(self):
        with self.lock:
            connections = [c for group in self.idle.values() for c in group]; self.idle.clear()
        for connection in connections: connection.close()


_POOL = _Pool()


def _request(url, headers, timeout, limit):
    return _POOL.request(url, headers, timeout, limit)


def _download(url, referer=None, timeout=15, limit=MAX_SEGMENT, *, headers=None, attempts=3):
    if headers is None:
        origin = urllib.parse.urlsplit(referer)
        headers = {'User-Agent': UA, 'Referer': referer, 'Origin': f'{origin.scheme}://{origin.netloc}', 'Accept':'*/*'}
    else:
        headers = {'User-Agent': UA, 'Accept': '*/*', **headers}
    for attempt in range(attempts):
        try:
            data = _request(url, headers, timeout, limit)
            break
        except urllib.error.HTTPError as exc:
            # A rejected playlist token is handled separately by Relay.get.
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
        time.sleep(.2 * (attempt + 1))
    if len(data) > limit: raise ValueError('response too large')
    return data


def _limited_gzip(data):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
        out = g.read(MAX_SEGMENT + 1)
    if len(out) > MAX_SEGMENT: raise ValueError('segment too large')
    return out


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def unwrap_segment(data):
    if data[:1] == b'\x47': return data
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        pos = 12
        while pos + 8 <= len(data):
            tag = data[pos:pos+4]; n = struct.unpack_from('<I', data, pos+4)[0]; pos += 8
            if pos + n > len(data): break
            if tag == b'EXIF' and data[pos:pos+1] == b'\x47': return data[pos:pos+n]
            pos += n + (n & 1)
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        pos, chunks, width, height, ctype = 8, [], 0, 0, 0
        while pos + 12 <= len(data):
            n = struct.unpack_from('>I', data, pos)[0]; tag = data[pos+4:pos+8]
            if n > len(data) - pos - 12: raise ValueError('invalid PNG')
            payload = data[pos+8:pos+8+n]; pos += n+12
            if tag == b'IHDR':
                width, height, depth, ctype, _, _, interlace = struct.unpack('>IIBBBBB', payload)
                if depth != 8 or ctype not in (2, 6) or interlace or width*height > 6_000_000:
                    raise ValueError('unsupported PNG format')
            elif tag == b'IDAT': chunks.append(payload)
            elif tag == b'tsDa':
                # StreamFree stores unmodified MPEG transport packets in a PNG chunk.
                if len(payload) < 377 or len(payload) % 188 or any(payload[i] != 0x47 for i in (0, 188, 376)):
                    raise ValueError('invalid transport stream in PNG')
                return payload
            elif tag == b'IEND':
                tail = data[pos:]
                if len(tail) > 188 and tail[0] == 0x47 and tail[188] == 0x47: return tail
                break
        if not width or not height or not chunks: raise ValueError('incomplete PNG')
        bpp = 4 if ctype == 6 else 3; stride = width*bpp
        if (stride+1)*height > MAX_SEGMENT: raise ValueError('PNG too large')
        decomp = zlib.decompressobj()
        raw = decomp.decompress(b''.join(chunks), (stride+1)*height+1)
        if len(raw) != (stride+1)*height: raise ValueError('invalid PNG data')
        rgb = bytearray(); prev = bytearray(stride); pos = 0
        for _ in range(height):
            filt = raw[pos]; pos += 1; row = bytearray(raw[pos:pos+stride]); pos += stride
            if filt:
                for i, val in enumerate(row):
                    a = row[i-bpp] if i >= bpp else 0
                    b = prev[i]
                    c = prev[i-bpp] if i >= bpp else 0
                    if filt == 1: val += a
                    elif filt == 2: val += b
                    elif filt == 3: val += (a+b)//2
                    elif filt == 4: val += _paeth(a,b,c)
                    else: raise ValueError('invalid PNG filter')
                    row[i] = val & 255
            if bpp == 3: rgb.extend(row)
            else:
                for i in range(0, len(row), 4): rgb.extend(row[i:i+3])
            prev = row
        if not rgb.startswith(b'TIKTIKPX') or len(rgb) < 12: raise ValueError('PNG has no transport stream')
        n = struct.unpack_from('>I', rgb, 8)[0]
        if n < 1 or n > len(rgb)-12: raise ValueError('invalid image payload length')
        data = _limited_gzip(bytes(rgb[12:12+n]))
    else:
        i = data.find(b'TIKTIKRAW')
        if i >= 0: data = data[i+9:]
        else:
            i = data.find(b'TIKTIKTSGZ')
            if i >= 0: data = _limited_gzip(data[i+10:])
    if len(data) < 189 or data[0] != 0x47 or data[188] != 0x47:
        raise ValueError('invalid transport stream')
    return data


def _media(data):
    """Unwrap image-wrapped transport streams; pass other media through unchanged."""
    if data[:1] == b'\x47': return data
    if data.lstrip()[:1] == b'<': raise ValueError('received a web page instead of media')
    try: return unwrap_segment(data)
    except ValueError:
        if data.startswith((b'\x89PNG', b'RIFF')): raise
        return data


class Merged:
    """Download live segments ahead of the player and fail over between equivalent sources."""
    IDLE = 45          # stop downloading when the player has not asked for a playlist
    SAME_CLOCK = 90    # transport-stream timestamps this close are one encoder's clock, which realigns exactly
    CACHE_BYTES = 192 * 1024 * 1024
    RETURN_AFTER = 30  # seconds the chosen source must stay healthy before playback returns to it
    LAG_MEMORY = 300   # seconds a source left for being too slow is not switched back to for speed

    def __init__(self, relay, sid, sources):
        self.relay, self.sid = relay, sid
        # Each source: page URL, headers, resolved media playlist URL, last playlist, health.
        self.sources = [{'url':url, 'headers':headers or {}, 'media':None, 'playlist':None,
                         'advanced':time.monotonic(), 'newest':None, 'failures':0,
                         'pace':[], 'lagging':None, 'up':None} for url, headers in sources]
        self.active = 0
        self.timeline = merge.Timeline()
        self.cache = {}; self.cache_bytes = 0
        self.lock = threading.Condition()
        self.stopped = threading.Event()
        self.requested = time.monotonic()
        self.thread = None
        self.error = None
        self.switches = 0
        self.failed = {}
        self.loads = {}  # source index -> pending playlist load
        self.exact = set()  # sources whose timestamps matched the chosen source's, so switching back is exact
        self.kept = list(range(len(sources)))  # which of the offered sources survived prepare, in order
        self.loader = concurrent.futures.ThreadPoolExecutor(max_workers=len(sources))

    def fetch(self, url, headers, timeout, limit=MAX_SEGMENT, attempts=2):
        if url.startswith(self.relay.base + '/'):
            # Wrapped and tokenized sources already live in this relay.
            return self.relay.get(url[len(self.relay.base):])[0]
        return _download(url, headers=headers, timeout=timeout, limit=limit, attempts=attempts)

    def load(self, index):
        source = self.sources[index]
        timeout = 6 if source['playlist'] is None else max(3, min(8, source['playlist'].target))
        for _ in range(2):
            url = source['media'] or source['url']
            text = self.fetch(url, source['headers'], timeout, limit=1_000_000).decode('utf8', 'replace')
            variant = merge.variant_url(text, url)
            if variant is None:
                source['media'] = url
                playlist = merge.parse_media(text, url)
                break
            source['media'] = variant
        else:
            raise merge.Unsupported('nested master playlists')
        if playlist.endlist: raise merge.Unsupported('not a live playlist')
        newest = playlist.segments[-1].seq if playlist.segments else None
        if newest != source['newest']: source['advanced'] = time.monotonic(); source['newest'] = newest
        source['playlist'] = playlist; source['failures'] = 0
        return playlist

    def healthy(self, index):
        source = self.sources[index]; playlist = source['playlist']
        if playlist is None or source['failures'] >= 2: return False
        # Fail over before the player's buffer runs out; a healthy backup makes switching cheap.
        return time.monotonic() - source['advanced'] < max(3, 1.5 * playlist.target + .5)

    def prepare(self):
        """Check each source once; return how many can be merged. The primary must work."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.sources)) as pool:
            results = list(pool.map(lambda i: self._try_load(i), range(len(self.sources))))
        if not results[0]: return 0
        usable = [i for i, ok in enumerate(results) if ok]
        if len(usable) > 2: usable = [0, self.best_backup(usable)]
        self.sources, self.kept = [self.sources[i] for i in usable], usable
        return len(self.sources)

    def best_backup(self, usable):
        """Of several working backups, the first that lines up with the primary, else the first.

        Sources from different encoders share no clock or segment names, so switching to them replays
        or skips seconds; one that lines up resumes exactly where the primary stopped."""
        return next((i for i in usable[1:] if self.lines_up(self.sources[0], self.sources[i])), usable[1])

    def lines_up(self, main, other):
        a, b = main['playlist'].segments, other['playlist'].segments
        if not a or not b: return False
        if {s.name for s in a} & {s.name for s in b}: return True
        if a[-1].pdt is not None and b[-1].pdt is not None: return abs(a[-1].pdt - b[-1].pdt) <= 30
        try:
            clocks = [merge.first_pts(_media(self.fetch(source['playlist'].segments[-1].url, source['headers'], 15)))
                      for source in (main, other)]
        except (OSError, ValueError, urllib.error.URLError): return False
        return None not in clocks and abs(merge.pts_seconds(*clocks)) <= self.SAME_CLOCK

    def _try_load(self, index):
        try: self.load(index); return True
        except (OSError, ValueError, UnicodeError, urllib.error.URLError): return False

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive(): return
            self.requested = time.monotonic(); self.error = None
            self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()

    def stop(self): self.stopped.set(); self.loader.shutdown(wait=False)

    def switch(self, reason):
        now = time.monotonic()
        if reason == 'falling behind': self.sources[self.active]['lagging'] = now
        for offset in range(1, len(self.sources)):
            candidate = (self.active + offset) % len(self.sources)
            # A slow source still beats a stalled one, but leaving one slow source for another gains nothing.
            if self.healthy(candidate) and not (reason == 'falling behind' and self.lagged(candidate, now)):
                self.active = candidate; self.switches += 1; self.sources[candidate]['pace'] = []
                return True
        return False

    def lagged(self, index, now):
        lagging = self.sources[index]['lagging']
        return lagging is not None and now - lagging < self.LAG_MEMORY

    def steer(self):
        """Leave a stalled source or one slower than real time; return to the chosen source once it is steady again."""
        now = time.monotonic()
        chosen, source = self.sources[0], self.sources[self.active]
        chosen['up'] = (chosen['up'] or now) if self.healthy(0) else None
        if not self.healthy(self.active):
            self.switch('stalled')
        elif len(source['pace']) == 2 and min(source['pace']) > 1:
            # Its playlist advances, so it looks healthy, but the player's buffer drains while it downloads.
            self.switch('falling behind')
        elif (self.active != 0 and self.active in self.exact and chosen['up'] is not None
              and now - chosen['up'] >= self.RETURN_AFTER and not self.lagged(0, now)):
            self.active = 0; self.switches += 1; chosen['pace'] = []

    def run(self):
        while not self.stopped.is_set():
            if time.monotonic() - self.requested > self.IDLE: return
            started = time.monotonic()
            self.refresh()
            self.steer()
            progressed = self.pump()
            target = (self.sources[self.active]['playlist'] or merge.Playlist(4, [], False)).target
            if not progressed:
                self.stopped.wait(max(.5, min(3, target / 2) - (time.monotonic() - started)))

    def refresh(self):
        """Reload every playlist at once, waiting only for the active one; a hung backup must not hold up playback."""
        for index in range(len(self.sources)):
            if index not in self.loads or self.loads[index].done():
                self.loads[index] = self.loader.submit(self.load, index)
        playlist = self.sources[self.active]['playlist']
        # Waiting longer than the stall window would leave the backups' last updates looking stale too.
        concurrent.futures.wait([self.loads[self.active]], timeout=max(3, 1.5 * playlist.target + .5) if playlist else 8)
        for index, future in list(self.loads.items()):
            if not future.done(): continue
            del self.loads[index]
            try:
                playlist = future.result()
                if index == self.timeline.last_source: self.timeline.observe(index, playlist)
            except (OSError, ValueError, UnicodeError, urllib.error.URLError):
                self.sources[index]['failures'] += 1

    def alternates(self, segment):
        for index, source in enumerate(self.sources):
            if index == self.active or not source['playlist']: continue
            for other in source['playlist'].segments:
                if other.name == segment.name: yield source, other.url

    def align(self, source, segments, plan):
        """Use transport-stream timestamps to resume exactly when both sources share a clock."""
        first = plan[0][0]
        try: data = _media(self.fetch(first.url, source['headers'], max(4, min(15, first.duration * 1.5))))
        except (OSError, ValueError, urllib.error.URLError): return plan, {}
        measured, expected = merge.first_pts(data), self.timeline.end_pts
        # Unrelated encoders have unrelated clocks; keep the live-edge estimate then.
        if measured is None or abs(merge.pts_seconds(expected, measured)) > 90: return plan, {first.url: data}
        self.exact.add(self.sources.index(source))
        position = segments.index(first)
        best = merge.realign(segments, position, measured, expected)
        if best >= len(segments): return [], {}
        aligned = [(segment, i == 0 or segment.discontinuity) for i, segment in enumerate(segments[best:])]
        return aligned, ({first.url: data} if best == position else {})

    def pump(self):
        source = self.sources[self.active]
        if not source['playlist']: return False
        segments = source['playlist'].segments
        plan = self.timeline.plan(self.active, source['playlist'])
        prefetched = {}
        if self.timeline.needs_alignment(self.active, plan):
            plan, prefetched = self.align(source, segments, plan)
        for segment, discontinuity in plan[:3]:
            timeout = max(4, min(15, segment.duration * 1.5))
            data = prefetched.pop(segment.url, None)
            owner, started = source, time.monotonic()
            candidates = [] if data else [(source, segment.url), *self.alternates(segment)]
            for owner, url in candidates:
                try:
                    # One try on the active source when a mirror can serve the same segment.
                    attempts = 1 if owner is source and len(candidates) > 1 else 2
                    data = _media(self.fetch(url, owner['headers'], timeout, attempts=attempts))
                    break
                except (OSError, ValueError, urllib.error.URLError):
                    continue
            if candidates and owner is source and data is not None:
                source['pace'] = (source['pace'] + [(time.monotonic() - started) / max(.1, segment.duration)])[-2:]
            if data is None:
                source['failures'] += 1
                if self.switch('segment failed'): return True
                attempts = self.failed.get(segment.url, 0) + 1
                self.failed = {segment.url: attempts}
                if attempts >= 2:
                    # Nowhere else to get it: drop one segment rather than freeze live playback.
                    with self.lock: self.timeline.skip(self.active, segment)
                    return True
                return False
            key = f'{self.timeline.next_seq}'
            with self.lock:
                self.timeline.commit(self.active, segment, discontinuity, key, merge.first_pts(data))
                self.cache[key] = data; self.cache_bytes += len(data)
                self.failed = {}
                keep = self.timeline.keys()
                for old in [k for k in self.cache if k not in keep]:
                    self.cache_bytes -= len(self.cache.pop(old))
                while self.cache_bytes > self.CACHE_BYTES and len(self.cache) > 1:
                    self.cache_bytes -= len(self.cache.pop(next(iter(self.cache))))
                self.lock.notify_all()
            if owner is not source:
                # The active source's playlist still advances but its segments fail; follow the mirror that served this one.
                self.active = self.sources.index(owner); self.switches += 1
                return True
        return bool(plan)

    def playlist(self):
        self.requested = time.monotonic(); self.start()
        deadline = time.monotonic() + 20
        with self.lock:
            # Give the first request a short buffer instead of a single segment.
            while len(self.timeline.entries) < min(3, self.timeline.start) and time.monotonic() < deadline:
                if self.timeline.entries and time.monotonic() > deadline - 12: break
                self.lock.wait(.25)
            return self.timeline.render(lambda key: f'{self.relay.base}/m/{self.sid}/{key}.ts').encode()

    def segment(self, key):
        self.requested = time.monotonic()
        with self.lock:
            data = self.cache.get(key)
        if data is None: raise ValueError('segment expired')
        return data


class Relay:
    def __init__(self):
        self.sessions = {}; self.resources = {}; self.merged = {}; self.lock = threading.Lock()
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_GET(self):
                try:
                    content, kind = owner.get(self.path)
                    self.send_response(200); self.send_header('Content-Type', kind)
                    self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(content)))
                    self.end_headers(); self.wfile.write(content)
                except Exception as exc:
                    data = str(exc).encode('utf8','replace')[:300]
                    try:
                        self.send_response(502); self.send_header('Content-Type','text/plain')
                        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError): pass
        self.http = _Server(('127.0.0.1',0),Handler)
        self.http.daemon_threads = True
        self.base = f'http://127.0.0.1:{self.http.server_port}'
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()

    def add(self, url, edge, channel, referer):
        sid = secrets.token_urlsafe(18)
        with self.lock:
            self.sessions[sid] = {'url':url, 'edge':edge, 'channel':channel, 'referer':referer,
                                  'token':'', 'expires':0, 'token_lock':threading.Lock(),
                                  'playlist_lock':threading.Lock(), 'cached_playlist':None,
                                  'cached_until':0}
        return self.base + '/playlist/' + sid + '.m3u8'

    def add_streamfree(self, url, referer, renew, initial_playlist=None):
        sid = secrets.token_urlsafe(18)
        with self.lock:
            self.sessions[sid] = {'url':url, 'referer':referer, 'renew':renew,
                                  'playlist_lock':threading.Lock(), 'cached_playlist':None,
                                  'cached_until':0, 'kind':'streamfree', 'initial_playlist':initial_playlist}
        return self.base + '/playlist/' + sid + '.m3u8'

    def playlist_token(self, session):
        with session['token_lock']:
            if time.time() < session['expires']: return session['token']
            url = session['edge'] + '/api/playlist-token?' + urllib.parse.urlencode({'slug':session['channel']})
            data = json.loads(_download(url, session['referer'], limit=100_000))
            token = data.get('token')
            if not token: raise ValueError('playlist token unavailable')
            session['token'] = str(token); session['expires'] = time.time() + max(5, min(50, int(data.get('ttl') or 120)//2))
            return session['token']

    def get_playlist(self, sid, session):
        # Players may open the same playlist several times at once. Serializing
        # refreshes prevents one rejected token from invalidating a newer token
        # that another request is already using.
        with session['playlist_lock']:
            if session['cached_playlist'] is not None and time.monotonic() < session['cached_until']:
                return session['cached_playlist'], 'application/vnd.apple.mpegurl'
            src = session['url']
            text = session.pop('initial_playlist', None)
            if text is None:
                for attempt in range(3):
                    if session.get('kind') == 'streamfree':
                        playlist_url = src
                    else:
                        token = self.playlist_token(session)
                        query = urllib.parse.urlencode({'t':token, '_':int(time.time()*1000)})
                        playlist_url = src + ('&' if '?' in src else '?') + query
                    try:
                        text = _download(playlist_url, session['referer'], limit=1_000_000).decode('utf8')
                        break
                    except urllib.error.HTTPError as exc:
                        # Refresh a rejected, short-lived provider token.
                        if exc.code != 403 or attempt == 2:
                            raise
                        if session.get('kind') == 'streamfree':
                            src, headers = session['renew']()
                            session['url'] = src
                            session['referer'] = headers.get('Referer', session['referer'])
                        else:
                            with session['token_lock']:
                                session['expires'] = 0
                        time.sleep(.2 * (attempt + 1))
            if not text.lstrip().startswith('#EXTM3U'): raise ValueError('invalid playlist')
            if '#EXT-X-KEY:' in text and 'METHOD=NONE' not in text: raise ValueError('encrypted playlist is unsupported')
            output = []
            for line in text.splitlines():
                if line and not line.startswith('#'):
                    target = urllib.parse.urljoin(src, line.strip())
                    key = hashlib.sha256((sid+target).encode()).hexdigest()[:32]
                    with self.lock:
                        if len(self.resources) > 4000: self.resources.clear()
                        self.resources[key] = (target, session['referer'])
                    output.append(self.base+'/segment/'+key+'.ts')
                else: output.append(line)
            playlist = ('\n'.join(output)+'\n').encode()
            session['cached_playlist'] = playlist
            session['cached_until'] = time.monotonic() + 1.5
            return playlist, 'application/vnd.apple.mpegurl'

    def add_merged(self, sources, minimum=2):
        """Serve live sources as one prefetched playlist; return (url, usable sources) or (None, usable)."""
        sid = secrets.token_urlsafe(18)
        session = Merged(self, sid, sources)
        usable = session.prepare()
        if usable < max(1, minimum): return None, usable
        with self.lock: self.merged[sid] = session
        session.start()
        return self.base + '/m/' + sid + '.m3u8', usable

    def kept(self, merged_url):
        """Positions, among the sources offered to add_merged, of the ones a merged stream uses."""
        session = self.merged.get(merged_url.rsplit('/', 1)[-1].removesuffix('.m3u8'))
        return session.kept if session else None

    def get(self, path):
        path = urllib.parse.urlsplit(path).path
        if path.startswith('/m/'):
            name = path[3:]
            if name.endswith('.m3u8') and '/' not in name:
                session = self.merged.get(name[:-5])
                if not session: raise ValueError('unknown stream')
                return session.playlist(), 'application/vnd.apple.mpegurl'
            sid, _, key = name.partition('/')
            session = self.merged.get(sid)
            if session and key.endswith('.ts'): return session.segment(key[:-3]), 'video/mp2t'
            raise ValueError('unknown segment')
        if path.startswith('/playlist/') and path.endswith('.m3u8'):
            sid = path[len('/playlist/'):-len('.m3u8')]
            session = self.sessions.get(sid)
            if not session: raise ValueError('unknown stream')
            return self.get_playlist(sid, session)
        if path.startswith('/segment/') and path.endswith('.ts'):
            key = path[len('/segment/'):-3]
            resource = self.resources.get(key)
            if not resource: raise ValueError('unknown segment')
            return unwrap_segment(_download(*resource)), 'video/mp2t'
        raise ValueError('unknown path')

    def close(self):
        for session in list(self.merged.values()): session.stop()
        self.http.shutdown(); self.http.server_close()
