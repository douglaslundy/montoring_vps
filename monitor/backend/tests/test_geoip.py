import pytest


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


class _FakeAsyncClient:
    calls = 0

    def __init__(self, json_data=None, exc=None):
        self._json_data = json_data
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        _FakeAsyncClient.calls += 1
        if self._exc:
            raise self._exc
        return _FakeResponse(self._json_data)


@pytest.mark.asyncio
async def test_ip_privado_nao_chama_api_externa(test_db, monkeypatch):
    import collector.geoip as geoip
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(geoip.httpx, "AsyncClient", lambda timeout=5.0: _FakeAsyncClient())

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        result = await geoip.lookup_ip("192.168.1.10", session)

    assert result["is_private"] is True
    assert _FakeAsyncClient.calls == 0


@pytest.mark.asyncio
async def test_ip_publico_chama_api_e_grava_cache(test_db, monkeypatch):
    import collector.geoip as geoip
    fake_data = {
        "status": "success", "country": "Brazil", "regionName": "SP",
        "city": "São Paulo", "isp": "Provedor X", "org": "Org Y",
        "lat": -23.5, "lon": -46.6, "query": "203.0.113.10",
    }
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(geoip.httpx, "AsyncClient", lambda timeout=5.0: _FakeAsyncClient(json_data=fake_data))

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        result = await geoip.lookup_ip("203.0.113.10", session)

    assert result["is_private"] is False
    assert result["country"] == "Brazil"
    assert result["city"] == "São Paulo"
    assert _FakeAsyncClient.calls == 1

    with Session(test_db.engine) as session:
        cached = session.get(test_db.IpGeoCache, "203.0.113.10")
    assert cached is not None
    assert cached.isp == "Provedor X"


@pytest.mark.asyncio
async def test_segunda_chamada_usa_cache(test_db, monkeypatch):
    import collector.geoip as geoip
    fake_data = {"status": "success", "country": "Brazil", "query": "203.0.113.10"}
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(geoip.httpx, "AsyncClient", lambda timeout=5.0: _FakeAsyncClient(json_data=fake_data))

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        await geoip.lookup_ip("203.0.113.10", session)
    with Session(test_db.engine) as session:
        await geoip.lookup_ip("203.0.113.10", session)

    assert _FakeAsyncClient.calls == 1


@pytest.mark.asyncio
async def test_erro_api_nao_lanca_excecao(test_db, monkeypatch):
    import collector.geoip as geoip
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(
        geoip.httpx, "AsyncClient",
        lambda timeout=5.0: _FakeAsyncClient(exc=Exception("timeout")),
    )

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        result = await geoip.lookup_ip("203.0.113.10", session)

    assert result["is_private"] is False
    assert result["country"] is None

    with Session(test_db.engine) as session:
        cached = session.get(test_db.IpGeoCache, "203.0.113.10")
    assert cached is not None
