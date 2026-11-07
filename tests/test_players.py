import concurrent.futures
import datetime as dt
import threading
import time
import unittest
from unittest.mock import patch

from sportscli import app, players, smart_search


class PlayerDirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = smart_search.default_index()
        cls.warriors = next(team for team in cls.index.teams
                            if team.league == 'nba' and team.display == 'Golden State Warriors')
        cls.fever = next(team for team in cls.index.teams
                         if team.league == 'wnba' and team.display == 'Indiana Fever')

    def rows(self):
        return {'results':[{'type':'player', 'contents':[
            {'uid':'s:40~l:46~a:3975', 'displayName':'Stephen Curry',
             'defaultLeagueSlug':'nba', 'subtitle':'Golden State Warriors'},
            {'uid':'s:40~l:46~a:100', 'displayName':'Former Player',
             'defaultLeagueSlug':'nba', 'subtitle':'Golden State Warriors'},
            {'uid':'s:40~l:59~a:4433403', 'displayName':'Caitlin Clark',
             'defaultLeagueSlug':'wnba', 'subtitle':'Indiana Fever'},
            {'uid':'s:99~a:1', 'displayName':'Unknown',
             'defaultLeagueSlug':'other', 'subtitle':'Unknown'},
        ]}]}

    def test_filters_retired_players_and_reuses_grouped_roster(self):
        directory = players.Directory(self.index, app.LEAGUES)
        calls = []
        def request(url, **kwargs):
            calls.append(url)
            if '/search/v2?' in url: return self.rows()
            if '/basketball/nba/' in url:
                return {'athletes':[{'position':'Guard', 'items':[{'id':'3975'}]}]}
            return {'athletes':[{'id':'4433403'}]}
        updates = []
        found = directory.search('curry', None, request, on_update=updates.append)
        self.assertTrue(updates and all(not player.verified for player in updates[0]))
        self.assertEqual(updates[-1], found)
        self.assertEqual([(x.name, x.team, x.verified) for x in found],
                         [('Stephen Curry', self.warriors, True), ('Caitlin Clark', self.fever, True)])
        self.assertEqual(len(calls), 3)
        directory.search('stephen', 'nba', request)
        self.assertEqual(len(calls), 4, 'cached roster should not be downloaded again')

    def test_rosters_are_checked_at_once(self):
        directory = players.Directory(self.index, app.LEAGUES)
        def request(url, **kwargs):
            if '/search/v2?' in url: return self.rows()
            time.sleep(.4)
            return {'athletes':[{'id':'3975'}, {'id':'4433403'}]}
        updates = []
        started = time.monotonic()
        found = directory.search('curry', None, request, on_update=updates.append)
        self.assertLess(time.monotonic() - started, .65)
        self.assertEqual([(x.name, x.verified) for x in found], [('Stephen Curry', True), ('Caitlin Clark', True)])
        self.assertEqual(len(updates), 3, 'one tentative list, then one update per roster in order')
        self.assertEqual([x.verified for x in updates[1]], [True, False])

    def test_unavailable_roster_is_labeled_unverified(self):
        directory = players.Directory(self.index, app.LEAGUES)
        def request(url, **kwargs):
            if '/search/v2?' in url: return self.rows()
            raise OSError('offline')
        found = directory.search('curry', 'nba', request)
        self.assertEqual([player.name for player in found], ['Stephen Curry', 'Former Player'])
        self.assertTrue(all(not player.verified for player in found))

    def test_canceled_search_stops_before_request(self):
        directory = players.Directory(self.index, app.LEAGUES)
        with self.assertRaises(concurrent.futures.CancelledError):
            directory.search('curry', None, lambda *args, **kwargs: self.fail('unexpected request'), lambda: True)

    def test_suggestions_arrive_before_slow_roster_checks(self):
        directory = players.Directory(self.index, app.LEAGUES)
        started, release, published = threading.Event(), threading.Event(), threading.Event()
        updates = []
        def request(url, **kwargs):
            if '/search/v2?' in url: return self.rows()
            started.set(); release.wait(2)
            return {'athletes':[{'id':'3975'}]}
        def publish(value):
            updates.append(value); published.set()
        worker = threading.Thread(target=directory.search,
                                  args=('curry', 'nba', request), kwargs={'on_update':publish})
        try:
            worker.start()
            self.assertTrue(published.wait(1))
            self.assertTrue(started.wait(1))
            self.assertFalse(updates[0][0].verified)
            self.assertTrue(worker.is_alive())
            release.set(); worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(updates[-1][0].verified)
            self.assertEqual([player.name for player in updates[-1]], ['Stephen Curry'])
        finally:
            release.set(); worker.join(2)


class PlayerUiTests(unittest.TestCase):
    def setUp(self):
        self.app = app.App(None, {})
        self.app.mode = 'search'
        self.addCleanup(self.app.close)
        self.today = dt.date(2026, 10, 29)
        index = smart_search.default_index()
        team = next(t for t in index.teams if t.league == 'nba' and t.display == 'Golden State Warriors')
        self.player = players.Player('3975', 'Stephen Curry', team, True)

    def test_player_result_opens_team_with_date_and_opponent(self):
        self.app.search_query = 'stephen curry vs lakers oct 27 replay'
        with patch.object(app, 'default_day', return_value=self.today), \
             patch.object(app.replays, 'catalogs', return_value=[]):
            self.app.update_global_search()
            self.app.player_cache[self.app.player_key] = (time.monotonic()+60, [self.player], None)
            self.app.player_due = None
            self.app.rebuild_global_results()
            self.assertEqual(self.app.search_results[0].kind, 'player')
            self.assertIn('Golden State Warriors', '\n'.join(row[1] for row in self.app.search_rows()))
            self.assertIn('Stephen Curry · NBA · Warriors', '\n'.join(row[1] for row in self.app.search_rows(40)))
            self.app.open_global_result()
            self.assertEqual(self.app.search_plan.primary.display, 'Golden State Warriors')
            self.assertEqual(self.app.search_plan.query.fragments, ('golden state warriors', 'lakers'))
            self.assertEqual(self.app.search_plan.query.date, dt.date(2026,10,27))
            self.assertEqual(self.app.search_plan.query.intent, 'replay')

    def test_stale_player_lookup_cannot_replace_new_query(self):
        started, release = threading.Event(), threading.Event()
        self.app.search_query = 'stephen curry'
        with patch.object(app, 'default_day', return_value=self.today), \
             patch.object(app.replays, 'catalogs', return_value=[]):
            self.app.update_global_search()
            def search(fragment, league, request, canceled, on_update):
                started.set(); release.wait(2)
                return [self.player]
            try:
                with patch.object(self.app.player_directory, 'search', side_effect=search):
                    self.app.player_due = 0; self.app.start_player_search()
                    self.assertTrue(started.wait(1))
                    self.app.search_query = 'warriors'; self.app.update_global_search()
                    release.set()
                    deadline = time.monotonic()+2
                    while self.app.player_inflight and time.monotonic() < deadline:
                        self.app.poll_player_search(); time.sleep(.01)
                    self.assertFalse(any(hit.kind == 'player' for hit in self.app.search_results))
                    self.assertEqual(self.app.search_plan.primary.display, 'Golden State Warriors')
            finally:
                release.set()

    def test_partial_player_results_are_updated_by_roster_verification(self):
        self.app.search_query = 'stephen curry'
        self.app.update_global_search()
        key = self.app.player_key
        tentative = players.Player(self.player.id, self.player.name, self.player.team, False)
        self.app.player_inflight.add(key)
        self.app.player_queue.put((key, [tentative], None, False, True))
        self.app.poll_player_search()
        self.assertEqual(self.app.search_results[0].kind, 'player')
        self.assertIn('roster unverified', '\n'.join(row[1] for row in self.app.search_rows()))
        self.assertIn(key, self.app.player_inflight)
        self.app.player_queue.put((key, [self.player], None, False, False))
        self.app.poll_player_search()
        self.assertNotIn('roster unverified', '\n'.join(row[1] for row in self.app.search_rows()))
        self.assertNotIn(key, self.app.player_inflight)


if __name__ == '__main__':
    unittest.main()
