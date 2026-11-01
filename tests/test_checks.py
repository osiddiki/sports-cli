import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sportscli import app as nba
from sportscli import checks


class Clock:
    def __init__(self): self.now = 1_000_000.0
    def __call__(self): return self.now


class CheckCacheTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, 'nested', 'checks.json')
        self.clock = Clock()
        self.feed = nba.Feed('Streamed', 'Server 1', url='https://example.com/live.m3u8?token=secret', kind='direct')

    def cache(self): return checks.CheckCache(self.path, clock=self.clock)

    def test_entry_is_kept_for_two_hours_across_restarts(self):
        first = self.cache(); first.put(self.feed, 'OK', 1080); first.save()
        self.clock.now += 2 * 3600 - 1
        entry = self.cache().get(self.feed)
        self.assertEqual((entry.status, entry.height), ('OK', 1080))
        self.clock.now += 2
        self.assertIsNone(self.cache().get(self.feed))

    def test_same_provider_and_label_in_two_games_do_not_share_results(self):
        other = nba.Feed('Streamed', 'Server 1', url='https://example.com/other-game.m3u8', kind='direct')
        cache = self.cache(); cache.put(self.feed, 'HTTP 404')
        self.assertEqual(cache.get(self.feed).status, 'HTTP 404')
        self.assertIsNone(cache.get(other))

    def test_saved_file_contains_no_url_and_is_owner_only(self):
        cache = self.cache(); cache.put(self.feed, 'OK', 720); cache.save()
        with open(self.path) as saved: text = saved.read()
        self.assertNotIn('example.com', text); self.assertNotIn('secret', text)
        self.assertEqual(os.stat(os.path.dirname(self.path)).st_mode & 0o777, 0o700)

    def test_unfinished_results_are_not_remembered(self):
        cache = self.cache()
        for status in ('?', 'checking'): cache.put(self.feed, status)
        self.assertIsNone(cache.get(self.feed))

    def test_corrupt_file_loads_empty(self):
        os.makedirs(os.path.dirname(self.path))
        for content in ('{not json', '[1, 2]', json.dumps({'x': {'status': 'OK'}})):
            with open(self.path, 'w') as out: out.write(content)
            cache = self.cache()
            self.assertIsNone(cache.get(self.feed))
            cache.put(self.feed, 'OK', None); cache.save()
            self.assertEqual(self.cache().get(self.feed).status, 'OK')


class ReopenSourcesTests(unittest.TestCase):
    def setUp(self):
        self.app = nba.App(None, {})
        self.addCleanup(self.app.close)
        self.app.mode = 'feeds'; self.app.feed_context = 'tv'
        self.app.show_failed = True  # these tests follow failed sources too; v shows them in the app
        self.calls = []
        self.lock = threading.Lock()
        self.results = {'low': 480, 'high': 1080, 'down': 404}

    def listing(self):
        return [nba.Feed('TV', name, url=f'https://example.com/{name}.m3u8', kind='direct') for name in ('down', 'low', 'high')]

    def probe(self, feed):
        with self.lock: self.calls.append(feed.label)
        height = self.results[feed.label]
        if height == 404:
            raise nba.urllib.error.HTTPError(feed.url, 404, 'Not Found', {}, None)
        return {'height': height}

    def open_sources(self, recheck=False):
        listing = self.listing()
        source = type('Channel', (), {'name': 'Example TV', 'sources': [type('S', (), dict(provider=f.source, label=f.label, url=f.url, kind=f.kind,
                                                                      headers={}, referer=''))() for f in listing]})()
        self.app.current_channel = source
        self.app.refresh_feeds(recheck=recheck)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.poll_feeds(); self.app.poll_auto_checks()
            statuses = [self.app.auto_checks.get(id(feed)) for feed in self.app.feeds]
            if self.app.feeds and all(status not in (None, 'checking') for status in statuses): break
            time.sleep(.01)
        return [(feed.label, self.app.auto_checks[id(feed)], self.app.auto_heights.get(id(feed)))
                for feed in self.app.visible_feeds()]

    def test_reopen_keeps_order_and_results_without_checking_again(self):
        with patch.object(nba, 'quick_probe_feed', side_effect=self.probe):
            first = self.open_sources()
            self.assertEqual(first, [('high', 'OK', 1080), ('low', 'OK', 480), ('down', 'HTTP 404', None)])
            self.assertEqual(sorted(self.calls), ['down', 'high', 'low'])
            self.calls.clear()
            self.assertEqual(self.open_sources(), first)
            self.assertEqual(self.calls, [])

    def test_cached_result_shows_its_age(self):
        with patch.object(nba, 'quick_probe_feed', side_effect=self.probe):
            self.open_sources(); self.open_sources()
        screen = FakeScreen()
        self.app.scr = screen
        with patch.object(nba.time, 'time', return_value=time.time() + 40 * 60): self.app.render()
        text = '\n'.join(screen.lines)
        self.assertIn('OK 40m', text); self.assertIn('HTTP 404 40m', text)
        self.assertEqual(nba.check_age(20), 'now')

    def test_r_rechecks_every_source(self):
        with patch.object(nba, 'quick_probe_feed', side_effect=self.probe):
            self.open_sources(); self.calls.clear()
            self.results['high'] = 404
            self.app.handle_key(ord('r'), 'r')
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and len(self.calls) < 3:
                self.app.poll_feeds(); self.app.poll_auto_checks(); time.sleep(.01)
            self.app.poll_auto_checks()
            self.assertEqual(sorted(self.calls), ['down', 'high', 'low'])
            self.assertEqual(self.open_sources()[-1][:2], ('high', 'HTTP 404'))

    def test_expired_result_is_checked_again(self):
        with patch.object(nba, 'quick_probe_feed', side_effect=self.probe):
            self.open_sources(); self.calls.clear()
            later = self.app.check_cache.clock() + checks.KEEP + 1
            self.app.check_cache.clock = lambda: later
            self.open_sources()
            self.assertEqual(sorted(self.calls), ['down', 'high', 'low'])


class FakeScreen:
    def __init__(self): self.lines = []
    def getmaxyx(self): return 30, 120
    def erase(self): self.lines = []
    def clear(self): self.lines = []
    def addnstr(self, y, x, s, n, attr=0): self.lines.append(s[:n])
    def refresh(self): pass
    def redrawwin(self): pass


if __name__ == '__main__':
    unittest.main()
