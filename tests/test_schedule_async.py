import datetime as dt
import threading
import time
import unittest
from unittest.mock import patch

from sportscli import app as nba


class Screen:
    def getmaxyx(self): return (24, 90)
    def erase(self): pass
    def addnstr(self, *args): pass
    def refresh(self): pass
    def keypad(self, *args): pass
    def timeout(self, *args): pass
    def get_wch(self): return 'q'


def game(ident, title):
    return nba.Game(ident, title, dt.datetime(2026, 10, 29, tzinfo=dt.timezone.utc),
                    title, title, title, title)


class ScheduleAsyncTests(unittest.TestCase):
    def setUp(self):
        self.app = nba.App(Screen(), {})
        self.app.mode = 'games'
        self.app.day = dt.date(2026, 10, 29)
        self.addCleanup(self.app.close)

    def poll_until(self, condition):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.app.poll_games()
            if condition(): return
            time.sleep(.005)
        self.fail('schedule result did not arrive')

    def test_refresh_returns_while_fetch_is_blocked_and_preserves_filter_selection(self):
        entered, release = threading.Event(), threading.Event()
        old = [game('1', 'Lakers at Heat'), game('2', 'Knicks at Heat')]
        self.app.all_games = self.app.games = old
        self.app.gi = 1
        self.app.search = 'Heat'

        def fetch(day, league):
            entered.set()
            release.wait(2)
            return [game('2', 'Knicks at Heat'), game('3', 'Celtics at Bulls')]

        try:
            with patch.object(nba, 'get_games', side_effect=fetch):
                started = time.monotonic()
                self.app.refresh_games()
                self.assertLess(time.monotonic() - started, .2)
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.app.loading_games)
                self.assertEqual(self.app.games, [])
                release.set()
                self.poll_until(lambda: not self.app.loading_games)
            self.assertEqual([g.id for g in self.app.games], ['2'])
            self.assertEqual(self.app.gi, 0)
            self.assertEqual(self.app.search, 'Heat')
        finally:
            release.set()

    def test_rapid_date_changes_keep_only_latest_pending_request(self):
        entered = [threading.Event(), threading.Event()]
        release = threading.Event()
        calls = []
        lock = threading.Lock()

        def fetch(day, league):
            with lock:
                calls.append(day)
                index = len(calls)
            if index <= 2:
                entered[index - 1].set()
                release.wait(2)
            return [game(str(day.day), f'Game {day.day}')]

        try:
            with patch.object(nba, 'get_games', side_effect=fetch):
                self.app.refresh_games()
                self.assertTrue(entered[0].wait(1))
                self.app.day += dt.timedelta(days=1)
                self.app.refresh_games()
                self.assertTrue(entered[1].wait(1))
                for _ in range(20):
                    self.app.day += dt.timedelta(days=1)
                    self.app.refresh_games()
                self.assertEqual(len(calls), 2)
                self.assertLessEqual(self.app.schedule_jobs.qsize(), 1)
                release.set()
                self.poll_until(lambda: not self.app.loading_games)
            self.assertEqual([g.id for g in self.app.games], [str(self.app.day.day)])
            self.assertEqual(calls[-1], self.app.day)
        finally:
            release.set()

    def test_temporary_view_keeps_pending_result_without_changing_message(self):
        entered, release = threading.Event(), threading.Event()

        def fetch(day, league):
            entered.set()
            release.wait(2)
            return [game('fresh', 'Fresh game')]

        try:
            with patch.object(nba, 'get_games', side_effect=fetch):
                self.app.refresh_games()
                self.assertTrue(entered.wait(1))
                self.app.mode = 'leagues'
                self.app.message = 'Choose a sport or league.'
                release.set()
                self.assertTrue(self.app.schedule_workers[0].is_alive())
                deadline = time.monotonic() + 1
                while self.app.schedule_result is None and time.monotonic() < deadline:
                    time.sleep(.005)
                self.app.poll_games()
                self.assertTrue(self.app.loading_games)
                self.assertEqual(self.app.message, 'Choose a sport or league.')
                self.app.mode = 'games'
                self.poll_until(lambda: not self.app.loading_games and bool(self.app.games))
            self.assertEqual([g.id for g in self.app.games], ['fresh'])
        finally:
            release.set()

    def test_date_change_does_not_select_previous_date_game(self):
        old = game('old', 'Old game')
        self.app.games = self.app.all_games = [old]
        self.app.schedule_selected_key = (self.app.league, self.app.day)
        self.app.day += dt.timedelta(days=1)
        with patch.object(nba, 'get_games', return_value=[game('new', 'New game'), old]):
            self.app.refresh_games()
            self.poll_until(lambda: not self.app.loading_games)
        self.assertEqual(self.app.gi, 0)

    def test_quit_does_not_wait_for_network_and_workers_stop_after_release(self):
        entered, release = threading.Event(), threading.Event()

        def fetch(day, league):
            entered.set()
            release.wait(2)
            return []

        try:
            with patch.object(nba, 'get_games', side_effect=fetch), patch.object(self.app, 'refresh_tonight'), \
                 patch.object(nba.curses, 'curs_set'), \
                 patch.object(nba.curses, 'has_colors', return_value=False):
                started = time.monotonic()
                self.app.run()
                self.assertLess(time.monotonic() - started, .2)
                release.set()
                for worker in self.app.schedule_workers:
                    worker.join(timeout=1)
                self.assertTrue(all(not worker.is_alive() for worker in self.app.schedule_workers))
        finally:
            release.set()

    def test_fetch_error_keeps_existing_retry_message(self):
        with patch.object(nba, 'get_games', side_effect=OSError('offline')):
            self.app.refresh_games()
            self.poll_until(lambda: not self.app.loading_games)
        self.assertEqual(self.app.message, 'Schedule unavailable: OSError. Press r to retry.')


if __name__ == '__main__':
    unittest.main()
