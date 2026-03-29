"""
Notifier Agent 테스트.

action에 따라 실제 효과를 실행:
  auto_register → create_event + write_pattern + mark_read
  hitl_required → 로그 (Week 2-3: Slack)
  skip          → mark_read만
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from utils.models import EventJSON


class TestNotifierAgent:

    def test_auto_register_creates_event(self):
        """auto_register → create_event 호출됨."""
        from agents.notifier import notify_node

        parsed = EventJSON(
            title="팀 미팅",
            event_datetime=datetime(2026, 3, 31, 14, 0),
            duration=60,
        )

        with (
            patch("agents.notifier.create_event_logic") as mock_create,
            patch("agents.notifier.mark_read_logic") as mock_mark,
            patch("agents.notifier.get_memory_store") as mock_mem,
        ):
            mock_create.return_value = {"status": "dry_run", "summary": "팀 미팅"}
            mock_mark.return_value = True

            result = notify_node({
                "email_id": "msg_1",
                "parsed_event": parsed,
                "confidence": 0.9,
                "action": "auto_register",
            })

        mock_create.assert_called_once()
        mock_mark.assert_called_once()
        assert result["notification"] == "auto_register"

    def test_auto_register_writes_pattern(self):
        """auto_register → memory에 패턴 저장."""
        from agents.notifier import notify_node

        parsed = EventJSON(
            title="치과",
            event_datetime=datetime(2026, 4, 1, 15, 0),
        )

        with (
            patch("agents.notifier.create_event_logic") as mock_create,
            patch("agents.notifier.mark_read_logic"),
            patch("agents.notifier.get_memory_store") as mock_mem,
        ):
            mock_store = MagicMock()
            mock_mem.return_value = mock_store
            mock_create.return_value = {"status": "dry_run"}

            notify_node({
                "email_id": "msg_2",
                "parsed_event": parsed,
                "confidence": 0.85,
                "action": "auto_register",
            })

        mock_store.write_pattern.assert_called_once()

    def test_hitl_required_does_not_create_event(self):
        """hitl_required → create_event 호출 안 됨."""
        from agents.notifier import notify_node

        parsed = EventJSON(title="뭔가 약속")

        with (
            patch("agents.notifier.create_event_logic") as mock_create,
            patch("agents.notifier.mark_read_logic"),
            patch("agents.notifier.get_memory_store"),
        ):
            result = notify_node({
                "email_id": "msg_3",
                "parsed_event": parsed,
                "confidence": 0.5,
                "action": "hitl_required",
            })

        mock_create.assert_not_called()
        assert result["notification"] == "hitl_required"

    def test_skip_marks_read_only(self):
        """skip → mark_read만 호출, create_event 안 함."""
        from agents.notifier import notify_node

        with (
            patch("agents.notifier.create_event_logic") as mock_create,
            patch("agents.notifier.mark_read_logic") as mock_mark,
            patch("agents.notifier.get_memory_store"),
        ):
            mock_mark.return_value = True

            result = notify_node({
                "email_id": "msg_4",
                "parsed_event": None,
                "confidence": 0.0,
                "action": "skip",
            })

        mock_create.assert_not_called()
        mock_mark.assert_called_once()
        assert result["notification"] == "skip"

    def test_create_event_failure_still_continues(self):
        """create_event 실패해도 mark_read는 실행됨."""
        from agents.notifier import notify_node

        parsed = EventJSON(
            title="미팅",
            event_datetime=datetime(2026, 3, 31, 14, 0),
        )

        with (
            patch("agents.notifier.create_event_logic") as mock_create,
            patch("agents.notifier.mark_read_logic") as mock_mark,
            patch("agents.notifier.get_memory_store") as mock_mem,
        ):
            mock_create.return_value = {"status": "error", "error": "403"}
            mock_mark.return_value = True
            mock_mem.return_value = MagicMock()

            result = notify_node({
                "email_id": "msg_5",
                "parsed_event": parsed,
                "confidence": 0.9,
                "action": "auto_register",
            })

        mock_mark.assert_called_once()
        assert result["notification"] == "auto_register"
