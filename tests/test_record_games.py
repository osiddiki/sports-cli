import pathlib
import tempfile
import unittest

from sportscli.tools import record_games


class GrowthTests(unittest.TestCase):
    def test_a_recording_that_stops_growing_is_stalled(self):
        now = [0.0]
        with tempfile.TemporaryDirectory() as folder:
            output = pathlib.Path(folder) / 'game.ts'
            growth = record_games.Growth(output, clock=lambda: now[0])
            output.write_bytes(b'x')
            now[0] = 100; self.assertFalse(growth.stalled())
            output.write_bytes(b'xx')
            now[0] = 200; self.assertFalse(growth.stalled())
            now[0] = 330; self.assertTrue(growth.stalled())


    def test_parts_skip_files_already_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = pathlib.Path(folder)
            (folder / 'game-part01.ts').write_bytes(b'x')
            self.assertEqual(record_games.next_part(folder, 'game', 0), (2, folder / 'game-part02.ts'))


class PreferTests(unittest.TestCase):
    def test_preferred_sources_match_source_or_label_ignoring_case(self):
        from types import SimpleNamespace
        feed = SimpleNamespace(source='NTV', label='Phoenix | NBCS Boston USA')
        self.assertTrue(record_games.preferred(feed, ['nbcs']))
        self.assertFalse(record_games.preferred(feed, ['ESPN']))
        self.assertFalse(record_games.preferred(feed, []))


if __name__ == '__main__':
    unittest.main()
