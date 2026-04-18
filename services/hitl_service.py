"""
HITL 관련 비즈니스 로직.

resume_fn을 파라미터로 받아 app.py의 _resume_hitl 순환 참조를 방지.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from fastapi import HTTPException

from db.hitl_store import HitlStore


def list_pending(hitl: HitlStore) -> dict:
    return {"pending": hitl.list_pending()}


def _trigger_resume(hitl: HitlStore, slack_ts: str, decision: str, resume_fn: Callable) -> dict:
    if not hitl.lookup_by_slack_ts(slack_ts):
        raise HTTPException(status_code=404, detail="HITL entry not found")
    asyncio.create_task(asyncio.to_thread(resume_fn, slack_ts, decision))
    return {"ok": True, "slack_ts": slack_ts, "decision": decision}


def approve(hitl: HitlStore, slack_ts: str, resume_fn: Callable) -> dict:
    return _trigger_resume(hitl, slack_ts, "approve", resume_fn)


def reject(hitl: HitlStore, slack_ts: str, resume_fn: Callable) -> dict:
    return _trigger_resume(hitl, slack_ts, "reject", resume_fn)
