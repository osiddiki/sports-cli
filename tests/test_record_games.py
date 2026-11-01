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


if __name__ == '__main__':
    unittest.main()
