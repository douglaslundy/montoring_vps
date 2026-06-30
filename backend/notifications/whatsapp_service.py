import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_config(session, key: str, default: str = "") -> str:
    """Lazy proxy — defers api.config import; replaceable in tests via patch."""
    from api.config import get_config as _real
    return _real(session, key, default)


def _headers(api_key: str) -> dict:
    return {"apikey": api_key, "Content-Type": "application/json"}


def _base(url: str) -> str:
    return url.rstrip("/")


def get_status(evolution_url: str, api_key: str, instance: str) -> str:
    """Retorna: 'connected' | 'disconnected' | 'no_instance' | 'error'"""
    try:
        r = httpx.get(
            f"{_base(evolution_url)}/instance/connectionState/{instance}",
            headers=_headers(api_key), timeout=5,
        )
        if r.status_code == 404:
            return "no_instance"
        data = r.json()
        state = data.get("instance", {}).get("state", "")
        return "connected" if state == "open" else "disconnected"
    except Exception:
        return "error"


def get_or_create_qr(evolution_url: str, api_key: str, instance: str) -> str:
    """Garante que instância existe e retorna QR code base64."""
    # Verifica se instância existe
    r = httpx.get(
        f"{_base(evolution_url)}/instance/fetchInstances",
        headers=_headers(api_key), timeout=5,
    )
    instances = r.json() if r.status_code == 200 else []
    names = [i.get("instance", {}).get("instanceName", "") for i in instances]

    if instance not in names:
        httpx.post(
            f"{_base(evolution_url)}/instance/create",
            headers=_headers(api_key),
            json={"instanceName": instance, "qrcode": True},
            timeout=10,
        )

    # Obtém QR
    r = httpx.get(
        f"{_base(evolution_url)}/instance/connect/{instance}",
        headers=_headers(api_key), timeout=10,
    )
    data = r.json()
    return data.get("base64", data.get("qrcode", {}).get("base64", ""))


def disconnect(evolution_url: str, api_key: str, instance: str) -> None:
    httpx.delete(
        f"{_base(evolution_url)}/instance/logout/{instance}",
        headers=_headers(api_key), timeout=5,
    )


def delete_instance(evolution_url: str, api_key: str, instance: str) -> None:
    httpx.delete(
        f"{_base(evolution_url)}/instance/delete/{instance}",
        headers=_headers(api_key), timeout=5,
    )


def _send_text(evolution_url: str, api_key: str, instance: str, number: str, text: str) -> None:
    httpx.post(
        f"{_base(evolution_url)}/message/sendText/{instance}",
        headers=_headers(api_key),
        json={"number": number, "text": text},
        timeout=10,
    )


def _format_alert(alert: dict, server_name: str, public_url: str) -> str:
    sev = alert.get("severidade", "aviso")
    sev_icon = "🔴" if sev == "critico" else "⚠️"
    triggered_at = alert.get("triggered_at", "")
    if triggered_at:
        try:
            dt = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
            triggered_at = dt.strftime("%H:%M:%S (%d/%m/%Y)")
        except Exception:
            pass
    lines = [
        "🚨 *ALERTA VPS MONITOR*",
        f"Severidade: {sev_icon} {sev.upper()}",
        "",
        f"📊 *Métrica:* {alert.get('metrica', '')}",
        f"📋 *Mensagem:* {alert.get('mensagem', '')}",
    ]
    if alert.get("valor_no_disparo") is not None:
        lines.append(f"📈 *Valor atual:* {alert['valor_no_disparo']:.1f}")
    lines += [
        f"🕐 *Horário:* {triggered_at}",
        "",
        f"🖥️ Servidor: {server_name}",
    ]
    if public_url:
        lines.append(f"🌐 Acesse o painel: {public_url}")
    lines.append("\n_Alerta gerado automaticamente pelo VPS Monitor_")
    return "\n".join(lines)


def send_alert(alert: dict, session: Session) -> None:
    url = get_config(session, "evolution_url")
    api_key = get_config(session, "evolution_api_key")
    instance = get_config(session, "evolution_instance", "vps-monitor")
    recipients_raw = get_config(session, "evolution_recipients")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    server_name = get_config(session, "server_name", "VPS Monitor")
    public_url = get_config(session, "public_url", "")

    if not url or not recipients:
        raise ValueError("WhatsApp não configurado")

    text = _format_alert(alert, server_name, public_url)
    for number in recipients:
        _send_text(url, api_key, instance, number, text)


def send_resolution(alert: dict, session: Session) -> None:
    url = get_config(session, "evolution_url")
    api_key = get_config(session, "evolution_api_key")
    instance = get_config(session, "evolution_instance", "vps-monitor")
    recipients_raw = get_config(session, "evolution_recipients")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    server_name = get_config(session, "server_name", "VPS Monitor")

    if not url or not recipients:
        return

    resolved_at = alert.get("resolved_at", "")
    if resolved_at:
        try:
            dt = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
            resolved_at = dt.strftime("%H:%M:%S (%d/%m/%Y)")
        except Exception:
            pass

    text = (
        f"✅ *ALERTA RESOLVIDO — VPS Monitor*\n\n"
        f"📋 {alert.get('mensagem', '')}\n"
        f"🕐 Resolvido em: {resolved_at}\n"
        f"🖥️ Servidor: {server_name}\n\n"
        "_Notificação automática do VPS Monitor_"
    )
    for number in recipients:
        _send_text(url, api_key, instance, number, text)
