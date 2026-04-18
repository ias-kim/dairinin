from fastapi import APIRouter, Depends

from dependencies import get_log_store, verify_api_key
from services import email_service

router = APIRouter(prefix="/api/emails", dependencies=[Depends(verify_api_key)])


@router.get("")
def list_emails(limit: int = 50, offset: int = 0, log=Depends(get_log_store)):
    return email_service.list_emails(log, limit, offset)
