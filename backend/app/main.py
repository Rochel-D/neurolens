"""NeuroLens — FastAPI entry point.

Run:  uvicorn backend.app.main:app --reload --port 8000   (from the project root)
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal, init_db
from .models import User
from .routers import admin, auth, chat, memory_routes, uploads
from .security import hash_password
from .services.model_router import get_router

log = logging.getLogger("neurolens")
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def seed_admin() -> None:
    s = get_settings()
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.email == s.admin_email.lower())):
            db.add(User(name="Administrator", email=s.admin_email.lower(), password_hash=hash_password(s.admin_password), role="admin"))
            db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_admin()
    with SessionLocal() as db:
        admin.apply_saved_settings(db)
    yield


settings = get_settings()
app = FastAPI(
    title="NeuroLens API",
    version="2.1.0",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; media-src 'self' blob:"
    )
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errs = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errs[0]["message"] if errs else "Invalid input", "errors": errs})


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception):
    log.exception("Unhandled error", exc_info=exc)  # full trace only in server logs
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


for r in (auth.router, uploads.router, chat.router, memory_routes.router, admin.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.1.0", "llm_keys": len(get_router().slots), "mcp": settings.use_mcp}


@app.get("/api/config")
def public_config():
    """Non-secret settings the interface needs (languages, which optional features are on)."""
    from .services import mri as mri_service
    from .services.translate import LANGS

    return {"version": "2.1.0", "languages": LANGS, "llm": bool(get_router().slots),
            "mri_model": mri_service.model_status()["loaded"], "ocr": settings.ocr_engines}


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
