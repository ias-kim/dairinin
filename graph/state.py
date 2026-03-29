"""
LangGraph 상태 정의.

전체 아키텍처에서의 역할:
    StateGraph의 모든 노드가 이 상태를 읽고 쓴다.
    강의의 messages[] 배열 대신, 현재 이메일 1건의 처리 상태를 담는 typed dict.

    ┌──────────────────────────────────────────┐
    │  ScheduleState                           │
    │                                          │
    │  email_id ─────── 어떤 이메일인지 식별     │
    │  raw_email ────── Parser의 입력           │
    │  parsed_event ─── Parser의 출력           │
    │  confidence ───── compute_confidence 결과  │
    │  conflicts ────── Scheduler의 출력        │
    │  action ───────── Conflict의 판단         │
    │  hitl_response ── Slack 반응 (HITL 후)    │
    └──────────────────────────────────────────┘

    각 노드가 자기 담당 필드만 채움:
        Parser    → parsed_event, confidence
        Scheduler → conflicts
        Conflict  → action, proposed_alternative
        Notifier  → (외부 효과: Calendar 등록 or Slack 전송)

왜 TypedDict인가:
    LangGraph의 StateGraph는 TypedDict를 상태 스키마로 사용.
    Pydantic BaseModel이 아님 — LangGraph의 상태 머지 로직이
    TypedDict 기반으로 동작하기 때문.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, TypedDict

from utils.models import EventJSON


class ScheduleState(TypedDict, total=False):
    """이메일 1건의 처리 상태.

    total=False: 모든 필드가 optional. 노드가 자기 필드만 채우면 됨.
    """

    # 입력 (폴링 루프가 채움)
    email_id: str
    raw_email: str
    subject: str
    sender: str

    # Parser Agent 출력
    parsed_event: Optional[EventJSON]
    confidence: float

    # Scheduler Agent 출력 (Step 6에서 추가)
    conflicts: list[dict]

    # Conflict Agent 출력 (Step 6에서 추가)
    action: Literal["auto_register", "hitl_required", "skip"]
    proposed_alternative: Optional[datetime]

    # HITL 응답 (Week 2-3에서 추가)
    hitl_response: Optional[str]
