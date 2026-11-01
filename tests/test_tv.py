import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sportscli import ai_ad_mute, app as nba, tv

PLAYLIST = '''#EXTM3U
#EXTINF:-1 tvg-id="CNN.us@SD" group-title="News",CNN (720p)
#EXTVLCOPT:http-referrer=https://player.example/
#EXTVLCOPT:http-user-agent=Agent/1
https://cdn.example/cnn/index.m3u8
#EXTINF:-1 tvg-id="CNBC.us@SD",CNBC (720p) [Geo-blocked]
https://cdn.example/cnbc.m3u8
#EXTINF:-1,Late Night 18+ (480p)
https://cdn.example/adult.m3u8
#EXTINF:-1,Local Loopback
http://127.0.0.1/stream.m3u8
#EXTINF:-1,Radio
rtmp://cdn.example/radio
'''

DADDY = '''<a href="/watch.php?id=345"><span>CNN USA</span> ID: 345</a>
<a class="x" href="/watch.php?id=327">MSNBC
   ID: 327</a>
<a href="/watch.php?id=800">6&#039;eren Denmark ID: 800</a>
<a href="/watch.php?id=501">18+ (Player-01) ID: 501</a>
<a href="/watch.php?id=345">CNN USA ID: 345</a>
<a href="/schedule.php">Schedule</a>'''


class ChannelTests(unittest.TestCase):
    def test_playlist_keeps_headers_and_skips_unplayable_entries(self):
        found = tv.iptv_channels(PLAYLIST)
        self.assertEqual([name for name, _ in found], ['CNN', 'CNBC'])
        cnn = found[0][1]
        self.assertEqual(cnn.kind, 'direct')
        self.assertEqual(dict(cnn.headers), {'Referer': 'https://player.example/', 'User-Agent': 'Agent/1'})
        self.assertEqual(cnn.label, 'stream')  # the measured height replaces the playlist's claim
        self.assertEqual(found[1][1].label, 'stream (may be US-only)')
        self.assertEqual(found[1][1].headers, ())

    def test_other_playlists_keep_their_name_and_skip_dead_or_local_streams(self):
        text = '''#EXTM3U
#EXTINF:-1 tvg-status="stable",MS NOW
http://95.143.42.59:1234/stream/msnow/master.m3u8
#EXTINF:-1 tvg-status="not-working",C-SPAN
http://95.143.42.59:1234/stream/cspan/master.m3u8
#EXTINF:-1,Router
http://192.168.1.1:8080/live.m3u8
'''
        found = tv.iptv_channels(text, 'Aria')
        self.assertEqual([(name, source.provider, source.url) for name, source in found],
                         [('MS NOW', 'Aria', 'http://95.143.42.59:1234/stream/msnow/master.m3u8')])

    def test_daddylive_list_resolves_stream_pages_on_selection(self):
        found = tv.daddylive_channels(DADDY, 'https://thedaddy.example/24-7-channels.php')
        self.assertEqual([name for name, _ in found], ['CNN USA', 'MSNBC', "6'eren Denmark"])
        cnn = found[0][1]
        self.assertEqual((cnn.kind, cnn.url, cnn.referer),
                         ('candidate', 'https://thedaddy.example/stream/stream-345.php',
                          'https://thedaddy.example/watch.php?id=345'))

    def test_featured_channels_merge_spellings_and_prefer_direct_streams(self):
        found = tv.daddylive_channels(DADDY, 'https://thedaddy.example/') + tv.iptv_channels(PLAYLIST)
        channels = tv.group(found)
        self.assertEqual([c.name for c in channels[:3]], ['CNN', 'CNBC', 'MS NOW'])
        self.assertTrue(all(c.featured for c in channels[:3]))
        self.assertEqual([s.provider for s in channels[0].sources], ['iptv-org', 'DaddyLive'])
        self.assertEqual(channels[-1].name, "6'eren Denmark")
        self.assertFalse(channels[-1].featured)

    def test_redzone_feeds_merge_into_one_featured_channel(self):
        text = """#EXTM3U
#EXTINF:-1,NFL RedZone
https://cdn.example/rz1.m3u8
#EXTINF:-1,NFL RedZone | Main Feed (FUTBOLX)
https://cdn.example/rz2.m3u8
"""
        channels = tv.group(tv.iptv_channels(text, 'doms9'))
        self.assertEqual([(c.name, c.featured, len(c.sources)) for c in channels], [('NFL RedZone', True, 2)])
        self.assertTrue(tv.matches(channels[0], 'red zone'))

    def test_filter_matches_every_word(self):
        channel = tv.Channel('Fox News Channel')
        self.assertTrue(tv.matches(channel, 'fox news'))
        self.assertTrue(tv.matches(channel, ''))
        self.assertFalse(tv.matches(channel, 'fox business'))


class TvModeTests(unittest.TestCase):
    def test_channel_sources_become_feeds_with_headers_and_referers(self):
        app = nba.App(None, {})
        app.current_channel = tv.Channel('CNN', [
            tv.Source('iptv-org', '720p', 'https://cdn.example/cnn.m3u8', 'direct', (('Referer', 'https://p.example/'),)),
            tv.Source('DaddyLive', 'channel 345', 'https://d.example/stream/stream-345.php', 'candidate',
                      referer='https://d.example/watch.php?id=345')])
        app.feed_context = 'tv'
        app.feed_workers = [object()]  # keep the test from starting worker threads
        app.refresh_feeds()
        _, name, operation = app.feed_jobs.get_nowait()
        direct, page = operation()
        self.assertEqual(name, 'Channel sources')
        self.assertEqual((direct.kind, direct.headers), ('direct', {'Referer': 'https://p.example/'}))
        self.assertEqual((page.kind, page.metadata), ('candidate', {'referer': 'https://d.example/watch.php?id=345'}))

    def test_channel_list_loads_in_background_and_filters(self):
        app = nba.App(None, {})
        app.mode = 'games'
        channels = [tv.Channel('CNN', [object()], True), tv.Channel('Fox News', [object()], True)]
        with patch.object(tv, 'directory', return_value=(channels, ['iptv-org: URLError'])):
            app.open_section('tv')
            app.channel_queue.put(app.channel_queue.get(timeout=2))
            self.assertTrue(app.poll_channels())
        self.assertEqual(app.mode, 'channels')
        self.assertIn('iptv-org: URLError', app.message)
        app.channel_filter = 'fox'
        self.assertEqual([c.name for c in app.visible_channels()], ['Fox News'])

    def test_tv_channels_never_start_the_ad_detector(self):
        class Screen:
            def refresh(self): pass
        app = nba.App(Screen(), {}, ai_ad_mute_enabled=True)
        app.league = 'nba'; app.feed_context = 'tv'; app.render = lambda: None
        app.resolve_feed = lambda feed: ('https://example.com/cnn.m3u8', {})
        with patch.object(ai_ad_mute, 'installed', return_value=True), \
             patch.object(ai_ad_mute, 'Monitor') as monitor, \
             patch.object(nba.shutil, 'which', return_value='/usr/bin/mpv'), \
             patch.object(nba.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
             patch.object(nba.curses, 'def_prog_mode'), patch.object(nba.curses, 'endwin'), \
             patch.object(nba.curses, 'reset_prog_mode'):
            app.play(nba.Feed('iptv-org', '720p', kind='direct'))
        monitor.assert_not_called()
        self.assertIn('stays off for TV channels', app.message)

    def test_search_finds_channels_and_opens_their_sources(self):
        app = nba.App(None, {})
        app.channels = [tv.Channel('CNBC', [tv.Source('iptv-org', '720p', 'https://tv.example/cnbc.m3u8', 'direct')], True),
                        tv.Channel('CNN', [], True), tv.Channel('BBC News', [], False)]
        app.mode = 'home'; app.feed_workers = [object()]
        with patch.object(app, 'refresh_feeds'):
            app.handle_key(ord('/'), '/')
            for char in 'cnbc': app.handle_key(ord(char), char)
            channels = [hit for hit in app.search_results if hit.kind == 'channel']
            self.assertEqual([hit.title for hit in channels], ['CNBC'])
            self.assertEqual(app.search_results[0].title, 'CNBC')  # a channel name leads with channels
            self.assertIn((0, '   CNBC', ''), app.search_rows())
            app.search_position = app.search_results.index(channels[0])
            app.handle_key(10, None)
            self.assertEqual((app.mode, app.feed_context, app.current_channel.name), ('feeds', 'tv', 'CNBC'))
            app.handle_key(27, None)
            self.assertEqual(app.mode, 'search')

    def test_nfl_sunday_schedule_leads_with_redzone(self):
        import datetime as dt
        sunday, monday = dt.date(2026, 11, 1), dt.date(2026, 11, 2)
        game = nba.Game('1', 'Bills at Chiefs', dt.datetime(2026, 11, 1, 18, tzinfo=dt.timezone.utc), 'Kansas City Chiefs',
                        'Buffalo Bills', 'Chiefs', 'Bills', league='nfl', category='football', state='live')
        redzone = tv.Channel('NFL RedZone', [tv.Source('doms9', 'stream', 'https://tv.example/rz.m3u8', 'direct')], True)
        app = nba.App(None, {})
        app.league, app.mode, app.all_games, app.feed_workers = 'nfl', 'games', [game], [object()]
        app.day = monday; app.apply_search()
        self.assertEqual([g.title for g in app.games], ['Bills at Chiefs'])
        with patch.object(tv, 'directory', return_value=([redzone], [])):
            app.day = sunday; app.apply_search()
            self.assertEqual((app.games[0].id, app.games[0].state, app.games[1].title), (nba.REDZONE, 'redzone-live', 'Bills at Chiefs'))
            app.handle_key(10, None)  # Enter while the channel list is still loading
            self.assertIn('Loading NFL RedZone', app.message)
            app.channel_queue.put(app.channel_queue.get(timeout=2))
            with patch.object(app, 'refresh_feeds'): app.poll_channels()
        self.assertEqual((app.mode, app.feed_context, app.current_channel.name), ('feeds', 'tv', 'NFL RedZone'))
        app.handle_key(27, None)
        self.assertEqual((app.mode, app.games[app.gi].id), ('games', nba.REDZONE))
        app.search = 'chiefs'; app.apply_search()
        self.assertEqual([g.title for g in app.games], ['Bills at Chiefs'])

    def test_backups_cover_tv_channels(self):
        app = nba.App(None, {})
        app.feed_context = 'tv'
        first, second = nba.Feed('iptv-org', '720p', url='a', kind='direct'), nba.Feed('DaddyLive', 'ch', url='b', kind='candidate')
        app.feeds = [first, second]
        self.assertEqual(app.backup_candidates(first), [second])


if __name__ == '__main__':
    unittest.main()
