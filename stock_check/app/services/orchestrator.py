from stock_check.app.config import AppConfig
from stock_check.app.crawlers import CultizmCrawler, HyundaiCrawler
from stock_check.app.models import StockStatus
from stock_check.app.repositories.state_repository import StateRepository
from stock_check.app.services.alert_policy import AlertPolicyV1DailyOnce, AlertPolicyV2AckRepeat


class StockMonitorOrchestrator:
    def __init__(self, site: str, policy: str = "v1"):
        self.config = AppConfig.from_env()
        self.site = site
        self.repository = StateRepository(self.config.site_dir(site))
        if site == "cultizm":
            self.crawler = CultizmCrawler()
        elif site == "hyundai":
            self.crawler = HyundaiCrawler()
        else:
            raise ValueError(f"Unsupported site: {site}")

        self.policy = AlertPolicyV1DailyOnce() if policy == "v1" else AlertPolicyV2AckRepeat()

    def run_target(self, target: dict) -> dict:
        result = self.crawler.check_target(target)

        notifications = []
        for dedup_key in result.dedup_keys():
            state = self.repository.load().get(dedup_key, {})
            decision = self.policy.should_notify(result, state)
            self.repository.upsert_result(dedup_key, result.status.value, result.message)

            if decision.should_send and result.status == StockStatus.IN_STOCK:
                self.repository.mark_notified(dedup_key)
                notifications.append({"dedup_key": dedup_key, "reason": decision.reason})

        return {
            "site": result.site,
            "product": result.product,
            "status": result.status.value,
            "matched_sizes": result.matched_sizes,
            "available_sizes": result.available_sizes,
            "product_url": result.product_url,
            "notifications": notifications,
            "error": result.error,
        }
