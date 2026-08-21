"""App factory, CORS, /health."""
from __future__ import annotations

import warnings

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import Base, engine
from .routers import auth, pantry, plans, preferences


def create_app() -> FastAPI:
    Base.metadata.create_all(bind=engine)

    app = FastAPI(
        title="MealMind",
        description="Budget-aware meal planner for Indian households "
                    "(package-size ILP + Bayesian shelf-life decay + entropy "
                    "variety + ARIMA/GARCH price advisories).",
        version="1.0.0",
    )
    # single-household demo app served to a LAN PWA — permissive CORS is fine
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    api = "/api/v1"
    app.include_router(auth.router, prefix=api)
    app.include_router(pantry.router, prefix=api)
    app.include_router(plans.router, prefix=api)
    app.include_router(preferences.router, prefix=api)

    # A teammate cloning the repo inherits the placeholder secret from
    # .env.example; with it, anyone can mint a token for any account.
    if get_settings().jwt_secret == "dev-secret-change-me":
        warnings.warn(
            "JWT_SECRET is still the placeholder from .env.example — tokens are "
            "forgeable. Generate one before exposing this beyond localhost:\n"
            '  python3 -c "import secrets; print(secrets.token_hex(32))"',
            stacklevel=2,
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
