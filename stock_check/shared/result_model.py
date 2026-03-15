from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from status import StockStatus


@dataclass
class StockCheckResult:
    site: str
    product: str
    target_sizes: List[str] = field(default_factory=list)
    available_sizes: List[str] = field(default_factory=list)
    matched_sizes: List[str] = field(default_factory=list)
    status: StockStatus = StockStatus.UNKNOWN_ERROR
    message: str = ""
    product_url: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat())
    error: Optional[str] = None

    def to_dict(self):
        return {
            "site": self.site,
            "product": self.product,
            "target_sizes": self.target_sizes,
            "available_sizes": self.available_sizes,
            "matched_sizes": self.matched_sizes,
            "status": self.status.value,
            "message": self.message,
            "product_url": self.product_url,
            "checked_at": self.checked_at,
            "error": self.error,
        }
