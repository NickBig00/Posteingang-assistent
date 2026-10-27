"""FastAPI application entry point: assembles core and the enabled modules."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import RedirectResponse

from app.core.config import enabled_modules
from app.core.db import engine, init_db
from app.core.logging_config import setup_logging
from app.core.web import NavEntry
from app.core.web import router as core_router
from app.inbox import web as inbox_web
from app.social import web as social_web

# Available modules in navigation order: key -> (routes, navigation entry).
MODULES: dict[str, tuple[APIRouter, NavEntry]] = {
    "inbox": (inbox_web.router, inbox_web.NAV_ENTRY),
    "social": (social_web.router, social_web.NAV_ENTRY),
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    init_db(engine)
    yield


def create_app() -> FastAPI:
    """Build the application with the modules enabled via env `MODULES`."""
    application = FastAPI(title="Posteingangs-Assistent", version="0.1.0", lifespan=lifespan)
    application.include_router(core_router)
    navigation: list[NavEntry] = []
    for key in enabled_modules(list(MODULES)):
        router, entry = MODULES[key]
        application.include_router(router)
        navigation.append(entry)
    application.state.navigation = navigation
    if all(entry.path != "/" for entry in navigation):
        start = navigation[0].path

        @application.get("/", include_in_schema=False)
        def start_page() -> RedirectResponse:
            return RedirectResponse(start, status_code=307)

    return application


app = create_app()
