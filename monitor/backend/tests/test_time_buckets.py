from datetime import datetime, timedelta

from api.time_buckets import daily_buckets, hourly_buckets


def test_hourly_buckets_sem_dia_retorna_ultimas_12h():
    buckets = hourly_buckets()
    assert len(buckets) == 12
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    assert buckets[-1] == now
    assert buckets[0] == now - timedelta(hours=11)


def test_hourly_buckets_dia_passado_retorna_24h():
    dia = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d")
    buckets = hourly_buckets(dia)
    assert len(buckets) == 24
    assert buckets[0].strftime("%Y-%m-%d %H") == f"{dia} 00"
    assert buckets[-1].strftime("%Y-%m-%d %H") == f"{dia} 23"


def test_hourly_buckets_dia_de_hoje_vai_so_ate_hora_atual():
    hoje = datetime.utcnow().strftime("%Y-%m-%d")
    hora_atual = datetime.utcnow().hour
    buckets = hourly_buckets(hoje)
    assert len(buckets) == hora_atual + 1
    assert buckets[-1].hour == hora_atual


def test_daily_buckets_mes_passado_retorna_todos_os_dias():
    now = datetime.utcnow()
    ultimo_dia_mes_anterior = now.replace(day=1) - timedelta(days=1)
    mes_anterior = ultimo_dia_mes_anterior.strftime("%Y-%m")
    buckets = daily_buckets(mes_anterior)
    assert len(buckets) == ultimo_dia_mes_anterior.day
    assert buckets[0] == f"{mes_anterior}-01"
    assert buckets[-1] == ultimo_dia_mes_anterior.strftime("%Y-%m-%d")


def test_daily_buckets_mes_atual_vai_so_ate_hoje():
    now = datetime.utcnow()
    buckets = daily_buckets(now.strftime("%Y-%m"))
    assert len(buckets) == now.day
    assert buckets[-1] == now.strftime("%Y-%m-%d")


def test_daily_buckets_sem_mes_usa_mes_atual():
    assert daily_buckets() == daily_buckets(datetime.utcnow().strftime("%Y-%m"))
