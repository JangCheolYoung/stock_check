from stock_check.app.crawlers.base import BaseCrawler
from stock_check.app.models import StockCheckResult

from stock_check.hyundai.stock_checker import check_single_stock


class HyundaiCrawler(BaseCrawler):
    site_name = "hyundai"

    def check_target(self, target: dict) -> StockCheckResult:
        payload = check_single_stock(target)
        return StockCheckResult.from_legacy(self.site_name, payload, target.get("sizes", []))
