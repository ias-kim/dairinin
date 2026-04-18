"""
FastAPI 공통 의존성 — Depends()로 주입되는 함수 모음.

Spring의 Filter/Interceptor + @Autowired 역할을 여기서 담당.
라우터는 이 파일의 함수를 Depends()로 선언만 하면 됨.
"""

import os

from fastapi import HTTPException, Request

from db.email_log import EmailLogStore, get_email_log_store
from db.hitl_store import HitlStore


def verify_api_key(request: Request) -> None:
    """API_KEY 환경변수가 있으면 Authorization 헤더 검증. 없으면 개발 모드로 스킵."""
    api_key = os.getenv("API_KEY", "")
    if not api_key:
        return
    if request.headers.get("Authorization") != f"Bearer {api_key}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_log_store() -> EmailLogStore:
    return get_email_log_store()


def get_hitl_dep() -> HitlStore:
    from agents.notifier import get_hitl_store
    return get_hitl_store()
