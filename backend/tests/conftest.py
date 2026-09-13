"""Test isolation.

Ordering matters and is easy to get wrong here. `app.db` builds its
SQLAlchemy engine at import time from `get_settings()`, and several test
modules do `from app.db import SessionLocal` at module level — which pytest
executes during *collection*, before any fixture runs. A fixture that sets
DATABASE_URL and then calls `Base.metadata.drop_all(bind=engine)` is
therefore dropping tables on whatever database `.env` happened to name.
With `backend/.env` present that is `./kamion.db`: the real demo database.

Two defences, in this order:

1. This module sets the test environment at *import* time. pytest imports
   the rootdir conftest before collecting test modules, so `app.db` binds
   to the temporary database no matter which module imports it first.
2. `_guard_database_is_disposable` refuses to let the suite run at all if
   the engine still resolves somewhere else. A destructive fixture must
   fail loudly rather than quietly destroy real rows.
"""

import os
import tempfile
from pathlib import Path

import pytest

# --- 1. Set before ANY app import, including collection-time imports. ------
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="tirage-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_ROOT / 'test.db'}"
os.environ["MEDIA_DIR"] = str(_TEST_ROOT / "media")
os.environ["VISION_ADAPTER"] = "mock"
# Mock tests must never be able to spend a real API call.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["ANTHROPIC_WORKSPACE_ID"] = ""


def _resolved_sqlite_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    return Path(url[len(prefix) :]).resolve()


@pytest.fixture(autouse=True, scope="session")
def _guard_database_is_disposable():
    """Abort the run if the engine is not bound to a throwaway database.

    Without this, an import-order regression silently turns the whole
    suite into a destructive operation against real inspection data.
    """
    from app.db import engine

    url = str(engine.url)
    resolved = _resolved_sqlite_path(url)
    if resolved is None or _TEST_ROOT.resolve() not in resolved.parents:
        pytest.exit(
            "REFUSING TO RUN: tests would operate on a non-temporary database "
            f"({url!r}). Expected something under {_TEST_ROOT}. "
            "app.db was probably imported before this conftest set DATABASE_URL.",
            returncode=3,
        )
    yield


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Per-test media dir and a clean schema on the temporary database."""
    media_dir = tmp_path / "media"
    monkeypatch.setenv("MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("VISION_ADAPTER", "mock")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import Base, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # The analysis rate limiter is process-wide state; one test's uploads
    # must not throttle the next test.
    from app.routers import media as media_router

    media_router._analysis_times.clear()

    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
