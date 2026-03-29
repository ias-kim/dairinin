"""
Notifier Agent — action에 따라 실제 효과를 실행.

auto_register: 캘린더 등록 + 패턴 저장 + 이메일 읽음 처리
hitl_required: Slack 전송 + interrupt() (그래프 일시정지)
skip:          이메일 읽음 처리만
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import timedelta, timezone

from langgraph.types import interrupt

from db.hitl_store import HitlStore
from graph.state import ScheduleState
from mcp_servers.calendar_mcp import create_event_logic
from mcp_servers.gmail_mcp import mark_read_logic
from mcp_servers.memory_mcp import MemoryStore
from mcp_servers.slack_mcp import send_hitl_message

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

_memory_store: MemoryStore | None = None
_hitl_store: HitlStore | None = None


def get_memory_store() -> MemoryStore:
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store


def get_hitl_store() -> HitlStore:
    global _hitl_store
    if _hitl_store is None:
        _hitl_store = HitlStore()
    return _hitl_store


def notify_node(state: ScheduleState) -> dict:
    """LangGraph 노드: action에 따라 실행."""
    action = state.get("action", "skip")
    email_id = state.get("email_id", "")
    parsed = state.get("parsed_event")

    if action == "auto_register" and parsed:
        _handle_auto_register(state)
        _do_mark_read(email_id)
        return {"notification": "auto_register"}

    elif action == "hitl_required":
        _handle_hitl(state)
        # interrupt() 후 resume되면 여기로 돌아옴
        # hitl_response가 state에 있으면 resume된 것
        hitl_response = state.get("hitl_response")
        if hitl_response == "approve":
            _handle_auto_register(state)
        _do_mark_read(email_id)
        return {"notification": "hitl_resolved"}

    # skip
    _do_mark_read(email_id)
    return {"notification": "skip"}


def _do_mark_read(email_id: str):
    """이메일 읽음 처리 (공통)."""
    if not email_id:
        return
    try:
        from mcp_servers.gmail_mcp import build_gmail_service
        service = build_gmail_service()
        mark_read_logic(service, email_id)
    except Exception as e:
        logger.warning(f"mark_read skipped: {e}")


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
    """Slack 전송 + interrupt()로 그래프 일시정지."""
    parsed = state.get("parsed_event")
    confidence = state.get("confidence", 0)
    conflicts = state.get("conflicts", [])
    email_id = state.get("email_id", "")

    # 이미 resume된 경우 (hitl_response가 있으면) → Slack 전송 스킵
    if state.get("hitl_response"):
        return

    # Slack 전송 시도
    slack_channel = os.getenv("SLACK_CHANNEL_ID", "")
    if slack_channel:
        try:
            from mcp_servers.slack_mcp import build_slack_client
            client = build_slack_client()

            conflict_names = [c.get("summary", "Unknown") for c in conflicts]
            result = send_hitl_message(
                client=client,
                channel=slack_channel,
                title=parsed.title if parsed else "Unknown",
                datetime_str=str(parsed.event_datetime) if parsed and parsed.event_datetime else "미정",
                confidence=confidence,
                conflicts=conflict_names,
                email_id=email_id,
                sender=state.get("sender", ""),
                snippet=state.get("raw_email", ""),
            )

            if result and result.get("ts"):
                hitl = get_hitl_store()
                thread_id = state.get("_thread_id", str(uuid.uuid4()))
                hitl.insert(result["ts"], thread_id, email_id)

        except Exception as e:
            logger.error(f"Slack HITL failed: {e}")
    else:
        logger.warning(
            f"HITL (no Slack): {parsed.title if parsed else 'Unknown'} "
            f"(confidence={confidence}, conflicts={len(conflicts)})"
        )

    # 그래프 일시정지 — Slack reaction 올 때까지 대기
    # resume 시 hitl_response가 state에 주입됨
    interrupt(f"HITL required: {parsed.title if parsed else 'Unknown'}")
