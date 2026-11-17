"""FastAPI application entry point."""
import importlib
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .core.audit import request_id_var
from .core.headers import SecurityHeadersMiddleware
from .db import init_db

# Feature routers, mounted in this order.
ROUTER_MODULES = [
    "auth",
    "registration",
    "users",
    "audit",
    "curriculum",
    "outcomes",
    "offerings",
    "criteria",
    "mastery",
    "sources",
    "ai",
    "mappings",
    "enrollment",
    "items",
    "content",
    "diagnostic",
    "paths",
    "learn",
    "quiz",
    "hints",
    "debrief",
    "reteach",
    "console",
    "escalations",
    "path_control",
    "progress",
    "appeals",
    "topics",
    "tutor",
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    from .services import automation

    automation.resume_pending()  # finish question drafting that a restart interrupted (automation mode)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI Tutor API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url, "http://localhost:5173", "https://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        """Tag every request so audit events can be correlated for troubleshooting."""
        rid = uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "env": settings.app_env}

    for name in ROUTER_MODULES:
        app.include_router(importlib.import_module(f".routers.{name}", __package__).router)
    if settings.is_dev:
        from .routers import dev

        app.include_router(dev.router)
    return app


app = create_app()
