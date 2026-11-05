"""The headless app behind the OpenTUI front end takes the same keys through the same statechart."""
import unittest
from unittest.mock import patch

from sportscli import app as nba, remote
from tests.test_screens import CHANNELS, GAMES, Screen, feeds, make_app, press

REAL_HEADLESS = remote.Headless
FRONT_END_KEYS = {'Enter': 'enter', 'Esc': 'escape', 'Down': 'down', 'Up': 'up', 'Left': 'left', 'Right': 'right'}
JOURNEYS = [
    ['1', 'Enter', 'Enter', 'Esc', 'Esc', 'Esc', 'Esc'],
    ['3', 'f', 'Enter', 'Down', 'Up', 'Esc', 'Esc'],
    ['2', 'Enter', 's', 'Esc', 'Esc', 'Esc', 'Esc'],
    ['/', 'l', 'a', 'Esc', ',', 'Down', ',', '?', 'j', 'Esc', 'Left'],
    ['1', 'Enter', 'Enter', 'a', 'o', 'g', 'v', 'x', 'i', 'r', 's', 'Esc', 'Esc', 'q'],
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

    def test_home_lists_the_sections_with_their_keys(self):
        view = self.remote.view()
        self.assertEqual((view['mode'], view['selected']), ('home', 0))
        self.assertEqual([row['cells'][:2] for row in view['rows']],
                         [[key, label] for _, key, label, _ in nba.HOME_ITEMS])

    def test_schedule_and_sources_describe_what_the_terminal_draws(self):
        self.remote.key('1'); view = self.remote.key('enter')
        self.assertEqual([row['text'] for row in view['rows']], [game.title for game in GAMES])
        self.assertEqual(view['rows'][0]['style'], 'live')
        view = self.remote.key('enter')
        self.assertEqual((view['mode'], view['columns']), ('feeds', ['SOURCE', 'SIDE', 'QUALITY', 'CHECK']))
        self.assertEqual(len(view['rows']), len(feeds()))
        self.assertIn('Enter play', view['actions'])

    def test_keys_that_ask_a_question_become_a_prompt_the_front_end_answers(self):
        view = self.remote.key('3')
        self.assertEqual(len(view['rows']), len(CHANNELS))
        asked = self.remote.key('f')['events']
        self.assertEqual([(event['type'], event['key']) for event in asked], [('prompt', 'f')])
        view = self.remote.key('f', 'cnn')
        self.assertEqual([row['text'] for row in view['rows']], ['CNN'])
        self.assertEqual(view['events'], [])

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
        self.assertEqual(self.remote.view()['events'], [])
        app.hidden_feeds.clear()
        self.remote.exited(3)
        self.assertIn(id(feed), app.hidden_feeds)
        self.assertIn('status 3', app.message)


if __name__ == '__main__':
    unittest.main()
