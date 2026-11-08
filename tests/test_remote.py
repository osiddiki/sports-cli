"""The headless app behind the OpenTUI front end takes the same keys through the same statechart."""
import curses
import datetime as dt
import queue
import threading
import time
import unittest
from unittest.mock import patch

from sportscli import app as nba, remote
from tests.test_screens import CHANNELS, GAMES, Screen, feeds, make_app, press

REAL_HEADLESS = remote.Headless
FRONT_END_KEYS = {'Enter': 'enter', 'Esc': 'escape', 'Down': 'down', 'Up': 'up', 'Left': 'left', 'Right': 'right', 'Tab': 'tab'}
SECTIONS = ['Replays', 'Live TV', 'Search', 'Settings']
JOURNEYS = [
    ['s', 'Enter', 'Enter', 'Esc', 'Esc', 'Esc', 'Esc'],
    ['Tab', 'Down', 'Enter', 'f', 'Enter', 'Down', 'Up', 'Esc', 'Esc'],
    ['3', 'Enter', 's', 'Esc', 'Esc', 'Esc', 'Esc'],
    ['/', 'l', 'a', 'Esc', ',', 'Down', ',', '?', 'j', 'Esc', 'Left'],
    ['Enter', 'Enter', 'a', 'o', 'g', 'v', 'x', 'i', 'r', 's', 'Esc', 'Esc', 'Esc', 'q'],
]


def headless():
    app = make_app(lambda: REAL_HEADLESS({}))
    # Keep the app's own prompt, play and feed actions; make_app replaced them with recorders.
    app.prompt = REAL_HEADLESS.prompt.__get__(app)
    app.play = REAL_HEADLESS.play.__get__(app)
    return app


class RemoteTests(unittest.TestCase):
    def setUp(self):
        for item in (patch.object(nba, 'save_config', lambda config: None), patch.object(nba, 'find_iina', lambda: None),
                     patch.object(nba, 'find_celluloid', lambda: None)):
            item.start(); self.addCleanup(item.stop)
        item = patch.object(remote, 'Headless', lambda config: headless())
        item.start(); self.addCleanup(item.stop)
        self.remote = remote.Remote({})
        self.addCleanup(self.remote.close)

    def test_keys_reach_the_same_screens_as_the_terminal_app(self):
        for journey in JOURNEYS:
            plain, shown = make_app(), remote.Remote({})
            self.addCleanup(shown.close)
            for name in journey:
                running = press(plain, name)
                answer = shown.key(FRONT_END_KEYS.get(name, name))
                self.assertEqual(answer['mode'], plain.mode, f'{journey} at {name}')
                if not running:
                    self.assertIn({'type': 'quit'}, answer['events'])
                    break

    def test_home_is_the_sections_then_tonights_games(self):
        view = self.remote.view()
        self.assertEqual((view['mode'], view['title']), ('home', 'Tonight'))
        self.assertEqual([row['text'] for row in view['rows'] if row['pick']], SECTIONS + [game.title for game in GAMES])
        self.assertEqual([row['text'] for row in view['rows'] if not row['pick']], ['LIVE', 'LATER TONIGHT'])
        self.assertEqual(view['rows'][view['selected']]['text'], GAMES[0].title)
        live, later = (row for row in view['rows'] if row['league'])
        self.assertEqual((live['cells'][0], live['style'], later['cells'][0], later['style']), ('NBA', 'live', 'NBA', ''))
        self.assertNotIn('1-9', view['global'])
        self.assertEqual(self.remote.key('enter')['mode'], 'feeds')

    def test_terminal_home_draws_league_marks_where_the_numbers_were(self):
        app = make_app(); app.render()
        lines = [app.scr.text[y] for y in sorted(app.scr.text)]
        games = [line for line in lines if any(game.title in line for game in GAMES)]
        self.assertEqual([line.split()[:2] for line in games], [['>', 'NBA'], ['NBA', GAMES[1].title.split()[0]]])
        self.assertIn('●', games[0])
        self.assertEqual(lines[2].split(), ['Replays', 'Live', 'TV', 'Search', 'Settings'], 'the sections head Home, above every game')
        self.assertFalse(any(line.lstrip('> ')[:1].isdigit() for line in lines), lines)

    def test_schedule_and_sources_describe_what_the_terminal_draws(self):
        self.remote.key('s'); view = self.remote.key('enter')
        self.assertEqual([row['text'] for row in view['rows']], [game.title for game in GAMES])
        self.assertEqual(view['rows'][0]['style'], 'live')
        view = self.remote.key('enter')
        self.assertEqual((view['mode'], view['columns']), ('feeds', ['SOURCE', 'SIDE', 'QUALITY', 'CHECK']))
        self.assertEqual(len(view['rows']), len(feeds()))
        self.assertIn('Enter play', view['actions'])

    def test_keys_that_ask_a_question_become_a_prompt_the_front_end_answers(self):
        view = self.remote.key('2')
        self.assertEqual(len(view['rows']), len(CHANNELS))
        asked = self.remote.key('f')['events']
        self.assertEqual([(event['type'], event['key']) for event in asked], [('prompt', 'f')])
        view = self.remote.key('f', 'cnn')
        self.assertEqual([row['text'] for row in view['rows']], ['CNN'])
        self.assertEqual(view['events'], [])

    def test_views_are_numbered_in_the_order_they_were_made(self):
        first, second = self.remote.view()['seq'], self.remote.key('down')['seq']
        self.assertLess(first, second)
        self.assertLess(second, self.remote.view()['seq'])

    def test_unknown_key_is_refused(self):
        with self.assertRaises(ValueError): self.remote.key('f13')

    def test_play_hands_mpv_to_the_front_end_and_reports_its_exit(self):
        app = self.remote.app
        feed = feeds()[0]; app.feeds = [feed]; app.player_ready = lambda item: True
        app.play(feed, resolved=('https://stream.example/a.m3u8', {'Referer': 'https://stream.example/'}))
        events = self.remote.view()['events']
        self.assertEqual([event['type'] for event in events], ['play'])
        self.assertEqual(events[0]['command'][0], 'mpv')
        self.assertEqual(events[0]['command'][-1], 'https://stream.example/a.m3u8')
        self.assertEqual((events[0]['url'], events[0]['headers']), ('https://stream.example/a.m3u8', {'Referer': 'https://stream.example/'}))
        self.assertEqual(self.remote.view()['events'], [])
        app.hidden_feeds.clear()
        self.remote.exited(3)
        self.assertIn(id(feed), app.hidden_feeds)
        self.assertIn('status 3', app.message)


class HomeTonightTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(nba, 'save_config', lambda config: None),
                        patch.object(nba, 'find_iina', lambda: None), patch.object(nba, 'find_celluloid', lambda: None)]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in self.patches: item.stop()

    def test_moving_onto_a_section_keeps_the_game_scroll(self):
        start = dt.datetime(2026, 11, 7, 23, tzinfo=dt.timezone.utc)
        app = nba.App(Screen(), {})
        app.tonight = [nba.Game(str(i), f'Match {i:02d}', start, 'Home', 'Away', 'Home', 'Away') for i in range(30)]
        app.home_index = len(nba.HOME_ITEMS) + 29
        app.render()
        on_game = '\n'.join(app.scr.text[y] for y in sorted(app.scr.text))
        self.assertIn('Match 29', on_game)
        self.assertNotIn('Match 00', on_game)
        app.home_index = 0
        app.render()
        on_section = '\n'.join(app.scr.text[y] for y in sorted(app.scr.text))
        self.assertIn('Replays', on_section)
        self.assertIn('Match 29', on_section)
        self.assertNotIn('Match 00', on_section)

    def test_the_menu_is_one_row_above_the_games(self):
        app = nba.App(Screen(), {})
        app.tonight = list(GAMES); app.home_index = app.first_game_row()
        walk = []
        for key in (curses.KEY_UP, curses.KEY_RIGHT, curses.KEY_RIGHT, curses.KEY_UP, curses.KEY_LEFT, curses.KEY_DOWN):
            app.handle_key(key, None); walk.append(app.home_rows()[app.home_index])
        self.assertEqual([item[1] if item in nba.HOME_ITEMS else item.title for item in walk],
                         ['Replays', 'Live TV', 'Search', 'Search', 'Live TV', GAMES[0].title])
        app.handle_key(curses.KEY_UP, None); app.handle_key(curses.KEY_RIGHT, None); app.render()
        footer = '\n'.join(app.scr.text.values())
        self.assertIn('Enter open Live TV', footer)
        self.assertEqual(app.mode, 'home')

    def test_a_long_channel_list_ends_at_a_whole_name(self):
        self.assertEqual(nba.fit_words('DAZN MIN (Home), KJZZ-TV (Away), Jazz+ (Away)', 24), 'DAZN MIN (Home)…')
        self.assertEqual(nba.fit_words('CBS', 24), 'CBS')

    def test_a_successful_reload_clears_a_failed_league(self):
        app = nba.App(Screen(), {})
        app.tonight_queue.put((app.tonight_generation, [], ['nba']))
        app.poll_tonight()
        self.assertIn('Could not load NBA', app.message)
        app.tonight_queue.put((app.tonight_generation, list(GAMES), []))
        app.poll_tonight()
        self.assertNotIn('Could not load', app.message)
        self.assertEqual(app.message, nba.App.ARRIVAL['home'])

    def test_quit_cancels_tonights_fetch(self):
        entered, release = threading.Event(), threading.Event()

        def fetch(day, league):
            entered.set(); release.wait(5); return []

        app = nba.App(Screen(), {})
        try:
            with patch.object(nba, 'get_games', side_effect=fetch):
                app.refresh_tonight()
                self.assertTrue(entered.wait(1), 'the tonight fetch never started')
                started = time.monotonic()
                app.close()
                item = None
                while time.monotonic() - started < 0.5:
                    try: item = app.tonight_queue.get_nowait()
                    except queue.Empty: time.sleep(0.01)
                    else: break
                self.assertIsNotNone(item, 'quit left the tonight fetch running')
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertEqual(item[1:], ([], []))
                busy = [t.name for t in threading.enumerate() if t is not threading.main_thread() and not t.daemon]
                self.assertEqual(busy, [])
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
