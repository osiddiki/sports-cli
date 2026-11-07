"""The terminal app's screens, driven over HTTP by another front end (the OpenTUI prototype).

A headless App runs the same statechart (sportscli/screens.py) and the same keys as the curses
screen. The front end sends key names and draws the screen description `view` returns. Work that
the curses app does in the terminal itself (prompts, starting mpv, printing a media URL) becomes
an event for the front end to carry out.
"""
from __future__ import annotations

import curses
import threading
import time

from . import app, breaks

CODES = {'up': curses.KEY_UP, 'down': curses.KEY_DOWN, 'left': curses.KEY_LEFT, 'right': curses.KEY_RIGHT,
         'enter': 10, 'escape': 27, 'backspace': 127, 'tab': 9, 'ctrl-n': 14, 'ctrl-p': 16, 'ctrl-q': 17,
         'ctrl-r': 18, 'ctrl-u': 21, 'ctrl-x': 24}
GLOBAL_KEYS = '1-9 open row   / search   , settings   ? help   q quit'
STATUS = {'live': 'LIVE', 'upcoming': 'UPCOMING', 'redzone-live': 'LIVE', 'redzone': 'CHANNEL'}


class NeedText(Exception):
    def __init__(self, label, initial=''):
        super().__init__(label); self.label, self.initial = label, initial


class Screen:
    """Stands in for the curses window: the headless app never draws."""
    def getmaxyx(self): return (40, 120)
    def erase(self): pass
    def refresh(self): pass
    def clear(self): pass


class Headless(app.App):
    def __init__(self, config):
        super().__init__(Screen(), config)
        self.events, self.answer, self.playing = [], None, None

    def render(self): pass

    def prompt(self, label, initial=''):
        if self.answer is None: raise NeedText(label, initial)
        answer, self.answer = self.answer, None
        return answer.strip()

    def title(self):
        if self.feed_context == 'tv' and self.current_channel: return self.current_channel.name
        if self.feed_context == 'replay' and self.current_replay: return self.current_replay.title
        return self.games[self.gi].title if self.gi < len(self.games) else 'Sports CLI'

    def play(self, feed, *, resolved=None):
        # IINA and Celluloid start detached already; only mpv waits for the player, so the front end starts that.
        if self.player != 'mpv': return super().play(feed, resolved=resolved)
        if not self.player_ready(feed): return
        if resolved is None:
            try: resolved = self.resolve_feed(feed)
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                if not isinstance(exc, app.DependencyError): self.hide_feed(feed)
                self.message = f'Stream unavailable: {exc}.'; return
        url, headers = resolved
        note = self.stream_notes.pop(url, '')
        self.playing = feed
        self.events.append({'type': 'play', 'title': self.title(), 'url': url, 'headers': dict(headers),
                            'command': app.mpv_command(url, headers, cache=self.cache)})
        self.message = 'Playing; keep browsing here.' + note

    def player_exited(self, code):
        feed, self.playing = self.playing, None
        if code and feed is not None:
            self.hide_feed(feed); self.message = f'mpv exited with status {code}; source hidden until refresh.'
        else:
            self.message = 'mpv closed.'

    def show_stream_url(self, url, headers):
        self.events.append({'type': 'inspect', 'url': url, 'headers': dict(headers)})
        self.message = 'Stream link shown.'

    def poll(self):
        self.poll_games(); self.poll_global_search(); self.poll_player_search(); self.start_global_search()
        self.start_player_search(); self.poll_search_preview(); self.poll_replays(); self.poll_channels()
        self.poll_feeds(); self.poll_auto_checks(); self.poll_manual_checks(); self.cancel_feed_action_if_changed()
        self.poll_feed_actions(); self.poll_gui_players()


def row(text, *, detail='', style='', pick=True, cells=None):
    return {'text': text, 'cells': cells or [text], 'detail': detail, 'style': style, 'pick': pick}


def view(a):
    """What the screen shows: the same rows, selection and actions as App.render, without the drawing."""
    heading = {'home': 'Home', 'settings': 'Settings', 'search': 'Search', 'games': 'Games', 'replays': 'Replays',
               'replay_sources': 'Replay sites', 'channels': 'Live TV', 'feeds': 'Sources', 'help': 'Help',
               'leagues': 'Live sports: choose a sport' if a.section == 'sports' else 'Replays: choose a sport'}
    tv = a.mode == 'channels' or a.mode == 'feeds' and a.feed_context == 'tv'
    out = {'mode': a.mode, 'accent': 'tv' if tv else a.league, 'title': heading.get(a.mode, ''), 'busy': a.busy(), 'message': a.message, 'info': [],
           'columns': None, 'rows': [], 'selected': -1, 'empty': '', 'actions': '', 'global': GLOBAL_KEYS, 'query': None}
    rows, selected = out['rows'], None
    if a.mode == 'search':
        out['query'] = a.search_query
        for index, text, style in a.search_rows(120):
            if index is not None and index == a.search_position: selected = len(rows)
            rows.append(row(('  ' if index is not None and style != 'team' else '') + text.strip(), style={'player-heading': 'heading', 'channel-heading': 'heading'}.get(style, style),
                            pick=index is not None))
        if not a.search_results:
            waiting = bool(a.search_inflight & a.search_needed or a.player_due or a.player_key in a.player_inflight)
            out['empty'] = ('Try: stephen curry  |  lakers today  |  mavs vs rockets' if not a.search_query else
                            'Looking for matches' if waiting else 'Keep typing a player, team or league.')
        out['actions'] = 'Type to search   ↑/↓ select   Enter open'
        out['global'] = 'Esc back   Ctrl-U clear   Ctrl-X quit'
    elif a.mode == 'help':
        rows.extend(row(line, pick=False) for line in app.HELP_LINES[a.help_scroll:])
        out['actions'] = 'j/k scroll   Esc back'
    elif a.mode == 'home':
        for _, key, label, detail in app.HOME_ITEMS: rows.append(row(label, cells=[key, label, detail], style='home'))
        selected = a.home_index; out['actions'] = 'Enter open'
    elif a.mode == 'settings':
        for label, value, _ in a.settings_rows(): rows.append(row(label, cells=[label, value]))
        selected = a.settings_index; out['actions'] = 'Enter change   Esc back'
    elif a.mode == 'leagues':
        for key in app.LEAGUES: rows.append(row(app.LEAGUES[key][0]))
        selected = a.league_index; out['actions'] = 'Enter choose   Esc back'
    elif a.mode == 'games':
        out['info'] = [f'{app.LEAGUES[a.league][0]}  ·  {a.day:%a %b %d} (ET date)  ·  filter: {a.search or "all"}']
        for game in a.games:
            when = 'Live TV channel' if game.id == app.REDZONE else game.start.astimezone().strftime('%a %I:%M %p')
            live = game.state in ('live', 'redzone-live')
            rows.append(row(game.title, cells=[STATUS.get(game.state, '?'), when, game.title],
                            detail=', '.join(game.broadcasts) or 'No TV listing', style='live' if live else ''))
        selected = a.gi
        out['empty'] = 'Loading schedule' if a.loading_games else 'No upcoming or live games. Change date or search.'
        out['actions'] = 'Enter sources   s sport   [ ] date   f filter   r refresh   Esc back'
    elif a.mode == 'replays':
        out['info'] = [f'{app.LEAGUES[a.league][0]}  ·  recent replays  ·  search: {a.replay_search or "all"}']
        for item in a.replays: rows.append(row(item.title, cells=[item.source, item.title]))
        selected = a.ri
        out['empty'] = 'Searching replay catalogs' if a.replay_pending else 'No replays found. Try another search or league.'
        out['actions'] = 'Enter sources   s sport   f search titles   r refresh   Esc back'
    elif a.mode == 'channels':
        shown = a.visible_channels()
        out['info'] = [f'{len(shown)} channels  ·  filter: {a.channel_filter or "all"}']
        for channel in shown:
            count = len(channel.sources)
            rows.append(row(channel.name, detail=f'{count} source{"s" if count != 1 else ""}', style='bold' if channel.featured else ''))
        selected = a.ci
        out['empty'] = 'Loading channels' if a.channels_loading else 'No channels match. Press f to change the filter.'
        out['actions'] = 'Enter sources   f filter   r refresh   Esc back'
    elif a.mode == 'replay_sources':
        out['info'] = [a.current_bundle.title]
        for item in a.current_bundle.sources: rows.append(row(item.source))
        selected = a.bundle_index; out['actions'] = 'Enter sources   Esc back'
    elif a.mode == 'feeds':
        feeds_view(a, out)
        selected = a.fi
    if selected is not None and 0 <= selected < len(rows): out['selected'] = selected
    return out


def feeds_view(a, out):
    if a.feed_context == 'replay' and a.current_replay:
        out['info'] = [a.current_replay.title, f'Replay sources from {a.current_replay.source}']
    elif a.feed_context == 'tv' and a.current_channel:
        out['info'] = [a.current_channel.name, 'Live TV']
    elif a.gi < len(a.games):
        game = a.games[a.gi]
        out['info'] = [f'{game.title}  ·  {game.start.astimezone():%a %b %d %I:%M %p}', 'TV: ' + (', '.join(game.broadcasts) or 'not listed')]
    ad_mode = 'AI NBA' if a.ai_ad_mute else 'AI preview' if a.ai_ad_observe else 'marked replays' if a.ad_mute else 'off'
    context = [a.player.upper()]
    if a.filter != 'all': context.append(f'side: {a.filter}')
    if a.provider_filter != 'all': context.append(f'source: {a.provider_filter}')
    if a.feed_search: context.append(f'search: {a.feed_search}')
    if a.cache != 15: context.append(f'cache: {a.cache}s')
    if ad_mode != 'off': context.append(f'ad mute: {ad_mode}')
    if a.ai_ad_mute and a.ad_breaks != 'mute': context.append('breaks: ' + breaks.LABELS[a.ad_breaks])
    if a.rank_by_checks: context.append('ranked')
    if not a.dual_stream and a.feed_context in ('live', 'tv'): context.append('backup: off')
    out['info'].append('  ·  '.join(context))
    out['columns'] = ['SOURCE', 'SIDE', 'QUALITY', 'CHECK']
    fs = a.visible_feeds()
    a.fi = min(a.fi, max(0, len(fs)-1))
    history = a.config.get('health') if isinstance(a.config.get('health'), dict) else {}
    for feed in fs:
        opening = a.action_current and a.action_current[1] == a.feed_generation and a.action_current[2] is feed
        status = (('Opening' if a.action_current[3] == 'play' else 'Inspecting') if opening else
                  'testing' if (a.feed_generation, id(feed)) in a.manual_checks else
                  a.manual_status.get(id(feed), a.auto_checks.get(id(feed))))
        if id(feed) in a.auto_checked_at and status == a.auto_checks.get(id(feed)) and id(feed) not in a.manual_status:
            status = f'{status} {app.check_age(time.time() - a.auto_checked_at[id(feed)])}'
        record = dict(history.get(app.grading.source_key(feed), {}))
        if id(feed) in a.auto_heights: record['height'] = a.auto_heights[id(feed)]
        if id(feed) in a.auto_speeds: record['speed'] = a.auto_speeds[id(feed)]
        shown = app.describe_feed(feed, record, status)
        fallback = {'ppv': 'Uses local resolver', 'web': 'Browser-only; no media URL'}.get(feed.kind, 'Direct stream')
        style = 'fail' if a.failed_check(feed) else 'warn' if shown.check.startswith('May lag') else 'ok' if str(status).startswith('OK') else 'busy' if status in ('checking', 'testing', 'Opening', 'Inspecting') else ''
        out['rows'].append(row(shown.name, cells=[shown.name, shown.side, shown.quality, shown.check],
                               detail=shown.detail or fallback, style=style))
    failed = (sum(1 for f in a.feeds if id(f) not in a.hidden_feeds and a.failed_check(f)) if a.show_failed else a.hidden_failed())
    out['actions'] = ('Enter play   x test   i inspect   u paste URL   ' + ('a side   ' if a.feed_context == 'live' else '')
                      + 'o source   f filter   g rank   ' + ('s sport   ' if a.feed_context != 'tv' else '')
                      + (f'v hide {failed} failed   ' if a.show_failed and failed else f'v show {failed} failed   ' if failed else '')
                      + 'r refresh   Esc back')
    out['empty'] = ('Looking for sources' if a.feed_pending else
                    f'All {failed} sources failed their checks. Press v to show them or r to check again.' if failed and not a.show_failed else
                    'No sources available. Refresh near game time or change the filter.')


class Remote:
    """One headless app behind a lock; a background thread runs the polling the curses loop does between keys."""
    def __init__(self, config):
        self.app, self.lock, self.closed, self.seq = Headless(config), threading.Lock(), threading.Event(), 0
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while not self.closed.wait(.1):
            with self.lock: self.app.poll()

    def view(self):
        """The screen and the events since the last view. seq orders views, so a client can drop one that arrives late."""
        with self.lock:
            out = view(self.app)
            out['events'], self.app.events = self.app.events, []
            self.seq += 1; out['seq'] = self.seq
            return out

    def key(self, name, text=None):
        """Apply one key, named as in CODES or a single typed character. text answers the prompt that key asked for."""
        code, typed = (CODES[name], None) if name in CODES else (ord(name), name) if len(name) == 1 else (None, None)
        if code is None: raise ValueError(f'unknown key {name!r}')
        with self.lock:
            self.app.answer = text
            try:
                if not self.app.handle_key(code, typed): self.app.events.append({'type': 'quit'})
            except NeedText as ask:
                self.app.events.append({'type': 'prompt', 'label': ask.label, 'initial': ask.initial, 'key': name})
            finally:
                self.app.answer = None
        return self.view()

    def exited(self, code):
        with self.lock: self.app.player_exited(code)
        return self.view()

    def close(self):
        self.closed.set()
        with self.lock: self.app.close()
