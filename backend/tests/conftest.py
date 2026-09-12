import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Every test gets its own media dir and a clean set of DB tables.

    Note: app.db creates its SQLAlchemy `engine` once at first import, so
    changing DATABASE_URL after that point does NOT move later tests to a
    new file — the engine is a process-wide singleton, same as it would be
    in the running app. So instead of relying on the env var to isolate
    tests, we explicitly drop and recreate every table before each test.
    """
    db_path = tmp_path / "test.db"
    media_dir = tmp_path / "media"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("VISION_ADAPTER", "mock")

    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import Base, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    # Imported after env vars are set so config/db pick up the test settings.
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
