import pytest
import os
from sqlalchemy import text

pytest_plugins = ['pytest_asyncio']

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")

@pytest.fixture
def test_db(db_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", db_path)
    import importlib
    import models.database as db_module
    importlib.reload(db_module)
    db_module.init_db()
    return db_module