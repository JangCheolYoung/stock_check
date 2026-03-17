from .base import BaseCrawler
from stock_check.cultizm import stock_checker


class CultizmCrawler(BaseCrawler):
    site_name = "cultizm"

    def check_single_stock(self, target):
        return stock_checker.check_single_stock(target)

    def run(self):
        return stock_checker.main()
