from fastapi import APIRouter, Depends

from dependencies import get_hitl_dep, get_log_store, verify_api_key
from services import email_service

router = APIRouter(prefix="/api/stats", dependencies=[Depends(verify_api_key)])


@router.get("")
def get_stats(log=Depends(get_log_store), hitl=Depends(get_hitl_dep)):
    return email_service.get_stats(log, hitl)
