from abc import ABC, abstractmethod

from stock_check.app.models import StockCheckResult


class BaseCrawler(ABC):
    site_name: str

    @abstractmethod
    def check_target(self, target: dict) -> StockCheckResult:
        raise NotImplementedError
