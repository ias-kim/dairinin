"""
FastAPI 앱 + 폴링 루프 테스트.

poll_gmail_loop의 핵심 로직:
    1. fetch_emails → 이메일 리스트
    2. 각 이메일에 대해 graph.invoke(state)
    3. 에러 발생해도 루프 계속 (시스템 죽으면 안 됨)
    4. 동시 실행 방지 (processing lock)
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app import process_single_email
from utils.models import EventJSON


class TestProcessSingleEmail:
    """이메일 1건 처리 테스트."""

    def test_processes_event_email(self):
        """이벤트 이메일 → graph 실행 → 결과 반환."""
        mock_event = EventJSON(
            title="Lunch",
            event_datetime=datetime(2026, 3, 31, 12, 0),
        )

        with patch("app.build_graph") as mock_build:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {
                "email_id": "msg_1",
                "parsed_event": mock_event,
                "confidence": 0.8,
            }
            mock_build.return_value = mock_graph

            result = process_single_email({
                "id": "msg_1",
                "from": "kim@example.com",
                "subject": "Lunch?",
                "snippet": "Let's grab lunch at noon",
            })

        assert result is not None
        assert result["confidence"] == 0.8

    def test_skips_non_event_email(self):
        """일정 아닌 이메일 → graph 실행 → None 결과."""
        with patch("app.build_graph") as mock_build:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {
                "email_id": "msg_2",
                "parsed_event": None,
                "confidence": 0.0,
            }
            mock_build.return_value = mock_graph

            result = process_single_email({
                "id": "msg_2",
                "from": "boss@example.com",
                "subject": "Report",
                "snippet": "Please review the attached",
            })

        assert result["parsed_event"] is None

    def test_handles_graph_error(self):
        """graph.invoke 실패 → None 반환, 시스템 계속."""
        with patch("app.build_graph") as mock_build:
            mock_graph = MagicMock()
            mock_graph.invoke.side_effect = Exception("LLM timeout")
            mock_build.return_value = mock_graph

            result = process_single_email({
                "id": "msg_3",
                "from": "test@example.com",
                "subject": "Test",
                "snippet": "test",
            })

        assert result is None
