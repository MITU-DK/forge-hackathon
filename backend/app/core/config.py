"""All configuration lives here. Never read os.environ anywhere else."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# repo-root/.env (works whether you start the API from backend/ or the repo root)
ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(str(ROOT_ENV), ".env"), extra="ignore")

    app_name: str = "KisaanMitra"
    domain: str = "farm"  # which module in app/domain/ to load

    # --- Live data sources (NO dummy data: every value must come from a live source) --------------------
    data_gov_api_key: str = ""  # free key from data.gov.in (mandi prices)
    mandi_resource_id: str = "9ef84268-d588-465a-a308-a864a43d0070"  # confirm with `make check-live`
    mandi_history_resource_id: str = "35985678-0d79-46b4-9ed6-6f13308a1d24"  # dated prices; the resource above is today-only
    mandi_filter_style: str = "auto"  # auto | lower | title  (data.gov.in field-name case)
    mandi_days: int = 10  # history window: must reach the day the 7-day trend compares with (5-9 days back)
    cache_db_path: str = "data/cache/http_cache.sqlite"  # last real responses survive a restart (stale-labelled); empty = memory only
    telegram_bot_token: str = ""  # from @BotFather; empty = no Telegram bot
    telegram_bot_username: str = ""  # optional, only used to show a "chat on Telegram" link
    watch_interval_min: int = 30  # how often the bot re-checks every watched farm
    subscriber_db_path: str = "data/cache/subscribers.sqlite"  # Telegram farmers: place, crop, language, last snapshot; empty = memory only
    warm_places: str = ""  # optional: "Nashik, Maharashtra|Onion; Pune, Maharashtra|Tomato" - plans fetched at start so the cache is warm
    snapshot_db_path: str = "data/cache/snapshots.sqlite"  # watch mode: last farm snapshot per device; empty = memory only
    session_db_path: str = "data/cache/sessions.sqlite"  # saved place/crop and recent turns survive a restart; empty = memory only
    cache_ttl_s: float = 900.0  # live-data cache (stale real data is shown labelled if the source is down)
    tool_timeout_s: float = 8.0
    cors_origins: str = "http://localhost:5173"
    ingest_api_key: str = ""  # empty = /api/ingest disabled; set to enable (send as X-Admin-Key)
    rate_limit_per_min: int = 20  # chat requests per client+session per minute (0 = off)

    # --- LLM providers (any OpenAI-compatible endpoint) -----------------
    # Primary: Groq (fast + free tier). Fallback: any second provider.
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_api_key3: str = ""  # a third key for the same provider, same idea
    llm_api_key2: str = ""  # a second key for the same provider (own daily quota): used when the first one is rate limited or blocked
    llm_model: str = "openai/gpt-oss-120b"
    llm_fast_model: str = "openai/gpt-oss-20b"  # router / cheap calls
    llm_vision_model: str = "qwen/qwen3.8-27b"
    stt_model: str = "whisper-large-v3-turbo"

    fallback_base_url: str = ""  # e.g. https://openrouter.ai/api/v1
    fallback_api_key: str = ""
    fallback_model: str = ""  # one model id, or several separated by commas: tried in order
    fallback2_base_url: str = ""  # optional second fallback provider (e.g. Google Gemini's OpenAI-compatible endpoint)
    fallback2_api_key: str = ""
    fallback2_model: str = ""

    llm_timeout_s: float = 25.0
    llm_temperature: float = 0.3

    # --- Memory ----------------------------------------------------------
    qdrant_url: str = ""  # empty => in-process memory (zero setup)
    qdrant_api_key: str = ""
    embeddings: str = "hash"  # "hash" (offline, no download) | "fastembed"
    embed_dim: int = 256  # only for "hash"
    rag_top_k: int = 6
    rag_min_score: float = 0.05

    # --- Agents ----------------------------------------------------------
    max_self_corrections: int = 2
    history_turns: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()
