from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from models.database import get_session

router = APIRouter(prefix="/api/notifications", dependencies=[Depends(verify_token_header)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.post("/test/email")
def test_email(session: Session = Depends(get_session)):
    try:
        from notifications.email_service import send_alert
        send_alert({
            "severidade": "aviso",
            "metrica": "test",
            "mensagem": "E-mail de teste do VPS Monitor",
            "triggered_at": _now_iso(),
            "valor_no_disparo": 0,
            "threshold": 0,
        }, session)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/test/whatsapp")
def test_whatsapp(session: Session = Depends(get_session)):
    try:
        from notifications.whatsapp_service import send_alert
        send_alert({
            "severidade": "aviso",
            "metrica": "test",
            "mensagem": "Mensagem de teste do VPS Monitor",
            "triggered_at": _now_iso(),
        }, session)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
