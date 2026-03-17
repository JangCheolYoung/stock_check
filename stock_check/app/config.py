import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    data_root: Path
    env_file: Path

    @classmethod
    def from_env(cls) -> "AppConfig":
        project_root = Path(__file__).resolve().parents[2]
        default_data_root = project_root / "stock_check"
        data_root = Path(os.getenv("STOCK_CHECK_DATA_ROOT", str(default_data_root)))
        env_file = Path(os.getenv("STOCK_CHECK_ENV_FILE", str(default_data_root / "shared" / ".env")))
        return cls(project_root=project_root, data_root=data_root, env_file=env_file)

    def site_dir(self, site: str) -> Path:
        return self.data_root / site
