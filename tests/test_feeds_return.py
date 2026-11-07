"""Leaving a source list for another screen and coming back keeps its loading and checks going."""
import datetime as dt
import tempfile
import threading
import time
import unittest
from unittest import mock

from sportscli import app, checks, remote


def wait(a, done, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        a.poll()
        if done(): return
        time.sleep(.02)


class FeedsReturn(unittest.TestCase):
    def setUp(self):
        self.gate = threading.Event()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def screen(self, tasks):
        patch = mock.patch.object(app, 'feed_tasks', lambda game, config: tasks)
        patch.start(); self.addCleanup(patch.stop)
        a = remote.Headless({}); self.addCleanup(a.close)
        a.check_cache = checks.CheckCache(path=f'{self.folder.name}/checks.json')
        game = app.Game('g1', 'A at B', dt.datetime.now(dt.timezone.utc), '', '', 'B', 'A', [], 'nba', 'basketball', state='live')
        a.games = a.all_games = [game]; a.gi = 0; a.mode = 'games'
        return a

    def test_sources_that_arrive_while_settings_is_open_are_shown_on_return(self):
        def slow():
            self.gate.wait(); return [app.Feed('X', 'one', url='http://example.invalid/a', kind='web')]
        for away in (',', '?'):
            with self.subTest(away=away):
                self.gate.clear()
                a = self.screen([('Slow', slow)])
                a.open_feeds('live', None)
                a.handle_key(ord(away), None)
                self.gate.set(); time.sleep(.2); a.poll()
                a.handle_key(27, None)
                wait(a, lambda: not a.feed_pending)
                self.assertEqual((a.mode, a.feed_pending, len(a.feeds)), ('feeds', 0, 1))
                self.assertFalse(remote.view(a)['busy'])

    def test_checks_finish_after_a_trip_to_another_section_and_back(self):
        feeds = [app.Feed('X', f'f{i}', url=f'http://example.invalid/{i}', kind='direct') for i in range(3)]
        def probe(feed):
            self.gate.wait(); return {'height': 720, 'speed': 3}
        patch = mock.patch.object(app, 'quick_probe_feed', probe)
        patch.start(); self.addCleanup(patch.stop)
        for away in (',', '/', 's'):
            with self.subTest(away=away):
                self.gate.clear()
                a = self.screen([('Fast', lambda: list(feeds))])
                a.open_feeds('live', None)
                wait(a, lambda: a.feeds and not a.feed_pending)
                a.handle_key(ord(away), None)
                self.gate.set(); time.sleep(.2); a.poll()
                a.handle_key(27, None)
                wait(a, lambda: all(a.auto_checks.get(id(f)) != 'checking' for f in a.feeds))
                self.assertEqual(a.mode, 'feeds')
                self.assertNotIn('checking', [a.auto_checks.get(id(f)) for f in a.feeds])


if __name__ == '__main__':
    unittest.main()
