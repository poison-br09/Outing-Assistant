"""Environment-driven settings and the editable estimation assumptions."""
import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).parent / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_nemotron_model: str = ""
    llm_request_timeout_seconds: float = 20.0
    llm_max_retries: int = 1

    geoapify_api_key: str = ""
    geoapify_base_url: str = "https://api.geoapify.com"
    google_maps_api_key: str = ""

    http_timeout_seconds: float = 8.0
    log_level: str = "INFO"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.nvidia_api_key and self.nvidia_nemotron_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_assumptions() -> dict:
    return json.loads((DATA_DIR / "assumptions.json").read_text(encoding="utf-8"))
