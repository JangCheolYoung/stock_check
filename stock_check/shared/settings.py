import os
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_stock_check_root() -> Path:
    env_root = os.getenv("STOCK_CHECK_BASE_DIR")
    if env_root:
        return Path(env_root).resolve()
    return get_project_root() / "stock_check"


def get_site_dir(site_name: str) -> Path:
    return get_stock_check_root() / site_name


def env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in {"1", "true", "yes", "y", "on"}
