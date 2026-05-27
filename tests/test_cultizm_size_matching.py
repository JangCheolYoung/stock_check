import unittest


class CultizmSizeMatchingTests(unittest.TestCase):
    def test_exact_size_match_case_insensitive(self):
        from stock_check.cultizm.stock_checker import match_size_stocks

        stocks = [
            {"size": "M", "qty": None, "soldout": False},
            {"size": "L", "qty": None, "soldout": False},
            {"size": "XL", "qty": None, "soldout": False},
        ]

        matched = match_size_stocks(stocks, ["m", "L"])
        self.assertEqual([m["size"] for m in matched], ["m", "L"])

    def test_soldout_is_excluded(self):
        from stock_check.cultizm.stock_checker import match_size_stocks

        stocks = [
            {"size": "M", "qty": 0, "soldout": True},
            {"size": "L", "qty": None, "soldout": False},
        ]

        matched = match_size_stocks(stocks, ["M", "L"])
        self.assertEqual([m["size"] for m in matched], ["L"])

    def test_no_substring_false_positive(self):
        from stock_check.cultizm.stock_checker import match_size_stocks

        stocks = [
            {"size": "XL", "qty": None, "soldout": False},
            {"size": "XXL", "qty": None, "soldout": False},
        ]

        matched = match_size_stocks(stocks, ["L"])
        self.assertEqual(matched, [])

    def test_product_title_matching_uses_meaningful_tokens(self):
        from stock_check.cultizm.stock_checker import _product_title_matches

        self.assertTrue(
            _product_title_matches(
                "RRL Buffalo Western Shirt Indigo/Cream",
                "RRL Buffalo Western Shirt Indigo/Cream",
                "/products/rrl-buffalo-western-shirt-indigocream",
            )
        )
        self.assertFalse(
            _product_title_matches(
                "RRL Slider-Buckle Black",
                "Studio D'Artisan 4619 Sashiko Denim 2nd Type Jacket One Wash Indigo",
                "/products/studio-dartisan-sd-4101e-gl3-1st-type-denim-jacket-one-wash-ecru",
            )
        )


if __name__ == "__main__":
    unittest.main()
