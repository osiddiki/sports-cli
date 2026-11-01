"""Short-lived memory of source check results, so reopening a game keeps its order."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass

KEEP = 2 * 3600


def default_path():
    base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(base, 'sports-live', 'checks.json')


def key(feed) -> str:
    # Hashed so listing URLs, which can carry tokens, never reach the disk.
    raw = '\n'.join((feed.source, feed.label, feed.url))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


@dataclass(frozen=True)
class Entry:
    status: str
    height: int | None
    checked_at: float
    speed: float | None = None     # seconds of video downloaded per second; below 1 the player falls behind


class CheckCache:
    def __init__(self, path=None, keep=KEEP, clock=time.time):
        self.path = path or default_path()
        self.keep = keep
        self.clock = clock
        self.entries = self._load()

    def _load(self):
        try:
            with open(self.path, encoding='utf-8') as source: data = json.load(source)
            return {name: Entry(item['status'], item.get('height') if isinstance(item.get('height'), int) else None,
                                float(item['checked_at']),
                                float(item['speed']) if isinstance(item.get('speed'), (int, float)) else None)
                    for name, item in data.items()
                    if isinstance(item, dict) and isinstance(item.get('status'), str)}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return {}

    def _fresh(self, entry):
        return self.clock() - entry.checked_at < self.keep

    def get(self, feed) -> Entry | None:
        entry = self.entries.get(key(feed))
        return entry if entry and self._fresh(entry) else None

    def put(self, feed, status, height=None, speed=None):
        if status != 'OK' and not (isinstance(status, str) and status.startswith('HTTP ')): return
        self.entries[key(feed)] = Entry(status, height if isinstance(height, int) else None, self.clock(),
                                        float(speed) if isinstance(speed, (int, float)) else None)

    def save(self):
        self.entries = {name: entry for name, entry in self.entries.items() if self._fresh(entry)}
        data = {name: {'status': entry.status, 'height': entry.height, 'checked_at': entry.checked_at, 'speed': entry.speed}
                for name, entry in self.entries.items()}
        folder = os.path.dirname(self.path)
        try:
            os.makedirs(folder, mode=0o700, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=folder, prefix='.checks-')
            try:
                with os.fdopen(handle, 'w', encoding='utf-8') as out: json.dump(data, out)
                os.replace(temporary, self.path)
            except BaseException:
                try: os.unlink(temporary)
                except OSError: pass
                raise
        except OSError:
            pass
