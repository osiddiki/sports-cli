"""Exhaustively check screen navigation against sportscli/screens.py.

Every reachable screen state (the screen plus where its back keys lead) is
found by breadth-first search from the opening screen, replaying key paths on
a fresh App with offline data. Each state is then checked with every key.
"""
import collections
import curses
import datetime as dt
import re
import time
import unittest
from unittest.mock import patch

from sportscli import app as nba, replays, screens, tv

KEYS = {'Enter': 10, 'Esc': 27, 'Backspace': 127, 'Tab': 9, 'Space': 32, 'Ctrl-F': 6, 'Ctrl-X': 24,
        'Up': curses.KEY_UP, 'Down': curses.KEY_DOWN, 'Left': curses.KEY_LEFT, 'Right': curses.KEY_RIGHT}
EXTRA = list('1234/,?qhtsrjk[]lfoixgua')
ALPHABET = sorted({*KEYS, *(k for keys in screens.CHART.values() for k in keys), *EXTRA})
MAX_DEPTH = 7

START = dt.datetime(2026, 10, 30, 23, tzinfo=dt.timezone.utc)
GAMES = [nba.Game('1', 'Knicks at 76ers', START, 'Philadelphia 76ers', 'New York Knicks', '76ers', 'Knicks', state='live'),
         nba.Game('2', 'Lakers at Warriors', START, 'Golden State Warriors', 'Los Angeles Lakers', 'Warriors', 'Lakers')]
REPLAYS = [replays.Replay('Archive', 'Bulls vs Celtics', 'https://replays.example/1'),
           replays.Replay('Archive', 'Heat vs Magic', 'https://replays.example/2')]
CHANNELS = [tv.Channel('CNN', [tv.Source('iptv-org', '720p', 'https://tv.example/cnn.m3u8', 'direct')], True),
            tv.Channel('CNBC', [tv.Source('iptv-org', '720p', 'https://tv.example/cnbc.m3u8', 'direct')], True)]


def feeds():
    return [nba.Feed('Directory', 'Home feed', 'home', 'https://feeds.example/1', 'direct'),
            nba.Feed('Directory', 'Away feed', 'away', 'https://feeds.example/2', 'direct')]


class Screen:
    def __init__(self, rows=30, columns=110): self.size = (rows, columns); self.text = {}
    def getmaxyx(self): return self.size
    def erase(self): self.text = {}
    def addnstr(self, y, x, text, limit, attr=0): self.text[y] = self.text.get(y, '') + str(text)[:limit]
    def refresh(self): pass
    def redrawwin(self): pass


def make_app(factory=None):
    app = factory() if factory else nba.App(Screen(), {})
    app.calls = []; app.tonight = list(GAMES)
    record = lambda name: (lambda *args, **kwargs: app.calls.append(name))
    def refresh_games(): app.games = list(GAMES); app.all_games = list(GAMES); app.loading_games = False; app.calls.append('refresh_games')
    def refresh_replays(): app.replays = list(REPLAYS); app.replay_pending = 0; app.calls.append('refresh_replays')
    def refresh_feeds(recheck=False): app.feeds = feeds(); app.feed_pending = 0; app.calls.append('refresh_feeds')
    def refresh_channels(): app.channels = list(CHANNELS); app.channels_loaded_at = time.monotonic(); app.calls.append('refresh_channels')
    def prompt(*args, **kwargs): app.calls.append('prompt'); return ''
    for name, value in {'refresh_games': refresh_games, 'refresh_replays': refresh_replays, 'refresh_feeds': refresh_feeds,
                        'refresh_channels': refresh_channels, 'refresh_tonight': record('refresh_tonight'), 'prompt': prompt,
                        'update_global_search': record('search'), 'rebuild_global_results': record('search'),
                        'begin_feed_action': record('begin_feed_action'), 'test_feed': record('test_feed'),
                        'play': record('play'), 'configure_addon': record('configure_addon'),
                        'configure_resolver': record('configure_resolver')}.items():
        setattr(app, name, value)
    return app


def press(app, name):
    code = KEYS.get(name, ord(name) if len(name) == 1 else None)
    return app.handle_key(code, name if len(name) == 1 else ' ' if name == 'Space' else None)


def state(app):
    """The screen and every history value that decides where later keys lead.

    Help and settings set their return screen on entry and read it only while open,
    so outside them it cannot change where later keys lead."""
    return (app.mode, app.feed_context, app.feed_return, app.search_return,
            app.help_return if app.mode == 'help' else None, app.league_cancel, app.section,
            app.settings_return if app.mode == 'settings' else None)


def snapshot(app):
    return (state(app), app.message, app.gi, app.ci, app.fi, app.ri, app.league_index, app.home_index,
            app.settings_index, app.day, app.filter,
            app.provider_filter, app.rank_by_checks, app.dual_stream, app.ad_breaks, app.cache, app.terminal_video,
            app.player, app.help_scroll, app.search_position, app.channel_filter, app.feed_search, len(app.calls))


def resolve(target, before):
    mode, context, feed_return, search_return, help_return, league_cancel, section, settings_return = before
    if target == '<back>':
        return {'help': help_return, 'search': search_return or 'home', 'leagues': league_cancel or 'home',
                'settings': settings_return or 'home'}[mode]
    if target == '<origin>':
        return feed_return or ('replays' if context == 'replay' else 'channels' if context == 'tv' else 'games')
    if target == '<section>': return 'replays' if section == 'replays' else 'games'
    return target


def chart_key(name):
    for key, aliases in screens.ALIASES.items():
        if name in aliases: return key
    return name


class ScreenChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patches = [patch.object(nba, 'save_config', lambda config: None),
                       patch.object(nba, 'find_iina', lambda: None), patch.object(nba, 'find_celluloid', lambda: None)]
        for item in cls.patches: item.start()
        cls.paths = cls.explore()

    @classmethod
    def tearDownClass(cls):
        for item in cls.patches: item.stop()

    @classmethod
    def explore(cls):
        """Breadth-first search over screen states; returns {state: shortest key path}."""
        app = make_app()
        paths = {state(app): ()}
        frontier = collections.deque([()])
        while frontier:
            path = frontier.popleft()
            if len(path) >= MAX_DEPTH: continue
            for name in ALPHABET:
                app = cls.replay(path)
                if not press(app, name): continue
                found = state(app)
                if found not in paths:
                    paths[found] = path + (name,); frontier.append(path + (name,))
        return paths

    @staticmethod
    def replay(path):
        app = make_app()
        for name in path: assert press(app, name), path
        return app

    def test_every_screen_is_reachable(self):
        self.assertEqual({key[0] for key in self.paths} - {'replay_sources'}, set(screens.CHART) - {'replay_sources'})

    def test_every_transition_is_in_the_chart_and_every_chart_edge_is_taken(self):
        taken, problems = set(), []
        for before, path in self.paths.items():
            for name in ALPHABET:
                app = self.replay(path)
                key = chart_key(name) if before[0] != 'search' else name
                running = press(app, name)
                after = state(app)[0] if running else screens.EXIT
                if after == before[0]: continue
                allowed = {resolve(target, before) for target in screens.CHART[before[0]].get(key, ())}
                if after not in allowed:
                    problems.append(f'{" ".join(path) or "start"} then {name}: {before[0]} -> {after}, chart allows {sorted(map(str, allowed))}')
                taken.add((before[0], key))
        # Pressing the key for the screen you are on (3 on Live TV, , in settings) stays put.
        declared = {(screen, key) for screen, keys in screens.CHART.items() for key, targets in keys.items()
                    if targets - {screen}}
        missing = declared - taken - screens.NEEDS_LIVE_DATA - {('replay_sources', key) for key in screens.CHART['replay_sources']}
        problems += [f'never taken: {screen} {key}' for screen, key in sorted(missing)]
        self.assertEqual(sorted(set(problems))[:40], [])

    def test_escape_always_leads_home(self):
        for before, path in self.paths.items():
            app = self.replay(path)
            for _ in range(8):
                if app.mode == screens.HOME: break
                self.assertTrue(press(app, 'Esc'))
            self.assertEqual(app.mode, screens.HOME, f'stuck after {" ".join(path)}: {state(app)}')

    def test_help_and_search_return_to_the_same_screen(self):
        for before, path in self.paths.items():
            if before[0] not in ('help', 'search'):  # in search, ? is typed into the query
                app = self.replay(path); press(app, '?'); press(app, 'Esc')
                self.assertEqual(state(app)[0], before[0], f'help from {" ".join(path)}')
            if before[0] not in ('search', 'help'):
                app = self.replay(path); press(app, '/'); press(app, 'Esc')
                self.assertEqual(state(app)[0], before[0], f'search from {" ".join(path)}')

    def test_quit_works_everywhere(self):
        for before, path in self.paths.items():
            self.assertFalse(press(self.replay(path), 'Ctrl-X' if before[0] == 'search' else 'q'), before)

    def test_every_key_shown_in_a_footer_does_something(self):
        problems = []
        words = {'Enter': ['Enter'], 'Esc': ['Esc'], 'Space': ['Space'], 'Tab': ['Tab'], 'Ctrl-F': ['Ctrl-F'],
                 'Ctrl-X': ['Ctrl-X'], '^X': ['Ctrl-X'], 'Up/Down': ['Down'], '↑↓': ['Down'], 'j/k': ['Down'],
                 'Ctrl-U': [], '^U': [], 'Type': []}
        for before, path in self.paths.items():
            app = self.replay(path); app.render()
            rows, _ = app.scr.getmaxyx()
            footer = '   '.join(app.scr.text.get(y, '') for y in (rows - 4, rows - 3) if y in app.scr.text)
            for hint in filter(None, (part.strip() for part in re.split(r'\s{3,}', footer))):
                keys = []
                for word in hint.split():
                    if word == 'or': continue
                    if word in words: keys += words[word]
                    elif len(word) == 1 and not word.isalnum() or len(word) == 1: keys.append(word)
                    else: break
                for name in keys:
                    app = self.replay(path); before_snapshot = snapshot(app)
                    if name in ('Down', 'Enter') and len(self.items(app)) < 2: continue  # nothing to move to or open
                    if screens.GLOBAL.get(name) == {before[0]}: continue  # the key for the screen you are on
                    running = press(app, name)
                    if running and snapshot(app) == before_snapshot:
                        problems.append(f'"{hint}" does nothing on {before[0]} ({" ".join(path) or "start"})')
        self.assertEqual(sorted(set(problems))[:40], [])

    def test_schedule_footer_keys_work_in_every_league(self):
        def schedule(league):
            app = make_app(); press(app, 's'); app.league_index = list(nba.LEAGUES).index(league); press(app, 'Enter')
            return app
        for league in nba.LEAGUES:
            app = schedule(league)
            self.assertEqual((app.mode, app.league), ('games', league))
            app.render(); rows, _ = app.scr.getmaxyx()
            footer = app.scr.text.get(rows - 4, '') + '   ' + app.scr.text.get(rows - 3, '')
            for hint, key in (('s sport', 's'), ('[ ] date', ']'), ('f filter', 'f'), ('r refresh', 'r')):
                self.assertIn(hint, footer)
                app = schedule(league); before = snapshot(app); press(app, key)
                self.assertNotEqual(snapshot(app), before, f'"{hint}" does nothing for {league}')

    def test_keys_never_change_meaning(self):
        # s always changes sport, / always searches, , always opens settings.
        for before, path in self.paths.items():
            if before[0] in ('search', 'help'): continue
            for key, expected in (('/', 'search'), (',', 'settings')):
                if key == ',' and before[0] == 'settings': continue  # , closes settings
                app = self.replay(path); press(app, key)
                self.assertEqual(app.mode, expected, f'{key} from {before[0]}')
            app = self.replay(path)
            if press(app, 's') and app.mode != before[0]:
                self.assertEqual(app.mode, 'leagues', f's from {before[0]}')

    def test_a_digit_opens_that_row(self):
        # 2 does what selecting the second row and pressing Enter does, on every list.
        for before, path in self.paths.items():
            if before[0] in ('search', 'help', 'feeds'): continue  # search types digits; feeds would play
            app = self.replay(path)
            if len(self.items(app)) < 2: continue
            press(app, '2'); by_digit = snapshot(app)
            app = self.replay(path); field, _ = app.cursor(); setattr(app, field, 1); press(app, 'Enter')
            self.assertEqual(by_digit, snapshot(app), f'2 on {before[0]} ({" ".join(path) or "start"})')

    def test_docs_show_the_current_chart(self):
        import pathlib
        doc = (pathlib.Path(__file__).resolve().parents[1] / 'docs' / 'screens.md').read_text(encoding='utf-8')
        self.assertIn(screens.mermaid(), doc, 'regenerate docs/screens.md from screens.mermaid()')

    @staticmethod
    def items(app):
        return {'search': app.search_results, 'leagues': nba.LEAGUES, 'games': app.games, 'replays': app.replays,
                'channels': app.visible_channels(), 'feeds': app.visible_feeds(), 'help': nba.HELP_LINES,
                'home': app.home_rows(), 'settings': app.settings_rows()}.get(app.mode, [])


if __name__ == '__main__':
    unittest.main()
