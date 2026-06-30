import smtplib
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SEV_COLOR = {"critico": "#e53935", "aviso": "#fb8c00", "info": "#1e88e5"}
_SEV_LABEL = {"critico": "🔴 CRÍTICO", "aviso": "⚠️ AVISO", "info": "ℹ️ INFO"}


def get_config(session, key: str, default: str = "") -> str:
    """Lazy proxy — defers api.config import; replaceable in tests via patch."""
    from api.config import get_config as _real
    return _real(session, key, default)


def _html_template(title: str, header_color: str, body_rows: list[tuple[str, str]], cta_url: str, server_name: str) -> str:
    rows_html = "".join(
        f"<tr><td style='padding:6px 12px;color:#6b7a99;width:140px'>{k}</td>"
        f"<td style='padding:6px 12px;color:#e8eaf0'>{v}</td></tr>"
        for k, v in body_rows
    )
    return f"""<!DOCTYPE html>
<html><body style='margin:0;padding:0;background:#0f1117;font-family:sans-serif'>
<div style='max-width:560px;margin:32px auto;background:#161b27;border-radius:8px;overflow:hidden'>
  <div style='background:{header_color};padding:20px 24px'>
    <h2 style='margin:0;color:#fff;font-size:18px'>{title}</h2>
    <p style='margin:4px 0 0;color:rgba(255,255,255,0.8);font-size:13px'>{server_name}</p>
  </div>
  <table style='width:100%;border-collapse:collapse;margin:16px 0'>{rows_html}</table>
  <div style='padding:0 24px 24px'>
    <a href='{cta_url}' style='display:inline-block;padding:10px 20px;background:#f5a623;color:#000;border-radius:6px;text-decoration:none;font-weight:700;font-size:14px'>Acessar Painel</a>
  </div>
  <div style='border-top:1px solid #2a3347;padding:12px 24px;color:#6b7a99;font-size:11px'>
    Alerta gerado automaticamente pelo VPS Monitor
  </div>
</div>
</body></html>"""


def _smtp_send(html: str, subject: str, recipients: list[str], session) -> None:
    host = get_config(session, "smtp_host")
    port = int(get_config(session, "smtp_port", "587"))
    user = get_config(session, "smtp_user")
    password = get_config(session, "smtp_password")
    tls_mode = get_config(session, "smtp_tls", "starttls")
    from_email = get_config(session, "smtp_from_email") or user
    from_name = get_config(session, "smtp_from_name", "VPS Monitor")

    if not host or not recipients:
        raise ValueError("SMTP não configurado")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    if tls_mode == "ssl":
        with smtplib.SMTP_SSL(host, port) as smtp:
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_email, recipients, msg.as_bytes())
    else:
        with smtplib.SMTP(host, port) as smtp:
            if tls_mode == "starttls":
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_email, recipients, msg.as_bytes())


def send_alert(alert: dict, session: Session) -> None:
    recipients_raw = get_config(session, "smtp_recipients")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    server_name = get_config(session, "server_name", "VPS Monitor")
    public_url = get_config(session, "public_url", "")
    severidade = alert.get("severidade", "aviso")

    triggered_at = alert.get("triggered_at", "")
    if triggered_at:
        try:
            dt = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
            triggered_at = dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            pass

    rows = [
        ("Severidade", _SEV_LABEL.get(severidade, severidade)),
        ("Métrica", alert.get("metrica", "")),
        ("Mensagem", alert.get("mensagem", "")),
        ("Horário", triggered_at),
    ]
    if alert.get("valor_no_disparo") is not None:
        rows.insert(2, ("Valor atual", f"{alert['valor_no_disparo']:.1f}"))
    if alert.get("threshold") is not None:
        rows.insert(3, ("Threshold", f"{alert['threshold']:.1f}"))

    html = _html_template(
        title=f"🚨 Alerta VPS Monitor — {_SEV_LABEL.get(severidade, severidade)}",
        header_color=_SEV_COLOR.get(severidade, "#fb8c00"),
        body_rows=rows,
        cta_url=public_url,
        server_name=server_name,
    )
    subject = f"[VPS Monitor] {_SEV_LABEL.get(severidade, severidade)} — {alert.get('mensagem', 'Alerta')}"
    _smtp_send(html, subject, recipients, session)


def send_resolution(alert: dict, session: Session) -> None:
    recipients_raw = get_config(session, "smtp_recipients")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    server_name = get_config(session, "server_name", "VPS Monitor")
    public_url = get_config(session, "public_url", "")

    resolved_at = alert.get("resolved_at", "")
    if resolved_at:
        try:
            dt = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
            resolved_at = dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            pass

    rows = [
        ("Métrica", alert.get("metrica", "")),
        ("Mensagem", alert.get("mensagem", "")),
        ("Resolvido em", resolved_at),
    ]
    html = _html_template(
        title="✅ Alerta Resolvido — VPS Monitor",
        header_color="#43a047",
        body_rows=rows,
        cta_url=public_url,
        server_name=server_name,
    )
    subject = f"[VPS Monitor] ✅ Resolvido — {alert.get('mensagem', 'Alerta')}"
    _smtp_send(html, subject, recipients, session)
