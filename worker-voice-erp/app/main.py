
"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.inventory import router as inventory_router
from app.api.production import router as production_router
from app.api.voice import router as voice_router

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Bicycle Assembly Voice ERP")

app.include_router(auth_router, prefix="/api/v1")
app.include_router(inventory_router, prefix="/api/v1")
app.include_router(production_router, prefix="/api/v1")
app.include_router(voice_router)

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
