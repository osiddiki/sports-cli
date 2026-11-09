"""The real Merged.prepare() against verification/relay/BackupChoice.tla, for every mix of up to three backups."""
import itertools
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sportscli import relay
from tests.test_merge import ts_with_pts

KINDS = ('names', 'dates', 'apart', 'clock', 'other', 'unreadable')
LINES_UP = {'names', 'dates', 'clock'}
NEEDS_CLOCK = {'clock', 'other', 'unreadable'}
PRIMARY_CLOCK = 1000
CLOCKS = {'clock': PRIMARY_CLOCK + 10, 'other': 90_000}


def playlist(names, date):
    lines = ['#EXTM3U', '#EXT-X-TARGETDURATION:2', '#EXT-X-MEDIA-SEQUENCE:0']
    for n in range(6):
        if date is not None: lines.append(f'#EXT-X-PROGRAM-DATE-TIME:2026-11-08T20:{date:02d}:{2 * n:02d}Z')
        lines.append(f'#EXTINF:2,\n{names}{n:03d}.ts')
    return ('\n'.join(lines) + '\n').encode()


def start(kinds, loads):
    """Offer the primary and one backup per kind; return (kept backup number or 0, hosts whose segment was downloaded)."""
    downloaded = []
    # The primary carries program dates only when a backup is compared by them; two dated playlists never reach the clocks.
    dated = any(kind in ('dates', 'apart') for kind in kinds)
    def fetch(url, *args, **kwargs):
        host = url.split('/')[2]
        number = int(host[1:]) if host != 'primary' else 0
        kind = kinds[number - 1] if number else None
        if url.endswith('.m3u8'):
            if number and not loads[number - 1]: raise OSError('playlist down')
            if not number: return playlist('seg', 0 if dated else None)
            return playlist('seg' if kind == 'names' else f'b{number}x', {'dates': 0, 'apart': 5}.get(kind))
        downloaded.append(host)
        if kind == 'unreadable': raise OSError('segment down')
        return ts_with_pts((CLOCKS[kind] if number else PRIMARY_CLOCK) * 90000)
    # Merged alone: starting the relay's local server for each of 1,884 cases would take minutes.
    session = relay.Merged(SimpleNamespace(base='http://127.0.0.1:0'), 'model',
                           [('https://primary/live.m3u8', {})] + [(f'https://b{n}/live.m3u8', {}) for n in range(1, len(kinds) + 1)])
    try:
        with patch.object(relay, '_download', side_effect=fetch):
            usable = session.prepare()
        return (session.kept[1] if usable == 2 else 0), sorted(downloaded)
    finally:
        session.stop()


def expected(kinds, loads):
    """BackupChoice.tla's Best(Ready), and the downloads Merged.best_backup() may make to decide it."""
    ready = [n for n, loaded in enumerate(loads, 1) if loaded]
    if not ready: return 0, []
    lining = [n for n in ready if kinds[n - 1] in LINES_UP]
    kept = lining[0] if lining else ready[0]
    if len(ready) < 2: return kept, []
    settled = next((n for n in ready if kinds[n - 1] in ('names', 'dates')), None)
    unknown = [n for n in ready if kinds[n - 1] in NEEDS_CLOCK and (settled is None or n < settled)]
    return kept, sorted(['primary'] + [f'b{n}' for n in unknown]) if unknown else []


class BackupChoiceModelTest(unittest.TestCase):
    def test_every_mix_of_backups_keeps_what_the_model_keeps_with_each_segment_downloaded_once(self):
        checked = 0
        for count in (1, 2, 3):
            for kinds in itertools.product(KINDS, repeat=count):
                # A dated primary and an undated backup compare by clock in the code, as the model's kinds say.
                for loads in itertools.product((True, False), repeat=count):
                    self.assertEqual(start(kinds, loads), expected(kinds, loads), f'backups {kinds}, loaded {loads}')
                    checked += 1
        self.assertEqual(checked, 2 * 6 + 4 * 36 + 8 * 216)


if __name__ == '__main__':
    unittest.main()
