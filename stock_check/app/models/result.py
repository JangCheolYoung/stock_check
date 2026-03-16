from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .status import StockStatus


@dataclass
class StockCheckResult:
    site: str
    product: str
    status: StockStatus
    target_sizes: list[str] = field(default_factory=list)
    available_sizes: list[str] = field(default_factory=list)
    matched_sizes: list[str] = field(default_factory=list)
    message: str = ""
    product_url: str = ""
    checked_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    error: str = ""

    def dedup_keys(self) -> list[str]:
        if self.matched_sizes:
            return [f"{self.site}|{self.product}|{size}|{self.status.value}" for size in self.matched_sizes]
        return [f"{self.site}|{self.product}|ALL|{self.status.value}"]

    @classmethod
    def from_legacy(cls, site: str, payload: dict[str, Any], target_sizes: list[str]) -> "StockCheckResult":
        status_map = {
            "success": StockStatus.IN_STOCK,
            "no_target_sizes": StockStatus.OUT_OF_STOCK,
            "no_sizes": StockStatus.OUT_OF_STOCK,
            "no_options": StockStatus.OUT_OF_STOCK,
            "no_results": StockStatus.PRODUCT_NOT_FOUND,
            "search_failed": StockStatus.SEARCH_FAILED,
            "verification_failed": StockStatus.PRODUCT_NOT_FOUND,
            "timeout": StockStatus.PAGE_ERROR,
            "error": StockStatus.UNKNOWN_ERROR,
        }
        legacy_status = payload.get("status", "error")
        status = status_map.get(legacy_status, StockStatus.UNKNOWN_ERROR)

        return cls(
            site=site,
            product=payload.get("product", ""),
            status=status,
            target_sizes=target_sizes,
            available_sizes=payload.get("available_sizes") or payload.get("available_options") or [],
            matched_sizes=payload.get("sizes") or [],
            message=payload.get("message", legacy_status),
            product_url=payload.get("url", ""),
            error=payload.get("error", ""),
        )
