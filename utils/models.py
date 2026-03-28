"""
이벤트 데이터 모델.

Parser Agent가 이메일을 파싱한 결과를 이 형태로 반환한다.
compute_confidence()가 이 모델을 받아서 신뢰도를 계산한다.

    Parser Agent
        │
        ▼
    EventJSON(title="회의", datetime="2026-03-29 14:00", ...)
        │
        ▼
    compute_confidence(event) → 0.95
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class EventJSON(BaseModel):
    """LLM이 이메일에서 추출한 이벤트 정보."""

    title: Optional[str] = None
    event_datetime: Optional[datetime] = None  # "datetime"은 Python 타입명과 충돌
    attendees: Optional[list[str]] = None
    location: Optional[str] = None
    duration: Optional[int] = None  # 분 단위
    description: Optional[str] = None
