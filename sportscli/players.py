"""Look up player names lazily and relate them to the bundled team index."""
from __future__ import annotations

import concurrent.futures
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass

from . import smart_search

SEARCH_URL = 'https://site.web.api.espn.com/apis/search/v2'
ROSTER_URL = 'https://site.api.espn.com/apis/site/v2/sports'


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    team: smart_search.Team
    verified: bool


class Directory:
    """One background worker uses a small, expiring cache of requested rosters."""
    def __init__(self, index, leagues):
        self.leagues = leagues
        self.slug_to_league = {path.rsplit('/', 1)[-1]: key
                               for key, (_, path, _) in leagues.items()}
        self.teams = {(team.league, smart_search.normalize(team.display)): team
                      for team in index.teams}
        self.rosters = {}
        self.roster_lock = threading.Lock()

    def clear(self):
        with self.roster_lock: self.rosters.clear()

    def remember(self, key, ids, seconds):
        with self.roster_lock:
            self.rosters[key] = (time.monotonic() + seconds, ids)
            while len(self.rosters) > 80: self.rosters.pop(next(iter(self.rosters)))

    def roster(self, team, request):
        key = (team.league, team.id)
        with self.roster_lock: cached = self.rosters.get(key)
        if cached and cached[0] > time.monotonic(): return cached[1]
        path = self.leagues[team.league][1]
        ident = urllib.parse.quote(team.id, safe='')
        try:
            data = request(f'{ROSTER_URL}/{path}/teams/{ident}/roster', timeout=4)
            rows = data.get('athletes') if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                self.remember(key, None, 30)
                return None
            athletes = []
            for row in rows:
                if not isinstance(row, dict): continue
                if isinstance(row.get('items'), list): athletes.extend(row['items'])
                else: athletes.append(row)
            ids = {str(row['id']) for row in athletes if isinstance(row, dict) and row.get('id')}
            if not ids:
                self.remember(key, None, 30)
                return None
            self.remember(key, ids, 900)
            return ids
        except (OSError, ValueError, TypeError, KeyError):
            self.remember(key, None, 30)
            return None

    def search(self, fragment, league, request, canceled=lambda: False, on_update=lambda value: None):
        if canceled(): raise concurrent.futures.CancelledError()
        params = urllib.parse.urlencode({'query':fragment, 'type':'player', 'limit':15})
        data = request(f'{SEARCH_URL}?{params}', timeout=6)
        if canceled(): raise concurrent.futures.CancelledError()
        candidates = []
        for group in data.get('results', []):
            if not isinstance(group, dict) or group.get('type') != 'player': continue
            for row in group.get('contents', []):
                if not isinstance(row, dict): continue
                candidate_league = self.slug_to_league.get(row.get('defaultLeagueSlug'))
                if not candidate_league or (league and candidate_league != league): continue
                team = self.teams.get((candidate_league, smart_search.normalize(row.get('subtitle', ''))))
                name = row.get('displayName')
                match = re.search(r'(?:^|~)a:(\d+)', str(row.get('uid', '')))
                if not match:
                    link = row.get('link') if isinstance(row.get('link'), dict) else {}
                    match = re.search(r'/id/(\d+)', str(link.get('web', '')))
                if team and isinstance(name, str) and name.strip() and match:
                    candidates.append((match.group(1), name.strip(), team))
        selected, checked, seen = [], [], set()
        for ident, name, team in candidates:
            if canceled(): raise concurrent.futures.CancelledError()
            identity = (team.league, ident)
            if identity in seen: continue
            seen.add(identity)
            key = (team.league, team.id)
            # Do not fetch a whole league's rosters on a broad partial query.
            if key not in checked and len(checked) >= 5: continue
            if key not in checked: checked.append(key)
            selected.append(Player(ident, name, team, False))
            if len(selected) >= 8: break
        result = selected
        if result: on_update(list(result))
        if not checked: return result
        if canceled(): raise concurrent.futures.CancelledError()
        teams = {(item.team.league, item.team.id): item.team for item in reversed(selected)}
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(checked))
        try:
            pending = [(key, pool.submit(self.roster, teams[key], request)) for key in checked]
            for (league_id, team_id), future in pending:
                ids = future.result()
                if canceled(): raise concurrent.futures.CancelledError()
                if ids is not None:
                    result = [Player(item.id, item.name, item.team, True)
                              if (item.team.league, item.team.id) == (league_id, team_id) else item
                              for item in result
                              if (item.team.league, item.team.id) != (league_id, team_id) or item.id in ids]
                on_update(list(result))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return result
