import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    DateTime, Text, text, ForeignKey
)
from sqlalchemy.orm import DeclarativeBase, Session

DB_PATH = os.environ.get("DB_PATH", "/app/data/monitor.db")
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)


class Base(DeclarativeBase):
    pass


class MetricsHistory(Base):
    __tablename__ = "metrics_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    cpu_percent = Column(Float)
    load_1m = Column(Float)
    load_5m = Column(Float)
    load_15m = Column(Float)
    ram_total_mb = Column(Float)
    ram_used_mb = Column(Float)
    ram_percent = Column(Float)
    disk_used_gb = Column(Float)
    disk_total_gb = Column(Float)
    disk_percent = Column(Float)
    net_rx_bytes_s = Column(Integer)
    net_tx_bytes_s = Column(Integer)
    temperature_c = Column(Float)


class ContainerMetrics(Base):
    __tablename__ = "container_metrics"
    id = Column(Integer, primary_key=True, autoincrement=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    container_id = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    cpu_percent = Column(Float)
    mem_used_mb = Column(Float)
    mem_limit_mb = Column(Float)
    mem_percent = Column(Float)
    net_rx_mb = Column(Float)
    net_tx_mb = Column(Float)
    status = Column(String)
    restart_count = Column(Integer)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    metrica = Column(String, nullable=False)
    operador = Column(String, nullable=False)
    threshold = Column(Float, nullable=False)
    duracao_minutos = Column(Integer, default=5)
    severidade = Column(String, nullable=False)
    canal_email = Column(Integer, default=1)
    canal_whatsapp = Column(Integer, default=1)
    cooldown_minutos = Column(Integer, default=30)
    ativo = Column(Integer, default=1)
    criado_em = Column(DateTime, default=datetime.utcnow)


class AlertLog(Base):
    __tablename__ = "alert_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("alert_rules.id"))
    triggered_at = Column(DateTime, nullable=False)
    resolved_at = Column(DateTime)
    severidade = Column(String)
    metrica = Column(String)
    valor_no_disparo = Column(Float)
    threshold = Column(Float)
    mensagem = Column(Text)
    notificado_email = Column(Integer, default=0)
    notificado_whatsapp = Column(Integer, default=0)
    erro_email = Column(Text)
    erro_whatsapp = Column(Text)
    last_notified_at = Column(DateTime, nullable=True)


class Config(Base):
    __tablename__ = "config"
    key = Column(String, primary_key=True)
    value = Column(Text)


_DEFAULT_RULES = [
    {"nome": "CPU Alta", "metrica": "cpu_percent", "operador": ">", "threshold": 80, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "CPU Crítica", "metrica": "cpu_percent", "operador": ">", "threshold": 95, "duracao_minutos": 2, "severidade": "critico", "cooldown_minutos": 15},
    {"nome": "RAM Alta", "metrica": "ram_percent", "operador": ">", "threshold": 85, "duracao_minutos": 3, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "RAM Crítica", "metrica": "ram_percent", "operador": ">", "threshold": 95, "duracao_minutos": 1, "severidade": "critico", "cooldown_minutos": 15},
    {"nome": "Disco Alto", "metrica": "disk_percent", "operador": ">", "threshold": 80, "duracao_minutos": 0, "severidade": "aviso", "cooldown_minutos": 120},
    {"nome": "Disco Crítico", "metrica": "disk_percent", "operador": ">", "threshold": 90, "duracao_minutos": 0, "severidade": "critico", "cooldown_minutos": 60},
    {"nome": "Temperatura Alta", "metrica": "temperature_c", "operador": ">", "threshold": 75, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "Load Alto", "metrica": "load_1m", "operador": ">", "threshold": 6.0, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "Container Parado", "metrica": "container_stopped", "operador": "==", "threshold": 1, "duracao_minutos": 0, "severidade": "critico", "cooldown_minutos": 0},
]

_DEFAULT_CONFIG = {
    "server_name": "VPS Principal",
    "timezone": "America/Sao_Paulo",
    "public_url": "",
    "smtp_enabled": "0",
    "whatsapp_enabled": "0",
    "require_auth": "1",
    "retention_detailed_days": "7",
    "retention_aggregated_days": "30",
}


def init_db():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.commit()
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE alert_log ADD COLUMN last_notified_at DATETIME"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
    with Session(engine) as session:
        if session.query(AlertRule).count() == 0:
            for rule in _DEFAULT_RULES:
                session.add(AlertRule(**rule))
        for key, value in _DEFAULT_CONFIG.items():
            if not session.get(Config, key):
                session.add(Config(key=key, value=value))
        session.commit()


def get_session():
    with Session(engine) as session:
        yield session