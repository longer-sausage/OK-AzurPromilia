import unittest

from src.data.lang import get_lang_accessor


class TestLangAccessor(unittest.TestCase):

    def test_example_module_reads_zh_cn(self):
        accessor = get_lang_accessor("zh_CN")
        self.assertEqual(accessor.example.confirm_button, "确认")

    def test_example_module_reads_en_us(self):
        accessor = get_lang_accessor("en_US")
        self.assertEqual(accessor.example.confirm_button, "Confirm")

    def test_missing_module_returns_empty(self):
        accessor = get_lang_accessor("zh_CN")
        self.assertEqual(dict(getattr(accessor, "no_such_module", {})._data or {}), {})


if __name__ == "__main__":
    unittest.main()