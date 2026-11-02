import datetime as dt
import os
import pathlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from sportscli import app as nba, providers, relay
import gzip
import http.server
import io
import struct
import zlib
import threading
import time
import urllib.error
from types import SimpleNamespace
from sportscli import grading, replays

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.start = dt.datetime(2026, 10, 29, 2, 30, tzinfo=dt.timezone.utc)
        self.game = nba.Game('1', 'Los Angeles Lakers at LA Clippers', self.start,
                             'LA Clippers', 'Los Angeles Lakers', 'Clippers', 'Lakers')

    def test_match_requires_both_teams(self):
        self.assertTrue(nba.title_matches('Los Angeles Lakers vs. LA Clippers', self.game))
        self.assertFalse(nba.title_matches('Los Angeles Lakers vs. Chicago Bulls', self.game))
        self.assertFalse(nba.title_matches('LA Clippers vs. Chicago Bulls', self.game))

    def test_feed_sides_and_time_matching(self):
        def event(start):
            return {'name': 'Lakers vs Clippers', 'starts_at': int(start.timestamp()),
                    'iframe': 'https://example.com/embed/game', 'source_tag': 'KTLA (Home)',
                    'substreams': [{'iframe': 'https://example.com/embed/away', 'source_tag': 'League Pass (Away)'}]}
        data = {'streams': [{'streams': [event(self.start), event(self.start + dt.timedelta(days=2))]}]}
        feeds = nba.ppv_feeds(data, self.game)
        self.assertEqual([(f.side, f.kind) for f in feeds], [('home', 'ppv'), ('away', 'ppv')])

    def test_ppv_omits_expired_events(self):
        event = {'name':'Lakers vs Clippers', 'starts_at':int(self.start.timestamp()),
                 'ends_at':int((self.start+dt.timedelta(hours=3)).timestamp()),
                 'iframe':'https://example.com/embed', 'source_tag':'Home'}
        data = {'streams':[{'streams':[event]}]}
        self.assertEqual(len(nba.ppv_feeds(data,self.game,now=self.start)),1)
        self.assertEqual(nba.ppv_feeds(data,self.game,now=self.start+dt.timedelta(hours=4)),[])
        event['always_live'] = 1
        self.assertEqual(len(nba.ppv_feeds(data,self.game,now=self.start+dt.timedelta(hours=4))),1)

    def test_scoreboard_handles_any_team_and_broadcasts(self):
        data = {'events': [{'id':'x', 'date':'2026-10-28T23:00Z', 'name':'Miami Heat at Atlanta Hawks',
                            'competitions':[{'competitors':[{'homeAway':'home', 'team':{'name':'Hawks','displayName':'Atlanta Hawks'}},
                                                            {'homeAway':'away', 'team':{'name':'Heat','displayName':'Miami Heat'}}],
                                              'broadcasts':[{'market':'away','names':['Example TV']}]}]}]}
        g, = nba.games_from_espn(data)
        self.assertTrue(nba.title_matches('Miami Heat vs. Atlanta Hawks', g))
        self.assertEqual(g.broadcasts, ['Example TV (Away)'])

    def test_finished_events_are_hidden_without_hiding_delayed_games(self):
        def event(ident, status):
            return {'id': ident, 'date':'2026-10-28T23:00Z', 'name':'Sparks at Sun',
                    'status':{'type':status}, 'competitions':[{'competitors':[
                        {'homeAway':'home','team':{'name':'Sun','displayName':'Connecticut Sun'}},
                        {'homeAway':'away','team':{'name':'Sparks','displayName':'Los Angeles Sparks'}}]}]}
        data = {'events':[event('final',{'state':'post','completed':True}),
                          event('delayed',{'state':'pre','completed':False}),
                          event('live',{'state':'in','completed':False}),
                          event('unknown',{})]}
        self.assertEqual({g.id for g in nba.games_from_espn(data,league='wnba')}, {'delayed','live','unknown'})

    def test_today_includes_late_games_still_live_from_yesterday(self):
        def event(ident, date, state):
            return {'id': ident, 'date': date, 'name': 'Lakers at Warriors', 'status': {'type': {'state': state}},
                    'competitions': [{'competitors': [
                        {'homeAway': 'home', 'team': {'name': 'Warriors', 'displayName': 'Golden State Warriors'}},
                        {'homeAway': 'away', 'team': {'name': 'Lakers', 'displayName': 'Los Angeles Lakers'}}]}]}
        boards = {'20261030': {'events': [event('late', '2026-10-31T02:00Z', 'in'), event('early', '2026-10-30T23:00Z', 'post'),
                                          event('pending', '2026-10-30T23:30Z', 'pre')]},
                  '20261031': {'events': [event('tonight', '2026-10-31T23:00Z', 'pre')]}}
        fetch = lambda url, **kwargs: boards[url.split('dates=')[1][:8]]
        today = dt.date(2026, 10, 31)
        with patch.object(nba, 'request', side_effect=fetch), patch.object(nba, 'default_day', return_value=today):
            self.assertEqual([g.id for g in nba.get_games(today)], ['late', 'tonight'])
            # A chosen past or future date shows only that date.
            self.assertEqual([g.id for g in nba.get_games(dt.date(2026, 10, 30))], ['pending', 'late'])

    def test_fetch_streams_is_game_specific_and_reports_source_failure(self):
        def fetch(url, **kwargs):
            if url == nba.PPV:
                return {'streams':[{'streams':[{'name':'Lakers vs Clippers', 'starts_at':int(self.start.timestamp()),
                    'iframe':'https://example.com/embed/game', 'source_tag':'Home'}]}]}
            raise OSError('unavailable')
        with patch.object(nba, 'request', side_effect=fetch), patch.object(nba, 'monster_feeds', return_value=[]), patch.object(nba, 'directory_feeds', return_value=[]), patch.object(nba, 'ntv_feeds', return_value=[]):
            feeds, errors = nba.fetch_feeds(self.game, {})
        self.assertEqual(len(feeds), 1)
        self.assertTrue(errors[0].startswith('StreamFree:'))

    def test_streamfree_presents_each_source_and_resolves_fresh_tokens(self):
        key = 'los-angeles-lakers-vs-la-clippers'
        base = nba.SF_ROOT
        source = f'https://strmfree.st/embed/basketball/{key}1080p2'
        listing = {'streams': {'basketball': [{'name':'Lakers vs Clippers', 'stream_key':key,
                                               'match_timestamp':int(self.start.timestamp())}]}}
        def fake_request(url, **kwargs):
            if '/api/v1/sources/' in url:
                return {'sources':[source], 'live_sources':[source]}
            if '/get-stream-key/' in url:
                return {'server_name':'cdn'}
            raise AssertionError(url)
        page = 'const _0x = {"1080p":{"_t":"fresh","_e":123,"_n":"nonce"}};'
        with patch.object(nba, 'request', side_effect=fake_request), patch.object(nba, 'request_text', return_value=page):
            feed, = nba.sf_feeds(listing, self.game)
            self.assertEqual(feed.label, 'Source 2 | 1080p | live')
            self.assertEqual(feed.kind, 'sf')
            url, headers = nba.resolve_streamfree(feed)
        self.assertTrue(url.startswith(f'{base}/live-cdn/{key}1080p2/index.m3u8?'))
        self.assertIn('_t=fresh', url)
        self.assertEqual(headers['Referer'], f'{base}/embed/basketball/{key}1080p2')

    def test_streamfree_omits_sources_marked_not_live(self):
        key = 'lakers-vs-clippers'
        active = f'https://strmfree.st/embed/basketball/{key}1080p1'
        inactive = f'https://strmfree.st/embed/basketball/{key}1080p2'
        listing = {'streams':{'basketball':[{'name':'Lakers vs Clippers','stream_key':key,
                                            'match_timestamp':int(self.start.timestamp())}]}}
        with patch.object(nba, 'request', return_value={'sources':[active,inactive],'live_sources':[active]}):
            feeds = nba.sf_feeds(listing,self.game)
        self.assertEqual([f.label for f in feeds], ['Source 1 | 1080p | live'])
        with patch.object(nba, 'request', return_value={'sources':[active,inactive],'live_sources':[]}):
            self.assertEqual(nba.sf_feeds(listing,self.game), [])
        with patch.object(nba, 'request', return_value={'sources':[active]}):
            self.assertEqual(nba.sf_feeds(listing,self.game)[0].label, 'Source 1 | 1080p | status unknown')
        objects = {'sources':[{'quality':'FHD','url':active},{'quality':'FHD','url':inactive}],
                   'live_sources':[{'url':active}]}
        with patch.object(nba, 'request', return_value=objects):
            self.assertEqual([f.label for f in nba.sf_feeds(listing,self.game)], ['Source 1 | 1080p | live'])

    def test_directory_matches_event_and_skips_other_matchups(self):
        card = f'<a class="card" href="/game/1"><span data-cd="{int(self.start.timestamp())}">Live</span><span>LA Clippers</span><span>Los Angeles Lakers</span></a>'
        def row(name, url, channel):
            return '<tr>' + ''.join(f'<td class="{cls}"><a href="{url}">{text}</a></td>' for cls,text in [('', 'x'),('who',name),('','Gold'),('','HD'),('','English'),('','2'),('',channel)]) + '</tr>'
        table = row('OK','https://example.com/lakers-vs-clippers/','Spectrum SportsNet USA') + row('Wrong','https://example.com/lakers-vs-celtics/','Other')
        def fetch(url, *args, **kwargs):
            return (card if url == providers.NBABITE + '/' else table, url)
        with patch.object(providers, 'fetch_html', side_effect=fetch):
            results=providers.nbabite_entries(self.game, nba.title_matches)
        self.assertEqual([r['name'] for r in results], ['OK'])
        self.game.broadcasts=['Spectrum Sports Net (Away)']
        self.assertEqual(nba.channel_side(results[0]['channel'], self.game), 'away')

    def test_candidate_follows_iframes_and_finds_tokenized_player(self):
        pages = {
            'https://one.example/watch':'<iframe src="https://two.example/player"></iframe>',
            'https://two.example/player': 'const SRC="https://edge.example/a/index.m3u8"; const EDGE="https://edge.example"; const CHANNEL="premium982"; fetch("/api/playlist-token");'
        }
        with patch.object(providers, 'fetch_html', side_effect=lambda url, *a, **kw:(pages[url],url)):
            result=providers.extract_player('https://one.example/watch')
        self.assertEqual(result['kind'], 'relay')
        self.assertEqual(result['channel'], 'premium982')
        self.assertFalse(providers.public_url('http://127.0.0.1/private'))

    def test_json_escaped_playlist_keeps_its_query(self):
        page = ('<div data-options="{&quot;hlsManifestUrl&quot;:&quot;https:\\/\\/cdn.example\\/video.m3u8'
                '?cmd=x\\u0026expires=1\\u0026sig=abc&quot;}"></div>')
        with patch.object(providers, 'fetch_html', return_value=(page, 'https://player.example/embed/1')):
            result = providers.extract_player('https://player.example/embed/1')
        self.assertEqual(result['url'], 'https://cdn.example/video.m3u8?cmd=x&expires=1&sig=abc')

    def test_image_wrapped_transport_stream(self):
        ts = b'\x47' + b'\x00'*187 + b'\x47' + b'\x00'*187
        payload = gzip.compress(ts)
        rgb = b'TIKTIKPX' + struct.pack('>I',len(payload)) + payload
        rgb += b'\x00' * (-len(rgb) % 3)
        def chunk(kind, value):
            return struct.pack('>I',len(value)) + kind + value + struct.pack('>I',zlib.crc32(kind+value))
        ihdr=struct.pack('>IIBBBBB',len(rgb)//3,1,8,2,0,0,0)
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+chunk(b'IDAT',zlib.compress(b'\x00'+rgb))+chunk(b'IEND',b'')
        self.assertEqual(relay.unwrap_segment(png),ts)

    def test_streamfree_png_chunk_is_unwrapped_and_relayed(self):
        ts = (b'\x47' + b'\x00'*187) * 3
        def chunk(kind, value):
            return struct.pack('>I',len(value)) + kind + value + struct.pack('>I',zlib.crc32(kind+value))
        ihdr = struct.pack('>IIBBBBB',1,1,8,6,0,0,0)
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',ihdr) + chunk(b'IDAT',zlib.compress(b'\x00'*5)) + chunk(b'tsDa',ts) + chunk(b'IEND',b'')
        self.assertEqual(relay.unwrap_segment(png), ts)
        manifest = '#EXTM3U\n#EXTINF:4,\nsegment.png\n'
        server = relay.Relay()
        try:
            url = server.add_streamfree('https://example.com/stream.m3u8', 'https://example.com/player',
                                        lambda: ('https://example.com/fresh.m3u8',{}), manifest)
            path = '/' + url.split('/', 3)[-1]
            with patch.object(relay, '_download', return_value=png):
                playlist, _ = server.get(path)
                segment = next(line for line in playlist.decode().splitlines() if line.startswith('http'))
                media, content_type = server.get('/' + segment.split('/', 3)[-1])
            self.assertEqual(media, ts)
            self.assertEqual(content_type, 'video/mp2t')
        finally:
            server.close()

    def test_streamfree_detects_wrapped_segments_and_preserves_direct_streams(self):
        def chunk(kind, value):
            return struct.pack('>I',len(value)) + kind + value + struct.pack('>I',zlib.crc32(kind+value))
        ihdr = struct.pack('>IIBBBBB',1,1,8,6,0,0,0)
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',ihdr) + chunk(b'IDAT',zlib.compress(b'\x00'*5)) + chunk(b'tsDa',(b'\x47'+b'\x00'*187)*3)
        manifest = '#EXTM3U\n#EXTINF:4,\nsegment.png\n'
        opener = SimpleNamespace(open=lambda *a, **kw: io.BytesIO(png))
        with patch.object(nba.urllib.request, 'build_opener', return_value=opener):
            self.assertTrue(nba.streamfree_wraps_segments('https://example.com/live.m3u8',manifest,{}))
        opener = SimpleNamespace(open=lambda *a, **kw: io.BytesIO(b'\x47'+b'\x00'*187))
        with patch.object(nba.urllib.request, 'build_opener', return_value=opener):
            self.assertFalse(nba.streamfree_wraps_segments('https://example.com/live.m3u8',manifest,{}))

    def test_streamfree_relay_renews_rejected_playlist_token(self):
        server = relay.Relay()
        denied = urllib.error.HTTPError('https://example.com/old.m3u8',403,'expired',{},None)
        renewed = ('https://example.com/fresh.m3u8', {'Referer':'https://example.com/new-player'})
        try:
            url = server.add_streamfree('https://example.com/old.m3u8', 'https://example.com/player', lambda: renewed)
            path = '/' + url.split('/',3)[-1]
            with patch.object(relay, '_download', side_effect=[denied,b'#EXTM3U\n#EXTINF:4,\nsegment.png\n']) as download, \
                 patch.object(relay.time, 'sleep'):
                playlist, _ = server.get(path)
            self.assertIn(b'/segment/',playlist)
            self.assertEqual(download.call_args_list[1].args[:2], (renewed[0], renewed[1]['Referer']))
        finally:
            server.close()

    def test_progressive_loading_preserves_user_selection(self):
        slow_gate = threading.Event()
        fast_feed = nba.Feed('Fast', 'One', url='https://example.com/a', kind='direct')
        other_feed = nba.Feed('Slow', 'Two', url='https://example.com/b', kind='direct')
        def slow():
            if not slow_gate.wait(2): raise RuntimeError('timed out')
            return [other_feed]
        app = nba.App(None,{})
        self.addCleanup(app.close)
        app.games = [self.game]; app.mode = 'feeds'
        probe_patcher = patch.object(app, 'start_auto_checks')
        probe_patcher.start(); self.addCleanup(probe_patcher.stop)
        try:
            # Slow source sorts first, but the fast one should appear immediately.
            with patch.object(nba, 'feed_tasks', return_value=[('Slow',slow),('Fast',lambda:[fast_feed])]):
                app.refresh_feeds()
            end = time.monotonic() + 1
            while not app.feeds and time.monotonic() < end:
                app.poll_feeds(); time.sleep(.01)
            self.assertEqual(app.visible_feeds(),[fast_feed])
            self.assertEqual(app.feed_pending,1)
            app.feed_selection_touched = True
            slow_gate.set()
            end = time.monotonic() + 1
            while app.feed_pending and time.monotonic() < end:
                app.poll_feeds(); time.sleep(.01)
            self.assertEqual(app.visible_feeds(),[other_feed,fast_feed])
            self.assertEqual(app.visible_feeds()[app.fi],fast_feed)
            app.feed_search='two'
            self.assertEqual(app.visible_feeds(),[other_feed])
        finally:
            slow_gate.set()

    def test_stale_feed_results_are_ignored(self):
        app = nba.App(None,{})
        app.games = [self.game]; app.mode = 'feeds'; app.feed_generation = 2
        app.feed_pending = 1; app.feed_order = ['New']
        app.feed_queue.put((1,'Old',[nba.Feed('Old','Old')],None))
        app.feed_queue.put((2,'New',[nba.Feed('New','New')],None))
        app.poll_feeds()
        self.assertEqual([f.label for f in app.feeds],['New'])
        self.assertEqual(app.feed_pending,0)

    def test_help_keeps_progressive_results(self):
        app = nba.App(None,{})
        app.games = [self.game]; app.mode = 'help'; app.help_return = 'feeds'
        app.feed_generation = 1; app.feed_pending = 1; app.feed_order = ['Source']
        app.feed_queue.put((1,'Source',[nba.Feed('Source','One')],None))
        app.poll_feeds()
        self.assertEqual([f.label for f in app.feeds], ['One'])
        self.assertEqual(app.feed_pending, 0)

    def test_failed_feed_is_hidden_until_refresh_but_missing_dependency_is_not(self):
        app = nba.App(None,{})
        self.addCleanup(app.close)
        app.games = [self.game]; app.mode = 'feeds'
        probe_patcher = patch.object(app, 'start_auto_checks')
        probe_patcher.start(); self.addCleanup(probe_patcher.stop)
        dead = nba.Feed('Source','Dead',url='https://example.com/a',kind='direct')
        good = nba.Feed('Source','Good',url='https://example.com/b',kind='direct')
        app.feeds = [dead,good]
        with patch.object(app,'render'), patch.object(app,'resolve_feed',side_effect=OSError('offline')), \
             patch.object(nba,'save_config'):
            app.test_feed(dead)
            end = time.monotonic() + 2
            while app.manual_checks and time.monotonic() < end:
                app.poll_manual_checks(); time.sleep(.01)
            self.assertFalse(app.manual_checks)
        self.assertEqual(app.visible_feeds(), [good])
        self.assertIn('hidden until refresh', app.message)
        self.assertEqual(app.config['health']['Source:Dead']['checks'], [False])
        with patch.object(nba,'feed_tasks',return_value=[('Source',lambda: [dead,good])]):
            app.refresh_feeds()
        end = time.monotonic() + 1
        while app.feed_pending and time.monotonic() < end:
            app.poll_feeds(); time.sleep(.01)
        self.assertEqual(app.visible_feeds(), [dead,good])
        with patch.object(app,'render'), patch.object(app,'resolve_feed',side_effect=nba.DependencyError('Install resolver')), \
             patch.object(nba,'save_config'):
            app.test_feed(good)
            end = time.monotonic() + 2
            while app.manual_checks and time.monotonic() < end:
                app.poll_manual_checks(); time.sleep(.01)
            self.assertFalse(app.manual_checks)
        self.assertEqual(app.visible_feeds(), [dead,good])
        self.assertEqual(app.message, 'Install resolver')

    def test_automatic_checks_cover_every_source_and_hide_failures_until_asked(self):
        app = nba.App(None,{})
        self.addCleanup(app.close)
        app.games = [self.game]; app.mode = 'feeds'; app.feed_generation = 1
        feeds = [nba.Feed('Source', name, url='https://example.com/' + name, kind='direct')
                 for name in ('one', 'two', 'three', 'four', 'five', 'six', 'seven')]
        feeds.append(nba.Feed('Source', 'page', url='https://example.com/page', kind='web'))
        app.feeds = feeds
        def probe(feed):
            if feed.label == 'two':
                raise urllib.error.HTTPError(feed.url, 403, 'forbidden', {}, None)
            return True
        with patch.object(nba, 'quick_probe_feed', side_effect=probe) as check:
            app.start_auto_checks(1, feeds)
            end = time.monotonic() + 1
            while 'checking' in app.auto_checks.values() and time.monotonic() < end:
                app.poll_auto_checks(); time.sleep(.01)
        self.assertEqual(check.call_count, 7)  # every sampleable source; a browser-only page is not
        self.assertEqual(app.auto_checks[id(feeds[0])], 'OK')
        self.assertEqual(app.auto_checks[id(feeds[1])], 'HTTP 403')
        self.assertNotIn(id(feeds[7]), app.auto_checks)
        # A failed check hides the source; v shows it again, last.
        self.assertEqual(app.visible_feeds(), [feeds[0], *feeds[2:7], feeds[7]])
        app.feed_key(ord('v'))
        self.assertEqual(app.visible_feeds(), [feeds[0], *feeds[2:7], feeds[7], feeds[1]])
        self.assertEqual(app.fi, 0)
        self.assertNotIn('health', app.config)

    def test_failed_check_hides_source_without_moving_a_chosen_selection(self):
        app = nba.App(None, {})
        patcher = patch.object(nba, 'MIN_VISIBLE_SOURCES', 0); patcher.start(); self.addCleanup(patcher.stop)
        self.addCleanup(app.close)
        app.feed_generation = 1
        one, two, three = feeds = [nba.Feed('Source', name, url='https://example.com/' + name, kind='direct')
                                   for name in ('one', 'two', 'three')]
        app.feeds = feeds
        for feed in feeds: app.auto_checks[id(feed)] = 'checking'
        app.auto_queue.put((1, one, 'HTTP 404')); app.poll_auto_checks()
        self.assertEqual(app.visible_feeds(), [two, three])
        self.assertIs(app.visible_feeds()[app.fi], two)  # untouched: the top source is selected
        app.fi = 1; app.feed_selection_touched = True  # the user picks three
        app.auto_queue.put((1, two, 'HTTP 403')); app.poll_auto_checks()
        self.assertEqual(app.visible_feeds(), [three])
        self.assertIs(app.visible_feeds()[app.fi], three)

    def test_failed_checks_never_leave_fewer_than_the_minimum_sources(self):
        app = nba.App(None, {})
        self.addCleanup(app.close)
        app.feed_generation = 1
        feeds = [nba.Feed('Source', f'feed{i}', url=f'https://example.com/{i}', kind='direct') for i in range(8)]
        app.feeds = feeds
        for feed in feeds: app.auto_checks[id(feed)] = 'checking'
        for feed in feeds[:7]: app.auto_queue.put((1, feed, 'HTTP 403'))
        app.poll_auto_checks()
        visible = app.visible_feeds()
        self.assertEqual(len(visible), nba.MIN_VISIBLE_SOURCES)
        self.assertIs(visible[0], feeds[7])  # the one that passed stays on top
        self.assertEqual(app.hidden_failed(), 3)
        app.hide_feed(feeds[0])  # an explicit failure stays hidden
        self.assertNotIn(feeds[0], app.visible_feeds())
        self.assertEqual(len(app.visible_feeds()), nba.MIN_VISIBLE_SOURCES)

    def test_smooth_sources_rank_above_faster_resolutions_that_may_lag(self):
        app = nba.App(None, {})
        self.addCleanup(app.close)
        app.feed_generation = 1
        laggy, smooth, unmeasured = feeds = [nba.Feed('Source', name, url='https://example.com/' + name, kind='direct')
                                             for name in ('laggy', 'smooth', 'unmeasured')]
        app.feeds = feeds
        for feed in feeds: app.auto_checks[id(feed)] = 'checking'
        for feed, height, speed in ((laggy, 1080, 0.8), (smooth, 720, 3.5), (unmeasured, 1080, None)):
            app.auto_queue.put((1, feed, 'OK', height, speed))
        app.poll_auto_checks()
        self.assertEqual(app.visible_feeds(), [smooth, unmeasured, laggy])
        self.assertEqual([nba.describe_feed(f, {'speed': app.auto_speeds.get(id(f))}, 'OK').check for f in app.visible_feeds()],
                         ['Smooth', 'OK', 'May lag'])

    def test_checked_sources_order_by_measured_resolution(self):
        app = nba.App(None, {})
        self.addCleanup(app.close)
        app.feed_generation = 1
        sd, unknown, hd = feeds = [nba.Feed('Source', name, url='https://example.com/' + name, kind='direct')
                                   for name in ('sd', 'unknown', 'hd')]
        app.feeds = feeds
        for feed in feeds: app.auto_checks[id(feed)] = 'checking'
        for feed, height in ((sd, 432), (unknown, None), (hd, 720)): app.auto_queue.put((1, feed, 'OK', height))
        app.poll_auto_checks()
        self.assertEqual(app.visible_feeds(), [hd, sd, unknown])

    def test_mpv_headers_are_arguments_not_shell_and_reject_injection(self):
        command = nba.mpv_command('https://example.com/a.m3u8',
                                  {'Referer':'https://example.com/', 'Bad\nHeader':'x', 'X-Unsafe':'a\nb'})
        self.assertIn('--http-header-fields-append=Referer: https://example.com/', command)
        self.assertFalse(any('Bad' in x or 'Unsafe' in x for x in command))
        self.assertEqual(command[-2:], ['--', 'https://example.com/a.m3u8'])

    def test_players_decode_video_in_hardware(self):
        # 1080p60 dropped about 8 frames a second when decoded in software.
        self.assertIn('--hwdec=auto-safe', nba.mpv_command('https://example.com/a.m3u8'))
        self.assertIn('--mpv-hwdec=auto-safe', nba.iina_command('/usr/bin/iina-cli', 'https://example.com/a.m3u8'))

    def test_missing_pieces_name_the_installer_command(self):
        app = nba.App.__new__(nba.App)
        app.player, app.ai_ad_mute, app.ai_ad_observe = 'mpv', False, False
        feed = nba.Feed('Example', 'Source', url='https://example.com/a.m3u8', kind='direct')
        with patch.object(nba.shutil, 'which', return_value=None):
            with patch.dict(nba.os.environ, {'SPORTS_CLI_COMMAND': 'sports'}):
                self.assertFalse(app.player_ready(feed))
                self.assertEqual(app.message, 'mpv is not installed. Quit and run: sports --setup')
            with patch.dict(nba.os.environ, {}, clear=True):
                self.assertFalse(app.player_ready(feed))
                self.assertEqual(app.message, 'mpv is not installed or not on PATH.')

    def test_settings_save_on_windows_without_posix_modes(self):
        with tempfile.TemporaryDirectory() as root, patch.object(nba, 'CONFIG', pathlib.Path(root) / 'config.json'), \
             patch.object(nba, 'os', SimpleNamespace(name='nt', open=os.open, fdopen=os.fdopen, O_WRONLY=os.O_WRONLY,
                                                     O_CREAT=os.O_CREAT, O_TRUNC=os.O_TRUNC)):
            nba.save_config({'player': 'mpv'})
            self.assertEqual(nba.load_config(), {'player': 'mpv'})

    def test_names_match_college_schools_and_soccer_clubs(self):
        def team(display, location, name, short):
            return {'displayName': display, 'location': location, 'name': name, 'shortDisplayName': short}
        def event(home, away, league):
            data = {'events': [{'date':'2026-10-28T23:00Z', 'competitions':[{'competitors':[
                {'homeAway':'home', 'team':home}, {'homeAway':'away', 'team':away}]}]}]}
            return nba.games_from_espn(data, league=league)[0]
        college = event(team('New Mexico State Aggies','New Mexico State','Aggies','New Mexico St'),
                        team('Jacksonville State Gamecocks','Jacksonville State','Gamecocks','Jax State'), 'ncaaf')
        self.assertTrue(nba.title_matches('Jacksonville St vs New Mexico State', college))
        self.assertFalse(nba.title_matches('Jacksonville State vs Texas A&M', college))
        soccer = event(team('Charlotte FC','Charlotte FC','Charlotte FC','Charlotte'),
                       team('CF Montréal','CF Montréal','CF Montréal','CF Montréal'), 'mls')
        self.assertTrue(nba.title_matches('Charlotte vs Montreal', soccer))
        self.assertFalse(nba.title_matches('Montreal vs Miami', soccer))
        same = event(team('Alpha Wildcats','Alpha','Wildcats','Alpha'),
                     team('Beta Wildcats','Beta','Wildcats','Beta'), 'ncaam')
        self.assertTrue(nba.title_matches('Alpha vs Beta', same))
        self.assertFalse(nba.title_matches('The Wildcats vs Gamma', same))

    def test_non_nba_categories_use_correct_directories_and_embeds(self):
        game = nba.Game('2','Charlotte FC vs CF Montréal',self.start,'Charlotte FC','CF Montréal',
                        'Charlotte FC','CF Montréal',league='mls',category='soccer')
        self.assertEqual([name for name, _ in nba.feed_tasks(game,{})], ['StreamFree','PPV','NTV'])
        key = 'charlotte-vs-montreal'
        listing = {'streams':{'soccer':[{'name':'Charlotte vs Montreal','stream_key':key,
                                        'match_timestamp':int(self.start.timestamp())}]}}
        source = f'https://strmfree.st/embed/soccer/{key}720p2'
        with patch.object(nba, 'request', return_value={'sources':[source], 'live_sources':[source]}):
            feed, = nba.sf_feeds(listing,game)
        self.assertEqual(feed.metadata['category'], 'soccer')
        self.assertEqual(providers.extract_player(source)['category'], 'soccer')

    def test_ntv_returns_healthy_server_when_other_is_down(self):
        result = {'success':True, 'all':[{'title':'Lakers vs Clippers',
            'date':int(self.start.timestamp()*1000), 'sources':[{'url':'https://example.com/stream',
            'source':'1','channelName':'Home'}]}]}
        def fetch(url, **kwargs):
            if 'falcon' in url: raise OSError('down')
            return result
        with patch.object(nba, 'request', side_effect=fetch):
            feeds = nba.ntv_feeds(self.game)
        self.assertEqual([(f.source, f.side, f.kind) for f in feeds], [('NTV','home','candidate')])
        with patch.object(nba, 'request', side_effect=OSError('down')):
            with self.assertRaises(OSError): nba.ntv_feeds(self.game)
        result['all'][0]['live'] = False
        with patch.object(nba, 'request', return_value=result):
            self.assertEqual(nba.ntv_feeds(self.game), [])

    def test_iina_receives_headers_and_keeps_the_tui_running(self):
        url = 'https://example.com/stream.m3u8?a=1&b=2'
        headers = {'Referer':'https://example.com/', 'Bad\nHeader':'x', 'Origin':'https://example.com'}
        command = nba.iina_command('/Applications/IINA.app/Contents/MacOS/iina-cli', url, headers)
        self.assertEqual(command[-1], url)
        self.assertIn('--no-stdin', command)
        self.assertIn('--mpv-http-header-fields=Referer: https://example.com/,Origin: https://example.com', command)
        self.assertFalse(any('Bad' in x or 'keep-running' in x for x in command))
        app = nba.App(None, {'player':'iina'})
        feed = nba.Feed('Test','Test',url=url,kind='direct')
        with patch.object(nba, 'find_iina', return_value='/path/iina-cli'), \
             patch.object(app, 'render'), patch.object(app, 'resolve_feed', return_value=(url, headers)), \
             patch.object(nba.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as launch:
            app.play(feed)
        self.assertIn('Keep this terminal open', app.message)
        self.assertEqual(launch.call_args.args[0][-1], url)

    def test_linux_celluloid_launch_is_independent_and_keeps_headers(self):
        url = 'https://example.com/stream.m3u8?a=1&b=2'
        headers = {'Referer':'https://example.com/', 'Origin':'https://example.com',
                   'Unsafe':"x' --ytdl=yes", 'Other':'value\\n'}
        command = nba.celluloid_command('/usr/bin/celluloid', url, headers, cache=30)
        self.assertIn('--no-existing-session', command)
        self.assertIn('--mpv-cache-secs=30', command)
        self.assertIn('--mpv-http-header-fields=Referer: https://example.com/,Origin: https://example.com', command)
        self.assertFalse(any('Unsafe:' in x or 'Other:' in x for x in command))
        self.assertEqual(command[-1], url)
        app = nba.App(None, {'player':'celluloid'})
        process = SimpleNamespace(poll=lambda: None)
        feed = nba.Feed('Test','Test',url=url,kind='direct')
        with patch.object(nba, 'find_celluloid', return_value='/usr/bin/celluloid'), \
             patch.object(app, 'render'), patch.object(app, 'resolve_feed', return_value=(url, headers)), \
             patch.object(nba.subprocess, 'Popen', return_value=process) as launch:
            app.play(feed)
        self.assertIn('Keep this terminal open', app.message)
        self.assertEqual(launch.call_args.args[0], nba.celluloid_command('/usr/bin/celluloid', url, headers))
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        self.assertEqual(app.gui_players, [process])
        app.poll_gui_players()
        self.assertEqual(app.gui_players, [process])

    def test_wnba_league_is_loaded_only_after_selection(self):
        class Screen:
            def __init__(self): self.keys = iter([ord('1'), ord('j'), 10, ord('q')])
            def keypad(self, value): pass
            def timeout(self, value): pass
            def getch(self): return next(self.keys)
        app = nba.App(Screen(), {})
        with patch.object(nba.curses, 'curs_set'), patch.object(app, 'render'), \
             patch.object(nba, 'get_games', return_value=[]) as fetch:
            app.run()
        self.assertEqual([call.args[1] for call in fetch.call_args_list], ['wnba'])

    def test_s_changes_sport_and_settings_set_the_resolver(self):
        # Home -> live sports -> NBA -> a game's sources -> s (sport picker) -> Esc back to sources
        # -> , settings -> the PPV resolver row -> Enter.
        class Screen:
            def __init__(self): self.keys = iter([10, 10, 10, ord('s'), 27, ord(','), *[ord('j')] * 6, 10, ord('q')])
            def keypad(self, value): pass
            def timeout(self, value): pass
            def getch(self): return next(self.keys)
        app = nba.App(Screen(), {})
        modes = []
        with patch.object(nba.curses, 'curs_set'), patch.object(app, 'render', side_effect=lambda: modes.append(app.mode)), \
             patch.object(nba, 'get_games', return_value=[self.game]), patch.object(nba, 'feed_tasks', return_value=[]), \
             patch.object(app, 'configure_resolver') as resolver:
            app.run()
        self.assertEqual(modes[0], 'home')
        self.assertIn('leagues', modes); self.assertEqual(modes[modes.index('leagues', modes.index('feeds')) + 1], 'feeds')
        self.assertEqual(modes[-1], 'settings')
        resolver.assert_called_once()

    def test_feed_loading_continues_while_sport_menu_is_open(self):
        app = nba.App(None,{})
        app.games = [self.game]; app.mode = 'leagues'; app.league_cancel = 'feeds'
        app.feed_generation = 1; app.feed_pending = 1; app.feed_order = ['Source']
        app.feed_queue.put((1, 'Source', [nba.Feed('Source','One')], None))
        app.poll_feeds()
        self.assertEqual([f.label for f in app.feeds], ['One'])
        self.assertEqual(app.feed_pending, 0)

    def test_replay_catalog_and_servers_are_discovered_on_demand(self):
        catalog = '<a href="/lakers-replay">Lakers vs Blazers NBA Full Game Replay</a>' \
                  '<a href="/wnba-replay">Dream vs Aces WNBA Full Game Replay</a>' \
                  '<a href="https://other.example/ad">Lakers vs Bulls NBA Replay</a>'
        article = '<p>Server #1</p><iframe src="https://video.example/e/abc"></iframe>' \
                  '<p><a class="myButton" href="https://video.example/e/xyz">Full Match</a></p>'
        def fetch(url, **kwargs):
            return (article if url.endswith('lakers-replay') else catalog, url)
        with patch.object(providers, 'fetch_html', side_effect=fetch):
            items = replays.listing(('Basketball Replays','https://example.com/','nba'))
            self.assertEqual(len(items),1)
            servers = replays.servers(items[0])
        self.assertEqual([s.url for s in servers], ['https://video.example/e/abc','https://video.example/e/xyz'])
        self.assertEqual(servers[0].referer, items[0].url)
        self.assertTrue(servers[1].label.startswith('Full Match'))

    def test_recent_replays_cover_several_archive_pages(self):
        pages = {'https://basketball-video.com/2026-27': '<a href="/a">Bulls vs Heat NBA October 29, 2026</a>',
                 'https://basketball-video.com/2026-27?page2': '<a href="/b">Lakers vs Jazz NBA October 27, 2026</a>'
                                                              '<a href="/a">Bulls vs Heat NBA October 29, 2026</a>',
                 'https://basketball-video.com/2026-27?page3': '<a href="/c">Knicks vs Nets NBA October 25, 2026</a>'}
        def fetch(url, **kwargs):
            if url not in pages: raise OSError('gone')  # a later page failing keeps what loaded
            return pages[url], url
        with patch.object(providers, 'fetch_html', side_effect=fetch):
            items = replays.recent(('Basketball Video', 'https://basketball-video.com/2026-27', 'nba'))
            self.assertEqual([i.url.rsplit('/', 1)[1] for i in items], ['a', 'b', 'c'])
            # Sites without numbered pages are read once rather than repeating page one.
            with patch.object(replays, 'listing', return_value=[]) as listing:
                replays.recent(('WatchReplay', 'https://watchreplay.net/category/nhl/', 'nhl'))
            self.assertEqual(listing.call_count, 1)

    def test_replays_from_both_archives_list_newest_first(self):
        app = nba.App(None, {})
        app.mode = 'replays'; app.replay_generation = 1; app.replay_pending = 2
        app.replay_order = ['Basketball Replays', 'Basketball Video']
        app.replay_queue.put((1, 'Basketball Replays', [replays.Replay('Basketball Replays', 'Suns vs Kings NBA October 26, 2026', 'u1')], None))
        app.replay_queue.put((1, 'Basketball Video', [replays.Replay('Basketball Video', 'Bulls vs Heat NBA October 29, 2026', 'u2')], None))
        app.poll_replays()
        self.assertEqual([r.url for r in app.replays], ['u2', 'u1'])

    def test_replay_player_api_produces_public_media_url(self):
        payload = b'{"streaming_url":"https://media.example/video/master.m3u8"}'
        calls = []
        def open_api(request, **kwargs):
            calls.append(request)
            return io.BytesIO(payload)
        with patch.object(providers.urllib.request, 'build_opener', return_value=SimpleNamespace(open=open_api)):
            found = providers.vidara_stream("fetch('/api/stream'); file=t.streaming_url", 'https://video.example/e/abc')
        self.assertEqual(found['url'], 'https://media.example/video/master.m3u8')
        self.assertEqual(calls[0].full_url, 'https://video.example/api/stream')
        self.assertIn(b'"filecode": "abc"', calls[0].data)
        self.assertIsNone(providers.vidara_stream('ordinary page', 'https://video.example/e/abc'))

    def test_relay_retries_temporary_errors_but_not_rejected_tokens(self):
        error = urllib.error.HTTPError('https://example.com', 502, 'unavailable', {}, None)
        with patch.object(relay, '_request', side_effect=[error, b'OK']) as fetch, \
             patch.object(relay.time, 'sleep'):
            self.assertEqual(relay._download('https://example.com/a','https://example.com/'), b'OK')
        self.assertEqual(fetch.call_count, 2)
        denied = urllib.error.HTTPError('https://example.com', 403, 'forbidden', {}, None)
        with patch.object(relay, '_request', side_effect=denied) as fetch:
            with self.assertRaises(urllib.error.HTTPError):
                relay._download('https://example.com/a','https://example.com/')
        self.assertEqual(fetch.call_count, 1)

    def test_relay_refreshes_rejected_playlist_tokens(self):
        relay_server = relay.Relay()
        rejected = urllib.error.HTTPError('https://example.com/playlist', 403, 'expired', {}, None)
        token_fetches = []
        playlist_fetches = []
        def download(url, referer, **kwargs):
            if '/playlist-token?' in url:
                token_fetches.append(url)
                return b'{"token":"new","ttl":120}'
            playlist_fetches.append(url)
            if len(playlist_fetches) < 3: raise rejected
            return b'#EXTM3U\n#EXTINF:5,\nsegment.png\n'
        try:
            url = relay_server.add('https://example.com/live.m3u8', 'https://example.com',
                                   'channel', 'https://example.com/player')
            path = '/' + url.split('/', 3)[-1]
            with patch.object(relay, '_download', side_effect=download), patch.object(relay.time, 'sleep'):
                manifest, kind = relay_server.get(path)
            self.assertEqual(len(token_fetches), 3)
            self.assertEqual(len(playlist_fetches), 3)
            self.assertIn(b'/segment/', manifest)
            self.assertEqual(kind, 'application/vnd.apple.mpegurl')
        finally:
            relay_server.close()

    def test_relay_shares_one_playlist_between_concurrent_player_requests(self):
        relay_server = relay.Relay()
        downloads = []
        def download(url, referer, **kwargs):
            if '/playlist-token?' in url:
                return b'{"token":"new","ttl":120}'
            downloads.append(url)
            time.sleep(.05)
            return b'#EXTM3U\n#EXTINF:5,\nsegment.png\n'
        try:
            url = relay_server.add('https://example.com/live.m3u8', 'https://example.com',
                                   'channel', 'https://example.com/player')
            path = '/' + url.split('/', 3)[-1]
            with patch.object(relay, '_download', side_effect=download):
                results = []
                threads = [threading.Thread(target=lambda: results.append(relay_server.get(path))) for _ in range(4)]
                for thread in threads: thread.start()
                for thread in threads: thread.join()
            self.assertEqual(len(downloads),1)
            self.assertEqual(len(results),4)
            self.assertTrue(all(result == results[0] for result in results))
        finally:
            relay_server.close()

    def test_grading_checks_playlist_and_segment_without_saving_url(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                bodies = {'/master.m3u8':b'#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1280x720\nmedia.m3u8\n',
                          '/media.m3u8':b'#EXTM3U\n#EXTINF:6,\nvideo.ts\n',
                          '/video.ts':b'\x47' + b'\x00'*600,
                          '/private.m3u8':b'#EXTM3U\n#EXTINF:6,\nhttp://127.0.0.1:1/private\n'}
                body = bodies[self.path]
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
        server = http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(grading.shutil, 'which', return_value=None):
                self.assertEqual(grading.inspect_stream(base+'/master.m3u8')['height'], 720)
                with self.assertRaisesRegex(ValueError, 'Private media'):
                    grading.inspect_stream(base+'/private.m3u8')
            fake_probe = SimpleNamespace(returncode=0, stdout=b'{"streams":[{"height":1}]}')
            with patch.object(grading.shutil, 'which', return_value='ffprobe'), \
                 patch.object(grading.subprocess, 'run', return_value=fake_probe):
                self.assertIsNone(grading.inspect_stream(base+'/media.m3u8')['height'])
        finally:
            server.shutdown(); server.server_close(); thread.join()
        feed = nba.Feed('StreamFree','Source 1 | 1080p | live',url='https://example.com/secret')
        self.assertNotIn('secret', grading.source_key(feed))
        self.assertEqual(grading.summary({'checks':[True,False], 'height':720}), '? 1/2 720p')
        self.assertEqual(grading.summary({'checks':[True,True,True]}), 'A 3/3')
        self.assertGreater(grading.score({'checks':[True,True,True]}), grading.score({}))

    def test_grading_measures_whether_a_server_keeps_up_with_playback(self):
        segment = b'\x47' * 200_000
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                if self.path.endswith('.m3u8'):
                    # Six seconds of video in each segment, or a fifth of a second for the slow server.
                    body = (b'#EXTM3U\n#EXTINF:%s,\n%s.ts\n' % (b'0.2' if 'slow' in self.path else b'6', self.path[1:-5].encode()))
                    self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
                    return
                self.send_response(200); self.send_header('Content-Length', str(len(segment))); self.end_headers()
                for start in range(0, len(segment), 20_000):
                    self.wfile.write(segment[start:start+20_000])
                    if 'slow' in self.path: time.sleep(.05)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(grading.shutil, 'which', return_value=None):
                fast = grading.inspect_stream(base + '/fast.m3u8', quick=True)['speed']
                slow = grading.inspect_stream(base + '/slow.m3u8', quick=True)['speed']
        finally:
            server.shutdown(); server.server_close(); thread.join()
        self.assertGreaterEqual(fast, 2)  # a local server delivers six seconds of video far faster than real time
        self.assertLess(slow, 1)          # half a second to deliver a fifth of a second of video: playback would stall
        self.assertEqual((nba.smoothness(fast), nba.smoothness(slow)), (0, 2))

if __name__ == '__main__':
    unittest.main()
