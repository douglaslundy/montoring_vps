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

    import os
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-32-chars-minimum!!")

    from main import app
    return TestClient(app)


def get_token(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin"})
    return r.json()["token"]


def auth(client):
    return {"Authorization": f"Bearer {get_token(client)}"}


def test_list_rules_requires_auth(client):
    r = client.get("/api/alerts/rules")
    assert r.status_code == 401


def test_list_rules_returns_defaults(client):
    r = client.get("/api/alerts/rules", headers=auth(client))
    assert r.status_code == 200
    assert len(r.json()) == 9


def test_create_and_delete_rule(client):
    h = auth(client)
    r = client.post("/api/alerts/rules", json={
        "nome": "Test", "metrica": "cpu_percent", "operador": ">",
        "threshold": 90.0, "severidade": "aviso"
    }, headers=h)
    assert r.status_code == 201
    rule_id = r.json()["id"]

    r2 = client.delete(f"/api/alerts/rules/{rule_id}", headers=h)
    assert r2.status_code == 200


def test_toggle_rule(client):
    h = auth(client)
    rules = client.get("/api/alerts/rules", headers=h).json()
    rid = rules[0]["id"]
    r = client.post(f"/api/alerts/rules/{rid}/toggle", headers=h)
    assert r.status_code == 200
    assert r.json()["ativo"] == 0


def test_active_alerts_empty_initially(client):
    r = client.get("/api/alerts/active", headers=auth(client))
    assert r.status_code == 200
    assert r.json() == []


def test_history_requires_auth(client):
    r = client.get("/api/alerts/history")
    assert r.status_code == 401


def test_update_rule(client):
    h = auth(client)
    rules = client.get("/api/alerts/rules", headers=h).json()
    rid = rules[0]["id"]
    r = client.put(f"/api/alerts/rules/{rid}", json={
        "nome": "CPU Alterado", "metrica": "cpu_percent", "operador": ">",
        "threshold": 99.0, "severidade": "critico",
    }, headers=h)
    assert r.status_code == 200
    updated = client.get("/api/alerts/rules", headers=h).json()
    rule = next(x for x in updated if x["id"] == rid)
    assert rule["threshold"] == 99.0
