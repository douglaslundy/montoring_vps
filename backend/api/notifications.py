from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from models.database import get_session

router = APIRouter(prefix="/api/notifications", dependencies=[Depends(verify_token_header)])


@router.post("/test/email")
def test_email(session: Session = Depends(get_session)):
    try:
        from notifications.email_service import send_alert
        send_alert({
            "severidade": "aviso",
            "metrica": "test",
            "mensagem": "E-mail de teste do VPS Monitor",
            "triggered_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "valor_no_disparo": 0,
            "threshold": 0,
        }, session)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
