from .base import BaseCrawler
from stock_check.hyundai import stock_checker


class HyundaiCrawler(BaseCrawler):
    site_name = "hyundai"

    def check_single_stock(self, target):
        return stock_checker.check_single_stock(target)

    def run(self):
        return stock_checker.main()
