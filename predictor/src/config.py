import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    postgres_url: str
    football_data_api_key: str
    balldontlie_api_key: str
    ollama_url: str
    ollama_model: str
    model_storage_path: str
    ingestion_hour_utc: int
    retrain_weekday: str   # "mon" | "tue" | ...
    retrain_hour_utc: int
    log_level: str


def load_settings() -> Settings:
    return Settings(
        postgres_url=_require("POSTGRES_URL"),
        football_data_api_key=_require("FOOTBALL_DATA_API_KEY"),
        balldontlie_api_key=os.environ.get("BALLDONTLIE_API_KEY", ""),
        ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
        model_storage_path=os.environ.get("MODEL_STORAGE_PATH", "/models"),
        ingestion_hour_utc=int(os.environ.get("INGESTION_HOUR_UTC", "6")),
        retrain_weekday=os.environ.get("RETRAIN_WEEKDAY", "mon"),
        retrain_hour_utc=int(os.environ.get("RETRAIN_HOUR_UTC", "3")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )


def _require(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Required env var {key!r} is not set")
    return val
