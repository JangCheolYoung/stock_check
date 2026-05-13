"""더현대Hi 리뉴얼 후 옵션 Drawer 파싱/매칭 단위 테스트."""

import unittest


SAMPLE_DRAWER_HTML = """
<div role="dialog" class="Drawer_root__Reib6">
  <div class="Drawer_body__hgZBA">
    <div class="CtaDrawer_drawer__MAdLo">
      <div class="CtaDrawer_sec-option__55wna">
        <div class="Select_root__rZO4v">
          <div class="Select_combobox__zC_uy" role="combobox" aria-label="사이즈">
            <span class="Select_value__ZFReF Select_placeholder__6BIr_">사이즈</span>
          </div>
          <div class="Select_listbox__zwPm6">
            <ul id="select-listbox" role="listbox" class="Select_options__ioGf8">
              <li class="Select_option___q_RU Select_disabled__Q8UwZ" role="option" aria-disabled="true">
                <div class="CtaDrawer_options__rpiQE">
                  <div class="CtaDrawer_left___2ULy"><span>XS</span><span>[품절]</span></div>
                </div>
              </li>
              <li class="Select_option___q_RU" role="option" aria-disabled="false">
                <div class="CtaDrawer_options__rpiQE">
                  <div class="CtaDrawer_left___2ULy"><span>S</span>[남은수량 : 10] </div>
                </div>
              </li>
              <li class="Select_option___q_RU" role="option" aria-disabled="false">
                <div class="CtaDrawer_options__rpiQE">
                  <div class="CtaDrawer_left___2ULy"><span>M</span>[남은수량 : 3] </div>
                </div>
              </li>
              <li class="Select_option___q_RU" role="option" aria-disabled="false">
                <div class="CtaDrawer_options__rpiQE">
                  <div class="CtaDrawer_left___2ULy"><span>L</span>[남은수량 : 10] </div>
                </div>
              </li>
              <li class="Select_option___q_RU" role="option" aria-disabled="false">
                <div class="CtaDrawer_options__rpiQE">
                  <div class="CtaDrawer_left___2ULy"><span>XL</span>[남은수량 : 6] </div>
                </div>
              </li>
              <li class="Select_option___q_RU Select_disabled__Q8UwZ" role="option" aria-disabled="true">
                <div class="CtaDrawer_options__rpiQE">
                  <div class="CtaDrawer_left___2ULy"><span>XXL</span><span>[품절]</span></div>
                </div>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
"""


class ParseSizeStocksTests(unittest.TestCase):
    def test_parse_full_drawer(self):
        from stock_check.hyundai.stock_checker import parse_size_stocks

        stocks = parse_size_stocks(SAMPLE_DRAWER_HTML)
        sizes = [s["size"] for s in stocks]
        self.assertEqual(sizes, ["XS", "S", "M", "L", "XL", "XXL"])

        by_size = {s["size"]: s for s in stocks}
        self.assertTrue(by_size["XS"]["soldout"])
        self.assertEqual(by_size["XS"]["qty"], 0)
        self.assertFalse(by_size["S"]["soldout"])
        self.assertEqual(by_size["S"]["qty"], 10)
        self.assertEqual(by_size["M"]["qty"], 3)
        self.assertEqual(by_size["L"]["qty"], 10)
        self.assertEqual(by_size["XL"]["qty"], 6)
        self.assertTrue(by_size["XXL"]["soldout"])

    def test_parse_empty(self):
        from stock_check.hyundai.stock_checker import parse_size_stocks

        self.assertEqual(parse_size_stocks(""), [])
        self.assertEqual(parse_size_stocks("<div>no options</div>"), [])


class MatchSizeStocksTests(unittest.TestCase):
    def _stocks(self):
        from stock_check.hyundai.stock_checker import parse_size_stocks

        return parse_size_stocks(SAMPLE_DRAWER_HTML)

    def test_exact_match_case_insensitive(self):
        from stock_check.hyundai.stock_checker import match_size_stocks

        matched = match_size_stocks(self._stocks(), ["m", "L"])
        self.assertEqual([m["size"] for m in matched], ["m", "L"])
        qty_map = {m["size"]: m["qty"] for m in matched}
        self.assertEqual(qty_map["m"], 3)
        self.assertEqual(qty_map["L"], 10)

    def test_soldout_is_excluded(self):
        from stock_check.hyundai.stock_checker import match_size_stocks

        # XS, XXL 은 품절 — 매칭에서 제외
        matched = match_size_stocks(self._stocks(), ["XS", "XXL", "S"])
        self.assertEqual([m["size"] for m in matched], ["S"])

    def test_no_substring_false_positive(self):
        from stock_check.hyundai.stock_checker import match_size_stocks

        # 'L' 타겟이 'XL'/'XXL' 에 부분일치하지 않아야 한다 (정확 일치)
        # 단 SAMPLE 에는 'L' 도 있어서 매칭됨 — 부분일치 오탐 검증을 위해 별도 stocks 구성
        stocks = [
            {"size": "XL", "qty": 1, "soldout": False},
            {"size": "XXL", "qty": 1, "soldout": False},
        ]
        matched = match_size_stocks(stocks, ["L"])
        self.assertEqual(matched, [])


if __name__ == "__main__":
    unittest.main()
