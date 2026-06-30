from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from api.config import get_config
from models.database import get_session
from notifications import whatsapp_service

router = APIRouter(prefix="/api/whatsapp", dependencies=[Depends(verify_token_header)])


def _cfg(session: Session):
    return (
        get_config(session, "evolution_url"),
        get_config(session, "evolution_api_key"),
        get_config(session, "evolution_instance", "vps-monitor"),
    )


@router.get("/status")
def status(session: Session = Depends(get_session)):
    url, key, instance = _cfg(session)
    if not url:
        return {"status": "not_configured", "detail": "Evolution URL não configurada"}
    result = whatsapp_service.get_status(url, key, instance)
    return result


@router.post("/connect")
def connect(session: Session = Depends(get_session)):
    url, key, instance = _cfg(session)
    if not url:
        return {"error": "Evolution URL não configurada"}
    try:
        qr = whatsapp_service.get_or_create_qr(url, key, instance)
        return {"qr": qr}
    except Exception as e:
        return {"error": str(e)}


@router.get("/qrcode")
def qrcode(session: Session = Depends(get_session)):
    url, key, instance = _cfg(session)
    if not url:
        return {"error": "Evolution URL não configurada"}
    try:
        qr = whatsapp_service.get_or_create_qr(url, key, instance)
        return {"qr": qr}
    except Exception as e:
        return {"error": str(e)}


@router.delete("/disconnect")
def disconnect(session: Session = Depends(get_session)):
    url, key, instance = _cfg(session)
    try:
        whatsapp_service.disconnect(url, key, instance)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.delete("/delete-instance")
def delete_instance(session: Session = Depends(get_session)):
    url, key, instance = _cfg(session)
    try:
        whatsapp_service.delete_instance(url, key, instance)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
