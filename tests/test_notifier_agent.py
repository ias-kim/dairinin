"""
Notifier Agent 테스트.

auto_register → create_event + write_pattern + mark_read
hitl_required → Slack 전송 + interrupt()
skip          → mark_read만
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from utils.models import EventJSON


class TestNotifierAutoRegister:

    def test_creates_event(self):
        from agents.notifier import notify_node

        parsed = EventJSON(title="팀 미팅", event_datetime=datetime(2026, 3, 31, 14, 0), duration=60)

        with (
            patch("agents.notifier.create_event_logic", return_value={"status": "dry_run", "summary": "팀 미팅"}),
            patch("agents.notifier.mark_read_logic", return_value=True),
            patch("agents.notifier.get_memory_store", return_value=MagicMock()),
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
        ):
            result = notify_node({
                "email_id": "msg_1",
                "parsed_event": parsed,
                "confidence": 0.9,
                "action": "auto_register",
            })

        assert result["notification"] == "auto_register"

    def test_writes_pattern(self):
        from agents.notifier import notify_node

        parsed = EventJSON(title="치과", event_datetime=datetime(2026, 4, 1, 15, 0))

        with (
            patch("agents.notifier.create_event_logic", return_value={"status": "dry_run"}),
            patch("agents.notifier.mark_read_logic"),
            patch("agents.notifier.get_memory_store") as mock_mem,
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
        ):
            mock_store = MagicMock()
            mock_mem.return_value = mock_store

            notify_node({
                "email_id": "msg_2",
                "parsed_event": parsed,
                "confidence": 0.85,
                "action": "auto_register",
            })

        mock_store.write_pattern.assert_called_once()

    def test_create_failure_still_marks_read(self):
        from agents.notifier import notify_node

        parsed = EventJSON(title="미팅", event_datetime=datetime(2026, 3, 31, 14, 0))

        with (
            patch("agents.notifier.create_event_logic", return_value={"status": "error", "error": "403"}),
            patch("agents.notifier.mark_read_logic") as mock_mark,
            patch("agents.notifier.get_memory_store", return_value=MagicMock()),
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
        ):
            notify_node({
                "email_id": "msg_3",
                "parsed_event": parsed,
                "confidence": 0.9,
                "action": "auto_register",
            })

        mock_mark.assert_called_once()


class TestNotifierHitl:

    def test_hitl_calls_interrupt(self):
        """hitl_required → interrupt() 호출됨."""
        from agents.notifier import notify_node

        parsed = EventJSON(title="뭔가 약속")

        with (
            patch("agents.notifier.interrupt", side_effect=Exception("interrupted")) as mock_int,
            patch("agents.notifier.mark_read_logic"),
            patch("agents.notifier.get_memory_store"),
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
        ):
            try:
                notify_node({
                    "email_id": "msg_4",
                    "parsed_event": parsed,
                    "confidence": 0.5,
                    "action": "hitl_required",
                })
            except Exception:
                pass

        mock_int.assert_called_once()

    def test_hitl_resume_approve_creates_event(self):
        """interrupt에서 resume + approve → auto_register 실행."""
        from agents.notifier import notify_node

        parsed = EventJSON(title="미팅", event_datetime=datetime(2026, 3, 31, 14, 0))

        with (
            patch("agents.notifier.create_event_logic", return_value={"status": "dry_run"}) as mock_create,
            patch("agents.notifier.mark_read_logic", return_value=True),
            patch("agents.notifier.get_memory_store", return_value=MagicMock()),
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
        ):
            # hitl_response가 있으면 resume된 상태 → interrupt 스킵
            result = notify_node({
                "email_id": "msg_5",
                "parsed_event": parsed,
                "confidence": 0.65,
                "action": "hitl_required",
                "hitl_response": "approve",
            })

        mock_create.assert_called_once()
        assert result["notification"] == "hitl_resolved"


class TestNotifierAutoRegisterReplyAndSlack:

    def test_auto_register_sends_reply(self):
        """auto_register → sender에게 답장 전송."""
        from agents.notifier import notify_node

        parsed = EventJSON(title="팀 미팅", event_datetime=datetime(2026, 5, 1, 14, 0), duration=60)

        with (
            patch("agents.notifier.create_event_logic", return_value={"status": "dry_run"}),
            patch("agents.notifier.mark_read_logic"),
            patch("agents.notifier.get_memory_store", return_value=MagicMock()),
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
            patch("agents.notifier.send_reply_logic") as mock_reply,
            patch("agents.notifier.send_reply_notification", return_value=True),
        ):
            notify_node({
                "email_id": "msg_10",
                "parsed_event": parsed,
                "confidence": 0.9,
                "action": "auto_register",
                "sender": "kim@example.com",
                "subject": "팀 미팅 요청",
            })

        mock_reply.assert_called_once()

    def test_auto_register_sends_slack_notification(self):
        """auto_register → Slack 알림 전송."""
        from agents.notifier import notify_node

        parsed = EventJSON(title="팀 미팅", event_datetime=datetime(2026, 5, 1, 14, 0), duration=60)

        with (
            patch("agents.notifier.create_event_logic", return_value={"status": "dry_run"}),
            patch("agents.notifier.mark_read_logic"),
            patch("agents.notifier.get_memory_store", return_value=MagicMock()),
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
            patch("agents.notifier.send_reply_logic"),
            patch("agents.notifier.send_reply_notification") as mock_slack,
        ):
            notify_node({
                "email_id": "msg_11",
                "parsed_event": parsed,
                "confidence": 0.9,
                "action": "auto_register",
                "sender": "kim@example.com",
                "subject": "팀 미팅 요청",
            })

        mock_slack.assert_called_once()

    def test_reply_failure_does_not_block_pipeline(self):
        """답장 실패해도 파이프라인 계속 진행."""
        from agents.notifier import notify_node

        parsed = EventJSON(title="미팅", event_datetime=datetime(2026, 5, 1, 14, 0))

        with (
            patch("agents.notifier.create_event_logic", return_value={"status": "dry_run"}),
            patch("agents.notifier.mark_read_logic"),
            patch("agents.notifier.get_memory_store", return_value=MagicMock()),
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
            patch("agents.notifier.send_reply_logic", side_effect=Exception("smtp error")),
            patch("agents.notifier.send_reply_notification", return_value=False),
        ):
            result = notify_node({
                "email_id": "msg_12",
                "parsed_event": parsed,
                "confidence": 0.9,
                "action": "auto_register",
                "sender": "kim@example.com",
                "subject": "미팅",
            })

        assert result["notification"] == "auto_register"


class TestNotifierSkip:

    def test_skip_marks_read_only(self):
        from agents.notifier import notify_node

        with (
            patch("agents.notifier.create_event_logic") as mock_create,
            patch("agents.notifier.mark_read_logic") as mock_mark,
            patch("agents.notifier.get_memory_store"),
            patch("agents.notifier.get_hitl_store", return_value=MagicMock()),
        ):
            mock_mark.return_value = True
            result = notify_node({
                "email_id": "msg_6",
                "parsed_event": None,
                "confidence": 0.0,
                "action": "skip",
            })

        mock_create.assert_not_called()
        mock_mark.assert_called_once()
        assert result["notification"] == "skip"
