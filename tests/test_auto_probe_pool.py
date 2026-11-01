import threading
import time
import unittest
from unittest.mock import patch

from sportscli import app as nba


class AutomaticProbePoolTests(unittest.TestCase):
    def test_refreshes_replace_pending_checks_and_limit_workers(self):
        app = nba.App(None, {})
        app.mode = 'feeds'; app.feed_generation = 1
        self.addCleanup(app.close)
        release, six_started = threading.Event(), threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0
        def probe(feed):
            nonlocal active, peak
            with lock:
                active += 1; peak = max(peak, active)
                if active == 6: six_started.set()
            release.wait(3)
            with lock: active -= 1
            return True
        def pair(label):
            return [nba.Feed(label, str(i), url='https://example.com/'+str(i), kind='direct') for i in range(2)]
        try:
            with patch.object(nba, 'quick_probe_feed', side_effect=probe):
                for group in range(3): app.start_auto_checks(1, pair(str(group)))
                self.assertTrue(six_started.wait(1))
                self.assertEqual(len(app.auto_workers), 6)
                for generation in range(2, 22):
                    app.feed_generation = generation
                    app.auto_checks.clear()
                    latest = pair('Latest')
                    app.start_auto_checks(generation, latest)
                self.assertLessEqual(app.auto_jobs.qsize(), 2)
                release.set()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    app.poll_auto_checks()
                    if all(app.auto_checks.get(id(feed)) == 'OK' for feed in latest): break
                    time.sleep(.01)
                self.assertTrue(all(app.auto_checks.get(id(feed)) == 'OK' for feed in latest))
                self.assertLessEqual(peak, 6)
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
