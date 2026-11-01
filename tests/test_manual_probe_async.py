import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sportscli import app as nba


class ManualProbeTests(unittest.TestCase):
    def setUp(self):
        self.app = nba.App(None, {})
        self.app.mode = 'feeds'
        self.app.feed_generation = 1
        self.first = nba.Feed('Example', 'First', url='https://example.com/one', kind='direct')
        self.second = nba.Feed('Example', 'Second', url='https://example.com/two', kind='direct')
        self.third = nba.Feed('Example', 'Third', url='https://example.com/three', kind='direct')
        self.app.feeds = [self.first, self.second, self.third]
        self.addCleanup(self.app.close)

    def wait_until_done(self):
        deadline = time.monotonic() + 3
        while self.app.manual_checks and time.monotonic() < deadline:
            self.app.poll_manual_checks()
            time.sleep(.01)
        self.assertFalse(self.app.manual_checks, 'manual source test did not finish')

    def test_slow_test_is_background_and_keeps_current_selection_after_ranking(self):
        started, release = threading.Event(), threading.Event()
        def resolve(feed):
            started.set()
            release.wait(3)
            return feed.url, {}
        self.app.rank_by_checks = True
        try:
            with patch.object(self.app, 'resolve_feed', side_effect=resolve), \
                 patch.object(nba.grading, 'inspect_stream', return_value={'latency_ms':42, 'height':720}), \
                 patch.object(nba, 'save_config'):
                before = time.monotonic()
                self.app.test_feed(self.third)
                self.assertLess(time.monotonic()-before, .5)
                self.assertTrue(started.wait(1))
                self.assertIn('keep browsing', self.app.message)
                self.app.fi = 1  # The user moves to the second source while the test runs.
                release.set()
                self.wait_until_done()
            self.assertIs(self.app.visible_feeds()[self.app.fi], self.second)
            self.assertEqual(self.app.config['health']['Example:Third']['checks'], [True])
            self.assertEqual(self.app.config['health']['Example:Third']['height'], 720)
            self.assertEqual(self.app.manual_status[id(self.third)], 'OK')
        finally:
            release.set()

    def test_old_test_result_does_not_affect_refreshed_sources(self):
        started, release = threading.Event(), threading.Event()
        def resolve(feed):
            started.set(); release.wait(3)
            raise OSError('offline')
        try:
            with patch.object(self.app, 'resolve_feed', side_effect=resolve), patch.object(nba, 'save_config'):
                self.app.test_feed(self.first)
                self.assertTrue(started.wait(1))
                self.app.feed_generation += 1
                self.app.feeds = [self.second]
                self.app.message = 'New sources loaded.'
                release.set()
                self.wait_until_done()
            self.assertEqual(self.app.visible_feeds(), [self.second])
            self.assertNotIn('health', self.app.config)
            self.assertEqual(self.app.message, 'New sources loaded.')
        finally:
            release.set()

    def test_duplicate_and_concurrent_tests_are_bounded(self):
        release = threading.Event()
        entered = []
        def resolve(feed):
            entered.append(feed.label); release.wait(3)
            return feed.url, {}
        try:
            with patch.object(self.app, 'resolve_feed', side_effect=resolve), \
                 patch.object(nba.grading, 'inspect_stream', return_value={'latency_ms':1, 'height':None}), \
                 patch.object(nba, 'save_config'):
                self.app.test_feed(self.first)
                self.app.test_feed(self.first)
                self.assertIn('already', self.app.message)
                self.app.test_feed(self.second)
                self.app.test_feed(self.third)
                self.assertIn('Two source tests', self.app.message)
                self.assertEqual(len(self.app.manual_checks), 2)
                release.set()
                self.wait_until_done()
            self.assertCountEqual(entered, ['First', 'Second'])
        finally:
            release.set()

    def test_completed_test_does_not_overwrite_another_screen(self):
        with patch.object(self.app, 'resolve_feed', return_value=(self.first.url, {})), \
             patch.object(nba.grading, 'inspect_stream', return_value={'latency_ms':1, 'height':None}), \
             patch.object(nba, 'save_config'):
            self.app.test_feed(self.first)
            self.app.mode = 'search'; self.app.message = 'Search message.'
            self.wait_until_done()
        self.assertEqual(self.app.message, 'Search message.')
        self.assertEqual(self.app.config['health']['Example:First']['checks'], [True])

    def test_reachability_ranking_preserves_selection_as_checks_finish(self):
        self.app.rank_by_checks = True
        self.app.fi = 1
        for feed in self.app.feeds: self.app.auto_checks[id(feed)] = 'checking'
        self.app.auto_queue.put((1, self.third, 'OK'))
        self.app.poll_auto_checks()
        self.assertEqual(self.app.visible_feeds(), [self.third, self.first, self.second])
        self.assertIs(self.app.visible_feeds()[self.app.fi], self.second)
        self.app.auto_queue.put((1, self.first, 'HTTP 403'))
        self.app.poll_auto_checks()
        self.assertEqual(self.app.visible_feeds(), [self.third, self.second])  # a failed source is hidden
        self.assertIs(self.app.visible_feeds()[self.app.fi], self.second)

    def test_manual_success_overrides_an_older_automatic_failure(self):
        self.app.auto_checks[id(self.first)] = 'HTTP 403'
        with patch.object(self.app, 'resolve_feed', return_value=(self.first.url, {})), \
             patch.object(nba.grading, 'inspect_stream', return_value={'latency_ms':1, 'height':None}), \
             patch.object(nba, 'save_config'):
            self.app.test_feed(self.first)
            self.wait_until_done()
        self.app.rank_by_checks = True
        self.assertIs(self.app.visible_feeds()[0], self.first)
        self.assertEqual(self.app.manual_status[id(self.first)], 'OK')

    def test_closing_prevents_late_relay_creation(self):
        self.app.close()
        with patch.object(nba.relay, 'Relay') as factory, self.assertRaises(nba.DependencyError):
            self.app.get_relay()
        factory.assert_not_called()

    def test_concurrent_resolutions_share_one_resolver_process(self):
        ready = threading.Event()
        errors = []
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            (root / 'src').mkdir(); (root / 'node_modules').mkdir()
            (root / 'src' / 'server.js').write_text('// test')
            self.app.config['resolver_dir'] = str(root)
            def resolve():
                try: self.app.ensure_helper()
                except Exception as exc: errors.append(exc)
            with patch.object(self.app, 'helper_running', side_effect=ready.is_set), \
                 patch.object(nba.shutil, 'which', return_value='/usr/bin/node'), \
                 patch.object(nba.subprocess, 'Popen') as launch:
                launch.return_value.poll.return_value = None
                workers = [threading.Thread(target=resolve) for _ in range(2)]
                for worker in workers: worker.start()
                deadline = time.monotonic() + 2
                while launch.call_count == 0 and time.monotonic() < deadline: time.sleep(.01)
                ready.set()
                for worker in workers: worker.join(timeout=2)
                self.assertFalse(any(worker.is_alive() for worker in workers))
                self.assertEqual(errors, [])
                launch.assert_called_once()


if __name__ == '__main__':
    unittest.main()
