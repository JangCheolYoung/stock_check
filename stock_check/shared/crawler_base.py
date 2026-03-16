from abc import ABC, abstractmethod
from typing import Dict


class BaseCrawler(ABC):
    @abstractmethod
    def check_single_stock(self, target: Dict):
        raise NotImplementedError
