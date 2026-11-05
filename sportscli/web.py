"""Phone-friendly web page served from this computer: browse games and TV channels, play in the browser.

Every request path starts with a secret token, so only people given the link can use it. Streams play
through /p/, which fetches each playlist and segment with the headers the source needs (a browser
cannot send Referer itself) and rewrites playlist links to come back through the same proxy.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import http.server
import json
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse

from . import app, relay, remote, tv

PORT = 8765
MIN_VISIBLE = 5
CHANNEL_TTL = 600
URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"')


class Listing:
    """The sources for one game or channel and their background check results."""
    def __init__(self, feeds, live=True):
        self.feeds, self.live = feeds, live
        self.checks = {}  # index -> ('checking' | 'OK' | 'n/a' | 'Failed' | 'HTTP n', height, speed)
        self.created = time.monotonic()

    def failed(self, index):
        status = self.checks.get(index, ('',))[0]
        return status == 'Failed' or status.startswith('HTTP ')

    def rows(self):
        def rank(index):
            status, height, speed = self.checks.get(index, ('', None, None))
            group = 0 if status == 'OK' else 2 if self.failed(index) else 1
            return group, app.smoothness(speed) if status == 'OK' else 1, -(height or 0)
        order = sorted(range(len(self.feeds)), key=rank)
        working = [i for i in order if not self.failed(i)]
        # Keep a few sources on screen even when every check failed; something beats nothing.
        shown = set(working) | set([i for i in order if self.failed(i)][:max(0, MIN_VISIBLE - len(working))])
        rows = []
        for index in order:
            feed = self.feeds[index]
            status, height, speed = self.checks.get(index, ('', None, None))
            display = app.describe_feed(feed, {'height': height, 'speed': speed}, status or None)
            rows.append({'i': index, 'name': display.name, 'side': display.side, 'quality': display.quality,
                         'status': display.check, 'detail': display.detail, 'failed': self.failed(index),
                         'hidden': index not in shown, 'ok': status == 'OK'})
        return rows


class Web:
    def __init__(self, config, token):
        self.token, self.config = token, config
        self.player = app.App(None, config)
        self.lock = threading.Lock()
        self.listings, self.proxied = {}, {}
        self.games, self.channels = {}, (0, [])
        self.checks = concurrent.futures.ThreadPoolExecutor(6)
        self.remote = None

    def screen(self):
        """The terminal app's screens for the OpenTUI front end, started on its first request."""
        with self.lock:
            if self.remote is None: self.remote = remote.Remote(self.config)
            return self.remote

    # Directories

    def leagues(self):
        return [{'id': key, 'name': value[0]} for key, value in app.LEAGUES.items()]

    def game_list(self, league):
        games = app.get_games(app.default_day(), league)
        with self.lock: self.games.update({(league, game.id): game for game in games})
        return [{'id': game.id, 'title': game.title, 'state': game.state,
                 'start': game.start.isoformat(), 'tv': ', '.join(game.broadcasts)} for game in games]

    def directory(self):
        with self.lock: loaded, channels = self.channels
        if not channels or time.monotonic() - loaded > CHANNEL_TTL:
            channels, _ = tv.directory()
            with self.lock: self.channels = (time.monotonic(), channels)
        return channels

    def channel_list(self):
        return [{'name': channel.name, 'featured': channel.featured, 'count': len(channel.sources)}
                for channel in self.directory()]

    def listing(self, query):
        if 'channel' in query:
            name = 'c:' + query['channel']
        else:
            name = f"g:{query['league']}:{query['game']}"
        with self.lock: found = self.listings.get(name)
        if found and (query.get('refresh') != '1'): return found
        if name.startswith('c:'):
            channel = next((item for item in self.directory() if item.name == query['channel']), None)
            if channel is None: raise KeyError('channel not found; reload the channel list')
            feeds = [app.Feed(source.provider, source.label, url=source.url, kind=source.kind,
                              headers=dict(source.headers), metadata={'referer': source.referer} if source.referer else {})
                     for source in channel.sources]
        else:
            with self.lock: game = self.games.get((query['league'], query['game']))
            if game is None: raise KeyError('unknown game; reload the schedule')
            feeds, _ = app.fetch_feeds(game, dict(self.config))
            feeds = [feed for feed in feeds if feed.kind != 'web']
        found = Listing(feeds)
        with self.lock:
            self.listings[name] = found
            if len(self.listings) > 40: self.listings.pop(next(iter(self.listings)))
        for index, feed in enumerate(feeds[:80]):
            if feed.kind in ('sf', 'direct', 'candidate'):
                found.checks[index] = ('checking', None, None)
                self.checks.submit(self.check, found, index)
        return found

    def check(self, found, index):
        try:
            result = app.quick_probe_feed(found.feeds[index])
            found.checks[index] = ('OK', result.get('height'), result.get('speed')) if result else ('n/a', None, None)
        except urllib.error.HTTPError as exc:
            found.checks[index] = (f'HTTP {exc.code}', None, None)
        except Exception:  # noqa: BLE001 - providers fail in many ways
            found.checks[index] = ('Failed', None, None)

    # Playback

    def play(self, query):
        found = self.listing(query)
        index = int(query['i'])
        feed = found.feeds[index]
        backups = self.backups(found, index)
        pool = concurrent.futures.ThreadPoolExecutor(len(backups)) if backups else None
        pending = [(pool.submit(self.player.resolve_feed, found.feeds[i]), found.feeds[i]) for i in backups] if pool else []
        try:
            url, headers = self.player.resolve_feed(feed)
            if pending: url, headers = self.player.merge_backup(url, headers, pending)
        except Exception as exc:
            found.checks[index] = ('Failed', None, None)
            raise RuntimeError(f'Source unavailable: {exc}') from exc
        finally:
            if pool: pool.shutdown(wait=False, cancel_futures=True)
        note = self.player.stream_notes.pop(url, '').strip()
        # The terminal front end starts mpv itself; give it the same cache, retry and stall options as the app.
        options = app.mpv_command('', cache=self.player.cache)[1:-2]
        return {'url': self.register(url, headers, '.m3u8'), 'note': note, 'mpv': options}

    def backups(self, found, index):
        """Up to two other working sources for the same broadcast side, never mixing home and away."""
        feed = found.feeds[index]
        options = [i for i, item in enumerate(found.feeds) if i != index and not found.failed(i) and item.url != feed.url
                   and (feed.side == 'unknown' or item.side in (feed.side, 'unknown'))]
        options.sort(key=lambda i: (found.checks.get(i, ('',))[0] != 'OK', found.feeds[i].side != feed.side,
                                    found.feeds[i].kind == 'ppv'))
        return options[:2]

    def register(self, url, headers, suffix):
        key = hashlib.sha256(json.dumps([url, headers], sort_keys=True).encode()).hexdigest()[:32]
        with self.lock:
            if len(self.proxied) > 8000: self.proxied.clear()
            self.proxied[key] = (url, dict(headers))
        return f'/{self.token}/p/{key}{suffix}'

    def proxy(self, key):
        with self.lock: target = self.proxied.get(key)
        if not target: raise KeyError('stream expired; choose the source again')
        url, headers = target
        data = relay._download(url, headers=headers, timeout=20)
        if data.lstrip()[:7] == b'#EXTM3U':
            return self.rewrite(data.decode('utf8', 'replace'), url, headers), 'application/vnd.apple.mpegurl'
        try: return relay.unwrap_segment(data), 'video/mp2t'
        except ValueError: return data, 'application/octet-stream'

    def rewrite(self, text, base, headers):
        def link(uri, line):
            target = urllib.parse.urljoin(base, uri.strip())
            playlist = '.m3u8' in urllib.parse.urlsplit(target).path or line.startswith('#EXT-X-STREAM-INF') \
                or line.startswith('#EXT-X-I-FRAME') or line.startswith('#EXT-X-MEDIA')
            return self.register(target, headers, '.m3u8' if playlist else '.ts')
        out, previous = [], ''
        for line in text.splitlines():
            if line.startswith('#'):
                line = URI_ATTRIBUTE.sub(lambda m: f'URI="{link(m.group(1), line)}"', line)
            elif line.strip():
                line = link(line, previous)
            out.append(line)
            if line.strip(): previous = line
        return ('\n'.join(out) + '\n').encode()

    def close(self):
        self.checks.shutdown(wait=False, cancel_futures=True)
        self.player.close()
        if self.remote: self.remote.close()


def serve(config, host='0.0.0.0', port=PORT):
    token = config.get('web_token')
    if not token:
        token = config['web_token'] = secrets.token_urlsafe(12)
        app.save_config(config)
    web = Web(config, token)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args): pass

        def reply(self, status, body, kind):
            self.send_response(status); self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            prefix = f'/{token}/'
            if not parts.path.startswith(prefix) and parts.path != f'/{token}':
                return self.reply(404, b'Not found', 'text/plain')
            path, query = parts.path[len(prefix):], dict(urllib.parse.parse_qsl(parts.query))
            try:
                if path in ('', 'index.html'): return self.reply(200, PAGE.encode(), 'text/html; charset=utf-8')
                if path == 'manifest.json': return self.reply(200, MANIFEST.encode(), 'application/manifest+json')
                if path.startswith('p/'):
                    body, kind = web.proxy(path[2:].split('.')[0])
                    return self.reply(200, body, kind)
                routes = {'api/leagues': lambda: web.leagues(),
                          'api/games': lambda: web.game_list(query.get('league', 'nba')),
                          'api/channels': web.channel_list,
                          'api/sources': lambda: {'sources': web.listing(query).rows()},
                          'api/play': lambda: web.play(query),
                          'api/app/view': lambda: web.screen().view(),
                          'api/app/key': lambda: web.screen().key(query['k'], query.get('text')),
                          'api/app/exit': lambda: web.screen().exited(int(query.get('code', 0)))}
                if path not in routes: return self.reply(404, b'Not found', 'text/plain')
                self.reply(200, json.dumps(routes[path]()).encode(), 'application/json')
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # noqa: BLE001 - report every failure to the page
                try: self.reply(502, json.dumps({'error': str(exc) or type(exc).__name__}).encode(), 'application/json')
                except (BrokenPipeError, ConnectionResetError): pass

    try:
        server = relay._Server((host, port), Handler)
    except OSError as exc:
        print(f'Cannot listen on port {port}: {exc}. Try --web-port with another number.')
        return 2
    server.daemon_threads = True
    print('Sports CLI web is running. Open one of these on your phone:\n')
    for address in addresses():
        print(f'  http://{address}:{port}/{token}/')
    print('\nThe link is the password; share it only with people you trust. Press Ctrl+C to stop.')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); web.close()
    return 0


def addresses():
    """Tailscale names first (they work away from home), then this computer's Wi-Fi address."""
    found = []
    command = shutil.which('tailscale') or '/Applications/Tailscale.app/Contents/MacOS/Tailscale'
    try:
        status = json.loads(subprocess.run([command, 'status', '--json'], capture_output=True, timeout=5).stdout)
        me = status.get('Self') or {}
        # The name and the 100.x address reach the same computer; list the address only when there is no name.
        if me.get('DNSName'): found.append(me['DNSName'].rstrip('.'))
        else: found.extend(ip for ip in me.get('TailscaleIPs') or [] if '.' in ip)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9)); found.append(probe.getsockname()[0])
    except OSError:
        pass
    return list(dict.fromkeys(found)) or ['localhost']


MANIFEST = json.dumps({'name': 'Sports CLI', 'short_name': 'Sports', 'display': 'standalone',
                       'background_color': '#0b0d10', 'theme_color': '#0b0d10', 'start_url': '.'})

PAGE = r'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Sports">
<link rel="manifest" href="manifest.json">
<title>Sports CLI</title>
<style>
:root{--bg:#0b0d10;--panel:#15191e;--line:#262c33;--text:#e8ecf0;--muted:#8b949e;--accent:#4aa3ff;--good:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}
a{color:inherit;text-decoration:none}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:8px;align-items:center}
header h1{font-size:17px;margin:0;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
button,.tab{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 12px}
.tab.on{background:var(--accent);border-color:var(--accent);color:#fff}
#back{display:none}
main{padding:12px 16px 40px}
.chips{display:flex;gap:8px;overflow-x:auto;padding-bottom:8px;margin-bottom:8px}
.chips button{white-space:nowrap}
.row{display:block;width:100%;text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:0 0 8px;color:var(--text)}
.row .t{font-weight:600}
.row .s{color:var(--muted);font-size:14px;margin-top:2px}
.live{color:var(--bad);font-weight:700;font-size:13px;margin-right:6px}
.Smooth{color:var(--good)}.OK{color:var(--good)}.lag{color:var(--warn)}.fail{color:var(--bad)}
input{width:100%;font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:10px}
video{width:100%;background:#000;border-radius:12px;aspect-ratio:16/9;display:none;margin-bottom:8px}
#status{color:var(--muted);font-size:14px;margin:6px 0 10px;min-height:1em}
.muted{color:var(--muted)}
</style></head><body>
<header><button id="back" onclick="history.back()">&larr;</button><h1 id="title">Sports</h1>
<a class="tab" id="tab-sports" href="#/">Sports</a><a class="tab" id="tab-tv" href="#/tv">TV</a></header>
<main><video id="video" controls playsinline autoplay></video><div id="status"></div><div id="view"></div></main>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js"></script>
<script>
const view = document.getElementById('view'), statusLine = document.getElementById('status'), video = document.getElementById('video');
let hls = null, timer = null, channels = null, showFailed = false, route = 0;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(path) {
  const r = await fetch(path); const data = await r.json().catch(() => ({error: 'Bad reply'}));
  if (!r.ok) throw new Error(data.error || r.status); return data;
}
function frame(title, back, tab) {
  document.getElementById('title').textContent = title;
  document.getElementById('back').style.display = back ? 'block' : 'none';
  document.getElementById('tab-sports').classList.toggle('on', tab === 'sports');
  document.getElementById('tab-tv').classList.toggle('on', tab === 'tv');
}
function stop() {
  if (hls) { hls.destroy(); hls = null; }
  video.pause(); video.removeAttribute('src'); video.load(); video.style.display = 'none';
}
function say(text) { statusLine.textContent = text || ''; }

async function sports(league) {
  league = league || localStorage.getItem('league') || 'nba';
  try { localStorage.setItem('league', league); } catch (e) {}
  frame('Sports', false, 'sports'); stop();
  const leagues = await api('api/leagues');
  view.innerHTML = '<div class="chips">' + leagues.map(l => `<a href="#/league/${l.id}"><button class="${l.id === league ? 'tab on' : ''}">${esc(l.name)}</button></a>`).join('') + '</div><div id="games" class="muted">Loading games...</div>';
  const list = document.getElementById('games'), mine = route;
  try {
    const games = await api('api/games?league=' + league); if (mine !== route) return;
    list.className = '';
    list.innerHTML = games.length ? games.map(g => `<a class="row" href="#/game/${league}/${g.id}"><div class="t">${g.state === 'live' ? '<span class="live">LIVE</span>' : ''}${esc(g.title)}</div><div class="s">${new Date(g.start).toLocaleString([], {weekday:'short', hour:'numeric', minute:'2-digit'})}${g.tv ? ' · ' + esc(g.tv) : ''}</div></a>`).join('')
      : '<p class="muted">No live or upcoming games today.</p>';
    document.querySelector('.chips .on')?.scrollIntoView({inline: 'center', block: 'nearest'});
  } catch (e) { list.textContent = 'Could not load games: ' + e.message; }
}

async function tvList() {
  frame('Live TV', false, 'tv'); stop();
  view.innerHTML = '<input id="q" placeholder="Search channels" autocomplete="off"><div id="ch" class="muted">Loading channels...</div>';
  const mine = route;
  try { channels = channels || await api('api/channels'); } catch (e) { document.getElementById('ch').textContent = 'Could not load channels: ' + e.message; return; }
  if (mine !== route) return;
  const q = document.getElementById('q'), box = document.getElementById('ch');
  const draw = () => {
    const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = channels.filter(c => words.every(w => c.name.toLowerCase().includes(w)));
    const shown = words.length ? hits.slice(0, 200) : hits.filter(c => c.featured).concat(hits.filter(c => !c.featured).slice(0, 60));
    box.className = '';
    box.innerHTML = shown.map(c => `<a class="row" href="#/channel/${encodeURIComponent(c.name)}"><div class="t">${esc(c.name)}</div><div class="s">${c.count} source${c.count === 1 ? '' : 's'}</div></a>`).join('') + (hits.length > shown.length ? `<p class="muted">Search to see all ${hits.length} channels.</p>` : '');
  };
  q.oninput = draw; draw();
}

async function sources(query, title, tab) {
  frame(title, true, tab); stop();
  view.innerHTML = '<p class="muted">Finding sources...</p>';
  const mine = route;
  const draw = async () => {
    if (mine !== route) return;
    let data;
    try { data = await api('api/sources?' + query); } catch (e) { view.innerHTML = '<p>Could not find sources: ' + esc(e.message) + '</p>'; return; }
    if (mine !== route) return;
    const rows = data.sources, hidden = rows.filter(r => r.hidden).length;
    const visible = rows.filter(r => showFailed || !r.hidden);
    const cls = s => s.startsWith('Smooth') || s.startsWith('OK') ? 'OK' : s.startsWith('May lag') ? 'lag' : /Failed|HTTP/.test(s) ? 'fail' : '';
    view.innerHTML = (visible.length ? visible.map(r => `<button class="row" onclick="play(${esc(JSON.stringify(query))}, ${r.i}, this)"><div class="t">${esc(r.name)}</div><div class="s"><span class="${cls(r.status)}">${esc(r.status)}</span> · ${esc(r.quality)}${r.side !== '-' ? ' · ' + esc(r.side) : ''}${r.detail ? ' · ' + esc(r.detail) : ''}</div></button>`).join('') : '<p class="muted">No sources found yet. Try again closer to start time.</p>')
      + (hidden ? `<button onclick="showFailed=!showFailed;refresh()">${showFailed ? 'Hide' : 'Show'} ${hidden} failed</button>` : '');
    if (rows.some(r => r.status === 'Checking')) timer = setTimeout(draw, 2000);
  };
  window.refresh = () => { clearTimeout(timer); draw(); };
  draw();
}

async function play(query, i, button) {
  stop(); say('Opening ' + button.querySelector('.t').textContent + '...');
  const mine = route;
  try {
    const data = await api('api/play?' + query + '&i=' + i);
    if (mine !== route) return;
    video.style.display = 'block';
    // Safari (every iPhone browser) plays HLS itself; elsewhere hls.js copes better with live playlists.
    const safari = /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Safari\//.test(navigator.userAgent) && !/Chrome|Chromium|Android/.test(navigator.userAgent));
    if (!safari && window.Hls && Hls.isSupported()) { hls = new Hls({liveSyncDurationCount: 3}); hls.loadSource(data.url); hls.attachMedia(video); }
    else if (video.canPlayType('application/vnd.apple.mpegurl')) { video.src = data.url; }
    else { say('This browser cannot play HLS video.'); return; }
    video.play().catch(() => {});
    say('Playing' + (data.note ? '. ' + data.note : '.'));
    scrollTo({top: 0, behavior: 'smooth'});
  } catch (e) { say(e.message + ' Try another source.'); refresh(); }
}
video.addEventListener('error', () => say('The player stopped. Try another source.'));

function go() {
  route++; clearTimeout(timer); say('');
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  if (parts[0] === 'tv') return tvList();
  if (parts[0] === 'league') return sports(parts[1]);
  if (parts[0] === 'game') return sources(`league=${parts[1]}&game=${parts[2]}`, 'Sources', 'sports');
  if (parts[0] === 'channel') return sources('channel=' + parts[1], decodeURIComponent(parts[1]), 'tv');
  return sports();
}
addEventListener('hashchange', go); go();
</script></body></html>
'''
