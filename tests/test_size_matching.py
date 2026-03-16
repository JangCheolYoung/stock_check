import unittest


class SizeMatchingTests(unittest.TestCase):
    def test_hyundai_exact_size_match_case_insensitive(self):
        from stock_check.hyundai.stock_checker import match_target_sizes

        available = ["S", "M", "L", "XL", "XXL"]
        targets = ["m", "L"]
        matched = match_target_sizes(available, targets)
        self.assertEqual(matched, ["m", "L"])

    def test_hyundai_no_substring_false_positive(self):
        from stock_check.hyundai.stock_checker import match_target_sizes

        available = ["XL", "XXL"]
        targets = ["L"]
        matched = match_target_sizes(available, targets)
        self.assertEqual(matched, [])


if __name__ == "__main__":
    unittest.main()
