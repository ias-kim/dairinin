"""
Orchestrator (StateGraph) 통합 테스트.

Parser Agent를 mock한 상태에서 그래프 전체 실행 테스트.
"노드가 올바른 순서로 실행되고, 분기가 제대로 동작하는가?"
"""

from datetime import datetime
from unittest.mock import patch

from graph.orchestrator import build_graph
from utils.models import EventJSON


class TestOrchestrator:

    def test_graph_processes_event_email(self):
        """이벤트 이메일 → parser 실행 → parsed_event + confidence 반환."""
        mock_event = EventJSON(
            title="Team standup",
            event_datetime=datetime(2026, 3, 31, 10, 0),
        )

        with patch("agents.parser.parse_with_llm", return_value=mock_event):
            graph = build_graph()
            result = graph.invoke({
                "email_id": "msg_1",
                "raw_email": "Daily standup at 10am tomorrow",
                "subject": "Standup",
                "sender": "team@example.com",
            })

        assert result["parsed_event"] is not None
        assert result["parsed_event"].title == "Team standup"
        assert result["confidence"] >= 0.8

    def test_graph_skips_non_event_email(self):
        """일정 아닌 이메일 → parser → None → END."""
        with patch("agents.parser.parse_with_llm", return_value=None):
            graph = build_graph()
            result = graph.invoke({
                "email_id": "msg_2",
                "raw_email": "Please review the attached document",
                "subject": "Document review",
                "sender": "boss@example.com",
            })

        assert result["parsed_event"] is None
        assert result["confidence"] == 0.0
