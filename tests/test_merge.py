import concurrent.futures
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sportscli import app as nba, merge, relay


def playlist(prefix, first, last, *, pdt=None, target=2):
    lines = ['#EXTM3U', f'#EXT-X-TARGETDURATION:{target}', f'#EXT-X-MEDIA-SEQUENCE:{first}']
    for n in range(first, last + 1):
        if pdt is not None: lines.append(f'#EXT-X-PROGRAM-DATE-TIME:2026-10-29T20:00:{pdt + 2 * n:02d}Z')
        lines += [f'#EXTINF:{target}.0,', f'{prefix}{n:03d}.ts']
    return merge.parse_media('\n'.join(lines) + '\n', 'https://example.com/live/index.m3u8')


def ts_with_pts(pts):
    p = bytes([0x21 | ((pts >> 29) & 0x0e), (pts >> 22) & 0xff, 0x01 | ((pts >> 14) & 0xfe),
               (pts >> 7) & 0xff, 0x01 | ((pts << 1) & 0xfe)])
    pes = b'\x00\x00\x01\xe0\x00\x00\x80\x80\x05' + p
    return b'\x47\x41\x00\x10' + pes + b'\xff' * (184 - len(pes))


class MergeTest(unittest.TestCase):
    def test_parses_media_playlists_and_rejects_features_it_cannot_splice(self):
        parsed = playlist('a_', 10, 12)
        self.assertEqual([s.seq for s in parsed.segments], [10, 11, 12])
        self.assertEqual(parsed.segments[0].url, 'https://example.com/live/a_010.ts')
        for tag in ('#EXT-X-MAP:URI="init.mp4"', '#EXT-X-KEY:METHOD=AES-128,URI="k"'):
            with self.assertRaises(merge.Unsupported):
                merge.parse_media(f'#EXTM3U\n{tag}\n#EXTINF:2,\na.ts\n', 'https://example.com/')
        self.assertEqual(len(merge.parse_media('#EXTM3U\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:2,\na.ts\n',
                                               'https://example.com/').segments), 1)

    def test_master_playlist_picks_highest_bandwidth_and_rejects_separate_audio(self):
        master = ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n'
                  '#EXT-X-STREAM-INF:BANDWIDTH=3000000,CODECS="avc1.64001f,mp4a.40.2"\nhigh.m3u8\n')
        self.assertEqual(merge.variant_url(master, 'https://example.com/x/master.m3u8'),
                         'https://example.com/x/high.m3u8')
        self.assertIsNone(merge.variant_url('#EXTM3U\n#EXTINF:2,\na.ts\n', 'https://example.com/'))
        with self.assertRaises(merge.Unsupported):
            merge.variant_url('#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="audio.m3u8"\n' + master[8:],
                              'https://example.com/')

    def test_timeline_starts_near_live_edge_and_continues_by_sequence(self):
        timeline = merge.Timeline()
        plan = timeline.plan(0, playlist('a_', 0, 9))
        self.assertEqual([s.seq for s, _ in plan], [7, 8, 9])
        for segment, discontinuity in plan: timeline.commit(0, segment, discontinuity, str(segment.seq))
        self.assertEqual([s.seq for s, _ in timeline.plan(0, playlist('a_', 5, 11))], [10, 11])

    def test_switch_continues_after_a_matching_segment_name_without_discontinuity(self):
        timeline = merge.Timeline()
        for segment, flag in timeline.plan(0, playlist('a_', 0, 5)): timeline.commit(0, segment, flag, segment.name)
        rehosted = merge.parse_media('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:900\n' +
                                     ''.join(f'#EXTINF:2,\nhttps://cdn2.example/a_{n:03d}.ts\n' for n in range(3, 8)),
                                     'https://cdn2.example/')
        plan = timeline.plan(1, rehosted)
        self.assertEqual([(s.name, flag) for s, flag in plan], [('a_006.ts', False), ('a_007.ts', False)])

    def test_switch_uses_program_date_time_when_both_sources_have_it(self):
        timeline = merge.Timeline()
        for segment, flag in timeline.plan(0, playlist('a_', 0, 5, pdt=0)): timeline.commit(0, segment, flag, segment.name)
        plan = timeline.plan(1, playlist('seg-', 0, 8, pdt=0))
        self.assertEqual(plan[0][0].name, 'seg-006.ts')
        self.assertTrue(plan[0][1])

    def test_render_marks_discontinuities_and_tracks_the_sequence(self):
        timeline = merge.Timeline(window=3, keep=4)
        for n in range(6):
            timeline.commit(0, merge.Segment(n, f'https://e/{n}.ts', 2.0), n == 3, f'k{n}')
        text = timeline.render(lambda key: f'http://127.0.0.1/m/s/{key}.ts')
        self.assertIn('#EXT-X-MEDIA-SEQUENCE:3', text)
        self.assertIn('#EXT-X-DISCONTINUITY-SEQUENCE:0\n#EXT-X-DISCONTINUITY\n#EXTINF:2.000,\nhttp://127.0.0.1/m/s/k3.ts', text)
        timeline.commit(0, merge.Segment(6, 'https://e/6.ts', 2.0), False, 'k6')
        self.assertIn('#EXT-X-DISCONTINUITY-SEQUENCE:1', timeline.render(lambda key: key))

    def test_timestamps_realign_and_wait_for_unpublished_media(self):
        self.assertEqual(merge.first_pts(ts_with_pts(123456)), 123456)
        self.assertAlmostEqual(merge.pts_seconds(5, (1 << 33) - 90000 + 5), 1.0)
        segments = playlist('seg-', 0, 9).segments
        # Segment 5 starts at 10 s; the player has already played up to 14 s.
        self.assertEqual(merge.realign(segments, 5, 10 * 90000, 14 * 90000), 7)
        self.assertEqual(merge.realign(segments, 5, 10 * 90000, 30 * 90000), len(segments))

    def test_relay_merged_session_fails_over_to_backup(self):
        server = relay.Relay()
        state = {'dead': False, 'newest': 6}
        def fetch(url, *args, **kwargs):
            if 'primary' in url:
                if state['dead']: raise relay.urllib.error.URLError('down')
                if url.endswith('.m3u8'): return ('\n'.join(['#EXTM3U', '#EXT-X-TARGETDURATION:2', '#EXT-X-MEDIA-SEQUENCE:0'] +
                                           [f'#EXTINF:2,\na_{n:03d}.ts' for n in range(state['newest'] + 1)]) + '\n').encode()
                return ts_with_pts(int(url[-6:-3]) * 180000)
            if url.endswith('.m3u8'): return ('\n'.join(['#EXTM3U', '#EXT-X-TARGETDURATION:2', '#EXT-X-MEDIA-SEQUENCE:0'] +
                                       [f'#EXTINF:2,\nseg-{n:03d}.ts' for n in range(12)]) + '\n').encode()
            return ts_with_pts(int(url[-6:-3]) * 180000)
        try:
            with patch.object(relay, '_download', side_effect=fetch), patch.object(relay.Merged, 'start'):
                url, usable = server.add_merged([('https://primary.example/live.m3u8', {}),
                                                 ('https://backup.example/live.m3u8', {})])
                session = next(iter(server.merged.values()))
                self.assertEqual(usable, 2)
                session.pump()
                self.assertEqual([e[1] for e in session.timeline.entries], ['0', '1', '2'])
                state['dead'] = True
                for source in range(2):
                    try: session.load(source)
                    except relay.urllib.error.URLError: session.sources[source]['failures'] += 1
                session.sources[0]['failures'] = 2
                self.assertTrue(session.switch('test'))
                committed = []
                original = session.timeline.commit
                session.timeline.commit = lambda i, seg, *a: (committed.append(seg.name), original(i, seg, *a))
                session.pump()
                # The primary played through 14 s; timestamps resume exactly there, not at the backup's edge.
                self.assertEqual(committed, ['seg-007.ts', 'seg-008.ts', 'seg-009.ts'])
                manifest, kind = server.get('/' + url.split('/', 3)[-1])
            self.assertIn(b'#EXT-X-DISCONTINUITY', manifest)
            self.assertEqual(kind, 'application/vnd.apple.mpegurl')
        finally:
            server.close()

    def test_relay_follows_a_mirror_when_the_active_source_segments_fail(self):
        server = relay.Relay()
        def fetch(url, *args, **kwargs):
            if url.endswith('.m3u8'): return ('\n'.join(['#EXTM3U', '#EXT-X-TARGETDURATION:2', '#EXT-X-MEDIA-SEQUENCE:0'] +
                                       [f'#EXTINF:2,\nseg-{n:03d}.ts' for n in range(8)]) + '\n').encode()
            # The primary's playlist keeps advancing while its segments time out.
            if 'primary' in url: raise TimeoutError('segment timed out')
            return ts_with_pts(int(url[-6:-3]) * 180000)
        try:
            with patch.object(relay, '_download', side_effect=fetch), patch.object(relay.Merged, 'start'):
                server.add_merged([('https://primary.example/live.m3u8', {}), ('https://mirror.example/live.m3u8', {})])
                session = next(iter(server.merged.values()))
                committed = []
                original = session.timeline.commit
                session.timeline.commit = lambda i, seg, *a: (committed.append(seg.name), original(i, seg, *a))
                session.pump()
                self.assertEqual(session.active, 1)
                session.pump()
            self.assertEqual(committed, ['seg-005.ts', 'seg-006.ts', 'seg-007.ts'])
        finally:
            server.close()

    def test_relay_does_not_wait_for_a_hung_backup_playlist(self):
        server = relay.Relay()
        hung, release = relay.threading.Event(), relay.threading.Event()
        def fetch(url, *args, **kwargs):
            if 'backup' in url and hung.is_set(): release.wait(30)
            return ('\n'.join(['#EXTM3U', '#EXT-X-TARGETDURATION:2', '#EXT-X-MEDIA-SEQUENCE:0'] +
                              [f'#EXTINF:2,\nseg-{n:03d}.ts' for n in range(8)]) + '\n').encode()
        try:
            with patch.object(relay, '_download', side_effect=fetch), patch.object(relay.Merged, 'start'):
                server.add_merged([('https://primary.example/live.m3u8', {}), ('https://backup.example/live.m3u8', {})])
                session = next(iter(server.merged.values()))
                hung.set()
                started = relay.time.monotonic()
                session.refresh()
                self.assertLess(relay.time.monotonic() - started, 1)
                self.assertTrue(session.healthy(0))
        finally:
            release.set(); server.close()


    def merged(self, fetch, server):
        with patch.object(relay, '_download', side_effect=fetch), patch.object(relay.Merged, 'start'):
            server.add_merged([('https://primary.example/live.m3u8', {}), ('https://backup.example/live.m3u8', {})])
        return next(iter(server.merged.values()))

    def test_relay_leaves_a_source_whose_segments_arrive_slower_than_real_time(self):
        server = relay.Relay()
        def fetch(url, *args, **kwargs):
            if url.endswith('.m3u8'): return ('\n'.join(['#EXTM3U', '#EXT-X-TARGETDURATION:1', '#EXT-X-MEDIA-SEQUENCE:0'] +
                                       [f'#EXTINF:0.1,\nseg-{n:03d}.ts' for n in range(12)]) + '\n').encode()
            # The primary's playlist keeps advancing, but each 0.1 s segment takes 0.15 s to arrive.
            if 'primary' in url: relay.time.sleep(.15)
            return ts_with_pts(int(url[-6:-3]) * 9000)
        try:
            session = self.merged(fetch, server)
            committed = []
            original = session.timeline.commit
            session.timeline.commit = lambda i, seg, *a: (committed.append((i, seg.name)), original(i, seg, *a))
            with patch.object(relay, '_download', side_effect=fetch):
                session.pump(); session.steer()
                self.assertEqual(session.active, 1)
                session.pump()
            self.assertEqual([name for _, name in committed], [f'seg-{n:03d}.ts' for n in range(9, 12)])
            self.assertEqual({source for source, _ in committed}, {0})
        finally:
            server.close()

    def shared_clock_session(self, server, state, backup_offset):
        def fetch(url, *args, **kwargs):
            if 'primary' in url and state['down']: raise relay.urllib.error.URLError('down')
            name = 'a' if 'primary' in url else 'b'
            if url.endswith('.m3u8'): return ('\n'.join(['#EXTM3U', '#EXT-X-TARGETDURATION:2', f'#EXT-X-MEDIA-SEQUENCE:{state["newest"] - 7}'] +
                                       [f'#EXTINF:2,\n{name}{n:03d}.ts' for n in range(state['newest'] - 7, state['newest'] + 1)]) + '\n').encode()
            return ts_with_pts(int(url[-6:-3]) * 180000 + (backup_offset if name == 'b' else 0))
        return fetch

    @staticmethod
    def reload(session):
        for index in range(len(session.sources)):
            try: session.load(index)
            except relay.urllib.error.URLError: session.sources[index]['failures'] += 1

    def drive_blip(self, backup_offset):
        """Primary goes down, playback moves to the backup, primary comes back and stays up."""
        server = relay.Relay()
        state = {'down': False, 'newest': 10}
        fetch = self.shared_clock_session(server, state, backup_offset)
        session = self.merged(fetch, server)
        session.RETURN_AFTER = 0
        committed = []
        original = session.timeline.commit
        session.timeline.commit = lambda i, seg, *a: (committed.append(seg.name), original(i, seg, *a))
        with patch.object(relay, '_download', side_effect=fetch):
            session.pump()
            state['down'] = True; state['newest'] += 2
            self.reload(session); self.reload(session)
            session.steer(); session.pump()
            state['down'] = False; state['newest'] += 2
            self.reload(session); session.steer(); session.pump()
        server.close()
        return session, committed

    def test_relay_returns_to_the_chosen_source_once_it_is_steady_again(self):
        session, committed = self.drive_blip(backup_offset=0)
        self.assertEqual(session.active, 0)
        # Timestamps line up, so each moment arrives once and in order across both switches.
        self.assertEqual([int(name[1:4]) for name in committed], list(range(8, 8 + len(committed))))
        self.assertIn('b', ''.join(name[0] for name in committed))
        self.assertEqual(committed[-1][0], 'a')

    def test_relay_stays_on_a_backup_whose_clock_differs_from_the_chosen_source(self):
        # Another encoder: returning could repeat or skip a few seconds, so the relay keeps the backup.
        session, _ = self.drive_blip(backup_offset=500 * 90000)
        self.assertEqual(session.active, 1)

class BackupSelectionTest(unittest.TestCase):
    def make_app(self):
        app = nba.App.__new__(nba.App)
        app.dual_stream, app.feed_context = True, 'live'
        app.hidden_feeds, app.manual_status, app.auto_checks, app.stream_notes = set(), {}, {}, {}
        return app

    def test_backups_keep_the_broadcast_side_and_prefer_reachable_sources(self):
        app = self.make_app()
        feed = lambda source, side, kind='direct': SimpleNamespace(source=source, side=side, kind=kind, url='u-' + source)
        chosen = feed('A', 'home')
        away, unknown, ok_home, web = feed('B', 'away'), feed('C', 'unknown'), feed('D', 'home'), feed('E', 'home', 'web')
        app.feeds = [chosen, away, unknown, ok_home, web]
        app.auto_checks = {id(ok_home): 'OK'}
        self.assertEqual(app.backup_candidates(chosen), [ok_home, unknown])
        ppv, other_home = feed('F', 'home', 'ppv'), feed('G', 'home')
        app.feeds = [chosen, ppv, other_home]
        self.assertEqual(app.backup_candidates(chosen), [other_home, ppv])
        # A PPV source gets backups too, so a stalled resolver stream can fail over.
        self.assertEqual(app.backup_candidates(ppv), [chosen, other_home])
        app.feeds = [chosen, away, unknown, ok_home, web]
        app.dual_stream = False
        self.assertEqual(app.backup_candidates(chosen), [])
        app.dual_stream, app.feed_context = True, 'replay'
        self.assertEqual(app.backup_candidates(chosen), [])

    def test_merge_backup_falls_back_to_the_selected_source(self):
        app = self.make_app()
        failed = concurrent.futures.Future(); failed.set_exception(OSError('no backup'))
        self.assertEqual(app.merge_backup('https://a/live.m3u8', {'Referer': 'x'}, [(failed, SimpleNamespace(source='B'))]),
                         ('https://a/live.m3u8', {'Referer': 'x'}))
        ready = concurrent.futures.Future(); ready.set_result(('https://b/live.m3u8', {}))
        local = SimpleNamespace(add_merged=lambda sources: ('http://127.0.0.1:1/m/x.m3u8', len(sources)))
        app.get_relay = lambda: local
        self.assertEqual(app.merge_backup('https://a/live.m3u8', {}, [(ready, SimpleNamespace(source='B'))]),
                         ('http://127.0.0.1:1/m/x.m3u8', {}))
        self.assertEqual(app.stream_notes['http://127.0.0.1:1/m/x.m3u8'], ' Backup: B.')




class BackupChoiceTest(unittest.TestCase):
    """With several working backups the relay keeps the one that resumes exactly where the primary stopped."""
    def prepared(self, sources, names, clocks, *, segment_delay=0, playlist_delay=None):
        server, probed = relay.Relay(), []
        def fetch(url, *args, **kwargs):
            if url.endswith('.m3u8'):
                host = url.split('/')[2]
                relay.time.sleep((playlist_delay or {}).get(host, 0))
                return ('\n'.join(['#EXTM3U', '#EXT-X-TARGETDURATION:2', '#EXT-X-MEDIA-SEQUENCE:0'] +
                                  [f'#EXTINF:2,\n{names[host]}{n:03d}.ts' for n in range(6)]) + '\n').encode()
            probed.append(url)
            relay.time.sleep(segment_delay)
            return ts_with_pts(clocks[url.split('/')[2]] * 90000)
        try:
            with patch.object(relay, '_download', side_effect=fetch), patch.object(relay.Merged, 'start'):
                url, usable = server.add_merged([(f'https://{host}/live.m3u8', {}) for host in sources])
            session = next(iter(server.merged.values()))
            return url, usable, session, server.kept(url), probed
        finally:
            server.close()

    def test_prefers_the_backup_sharing_the_primary_clock(self):
        names = {'main': 'a', 'other': 'b', 'same': 'c'}
        clocks = {'main': 1000, 'other': 90_000, 'same': 1010}
        _, usable, session, kept, _ = self.prepared(['main', 'other', 'same'], names, clocks)
        self.assertEqual((usable, kept), (2, [0, 2]))
        self.assertIn('same', session.sources[1]['url'])

    def test_prefers_the_backup_sharing_segment_names(self):
        names = {'main': 'seg', 'other': 'x', 'mirror': 'seg'}
        clocks = {'main': 1000, 'other': 50_000, 'mirror': 90_000}
        _, _, session, kept, _ = self.prepared(['main', 'other', 'mirror'], names, clocks)
        self.assertEqual(kept, [0, 2])

    def test_a_backup_sharing_names_needs_no_segment_download(self):
        names = {'main': 'seg', 'mirror': 'seg', 'other': 'x'}
        _, _, _, kept, probed = self.prepared(['main', 'mirror', 'other'], names, {'main': 1000, 'mirror': 90_000, 'other': 1000})
        self.assertEqual((kept, probed), ([0, 1], []))

    def test_the_primary_segment_is_downloaded_once_however_many_backups_are_compared(self):
        names = {'main': 'a', 'other': 'b', 'same': 'c'}
        clocks = {'main': 1000, 'other': 90_000, 'same': 1010}
        _, _, _, kept, probed = self.prepared(['main', 'other', 'same'], names, clocks)
        self.assertEqual(kept, [0, 2])
        self.assertEqual(sorted(url.split('/')[2] for url in probed), ['main', 'other', 'same'])

    def test_backups_are_compared_together_not_one_after_another(self):
        names = {'main': 'a', 'other': 'b', 'same': 'c'}
        clocks = {'main': 1000, 'other': 90_000, 'same': 1010}
        started = relay.time.monotonic()
        _, _, _, kept, _ = self.prepared(['main', 'other', 'same'], names, clocks, segment_delay=.3)
        self.assertEqual(kept, [0, 2])
        self.assertLess(relay.time.monotonic() - started, .75, 'three 0.3 s downloads in a row take 0.9 s')

    def test_a_backup_whose_playlist_is_slow_does_not_hold_up_playback(self):
        names = {'main': 'seg', 'slow': 'x', 'mirror': 'seg'}
        clocks = {'main': 1000, 'slow': 1000, 'mirror': 1000}
        started = relay.time.monotonic()
        with patch.object(relay.Merged, 'PREPARE_WAIT', .3, create=True):
            _, usable, session, kept, _ = self.prepared(['main', 'slow', 'mirror'], names, clocks, playlist_delay={'slow': 2})
        self.assertLess(relay.time.monotonic() - started, 1.5)
        self.assertEqual((usable, kept), (2, [0, 2]))

    def test_keeps_the_first_backup_when_none_line_up(self):
        names = {'main': 'a', 'one': 'b', 'two': 'c'}
        clocks = {'main': 1000, 'one': 50_000, 'two': 90_000}
        _, _, _, kept, _ = self.prepared(['main', 'one', 'two'], names, clocks)
        self.assertEqual(kept, [0, 1])

    def test_a_single_backup_is_used_as_is_without_probing(self):
        names = {'main': 'a', 'one': 'b'}
        _, usable, _, kept, probed = self.prepared(['main', 'one'], names, {'main': 1000, 'one': 90_000})
        self.assertEqual((usable, kept, probed), (2, [0, 1], []))

    def test_merge_backup_waits_a_moment_for_every_candidate_and_names_the_one_kept(self):
        app = BackupSelectionTest.make_app(self)
        first, second = concurrent.futures.Future(), concurrent.futures.Future()
        first.set_result(('https://b/live.m3u8', {})); second.set_result(('https://c/live.m3u8', {}))
        offered = []
        def add_merged(sources): offered.append(sources); return 'http://127.0.0.1:1/m/x.m3u8', 2
        local = SimpleNamespace(add_merged=add_merged, kept=lambda url: [0, 2])
        app.get_relay = lambda: local
        result = app.merge_backup('https://a/live.m3u8', {}, [(first, SimpleNamespace(source='B')), (second, SimpleNamespace(source='C'))])
        self.assertEqual(result, ('http://127.0.0.1:1/m/x.m3u8', {}))
        self.assertEqual([url for url, _ in offered[0]], ['https://a/live.m3u8', 'https://b/live.m3u8', 'https://c/live.m3u8'])
        self.assertEqual(app.stream_notes['http://127.0.0.1:1/m/x.m3u8'], ' Backup: C.')

    def test_merge_backup_stops_waiting_for_a_slow_candidate_after_the_grace(self):
        app = BackupSelectionTest.make_app(self)
        ready, slow = concurrent.futures.Future(), concurrent.futures.Future()
        ready.set_result(('https://b/live.m3u8', {}))
        offered = []
        local = SimpleNamespace(add_merged=lambda sources: (offered.append(sources), ('http://127.0.0.1:1/m/x.m3u8', 2))[1])
        app.get_relay = lambda: local
        started = relay.time.monotonic()
        app.merge_backup('https://a/live.m3u8', {}, [(ready, SimpleNamespace(source='B')), (slow, SimpleNamespace(source='C'))], grace=.3)
        self.assertLess(relay.time.monotonic() - started, 2)
        self.assertEqual(len(offered[0]), 2)


class RelayServerTests(unittest.TestCase):
    def test_player_dropping_a_kept_alive_connection_prints_nothing(self):
        import contextlib, io, socket, struct, time
        from sportscli import relay
        server = relay.Relay(); errors = io.StringIO()
        try:
            with contextlib.redirect_stderr(errors):
                connection = socket.create_connection(('127.0.0.1', server.http.server_port))
                connection.sendall(b'GET /missing HTTP/1.1\r\nHost: test\r\n\r\n'); connection.recv(1000)
                # An abortive close resets the connection while the handler waits for the next request.
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0)); connection.close()
                time.sleep(.3)
        finally:
            server.close()
        self.assertEqual(errors.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
