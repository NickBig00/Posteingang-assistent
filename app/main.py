"""FastAPI application entry point: assembles core and the enabled modules."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.core.config import app_title, enabled_modules
from app.core.db import engine, init_db
from app.core.logging_config import setup_logging
from app.core.overview import OverviewProvider
from app.core.web import (
    OVERVIEW_ENTRY,
    SETTINGS_ENTRY,
    BrandColorProvider,
    NavEntry,
    mount_static,
)
from app.core.web import router as core_router
from app.inbox import web as inbox_web
from app.inbox.overview import inbox_overview
from app.social import web as social_web
from app.social.overview import social_overview
from app.social.profiles import profile_brand_color

# Available modules in navigation order: key -> (routes, sidebar entries, start page figures).
MODULES: dict[str, tuple[APIRouter, list[NavEntry], OverviewProvider]] = {
    "inbox": (inbox_web.router, [inbox_web.NAV_ENTRY], inbox_overview),
    "social": (
        social_web.router,
        [social_web.NAV_ENTRY, social_web.CALENDAR_NAV_ENTRY],
        social_overview,
    ),
}


# Modules whose profile may set the brand colour of the interface.
BRAND_COLOR_PROVIDERS: dict[str, BrandColorProvider] = {"social": profile_brand_color}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    init_db(engine)
    yield


def create_app() -> FastAPI:
    """Build the application with the modules enabled via env `MODULES`."""
    application = FastAPI(title=app_title(), version="0.1.0", lifespan=lifespan)
    mount_static(application)
    application.include_router(core_router)
    navigation: list[NavEntry] = [OVERVIEW_ENTRY]
    providers: list[OverviewProvider] = []
    enabled = enabled_modules(list(MODULES))
    for key in enabled:
        router, entries, provider = MODULES[key]
        application.include_router(router)
        navigation.extend(entries)
        providers.append(provider)
    application.state.navigation = [*navigation, SETTINGS_ENTRY]
    application.state.overview_providers = providers
    # Brand colour of the interface from the brand profile (env BRAND_COLOR takes precedence).
    application.state.brand_color_providers = [
        provider for key, provider in BRAND_COLOR_PROVIDERS.items() if key in enabled
    ]
    return application


app = create_app()
