"""
Conflict Agent — confidence + conflicts로 최종 action 결정.

순수 규칙 기반. LLM 호출 없음.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from graph.state import ScheduleState

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.8


def conflict_decision_node(state: ScheduleState) -> dict:
    """LangGraph 노드: auto_register / hitl_required / skip 결정.

    판단 로직:
        1. parsed_event 없음 → skip
        2. event_datetime 없음 → skip
        3. event_datetime이 과거 → skip (mem0 학습은 notifier에서 처리)
        4. confidence < 0.8 → hitl_required
        5. 충돌 있음 → hitl_required
        6. 나머지 → auto_register
    """
    parsed = state.get("parsed_event")

    if not parsed:
        return {"action": "skip"}

    if not parsed.event_datetime:
        logger.info(f"Skip: no event_datetime for '{parsed.title}'")
        return {"action": "skip"}

    now = datetime.now(timezone.utc)
    event_dt = parsed.event_datetime
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=timezone.utc)
    if event_dt < now:
        logger.info(f"Skip: past event '{parsed.title}' at {parsed.event_datetime}")
        return {"action": "skip"}

    confidence = state.get("confidence", 0.0)
    conflicts = state.get("conflicts", [])

    if confidence < CONFIDENCE_THRESHOLD:
        logger.info(f"HITL: low confidence ({confidence})")
        return {"action": "hitl_required"}

    if conflicts:
        logger.info(f"HITL: {len(conflicts)} conflict(s) found")
        return {"action": "hitl_required"}

    logger.info(f"Auto-register: {parsed.title} (confidence={confidence})")
    return {"action": "auto_register"}
