import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    DateTime, Text, text, ForeignKey, Index
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
    swap_used_mb = Column(Float)
    swap_percent = Column(Float)
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


class ContainerDiskUsage(Base):
    __tablename__ = "container_disk_usage"
    id = Column(Integer, primary_key=True, autoincrement=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    container_id = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    size_rw_mb = Column(Float)
    size_rootfs_mb = Column(Float)


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
    vps_name = Column(String, nullable=True)
    contexto = Column(Text, nullable=True)
    notificado_email = Column(Integer, default=0)
    notificado_whatsapp = Column(Integer, default=0)
    erro_email = Column(Text)
    erro_whatsapp = Column(Text)
    last_notified_at = Column(DateTime, nullable=True)


class AlertNotification(Base):
    __tablename__ = "alert_notification"
    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_log_id = Column(Integer, ForeignKey("alert_log.id"), nullable=False)
    canal = Column(String, nullable=False)       # "email" | "whatsapp"
    tipo = Column(String, nullable=False)        # "disparo" | "resolucao"
    status = Column(String, nullable=False)      # "enviado" | "falhou" | "desabilitado"
    erro = Column(Text, nullable=True)
    tentativa_em = Column(DateTime, nullable=False, default=datetime.utcnow)


Index("ix_alert_notification_alert_log_id", AlertNotification.alert_log_id)


class ContainerActionLog(Base):
    __tablename__ = "container_action_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    username = Column(String, nullable=False)
    container_id = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    acao = Column(String, nullable=False)
    sucesso = Column(Integer, default=1)
    erro = Column(Text, nullable=True)


class Fail2banActionLog(Base):
    __tablename__ = "fail2ban_action_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    username = Column(String, nullable=False)
    jail_nome = Column(String, nullable=False)
    acao = Column(String, nullable=False)
    detalhes = Column(Text, nullable=True)
    sucesso = Column(Integer, default=1)
    erro = Column(Text, nullable=True)


class TraefikActionLog(Base):
    __tablename__ = "traefik_action_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    username = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    acao = Column(String, nullable=False)
    sucesso = Column(Integer, default=1)
    erro = Column(Text, nullable=True)


class BackupSchedule(Base):
    __tablename__ = "backup_schedule"
    projeto = Column(String, primary_key=True)
    frequencia = Column(String, nullable=False, default="off")
    hora = Column(Integer, nullable=False, default=3)


class BackupJob(Base):
    __tablename__ = "backup_job"
    id = Column(Integer, primary_key=True, autoincrement=True)
    projeto = Column(String, nullable=False)
    tipo = Column(String, nullable=False)
    arquivo = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)
    concluido_em = Column(DateTime, nullable=True)
    erro = Column(Text, nullable=True)
    username = Column(String, nullable=False)


class AccessLog(Base):
    __tablename__ = "access_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    accessed_at = Column(DateTime, nullable=False)
    ip = Column(String, nullable=False)
    sistema = Column(String, nullable=False)
    path = Column(String, nullable=False)
    method = Column(String, nullable=False)
    status_code = Column(Integer)
    user_agent = Column(Text, nullable=True)


Index("ix_access_log_accessed_at", AccessLog.accessed_at)
Index("ix_access_log_ip", AccessLog.ip)


class AccessLogDaily(Base):
    __tablename__ = "access_log_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    day = Column(String, nullable=False)
    ip = Column(String, nullable=False)
    sistema = Column(String, nullable=False)
    count = Column(Integer, nullable=False, default=0)


Index("ix_access_log_daily_day", AccessLogDaily.day)
Index("ix_access_log_daily_ip", AccessLogDaily.ip)


class AccessLogHourly(Base):
    __tablename__ = "access_log_hourly"
    id = Column(Integer, primary_key=True, autoincrement=True)
    hour = Column(String, nullable=False)      # "YYYY-MM-DD HH", UTC
    sistema = Column(String, nullable=False)
    count = Column(Integer, nullable=False, default=0)


Index("ix_access_log_hourly_hour", AccessLogHourly.hour)
Index("ix_access_log_hourly_sistema", AccessLogHourly.sistema)


class IpGeoCache(Base):
    __tablename__ = "ip_geo_cache"
    ip = Column(String, primary_key=True)
    country = Column(String, nullable=True)
    region = Column(String, nullable=True)
    city = Column(String, nullable=True)
    isp = Column(String, nullable=True)
    org = Column(String, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    is_private = Column(Integer, default=0)
    looked_up_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Config(Base):
    __tablename__ = "config"
    key = Column(String, primary_key=True)
    value = Column(Text)


_DEFAULT_RULES = [
    {"nome": "CPU Alta", "metrica": "cpu_percent", "operador": ">", "threshold": 80, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "CPU Crítica", "metrica": "cpu_percent", "operador": ">", "threshold": 95, "duracao_minutos": 2, "severidade": "critico", "cooldown_minutos": 15},
    {"nome": "RAM Alta", "metrica": "ram_percent", "operador": ">", "threshold": 85, "duracao_minutos": 3, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "RAM Crítica", "metrica": "ram_percent", "operador": ">", "threshold": 95, "duracao_minutos": 1, "severidade": "critico", "cooldown_minutos": 15},
    {"nome": "Swap Alto", "metrica": "swap_percent", "operador": ">", "threshold": 70, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "Swap Crítico", "metrica": "swap_percent", "operador": ">", "threshold": 90, "duracao_minutos": 2, "severidade": "critico", "cooldown_minutos": 15},
    {"nome": "Disco Alto", "metrica": "disk_percent", "operador": ">", "threshold": 80, "duracao_minutos": 0, "severidade": "aviso", "cooldown_minutos": 120},
    {"nome": "Disco Crítico", "metrica": "disk_percent", "operador": ">", "threshold": 90, "duracao_minutos": 0, "severidade": "critico", "cooldown_minutos": 60},
    {"nome": "Temperatura Alta", "metrica": "temperature_c", "operador": ">", "threshold": 75, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "Load Alto", "metrica": "load_1m", "operador": ">", "threshold": 6.0, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "Container Parado", "metrica": "container_stopped", "operador": "==", "threshold": 1, "duracao_minutos": 0, "severidade": "critico", "cooldown_minutos": 0},
    {"nome": "Espaço em Disco Reaproveitável", "metrica": "docker_reclaimable_mb", "operador": ">", "threshold": 500, "duracao_minutos": 0, "severidade": "aviso", "cooldown_minutos": 1440},
]

_DEFAULT_CONFIG = {
    "server_name": "VPS Monitor",
    "timezone": "America/Sao_Paulo",
    "public_url": "",
    "smtp_enabled": "0",
    "evolution_enabled": "0",
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
        try:
            conn.execute(text("ALTER TABLE alert_log ADD COLUMN vps_name VARCHAR"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
        try:
            conn.execute(text("ALTER TABLE alert_log ADD COLUMN contexto TEXT"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
        try:
            conn.execute(text("ALTER TABLE metrics_history ADD COLUMN swap_used_mb FLOAT"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
        try:
            conn.execute(text("ALTER TABLE metrics_history ADD COLUMN swap_percent FLOAT"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
    with Session(engine) as session:
        if session.query(AlertRule).count() == 0:
            for rule in _DEFAULT_RULES:
                session.add(AlertRule(**rule))
        if not session.query(AlertRule).filter_by(nome="Espaço em Disco Reaproveitável").first():
            session.add(AlertRule(
                nome="Espaço em Disco Reaproveitável", metrica="docker_reclaimable_mb",
                operador=">", threshold=500, duracao_minutos=0,
                severidade="aviso", cooldown_minutos=1440,
            ))
        if not session.query(AlertRule).filter_by(nome="Swap Alto").first():
            session.add(AlertRule(
                nome="Swap Alto", metrica="swap_percent", operador=">", threshold=70,
                duracao_minutos=5, severidade="aviso", cooldown_minutos=30,
            ))
        if not session.query(AlertRule).filter_by(nome="Swap Crítico").first():
            session.add(AlertRule(
                nome="Swap Crítico", metrica="swap_percent", operador=">", threshold=90,
                duracao_minutos=2, severidade="critico", cooldown_minutos=15,
            ))
        for key, value in _DEFAULT_CONFIG.items():
            if not session.get(Config, key):
                session.add(Config(key=key, value=value))
        session.commit()

        server_name_row = session.get(Config, "server_name")
        server_name = server_name_row.value if server_name_row else _DEFAULT_CONFIG["server_name"]
        session.query(AlertLog).filter(AlertLog.vps_name.is_(None)).update(
            {AlertLog.vps_name: server_name}, synchronize_session=False
        )
        session.commit()


def get_session():
    with Session(engine) as session:
        yield session