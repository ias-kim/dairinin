"""
이메일 로그 관련 비즈니스 로직.

FastAPI를 전혀 모름 — Request, HTTPException 없음. 순수 Python.
"""

from db.email_log import EmailLogStore
from db.hitl_store import HitlStore


def list_emails(log: EmailLogStore, limit: int, offset: int) -> dict:
    return {"emails": log.list_logs(limit=limit, offset=offset)}


def get_stats(log: EmailLogStore, hitl: HitlStore) -> dict:
    stats = log.get_stats()
    stats["hitl_pending"] = len(hitl.list_pending())
    return stats
