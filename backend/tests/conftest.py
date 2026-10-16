import os
os.environ["STORE"] = "memory"
os.environ["FAKE_AI"] = "1"
os.environ["CLIMATE_LIVE"] = "0"
import pytest


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    from app import store as s
    s._store = None
    os.environ.pop("FAKE_AI_CORRUPT", None)
    from app import main
    main._hits.clear()
    yield
