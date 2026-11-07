import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import walk


class ClickReport(unittest.TestCase):
    def test_x10_puts_each_coordinate_in_one_byte(self):
        self.assertEqual(walk.click_report({'1000'}, 95, 4), b'\x1b[M \x7f$\x1b[M#\x7f$')
        self.assertEqual(walk.click_report({'1000'}, 96, 4), b'\x1b[M \x80$\x1b[M#\x80$')
        self.assertEqual(walk.click_report({'1002'}, walk.COLUMNS, walk.ROWS), b'\x1b[M \x8e>\x1b[M#\x8e>')

    def test_sgr_sends_decimal_coordinates(self):
        self.assertEqual(walk.click_report({'1000', '1006'}, 96, 4), b'\x1b[<0;96;4M\x1b[<0;96;4m')


if __name__ == '__main__':
    unittest.main()
