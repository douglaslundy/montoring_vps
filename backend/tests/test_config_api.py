import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import models.database as db_module
    from sqlalchemy import create_engine
    test_engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    db_module.Base.metadata.create_all(test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    db_module.init_db()
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-32-chars-minimum!!")
    from main import app
    return TestClient(app)


def get_token(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin"})
    return r.json()["token"]


def auth(client):
    return {"Authorization": f"Bearer {get_token(client)}"}


def test_get_config_requires_auth(client):
    r = client.get("/api/config")
    assert r.status_code == 401


def test_get_config_returns_defaults(client):
    r = client.get("/api/config", headers=auth(client))
    assert r.status_code == 200
    data = r.json()
    assert "smtp_host" in data
    assert "server_name" in data


def test_put_config_saves_plain_value(client):
    h = auth(client)
    client.put("/api/config", json={"server_name": "Meu Servidor"}, headers=h)
    r = client.get("/api/config", headers=h)
    assert r.json()["server_name"] == "Meu Servidor"


def test_sensitive_field_is_masked_on_read(client):
    h = auth(client)
    client.put("/api/config", json={"smtp_password": "senha_secreta_longa"}, headers=h)
    r = client.get("/api/config", headers=h)
    val = r.json()["smtp_password"]
    assert val.startswith("****")
    assert not val.endswith("senha_secreta_longa")


def test_mask_not_overwritten(client):
    """Enviar valor mascarado não deve sobrescrever o valor original."""
    h = auth(client)
    client.put("/api/config", json={"smtp_password": "senha_original_1234"}, headers=h)
    client.put("/api/config", json={"smtp_password": "****...1234"}, headers=h)
    # lê internamente via get_config para ver o valor real
    from notifications.encryption import decrypt
    import models.database as db
    from sqlalchemy.orm import Session
    with Session(db.engine) as s:
        row = s.get(db.Config, "smtp_password")
        assert decrypt(row.value) == "senha_original_1234"
