from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def load_dotenv(dotenv_path: str = ".env") -> None:
    env_file = Path(dotenv_path)
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./news.db")
    app_timezone: str = os.getenv("APP_TIMEZONE", "Asia/Hong_Kong")
    sqlite_busy_timeout_seconds: int = int(os.getenv("SQLITE_BUSY_TIMEOUT_SECONDS", "30"))

    zhipu_api_key: str = os.getenv("ZHIPU_API_KEY", "")
    zhipu_search_engine: str = os.getenv("ZHIPU_SEARCH_ENGINE", "search_pro_sogou")
    zhipu_search_count: int = int(os.getenv("ZHIPU_SEARCH_COUNT", "15"))

    brave_api_key: str = os.getenv("BRAVE_API_KEY", "")
    brave_base_url: str = os.getenv("BRAVE_BASE_URL", "https://api.search.brave.com")
    brave_country: str = os.getenv("BRAVE_COUNTRY", "CN")
    brave_search_lang: str = os.getenv("BRAVE_SEARCH_LANG", "zh-hans")
    brave_fallback_lang: str = os.getenv("BRAVE_FALLBACK_LANG", "en")

    firecrawl_api_key: str = os.getenv("FIRECRAWL_API_KEY", "")
    firecrawl_base_url: str = os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev/v2")
    firecrawl_location: str = os.getenv("FIRECRAWL_LOCATION", "Hong Kong")
    firecrawl_country: str = os.getenv("FIRECRAWL_COUNTRY", "HK")
    jina_api_key: str = os.getenv("JINA_API_KEY", "")
    jina_base_url: str = os.getenv("JINA_BASE_URL", "https://r.jina.ai")
    scrape_timeout_seconds: int = int(os.getenv("SCRAPE_TIMEOUT_SECONDS", "20"))
    scrape_concurrency: int = int(os.getenv("SCRAPE_CONCURRENCY", "3"))
    domain_failure_threshold: int = int(os.getenv("DOMAIN_FAILURE_THRESHOLD", "2"))

    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_timeout_seconds: int = int(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "90"))
    kimi_api_key: str = os.getenv("KIMI_API_KEY", "")
    kimi_base_url: str = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    report_primary_model: str = os.getenv("REPORT_PRIMARY_MODEL", "kimi-k2.5")
    report_fallback_model: str = os.getenv("REPORT_FALLBACK_MODEL", "minimax/minimax-m2.7")
    strict_primary_model_for_tool_use: bool = _as_bool(os.getenv("STRICT_PRIMARY_MODEL_FOR_TOOL_USE"), default=True)
    strict_primary_model_for_all_llm: bool = _as_bool(os.getenv("STRICT_PRIMARY_MODEL_FOR_ALL_LLM"), default=True)
    tool_use_fallback_mode: str = os.getenv("TOOL_USE_FALLBACK_MODE", "disabled")

    report_hour: int = int(os.getenv("REPORT_HOUR", "10"))
    report_minute: int = int(os.getenv("REPORT_MINUTE", "0"))
    retrieval_window_hours: int = int(os.getenv("RETRIEVAL_WINDOW_HOURS", "24"))
    max_extractions_per_run: int = int(os.getenv("MAX_EXTRACTIONS_PER_RUN", "18"))
    max_items_per_section: int = int(os.getenv("MAX_ITEMS_PER_SECTION", "3"))
    report_min_formal_topics: int = int(os.getenv("REPORT_MIN_FORMAL_TOPICS", "3"))
    report_target_items: int = int(os.getenv("REPORT_TARGET_ITEMS", "4"))
    pipeline_version: str = os.getenv("PIPELINE_VERSION", "native-v2")

    shadow_mode: bool = _as_bool(os.getenv("SHADOW_MODE"), default=True)
    agent_mode: bool = _as_bool(os.getenv("AGENT_MODE"), default=True)
    agent_fallback_to_pipeline: bool = _as_bool(os.getenv("AGENT_FALLBACK_TO_PIPELINE"), default=True)

    report_title: str = os.getenv("REPORT_TITLE", "高分子加工全视界日报")
    admin_email: str = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "admin123456")
    session_days: int = int(os.getenv("SESSION_DAYS", "7"))

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)


settings = Settings()
