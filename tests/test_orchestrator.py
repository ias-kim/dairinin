"""
Orchestrator (StateGraph) 통합 테스트.

전체 파이프라인: parser → scheduler → conflict
"""

from datetime import datetime
from unittest.mock import patch

from graph.orchestrator import build_graph
from utils.models import EventJSON


class TestOrchestrator:

    def test_auto_register_flow(self):
        """이벤트 이메일 + 충돌 없음 → auto_register."""
        mock_event = EventJSON(
            title="Team standup",
            event_datetime=datetime(2026, 3, 31, 10, 0),
        )

        with (
            patch("agents.parser.parse_with_llm", return_value=mock_event),
            patch("agents.scheduler.get_events_logic", return_value=[]),
        ):
            graph = build_graph()
            result = graph.invoke({
                "email_id": "msg_1",
                "raw_email": "Daily standup at 10am",
                "subject": "Standup",
                "sender": "team@example.com",
            })

        assert result["action"] == "auto_register"
        assert result["confidence"] >= 0.8

    def test_hitl_flow_with_conflict(self):
        """이벤트 이메일 + 충돌 있음 → hitl_required."""
        mock_event = EventJSON(
            title="Meeting",
            event_datetime=datetime(2026, 3, 31, 14, 0),
            duration=60,
        )

        existing = [{
            "id": "evt_1",
            "summary": "기존 미팅",
            "start": {"dateTime": "2026-03-31T14:00:00+09:00"},
            "end": {"dateTime": "2026-03-31T15:00:00+09:00"},
        }]

        with (
            patch("agents.parser.parse_with_llm", return_value=mock_event),
            patch("agents.scheduler.get_events_logic", return_value=existing),
        ):
            graph = build_graph()
            result = graph.invoke({
                "email_id": "msg_2",
                "raw_email": "Let's meet at 2pm",
                "subject": "Meeting",
                "sender": "kim@example.com",
            })

        assert result["action"] == "hitl_required"
        assert len(result["conflicts"]) == 1

    def test_skip_non_event(self):
        """일정 아닌 이메일 → skip (scheduler/conflict 미실행)."""
        with patch("agents.parser.parse_with_llm", return_value=None):
            graph = build_graph()
            result = graph.invoke({
                "email_id": "msg_3",
                "raw_email": "Here is the report",
                "subject": "Report",
                "sender": "boss@example.com",
            })

        assert result["parsed_event"] is None
        assert result["confidence"] == 0.0

    def test_hitl_flow_low_confidence(self):
        """애매한 이메일 → hitl_required."""
        mock_event = EventJSON(title="Sometime next week")

        with (
            patch("agents.parser.parse_with_llm", return_value=mock_event),
            patch("agents.scheduler.get_events_logic", return_value=[]),
        ):
            graph = build_graph()
            result = graph.invoke({
                "email_id": "msg_4",
                "raw_email": "Let's meet sometime next week",
                "subject": "Meeting",
                "sender": "park@example.com",
            })

        assert result["action"] == "hitl_required"
        assert result["confidence"] < 0.8
