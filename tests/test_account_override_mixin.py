import unittest

from src.core.account_override_mixin import AccountOverrideMixin


class TestAccountOverrideMixin(unittest.TestCase):

    def test_coerce_bool_from_string_true(self):
        result = AccountOverrideMixin._coerce_override_value(True, "true")
        self.assertTrue(result)

    def test_coerce_bool_from_string_false(self):
        result = AccountOverrideMixin._coerce_override_value(False, "false")
        self.assertFalse(result)

    def test_coerce_bool_from_bool(self):
        self.assertFalse(AccountOverrideMixin._coerce_override_value(True, False))
        self.assertTrue(AccountOverrideMixin._coerce_override_value(False, True))

    def test_coerce_int_from_string(self):
        result = AccountOverrideMixin._coerce_override_value(42, "100")
        self.assertEqual(result, 100)
        self.assertIsInstance(result, int)

    def test_coerce_int_invalid_string(self):
        result = AccountOverrideMixin._coerce_override_value(42, "abc")
        self.assertEqual(result, 42)

    def test_coerce_float(self):
        result = AccountOverrideMixin._coerce_override_value(1.5, "2.5")
        self.assertEqual(result, 2.5)
        self.assertIsInstance(result, float)

    def test_coerce_list_passthrough(self):
        base = [1, 2, 3]
        override = [4, 5]
        result = AccountOverrideMixin._coerce_override_value(base, override)
        self.assertEqual(result, [4, 5])

    def test_coerce_list_mismatch_returns_base(self):
        result = AccountOverrideMixin._coerce_override_value([1, 2], "not a list")
        self.assertEqual(result, [1, 2])

    def test_coerce_str(self):
        result = AccountOverrideMixin._coerce_override_value("hello", 123)
        self.assertEqual(result, "123")

    def test_coerce_none_passthrough(self):
        self.assertIsNone(AccountOverrideMixin._coerce_override_value(None, None))
        self.assertEqual(AccountOverrideMixin._coerce_override_value("a", None), None)
        self.assertEqual(AccountOverrideMixin._coerce_override_value(None, "b"), "b")


if __name__ == "__main__":
    unittest.main()
