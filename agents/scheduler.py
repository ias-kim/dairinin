"""
Scheduler Agent — 이벤트 시간에 기존 일정 충돌 확인.

parsed_event의 날짜로 calendar-mcp에서 일정을 가져오고
check_conflicts_logic으로 겹치는 일정을 찾는다.
"""

from __future__ import annotations

import logging
from datetime import timedelta, timezone

from graph.state import ScheduleState
from mcp_servers.calendar_mcp import build_calendar_service, check_conflicts_logic, get_events_logic

logger = logging.getLogger(__name__)

DEFAULT_DURATION = 60  # 분


def schedule_check_node(state: ScheduleState) -> dict:
    """LangGraph 노드: 충돌 체크.

    1. parsed_event에서 날짜 추출
    2. calendar-mcp로 해당 날짜 일정 조회
    3. check_conflicts_logic으로 겹침 계산
    4. state["conflicts"] 반환
    """
    parsed = state.get("parsed_event")

    if not parsed or not parsed.event_datetime:
        return {"conflicts": []}

    dt = parsed.event_datetime
    # naive datetime이면 KST(+09:00) 붙이기 — Google Calendar는 aware 필요
    KST = timezone(timedelta(hours=9))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)

    duration = parsed.duration or DEFAULT_DURATION
    end_dt = dt + timedelta(minutes=duration)

    date_str = dt.strftime("%Y-%m-%d")
    start_str = dt.isoformat()
    end_str = end_dt.isoformat()

    try:
        service = build_calendar_service()
        existing = get_events_logic(service, date_str)
        conflicts = check_conflicts_logic(existing, start_str, end_str)
    except Exception as e:
        logger.error(f"Schedule check failed: {e}")
        conflicts = []

    return {"conflicts": conflicts}
