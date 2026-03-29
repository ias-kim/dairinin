"""
Notifier Agent — action에 따라 실제 효과를 실행.

auto_register: 캘린더 등록 + 패턴 저장 + 이메일 읽음 처리
hitl_required: 콘솔 로그 (Week 2-3: Slack + interrupt)
skip:          이메일 읽음 처리만
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta, timezone

from graph.state import ScheduleState
from mcp_servers.calendar_mcp import create_event_logic
from mcp_servers.gmail_mcp import mark_read_logic
from mcp_servers.memory_mcp import MemoryStore

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 싱글톤 — 앱 라이프사이클 동안 하나만
_memory_store: MemoryStore | None = None


def get_memory_store() -> MemoryStore:
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store


def notify_node(state: ScheduleState) -> dict:
    """LangGraph 노드: action에 따라 실행."""
    action = state.get("action", "skip")
    email_id = state.get("email_id", "")
    parsed = state.get("parsed_event")

    if action == "auto_register" and parsed:
        _handle_auto_register(state)
    elif action == "hitl_required":
        _handle_hitl(state)

    # skip이거나 처리 완료 후 → mark_read
    if email_id:
        try:
            from mcp_servers.gmail_mcp import build_gmail_service
            service = build_gmail_service()
            mark_read_logic(service, email_id)
        except Exception as e:
            logger.warning(f"mark_read skipped (no Gmail service): {e}")

    return {"notification": action}


def _handle_auto_register(state: ScheduleState):
    """캘린더 등록 + 패턴 저장."""
    parsed = state["parsed_event"]
    dt = parsed.event_datetime
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

    if dt:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)

        duration = parsed.duration or 60
        end_dt = dt + timedelta(minutes=duration)

        try:
            from mcp_servers.calendar_mcp import build_calendar_service
            service = build_calendar_service()
        except Exception:
            service = None

        result = create_event_logic(
            service=service,
            summary=parsed.title or "Untitled Event",
            start=dt.isoformat(),
            end=end_dt.isoformat(),
            dry_run=dry_run,
            location=parsed.location,
            description=parsed.description,
            attendees=parsed.attendees,
        )

        if result["status"] == "error":
            logger.error(f"Calendar write failed: {result['error']}")
        else:
            logger.info(f"Event {'[DRY_RUN] ' if dry_run else ''}registered: {parsed.title}")

    # 패턴 저장
    store = get_memory_store()
    try:
        store.write_pattern(
            user_id="default",
            pattern=f"{parsed.title} 자동 승인 (confidence={state.get('confidence', 0)})",
            metadata={
                "title": parsed.title,
                "datetime": str(dt) if dt else None,
                "confidence": state.get("confidence", 0),
            },
        )
    except Exception as e:
        logger.warning(f"Pattern write failed: {e}")


def _handle_hitl(state: ScheduleState):
    """Week 1: 콘솔 로그로 HITL. Week 2-3: Slack + interrupt."""
    parsed = state.get("parsed_event")
    confidence = state.get("confidence", 0)
    conflicts = state.get("conflicts", [])

    logger.warning(
        f"HITL REQUIRED: {parsed.title if parsed else 'Unknown'} "
        f"(confidence={confidence}, conflicts={len(conflicts)})"
    )
