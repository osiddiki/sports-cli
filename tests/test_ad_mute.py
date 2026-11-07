import http.server
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from sportscli import ad_mute, ai_ad_mute, app as nba, remote
from tests.test_screens import Screen


PLAYLIST = '''#EXTM3U
#EXT-X-VERSION:3
#EXTINF:2.5,
one.ts
#EXTINF:2.5,
two.ts
#EXT-X-CUE-OUT:5
#EXTINF:3,
ad1.ts
#EXTINF:2,
ad2.ts
#EXT-X-CUE-IN
#EXTINF:4,
three.ts
#EXT-X-ENDLIST
'''


class AdMuteTests(unittest.TestCase):
    def test_ad_mute_requires_explicit_run_flag_even_with_old_config(self):
        self.assertFalse(nba.App(None, {'ad_mute': True}).ad_mute)
        self.assertTrue(nba.App(None, {}, ad_mute_enabled=True).ad_mute)

    def test_cues_are_only_used_for_complete_playlists(self):
        self.assertEqual(ad_mute.cue_intervals(PLAYLIST), [(5.0, 10.0)])
        self.assertEqual(ad_mute.cue_intervals(PLAYLIST.replace('#EXT-X-ENDLIST', '')), [])
        self.assertEqual(ad_mute.cue_intervals(PLAYLIST.replace('#EXT-X-CUE-OUT:5', '# no cue')), [])
        unfinished = PLAYLIST.replace('#EXT-X-CUE-IN', '# no cue')
        self.assertEqual(ad_mute.cue_intervals(unfinished), [(5.0, 10.0)])

    def test_only_undoes_its_own_mute_and_respects_manual_unmute(self):
        state = ad_mute.MuteState()
        self.assertIsNone(state.update(False, False))
        self.assertTrue(state.update(True, False))
        self.assertIsNone(state.update(True, True))
        self.assertFalse(state.update(False, True))
        self.assertIsNone(state.update(True, True))
        self.assertIsNone(state.update(False, True))
        self.assertTrue(state.update(True, False))
        self.assertIsNone(state.update(True, False))  # User unmuted mid-break.
        self.assertIsNone(state.update(False, False))

    def test_follows_hls_variant_and_fetches_cues_locally(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/master.m3u8':
                    body = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=1280x720\nvideo.m3u8\n'
                elif self.path == '/video.m3u8': body = PLAYLIST
                else:
                    self.send_error(404); return
                body = body.encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def log_message(self, *args): pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/master.m3u8'
            self.assertEqual(ad_mute.marked_replay_intervals(url), [(5.0, 10.0)])
        finally:
            server.shutdown(); server.server_close(); worker.join()


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


class SettingsAdMuteTests(unittest.TestCase):
    def test_settings_offers_ad_mute_and_installs_the_model_only_when_chosen(self):
        calls = []

        def fake_install():
            if row(app, 'Ad detector') != 'installing':
                raise AssertionError('Settings did not show the detector as installing')
            calls.append('install')
            if len(calls) == 1:
                return 1
            python, model = ai_ad_mute.installation_paths()
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text('python')
            model.write_bytes(b'model')
            return 0

        def refuse_download(*_args, **_kwargs):
            raise AssertionError('the app downloaded the ad model')

        def row(app, label):
            return next(value for name, value, _ in app.settings_rows() if name == label)

        def choose(app, label):
            app.handle_key(ord(','), ',') if app.mode != 'settings' else None
            app.settings_index = [name for name, _, _ in app.settings_rows()].index(label)
            app.handle_key(10, '\n')

        config = {'ad_mute': True, 'ai_ad_mute': True, 'ai_ad_observe': True}
        with tempfile.TemporaryDirectory() as root, \
             patch.dict(os.environ, {'XDG_DATA_HOME': root}), \
             patch.object(ai_ad_mute, 'install', fake_install), \
             patch.object(ai_ad_mute.urllib.request, 'urlopen', refuse_download), \
             patch.object(nba.threading, 'Thread', ImmediateThread), \
             patch.object(nba, 'get_games', return_value=[]):
            app = nba.App(Screen(), config)
            self.assertFalse(app.ad_mute or app.ai_ad_mute or app.ai_ad_observe)
            app.handle_key(ord(','), ',')
            self.assertEqual(app.mode, 'settings')
            self.assertEqual(row(app, 'Ad mute'), 'off')
            self.assertEqual(row(app, 'Ad detector'), 'not installed')
            app.render()
            drawn = '\n'.join(app.scr.text[y] for y in sorted(app.scr.text))
            self.assertRegex(drawn, r'Ad mute\s+off')
            self.assertRegex(drawn, r'Ad detector\s+not installed')
            shown = remote.view(app)
            pairs = [tuple(item['cells'][:2]) for item in shown['rows']]
            self.assertEqual(shown['title'], 'Settings')
            self.assertIn(('Ad mute', 'off'), pairs)
            self.assertIn(('Ad detector', 'not installed'), pairs)
            self.assertEqual(calls, [])

            with patch.object(sys, 'argv', ['sports-cli', '--list', '--league', 'nba']):
                self.assertEqual(nba.main(), 0)
            with patch.object(sys, 'argv', ['sports-cli', '--ai-ad-mute']):
                self.assertEqual(nba.main(), 2)
            for enabled, label in (
                ({'ad_mute_enabled': True}, 'marked replays'),
                ({'ai_ad_observe_enabled': True}, 'preview, sound stays on'),
                ({'ai_ad_mute_enabled': True}, 'mute ads'),
            ):
                started = nba.App(Screen(), {}, **enabled)
                started.handle_key(ord(','), ',')
                self.assertEqual(row(started, 'Ad mute'), label)
                self.assertEqual(row(started, 'Ad detector'), 'not installed')
            self.assertEqual(calls, [])

            for label, marked, preview, mute in (
                ('marked replays', True, False, False),
                ('preview, sound stays on', False, True, False),
                ('mute ads', False, False, True),
                ('off', False, False, False),
            ):
                choose(app, 'Ad mute')
                self.assertEqual(row(app, 'Ad mute'), label)
                self.assertEqual((app.ad_mute, app.ai_ad_observe, app.ai_ad_mute), (marked, preview, mute))
                if label == 'preview, sound stays on':
                    self.assertIn('sound stays on', app.message)
                if label == 'mute ads':
                    self.assertIn('can mute game coverage', app.message)
                    self.assertIn('not installed', app.message)
                    self.assertFalse(app.player_ready(nba.Feed('Directory', 'Home', 'home', 'https://example.test/a', 'direct')))
                    self.assertIn('Choose Ad detector in Settings', app.message)
            self.assertEqual(config, {'ad_mute': True, 'ai_ad_mute': True, 'ai_ad_observe': True})
            self.assertEqual(calls, [])

            fresh = nba.App(Screen(), config)
            fresh.handle_key(ord(','), ',')
            self.assertEqual(row(fresh, 'Ad mute'), 'off')
            self.assertFalse(fresh.ad_mute or fresh.ai_ad_mute or fresh.ai_ad_observe)

            choose(app, 'Ad detector')
            self.assertEqual(calls, ['install'])
            self.assertEqual(row(app, 'Ad detector'), 'not installed')
            self.assertEqual(app.message, 'Ad detector installation failed.')
            choose(app, 'Ad detector')
            self.assertEqual(calls, ['install', 'install'])
            self.assertEqual(row(app, 'Ad detector'), 'installed')
            self.assertEqual(app.message, 'Ad detector is installed.')
            app.render()
            drawn = '\n'.join(app.scr.text[y] for y in sorted(app.scr.text))
            self.assertRegex(drawn, r'Ad detector\s+installed')
            choose(app, 'Ad detector')
            self.assertEqual(calls, ['install', 'install'])
            self.assertEqual(remote.view(app)['rows'][[name for name, _, _ in app.settings_rows()].index('Ad detector')]['cells'][1], 'installed')

            again = nba.App(Screen(), config)
            again.handle_key(ord(','), ',')
            self.assertEqual(row(again, 'Ad mute'), 'off')
            self.assertEqual(row(again, 'Ad detector'), 'installed')
            self.assertEqual(calls, ['install', 'install'])


if __name__ == '__main__':
    unittest.main()
