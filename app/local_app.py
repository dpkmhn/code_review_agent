"""Serve the built UI and review API together for local use."""

from pathlib import Path

from fastapi.staticfiles import StaticFiles


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def mount_frontend(app, directory: Path):
    if not (directory / "index.html").is_file():
        raise RuntimeError("Build the local UI first: cd frontend and run npm ci && npm run build.")
    # Mount last so API, health, readiness, and docs routes keep their behavior.
    app.mount("/", StaticFiles(directory=directory, html=True), name="local-ui")


def create_app():
    from app.config import settings
    from app.main import app

    if settings.environment != "development":
        raise RuntimeError("The local UI launcher requires environment=development. Use deploy/ for production.")
    mount_frontend(app, FRONTEND_DIST)
    return app
