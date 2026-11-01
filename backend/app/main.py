import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agents.orchestrator import get_orchestrator
from app.api.routes import router
from app.bot.telegram import TelegramBot
from app.core.subscribers import SubscriberStore
from app.llm.client import get_llm
from app.core.warmup import parse_places, warm_in_background
from app.core.config import get_settings
from app.memory.store import KNOWLEDGE, get_store

BACKEND_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = BACKEND_DIR / "data" / "knowledge"
DIST = BACKEND_DIR.parent / "frontend" / "dist"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for _noisy in ("httpx", "httpcore"):  # httpx logs full request URLs at INFO, and the data.gov.in key travels in the URL
    logging.getLogger(_noisy).setLevel(logging.WARNING)

log = logging.getLogger("startup")


@asynccontextmanager
async def lifespan(_: FastAPI):
    store = get_store()
    if store.count(KNOWLEDGE) == 0 and KNOWLEDGE_DIR.exists():
        log.info("seeded %d knowledge chunks", store.seed_dir(KNOWLEDGE_DIR))
    if not get_settings().llm_api_key:
        log.warning("LLM_API_KEY not set -> running in OFFLINE mode (retrieval only)")
    warm_in_background(parse_places(get_settings().warm_places), get_orchestrator().domain.tools["farm_plan"])
    bot = None
    if get_settings().telegram_bot_token:
        s = get_settings()
        bot = TelegramBot(s.telegram_bot_token, get_orchestrator(), get_llm(), SubscriberStore(s.subscriber_db_path), interval_min=s.watch_interval_min)
        bot.start()
        log.info("Telegram bot started")
    yield
    if bot:
        bot.stop()


def _mount_spa(app: FastAPI) -> None:
    """One-container deploy: serve the built SPA from the API (`npm run build` first)."""
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (DIST / path).resolve()
        safe = path and f.is_file() and DIST.resolve() in f.parents  # block ../ traversal
        return FileResponse(f if safe else DIST / "index.html")


def create_app() -> FastAPI:
    settings = get_settings()
    api = FastAPI(title=settings.app_name, lifespan=lifespan)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.include_router(router)
    if DIST.exists():
        _mount_spa(api)
    return api


app = create_app()
