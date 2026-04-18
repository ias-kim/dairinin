from fastapi import APIRouter, Depends

from dependencies import get_hitl_dep, verify_api_key
from services import hitl_service

router = APIRouter(prefix="/api/hitl", dependencies=[Depends(verify_api_key)])


@router.get("")
def list_pending(hitl=Depends(get_hitl_dep)):
    return hitl_service.list_pending(hitl)


@router.post("/{slack_ts}/approve")
def approve(slack_ts: str, hitl=Depends(get_hitl_dep)):
    from app import _resume_hitl
    return hitl_service.approve(hitl, slack_ts, _resume_hitl)


@router.post("/{slack_ts}/reject")
def reject(slack_ts: str, hitl=Depends(get_hitl_dep)):
    from app import _resume_hitl
    return hitl_service.reject(hitl, slack_ts, _resume_hitl)
