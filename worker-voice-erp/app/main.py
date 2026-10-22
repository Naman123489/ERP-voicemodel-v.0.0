"""FastAPI application entry point.

Person A registered the voice router and the static worker UI here because this
file was empty. Person B: include your auth/production/inventory routers above
the static mount (which must stay last, because it serves "/").
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.voice import router as voice_router

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Bicycle Assembly Voice ERP")

app.include_router(voice_router)

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
