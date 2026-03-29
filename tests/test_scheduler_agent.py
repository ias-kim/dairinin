"""
Scheduler Agent 테스트.

parsed_event의 날짜에 기존 일정이 있는지 calendar-mcp로 확인.
결과를 state["conflicts"]에 저장.
"""

from datetime import datetime
from unittest.mock import patch

from utils.models import EventJSON


class TestSchedulerAgent:

    def test_finds_conflicts(self):
        """기존 일정과 겹치면 conflicts에 추가."""
        from agents.scheduler import schedule_check_node

        parsed = EventJSON(
            title="New meeting",
            event_datetime=datetime(2026, 3, 31, 14, 30),
            duration=60,
        )

        existing = [
            {
                "id": "evt_1",
                "summary": "팀 미팅",
                "start": {"dateTime": "2026-03-31T14:00:00+09:00"},
                "end": {"dateTime": "2026-03-31T15:00:00+09:00"},
            },
        ]

        with patch("agents.scheduler.get_events_logic", return_value=existing):
            result = schedule_check_node({
                "email_id": "msg_1",
                "parsed_event": parsed,
                "confidence": 0.9,
            })

        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["summary"] == "팀 미팅"

    def test_no_conflicts(self):
        """겹치는 일정 없으면 빈 리스트."""
        from agents.scheduler import schedule_check_node

        parsed = EventJSON(
            title="Morning run",
            event_datetime=datetime(2026, 3, 31, 7, 0),
            duration=60,
        )

        with patch("agents.scheduler.get_events_logic", return_value=[]):
            result = schedule_check_node({
                "email_id": "msg_2",
                "parsed_event": parsed,
                "confidence": 0.9,
            })

        assert result["conflicts"] == []

    def test_skips_when_no_datetime(self):
        """event_datetime 없으면 충돌 체크 스킵."""
        from agents.scheduler import schedule_check_node

        parsed = EventJSON(title="Sometime next week")

        result = schedule_check_node({
            "email_id": "msg_3",
            "parsed_event": parsed,
            "confidence": 0.5,
        })

        assert result["conflicts"] == []

    def test_handles_calendar_error(self):
        """calendar API 에러 → 빈 충돌 리스트 (안전하게)."""
        from agents.scheduler import schedule_check_node

        parsed = EventJSON(
            title="Meeting",
            event_datetime=datetime(2026, 3, 31, 14, 0),
            duration=60,
        )

        with patch("agents.scheduler.get_events_logic", side_effect=Exception("API down")):
            result = schedule_check_node({
                "email_id": "msg_4",
                "parsed_event": parsed,
                "confidence": 0.9,
            })

        assert result["conflicts"] == []
