import unittest

from src.core.sequence_parser import parse_int_sequence, parse_sequence


class TestSequenceParser(unittest.TestCase):

    def test_parse_sequence_none(self):
        self.assertEqual(parse_sequence(None), [])

    def test_parse_sequence_list(self):
        self.assertEqual(parse_sequence(["a", " b ", "c"]), ["a", "b", "c"])

    def test_parse_sequence_tuple(self):
        self.assertEqual(parse_sequence(("x", "y")), ["x", "y"])

    def test_parse_sequence_string_english_comma(self):
        self.assertEqual(parse_sequence("a, b, c"), ["a", "b", "c"])

    def test_parse_sequence_string_chinese_comma(self):
        self.assertEqual(parse_sequence("a，b，c"), ["a", "b", "c"])

    def test_parse_sequence_empty_items_filtered(self):
        self.assertEqual(parse_sequence("a,, b, ,c"), ["a", "b", "c"])

    def test_parse_sequence_empty_string(self):
        self.assertEqual(parse_sequence(""), [])

    def test_parse_int_sequence_valid(self):
        self.assertEqual(parse_int_sequence("1, 2，3"), [1, 2, 3])

    def test_parse_int_sequence_with_spaces(self):
        self.assertEqual(parse_int_sequence(" 36，14, ,108 "), [36, 14, 108])

    def test_parse_int_sequence_invalid(self):
        with self.assertRaises(ValueError):
            parse_int_sequence("1, abc, 3")


if __name__ == "__main__":
    unittest.main()
