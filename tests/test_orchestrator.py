"""
Orchestrator 통합 테스트.

전체 파이프라인: parser → scheduler → conflict → notifier
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from graph.orchestrator import build_graph
from utils.models import EventJSON


class TestOrchestrator:

    def test_auto_register_full_flow(self):
        """이벤트 이메일 + 충돌 없음 → auto_register → notifier 실행."""
        mock_event = EventJSON(
            title="Team standup",
            event_datetime=datetime(2026, 3, 31, 10, 0),
        )

        with (
            patch("agents.parser.parse_with_llm", return_value=mock_event),
            patch("agents.scheduler.get_events_logic", return_value=[]),
            patch("agents.notifier.create_event_logic", return_value={"status": "dry_run"}),
            patch("agents.notifier.mark_read_logic", return_value=True),
            patch("agents.notifier.get_memory_store", return_value=MagicMock()),
        ):
            graph = build_graph()
            result = graph.invoke({
                "email_id": "msg_1",
                "raw_email": "Daily standup at 10am",
                "subject": "Standup",
                "sender": "team@example.com",
            })

        assert result["action"] == "auto_register"
        assert result["notification"] == "auto_register"

    def test_hitl_flow_with_conflict(self):
        """충돌 있음 → hitl_required → notifier는 로그만."""
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
            patch("agents.notifier.create_event_logic") as mock_create,
            patch("agents.notifier.mark_read_logic", return_value=True),
            patch("agents.notifier.get_memory_store", return_value=MagicMock()),
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
            patch("agents.notifier.interrupt"),  # interrupt를 mock해서 그래프 계속 실행
        ):
            graph = build_graph()
            result = graph.invoke({
                "email_id": "msg_2",
                "raw_email": "Let's meet at 2pm",
                "subject": "Meeting",
                "sender": "kim@example.com",
            })

        assert result["action"] == "hitl_required"
        assert result["notification"] == "hitl_resolved"
        mock_create.assert_not_called()  # resume 없으므로 approve 안 됨

    def test_skip_non_event(self):
        """이벤트 아닌 이메일 → skip → mark_read만."""
        with (
            patch("agents.parser.parse_with_llm", return_value=None),
            patch("agents.notifier.create_event_logic") as mock_create,
            patch("agents.notifier.mark_read_logic", return_value=True),
            patch("agents.notifier.get_memory_store", return_value=MagicMock()),
        ):
            graph = build_graph()
            result = graph.invoke({
                "email_id": "msg_3",
                "raw_email": "Here is the report",
                "subject": "Report",
                "sender": "boss@example.com",
            })

        assert result["notification"] == "skip"
        mock_create.assert_not_called()
