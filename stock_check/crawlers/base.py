from abc import ABC, abstractmethod
from typing import Dict


class BaseCrawler(ABC):
    site_name: str

    @abstractmethod
    def check_single_stock(self, target: Dict):
        raise NotImplementedError

    @abstractmethod
    def run(self):
        raise NotImplementedError
