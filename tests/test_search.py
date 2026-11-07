import datetime as dt
import time
import unittest
from unittest.mock import patch

from sportscli import app as nba, players, remote, replays, smart_search as search


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = search.default_index()
        cls.today = dt.date(2026, 10, 28)
        start = dt.datetime(2026, 10, 28, 23, tzinfo=dt.timezone.utc)
        cls.mavs = nba.Game('1', 'Dallas Mavericks at Houston Rockets', start,
                             'Houston Rockets', 'Dallas Mavericks', 'Rockets', 'Mavericks',
                             league='nba', state='live')
        cls.lakers = nba.Game('2', 'Los Angeles Lakers at LA Clippers', start,
                               'LA Clippers', 'Los Angeles Lakers', 'Clippers', 'Lakers',
                               league='nba', state='live')
        cls.archived = [
            ('nba', replays.Replay('Basketball Replays',
                'Portland Trail Blazers vs. Los Angeles Lakers - NBA Full Game Replay - 27 October 2026',
                'https://example.com/lakers-blazers')),
            ('nba', replays.Replay('Basketball Replays',
                'Los Angeles Lakers vs. Miami Heat - NBA Full Game Replay - October 26, 2026',
                'https://example.com/lakers-heat')),
            ('ncaaf', replays.Replay('NFL Video',
                'Texas A&M Aggies vs. Alabama Crimson Tide - College Football - October 24, 2026',
                'https://example.com/tamu-alabama')),
        ]

    def results(self, text):
        query = search.parse_query(text, self.today)
        plan = search.make_plan(self.index, query)
        return plan, search.rank(self.index, plan, [self.mavs, self.lakers], self.archived, self.today)

    def test_partial_prefix_suggests_mavs_and_ranks_live_first(self):
        plan, hits = self.results('ma')
        self.assertEqual(plan.primary.display, 'Dallas Mavericks')
        self.assertEqual(hits[0].kind, 'live')
        self.assertEqual(hits[0].item, self.mavs)
        self.assertIn('Dallas Mavericks', [hit.title for hit in hits if hit.kind == 'team'])

    def test_aliases_and_matchup_intent(self):
        for query in ('mavs vs rockets', 'mavs rockets'):
            plan, hits = self.results(query)
            self.assertEqual(plan.leagues, ('nba',))
            self.assertEqual([(hit.kind, hit.item) for hit in hits], [('live', self.mavs)])
        plan, hits = self.results('tamu vs alabama')
        self.assertEqual(plan.leagues[0], 'ncaaf')
        self.assertEqual([hit.item.url for hit in hits if hit.kind == 'replay'], ['https://example.com/tamu-alabama'])

    def test_incomplete_second_team_keeps_first_team_results(self):
        plan, hits = self.results('mavs vs r')
        self.assertEqual(plan.query.fragments, ('mavs',))
        self.assertEqual(hits[0].item, self.mavs)

    def test_date_and_live_replay_intent(self):
        plan, hits = self.results('lakers game today')
        self.assertEqual(plan.query.date, self.today)
        self.assertEqual([(hit.kind, hit.item) for hit in hits], [('live', self.lakers)])
        for query in ('lakers vs blazers oct 27', 'lakers vs blazers 10/27',
                      'lakers vs blazers 2026-10-27'):
            plan, hits = self.results(query)
            self.assertEqual(plan.query.date, dt.date(2026,10,27))
            self.assertEqual([hit.item.url for hit in hits], ['https://example.com/lakers-blazers'])
        self.assertEqual(search.parse_query('lakers last night', self.today).date, dt.date(2026,10,27))
        self.assertEqual(search.parse_query('lakers last night', self.today).intent, 'replay')
        self.assertEqual(search.parse_query('mavs tomorrow', self.today).intent, 'live')

    def test_typos_and_full_names_match_without_changing_meaning(self):
        for query in ('lakerz vs blazers oct 27', 'xakers vs blazers oct 27'):
            _, hits = self.results(query)
            self.assertEqual([hit.item.url for hit in hits], ['https://example.com/lakers-blazers'])
        plan, hits = self.results('los angeles lakers')
        self.assertEqual(plan.query.fragments, ('los angeles lakers',))
        self.assertEqual(hits[0].item, self.lakers)

    def test_flexible_replay_dates_and_nearest_year(self):
        for title in ('Lakers vs Blazers October 27, 2026', 'Lakers vs Blazers 27 Oct 2026',
                      'Lakers vs Blazers 2026-10-27'):
            self.assertEqual(search.replay_date(title,self.today), dt.date(2026,10,27))
        self.assertEqual(search.parse_query('lakers dec 31',dt.date(2027,1,1)).date,dt.date(2026,12,31))
        self.assertIsNone(search.parse_query('49ers vs rams',self.today).date)

    def test_search_fetches_only_relevant_league_and_reuses_cache(self):
        app = nba.App(None,{})
        app.mode = 'search'
        app.search_query = 'mavs'
        with patch.object(nba, 'default_day', return_value=self.today), \
             patch.object(replays, 'catalogs', return_value=[]), \
             patch.object(nba, 'feed_tasks', return_value=[]), \
             patch.object(nba, 'get_games', return_value=[self.mavs]) as get_games, \
             patch.object(nba, 'get_team_games', return_value=[self.mavs]) as team_games:
            app.update_global_search(); app.search_due = 0; app.start_global_search()
            end = time.monotonic()+2
            while app.search_inflight and time.monotonic() < end:
                app.poll_global_search(); time.sleep(.01)
            self.assertEqual(get_games.call_count,1)
            self.assertEqual(get_games.call_args.args, (self.today,'nba'))
            self.assertEqual(team_games.call_count,1)
            self.assertEqual(app.search_results[0].kind,'team')
            self.assertTrue(any(hit.kind == 'live' for hit in app.search_results))
            app.search_query = 'mavs vs rockets'
            app.update_global_search(); app.search_due = 0; app.start_global_search()
            self.assertEqual(get_games.call_count,1)
            self.assertEqual(team_games.call_count,1)
            self.assertTrue(any(hit.kind == 'live' and hit.item == self.mavs for hit in app.search_results))
        app.close()

    def test_matchup_search_finds_a_future_game_without_loading_every_league(self):
        future = nba.Game('3', 'Houston Rockets at Dallas Mavericks',
                          dt.datetime(2026,10,31,0,tzinfo=dt.timezone.utc),
                          'Dallas Mavericks','Houston Rockets','Mavericks','Rockets',
                          league='nba', state='upcoming')
        app = nba.App(None,{})
        app.mode = 'search'
        app.search_query = 'mavs rockets'
        with patch.object(nba, 'default_day', return_value=dt.date(2026,10,29)), \
             patch.object(replays, 'catalogs', return_value=[]), \
             patch.object(nba, 'get_games', return_value=[]), \
             patch.object(nba, 'get_team_games', return_value=[future]) as team_games:
            app.update_global_search(); app.search_due = 0; app.start_global_search()
            end = time.monotonic()+2
            while app.search_inflight and time.monotonic() < end:
                app.poll_global_search(); time.sleep(.01)
            self.assertEqual(team_games.call_args.args, ('6','nba',dt.date(2026,10,29)))
            self.assertEqual([(hit.kind,hit.item) for hit in app.search_results if hit.kind != 'team'], [('upcoming',future)])
        app.close()

    def test_matchup_replay_lookup_uses_bounded_pages_and_stops_on_match(self):
        spec = ('Basketball Video','https://basketball-video.com/2026-27','nba')
        replay = replays.Replay('Basketball Video',
            'Dallas Mavericks vs Houston Rockets - NBA - Full Game Replay - October 21, 2026',
            'https://example.com/mavs-rockets')
        def listing(page_spec):
            return [replay] if page_spec[1].endswith('?page4') else []
        with patch.object(replays, 'listing', side_effect=listing) as fetch:
            rows = nba.search_replay_pages(spec,'Mavericks','Rockets')
        self.assertEqual(rows, [replay])
        self.assertEqual(fetch.call_count, 3)

    def test_team_archive_lookup_keeps_games_from_later_pages(self):
        spec = ('Basketball Video', 'https://basketball-video.com/2026-27', 'nba')
        def listing(page_spec):
            page = page_spec[1].split('?page')[-1]
            return [replays.Replay('Basketball Video', 'Lakers vs Jazz NBA October '+page+', 2026',
                                   'https://example.com/game-'+page)]
        with patch.object(replays, 'listing', side_effect=listing) as fetch:
            rows = nba.search_replay_pages(spec, 'Lakers')
        self.assertEqual(len(rows), 5)
        self.assertEqual(fetch.call_count, 5)
        self.assertEqual(len({r.url for r in rows}), 5)

    def test_replays_filter_includes_older_archive_pages(self):
        app = nba.App(None, {})
        app.league = 'nba'; app.replay_search = 'Lakers'
        recent = [replays.Replay('Basketball Video', 'Lakers vs Clippers NBA October 28, 2026', 'https://example.com/28')]
        older = [replays.Replay('Basketball Video', 'Lakers vs Jazz NBA October 25, 2026', 'https://example.com/25'),
                 replays.Replay('Basketball Video', 'Lakers vs Clippers NBA October 28, 2026', 'https://example.com/28')]
        spec = ('Basketball Video', 'https://basketball-video.com/2026-27', 'nba')
        with patch.object(replays, 'catalogs', return_value=[spec]), patch.object(replays, 'listing', return_value=list(recent)), \
             patch.object(nba, 'search_replay_pages', return_value=older):
            app.refresh_replays()
            item = app.replay_queue.get(timeout=2)
        self.assertEqual([r.url for r in item[2]], ['https://example.com/28', 'https://example.com/25'])

    def test_matchup_replay_lookup_stops_when_query_changes(self):
        spec = ('Basketball Video','https://basketball-video.com/2026-27','nba')
        with patch.object(replays, 'listing') as fetch:
            with self.assertRaises(nba.concurrent.futures.CancelledError):
                nba.search_replay_pages(spec,'Mavericks','Rockets',canceled=lambda:True)
        fetch.assert_not_called()

    def test_selection_stays_on_user_choice_as_results_arrive(self):
        app = nba.App(None,{})
        app.mode = 'search'
        app.search_query = 'mavs'
        with patch.object(nba, 'default_day', return_value=self.today), patch.object(replays, 'catalogs', return_value=[]), \
             patch.object(nba, 'feed_tasks', return_value=[]):
            app.update_global_search()
            self.assertEqual(app.search_results[0].kind, 'team')
            selected = app.search_results[0].key
            app.search_touched = True
            app.search_cache[('games','nba',self.today)] = (time.monotonic()+60,[self.mavs],None)
            app.rebuild_global_results()
            self.assertTrue(any(hit.kind == 'live' for hit in app.search_results))
            self.assertEqual(app.search_results[app.search_position].key, selected)

    def test_grouped_results_combine_replay_sources_and_show_live_sides(self):
        app = nba.App(None,{})
        app.mode = 'search'; app.search_query = 'lakers'
        app.search_index = self.index
        app.search_plan = search.make_plan(self.index, search.parse_query('lakers', self.today))
        replay_a = self.archived[0][1]
        replay_b = replays.Replay('Basketball Video',
            'Portland Trail Blazers vs Los Angeles Lakers - NBA - Full Game Replay - October 27, 2026',
            'https://example.com/other-provider')
        hits = [search.Hit('live','nba',self.lakers.title,self.lakers,1,self.today),
                search.Hit('replay','nba',replay_a.title,replay_a,1,dt.date(2026,10,27)),
                search.Hit('replay','nba',replay_b.title,replay_b,1,dt.date(2026,10,27)),
                search.Hit('replay','nba',self.archived[1][1].title,self.archived[1][1],1,dt.date(2026,10,26))]
        with patch.object(nba, 'feed_tasks', return_value=[]):
            app.grouped_search_results(hits)
            app.search_preview_order = ['Example']
            app.search_preview_sections = {'Example':[nba.Feed('Example','Home',side='home',kind='direct'),
                                                       nba.Feed('Example','Away',side='away',kind='direct')]}
            app.search_results = app.grouped_search_results(hits)
        bundles = [hit.item for hit in app.search_results if hit.kind == 'bundle']
        self.assertEqual(len(bundles), 2)
        self.assertEqual(len(bundles[0].sources), 2)
        rows = '\n'.join(row[1] for row in app.search_rows())
        self.assertTrue(rows.startswith('NBA  Los Angeles Lakers'))
        self.assertIn('LIVE', rows); self.assertIn('REPLAYS', rows)
        self.assertNotIn('[REPLAY', rows)
        self.assertEqual([hit.side for hit in app.search_results if hit.kind == 'side'], ['home','away'])
        app.search_position = next(i for i, hit in enumerate(app.search_results) if hit.kind == 'side' and hit.side == 'away')
        with patch.object(app, 'start_auto_checks'):
            app.open_global_result()
        self.assertEqual((app.mode,app.filter), ('feeds','away'))
        self.assertEqual([feed.side for feed in app.visible_feeds()], ['away'])
        app.mode = 'search'
        app.search_position = next(i for i, hit in enumerate(app.search_results) if hit.kind == 'bundle')
        app.open_global_result()
        self.assertEqual(app.mode, 'replay_sources')
        self.assertEqual([source.source for source in app.current_bundle.sources],
                         ['Basketball Replays', 'Basketball Video'])

    def test_spurs_search_groups_the_two_full_name_listings(self):
        day = dt.date(2026, 11, 6)
        scored = replays.Replay(
            'Basketball Replays',
            'San Antonio Spurs vs. Sacramento Kings 121-98 - NBA Regular Season - Full Game Replay - 6 November 2026',
            'https://basketballreplays.net/spurs')
        plain = replays.Replay(
            'Basketball Video',
            'San Antonio Spurs vs Sacramento Kings - NBA - Full Game Replay - November 6, 2026',
            'https://basketball-video.com/spurs')
        short = replays.Replay(
            'WatchReplay',
            'Spurs vs Kings - NBA - Full Game Replay - November 6, 2026',
            'https://example.com/short')
        app = nba.App(None, {})
        app.mode = 'search'
        app.search_query = 'spurs'
        app.search_index = self.index
        app.search_plan = search.make_plan(self.index, search.parse_query('spurs', day))
        hits = search.rank(self.index, app.search_plan, [], [('nba', scored), ('nba', plain), ('nba', short)], day)
        app.search_results = app.grouped_search_results(hits)
        bundles = [hit.item for hit in app.search_results if hit.kind == 'bundle']
        full = [bundle for bundle in bundles if 'San Antonio' in bundle.title]
        self.assertEqual(len(full), 1)
        self.assertEqual(full[0].title, 'San Antonio Spurs vs Sacramento Kings')
        self.assertNotIn('121', full[0].title)
        shown = '\n'.join(text for _, text, _ in app.search_rows())
        self.assertEqual(shown.count('San Antonio Spurs vs Sacramento Kings'), 1)
        self.assertNotIn('121', shown)
        self.assertIn('Spurs vs Kings', shown)
        self.assertNotIn('https://example.com/short', [item.url for item in full[0].sources])
        app.search_position = next(i for i, hit in enumerate(app.search_results)
                                   if hit.kind == 'bundle' and hit.item is full[0])
        app.open_global_result()
        self.assertEqual(app.mode, 'replay_sources')
        self.assertEqual(sorted(source.source for source in app.current_bundle.sources),
                         ['Basketball Replays', 'Basketball Video'])
        app.close()

    def test_nba_replay_groups_full_name_listings_without_a_team(self):
        day = dt.date(2026, 11, 6)
        scored = replays.Replay(
            'Basketball Replays',
            'San Antonio Spurs vs. Sacramento Kings 121-98 - NBA Regular Season - Full Game Replay - 6 November 2026',
            'https://basketballreplays.net/spurs')
        plain = replays.Replay(
            'Basketball Video',
            'San Antonio Spurs vs Sacramento Kings - NBA - Full Game Replay - November 6, 2026',
            'https://basketball-video.com/spurs')
        short = replays.Replay(
            'WatchReplay',
            'Spurs vs Kings - NBA - Full Game Replay - November 6, 2026',
            'https://example.com/short')
        app = nba.App(None, {})
        app.mode = 'search'
        app.search_query = 'nba replay'
        app.search_index = self.index
        app.search_plan = search.make_plan(self.index, search.parse_query('nba replay', day))
        hits = search.rank(self.index, app.search_plan, [], [('nba', scored), ('nba', plain), ('nba', short)], day)
        app.search_results = app.grouped_search_results(hits)
        bundles = [hit.item for hit in app.search_results if hit.kind == 'bundle']
        full = [bundle for bundle in bundles if bundle.title == 'San Antonio Spurs vs Sacramento Kings']
        self.assertEqual(len(full), 1)
        self.assertNotIn('121', full[0].title)
        shown = '\n'.join(text for _, text, _ in app.search_rows())
        self.assertEqual(shown.count('San Antonio Spurs vs Sacramento Kings'), 1)
        self.assertNotIn('121', shown)
        self.assertNotIn('Full Game Replay', shown)
        self.assertIn('Spurs vs Kings', shown)
        self.assertNotIn('https://example.com/short', [item.url for item in full[0].sources])
        app.search_position = next(i for i, hit in enumerate(app.search_results)
                                   if hit.kind == 'bundle' and hit.item is full[0])
        app.open_global_result()
        self.assertEqual(app.mode, 'replay_sources')
        self.assertEqual(sorted(source.source for source in app.current_bundle.sources),
                         ['Basketball Replays', 'Basketball Video'])
        app.close()

    def test_live_preview_continues_loading_after_opening_the_source_list(self):
        app = nba.App(None,{})
        app.mode = 'feeds'; app.feed_context = 'live'
        app.games = [self.lakers]; app.gi = 0
        app.search_preview_key = ('nba', self.lakers.id)
        app.search_preview_generation = 7
        app.search_preview_order = ['Example']
        app.search_preview_pending = 1
        app.adopt_search_preview()
        feed = nba.Feed('Example','Home',side='home',kind='direct')
        app.search_preview_queue.put((7, 'Example', [feed], None))
        with patch.object(app, 'start_auto_checks'):
            app.poll_search_preview(); app.poll_feeds()
        self.assertEqual(app.feed_pending, 0)
        self.assertEqual(app.feeds, [feed])

    def test_stream_rows_distinguish_listed_and_measured_quality(self):
        feed = nba.Feed('NBA Bite', 'sportylive.xyz | Spectrum SportsNet | HD | English',
                        side='away', kind='candidate')
        view = nba.describe_feed(feed, {}, 'checking')
        self.assertEqual((view.name,view.side,view.quality,view.check),
                         ('NBA Bite / sportylive.xyz','AWAY','~HD','Checking'))
        self.assertIn('Spectrum SportsNet',view.detail)
        view = nba.describe_feed(feed, {'checks':[True,True,True],'height':720}, 'OK')
        self.assertEqual(view.quality,'720p')
        self.assertEqual(view.check,'OK A 3/3')

    def test_search_row_cursor_is_the_result_index(self):
        app = nba.App(remote.Screen(), {})
        app.mode = 'search'
        app.search_query = 'kings'
        app.search_index = self.index
        app.search_plan = search.make_plan(self.index, search.parse_query(app.search_query, self.today))
        team = app.search_plan.primary
        query = app.search_plan.query
        app.player_key = (query.fragments[0], query.league)
        app.player_cache[app.player_key] = (time.monotonic() + 60, [players.Player('p1', 'Shaq King', team, True)], None)
        app.search_work = []
        app.rebuild_global_results()
        shown = remote.view(app)['rows']
        seen = 0
        mismatch = None
        for visual, (index, text, _style) in enumerate(app.search_rows(120)):
            row = shown[visual]
            if index is None:
                self.assertFalse(row['pick'])
                self.assertNotIn('cursor', row)
                continue
            self.assertEqual(row['cursor'], index)
            if seen != index and mismatch is None:
                mismatch = (text.strip(), index, seen)
            seen += 1
        self.assertIsNotNone(mismatch)
        self.assertGreater(seen, 1)


if __name__ == '__main__':
    unittest.main()
