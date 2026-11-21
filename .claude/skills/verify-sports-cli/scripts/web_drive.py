#!/usr/bin/env python3
"""Drive the --web phone page like a user, on iPhone WebKit and desktop Chromium, and save each step as evidence.

    web_drive.py [--url-file FILE] [--out DIR] [--engines iphone,desktop] [--leagues all|nba,nfl] [--no-play]

With no --url-file it starts its own `./sports-cli --web --web-host 127.0.0.1` on a free port, with its own
settings folder and a throwaway token, and stops it at the end. With --url-file it drives a page that is
already running (the same link file a phone uses). The token never reaches the output: every line written
replaces it with <token>.

Engines (both run by default; iPhone is required, since iPhone Safari is how the page is used):
  iphone    Playwright WebKit with devices['iPhone 14 Pro'] (Safari's engine, iPhone screen and user agent)
  desktop   Playwright Chromium at 1280x800 (takes the page's hls.js path)

Steps per engine, each with a screenshot (ENGINE-NN-label.png), the page text (.txt) and a timing:
  home         Sports tab: games render, and FAIL if no rows show while api/games returns games
  league-ID    every league chip; rows on the page must match api/games for that league
  game         the first live game (else the first game): its Sources list and check results
  play-game    Play on the best checked source (next one on failure); the <video> must advance
  tv           TV tab lists channels; searching "espn" filters them
  channel      the first match's sources, then play-channel
  no-slash     the link without the slash after the token (as people type it) still renders games
  console      FAIL on any console error, uncaught page error or 4xx/5xx response during the run
problems.txt lists every console error and failed request; summary.txt has one PASS, FAIL or SKIP line per step.
Exit status 1 if any step failed.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[4]
VENV = pathlib.Path.home() / '.cache' / 'sports-cli-verify' / 'web-venv'
RUNS = pathlib.Path.home() / '.cache' / 'sports-cli-verify' / 'web'

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    # Playwright lives in its own venv so the app's runtime stays standard-library only.
    python = VENV / 'bin' / 'python'
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(VENV)], check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-q', 'playwright>=1.55'], check=True)
        subprocess.run([str(python), '-m', 'playwright', 'install', 'webkit', 'chromium'], check=True)
    os.execv(str(python), [str(python), __file__, *sys.argv[1:]])

NOT_LOADING = "([s, g]) => { const e = document.querySelector(s); return e && !e.textContent.includes(g); }"


class Run:
    def __init__(self, out, token):
        self.out, self.token, self.n, self.results, self.problems = out, token, 0, [], []
        self.engine = ''

    def hide(self, text):
        return str(text).replace(self.token, '<token>') if self.token else str(text)

    def shot(self, page, label):
        self.n += 1
        stem = self.out / f'{self.engine}-{self.n:02d}-{label}'
        page.screenshot(path=f'{stem}.png', full_page=True)
        stem.with_suffix('.txt').write_text(self.hide(page.inner_text('body')))
        return stem.name + '.png'

    def record(self, step, verdict, detail, started, shot=''):
        line = f'{verdict} {self.engine} {step} ({time.monotonic() - started:.1f}s) {self.hide(detail)}' + (f' [{shot}]' if shot else '')
        self.results.append((self.engine, step, verdict)); print(line, flush=True)
        with open(self.out / 'summary.txt', 'a') as f: f.write(line + '\n')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


def start_local():
    """A fresh ./sports-cli --web on localhost with its own settings and token. Returns (process, url, token, folder)."""
    folder = pathlib.Path(tempfile.mkdtemp(prefix='sports-web-verify-'))
    token, port = secrets.token_urlsafe(12), free_port()
    (folder / 'nba-tui').mkdir()
    (folder / 'nba-tui' / 'config.json').write_text(json.dumps({'web_token': token}))
    env = dict(os.environ, XDG_CONFIG_HOME=str(folder))
    process = subprocess.Popen([sys.executable, str(REPO / 'sports-cli'), '--web', '--web-host', '127.0.0.1', '--web-port', str(port)],
                               cwd=REPO, env=env, stdout=open(folder / 'server.log', 'w'), stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            socket.create_connection(('127.0.0.1', port), 0.2).close()
            return process, f'http://127.0.0.1:{port}/{token}/', token, folder
        except OSError:
            if process.poll() is not None: break
            time.sleep(0.1)
    raise SystemExit(f'sports-cli --web did not start; see {folder}/server.log')


def mark_stale(page):
    """Tag what is on screen now, so a wait after a click cannot pass on the screen the click is replacing."""
    page.evaluate("document.querySelectorAll('#view > *').forEach(e => e.setAttribute('data-stale', ''))")


def click_league(page, league, timeout):
    if page.evaluate('location.hash') == f'#/league/{league}': return  # already open; a click would change nothing
    mark_stale(page)
    page.click(f'.chips a[href="#/league/{league}"]')
    page.wait_for_selector(f'#games:not([data-stale])', timeout=timeout * 1000)
    page.wait_for_function(NOT_LOADING, arg=['#games', 'Loading games'], timeout=timeout * 1000)


def games_check(run, page, base, step, league, started):
    """Compare the rows on screen with what api/games returns for the same league."""
    expected = len(page.request.get(f'{base}api/games?league={league}').json())
    rows = len(page.query_selector_all('#games a.row'))
    live = len(page.query_selector_all('#games a.row .live'))
    text = page.inner_text('#games')[:120].replace('\n', ' ')
    ok = rows == expected and (rows or 'No live or upcoming games' in text)
    run.record(step, 'PASS' if ok else 'FAIL', f'{rows} games on the page ({live} live), api/games has {expected}' + ('' if ok else f'; page shows: {text!r}'),
               started, run.shot(page, step))
    return rows, live


def sources_step(run, page, step, timeout):
    started = time.monotonic()
    try:
        # Sources are ready when rows, or the page's own "no sources" or error line, replace the old screen.
        page.wait_for_function("() => !document.querySelector('#view > [data-stale]') && (document.querySelector('#view button.row') || /No sources found|Could not find sources/.test(document.getElementById('view').textContent))", timeout=timeout * 1000)
        # Checks run in the background; the page redraws every 2 s until none says Checking.
        try: page.wait_for_function("() => ![...document.querySelectorAll('#view .row .s')].some(e => e.textContent.startsWith('Checking'))", timeout=timeout * 1000)
        except Exception: pass
        rows = page.eval_on_selector_all('#view button.row', 'rs => rs.map(r => r.querySelector(".s").textContent)')
        shot = run.shot(page, step)
        if not rows:
            run.record(step, 'FAIL', 'no source rows: ' + page.inner_text('#view')[:160].replace('\n', ' '), started, shot); return False
        working = [s for s in rows if s.startswith(('Smooth', 'OK', 'May lag'))]
        run.record(step, 'PASS', f'{len(rows)} sources shown, {len(working)} checked working', started, shot); return True
    except Exception as exc:
        run.record(step, 'FAIL', f'{type(exc).__name__}: {str(exc).splitlines()[0]}', started, run.shot(page, step)); return False


def play_step(run, page, step, seconds):
    started = time.monotonic()
    rows = page.locator('#view button.row')  # the list redraws while checks finish, so find rows again each time
    last = ''
    for index in range(min(3, rows.count())):  # sources come best first; try the next on failure like a user
        page.evaluate("document.getElementById('status').textContent = ''")
        rows.nth(index).click()
        try: page.wait_for_function("() => /^Playing|Try another source|cannot play/.test(document.getElementById('status').textContent)", timeout=45000)
        except Exception: pass
        last = page.inner_text('#status')
        if 'cannot play HLS' in last:
            return run.record(step, 'SKIP', f'this engine has no HLS player ({last!r}); playback is proven on the other engine', started, run.shot(page, step))
        if not last.startswith('Playing'): continue
        first = page.evaluate('document.getElementById("video").currentTime')
        page.wait_for_timeout(seconds * 1000)
        state = page.evaluate('(() => { const v = document.getElementById("video"); return {t: v.currentTime, ready: v.readyState}; })()')
        if state['t'] > first:
            return run.record(step, 'PASS', f'source {index + 1}: video advanced {first:.1f}s -> {state["t"]:.1f}s', started, run.shot(page, step))
        last = f'video stuck at {state["t"]:.1f}s, readyState {state["ready"]}'
    run.record(step, 'FAIL', f'no source played; last: {last!r}', started, run.shot(page, step))


def drive(run, page, base, args):
    t = args.timeout
    started = time.monotonic()
    page.goto(base + '#/')
    try:
        page.wait_for_function(NOT_LOADING, arg=['#games', 'Loading games'], timeout=t * 1000)
        league = page.get_attribute('.chips button.on', 'class') and page.eval_on_selector('.chips button.on', 'b => b.parentElement.getAttribute("href").split("/").pop()')
        games_check(run, page, base, 'home', league, started)
    except Exception as exc:
        run.record('home', 'FAIL', f'games never rendered ({type(exc).__name__}); page shows: {page.inner_text("main")[:120]!r}', started, run.shot(page, 'home'))

    ids = page.eval_on_selector_all('.chips a', 'as => as.map(a => a.getAttribute("href").split("/").pop())')
    if args.leagues != 'all': ids = [i for i in ids if i in args.leagues.split(',')]
    best = None
    for league in ids:
        started = time.monotonic()
        try:
            click_league(page, league, t)
            rows, live = games_check(run, page, base, f'league-{league}', league, started)
            if rows and (best is None or (live and not best[1])): best = (league, bool(live))
        except Exception as exc:
            run.record(f'league-{league}', 'FAIL', f'games never loaded: {type(exc).__name__}', started, run.shot(page, f'league-{league}'))

    if best:
      try:
        click_league(page, best[0], t)
        target = page.locator('#games a.row:has(.live)').first if best[1] else page.locator('#games a.row').first
        print(f'  game: {target.inner_text().replace(chr(10), " ")}', flush=True)
        mark_stale(page); target.click()
        if sources_step(run, page, 'game', t) and args.play: play_step(run, page, 'play-game', args.play_seconds)
      except Exception as exc:
        run.record('game', 'FAIL', f'{type(exc).__name__}: {str(exc).splitlines()[0]}', time.monotonic(), run.shot(page, 'game'))
    else:
        run.record('game', 'SKIP', 'no league listed a game right now', time.monotonic())

    started = time.monotonic()
    try:
        mark_stale(page); page.click('#tab-tv')
        page.wait_for_selector('#ch', timeout=t * 1000)
        page.wait_for_function(NOT_LOADING, arg=['#ch', 'Loading channels'], timeout=t * 1000)
        count = len(page.query_selector_all('#ch a.row'))
        page.fill('#q', 'espn')
        hits = page.eval_on_selector_all('#ch a.row .t', 'es => es.map(e => e.textContent)')
        ok = count and hits and all('espn' in h.lower() for h in hits)
        run.record('tv', 'PASS' if ok else 'FAIL', f'{count} channels shown, search "espn" -> {len(hits)}; ' + ('' if ok else page.inner_text('#view')[:120]), started, run.shot(page, 'tv'))
        if hits:
            mark_stale(page); page.locator('#ch a.row').first.click()
            if sources_step(run, page, 'channel', t) and args.play: play_step(run, page, 'play-channel', args.play_seconds)
    except Exception as exc:
        run.record('tv', 'FAIL', f'channels never loaded: {type(exc).__name__}: {page.inner_text("#view")[:120]!r}', started, run.shot(page, 'tv'))

    started = time.monotonic()
    page.goto(base.rstrip('/') + '#/')
    try:
        page.wait_for_function(NOT_LOADING, arg=['#games', 'Loading games'], timeout=15000)
        rows = len(page.query_selector_all('#games a.row')); text = page.inner_text('#games')[:100]
        ok = rows or 'No live or upcoming games' in text
        run.record('no-slash', 'PASS' if ok else 'FAIL', f'{rows} games; ' + ('' if ok else repr(text)), started, run.shot(page, 'no-slash'))
    except Exception as exc:
        run.record('no-slash', 'FAIL', f'games never rendered ({type(exc).__name__}); page shows: {page.inner_text("main")[:120]!r}', started, run.shot(page, 'no-slash'))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url-file', type=pathlib.Path, help='file holding the page link (default: start a local server)')
    parser.add_argument('--out', type=pathlib.Path, help=f'evidence folder (default: {RUNS}/<time>)')
    parser.add_argument('--engines', default='iphone,desktop')
    parser.add_argument('--leagues', default='all', help='league ids to open, comma separated, or all')
    parser.add_argument('--no-play', dest='play', action='store_false', help='skip the Play steps')
    parser.add_argument('--play-seconds', type=int, default=8)
    parser.add_argument('--timeout', type=int, default=40, help='seconds to wait for a list to load')
    args = parser.parse_args()
    out = args.out or RUNS / dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True, exist_ok=True)

    server = folder = None
    if args.url_file:
        url = re.search(r'https?://\S+', args.url_file.read_text()).group(0)
        token = (url.split('/') + ['', '', '', ''])[3].split('#')[0]
        base = url.split('#')[0].rstrip('/') + '/'
    else:
        server, base, token, folder = start_local()
    run = Run(out, token)
    print(f'page: {run.hide(base)}  engines: {args.engines}  evidence: {out}', flush=True)
    try:
        with sync_playwright() as p:
            for engine in args.engines.split(','):
                run.engine, run.n, before = engine, 0, len(run.problems)
                if engine == 'iphone':
                    browser, context_args = p.webkit.launch(), dict(p.devices['iPhone 14 Pro'])
                else:
                    browser, context_args = p.chromium.launch(), {'viewport': {'width': 1280, 'height': 800}}
                page = browser.new_context(**context_args).new_page()
                page.on('console', lambda m, e=engine: m.type == 'error' and run.problems.append(f'{e} console: {m.text} at {m.location.get("url", "")}'))
                page.on('pageerror', lambda x, e=engine: run.problems.append(f'{e} page error: {x}'))
                page.on('response', lambda r, e=engine: r.status >= 400 and run.problems.append(f'{e} HTTP {r.status}: {r.url[:160]}'))
                started = time.monotonic()
                drive(run, page, base, args)
                found = run.problems[before:]
                # Media and stream hosts fail all the time; only the page's own requests and scripts count here.
                # Chromium also asks for /favicon.ico outside the token path; the page has no icon, so that 404 is expected.
                # A source that will not resolve answers api/play with 502 and the page says "Try another source"; that is the stream, not the page.
                own = [x for x in found if not any(k in x for k in ('/p/', 'favicon.ico', '/api/play?'))]
                run.record('console', 'FAIL' if own else 'PASS', f'{len(own)} console errors or failed page requests' + (f': {own[0]}' if own else ''), started)
                browser.close()
    finally:
        (out / 'problems.txt').write_text(''.join(run.hide(line) + '\n' for line in run.problems))
        if server:
            server.terminate()
            try: server.wait(5)
            except subprocess.TimeoutExpired: server.kill()
            (out / 'server.log').write_text(run.hide((folder / 'server.log').read_text()))
            subprocess.run(['rm', '-rf', str(folder)])
    failed = [r for r in run.results if r[2] == 'FAIL']
    print(f'{len(failed)} failed of {len(run.results)} steps. Evidence: {out}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
