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

    def test_hitl_uses_state_thread_id_for_mapping(self):
        """_thread_id가 state에 있으면 그 값으로 hitl.insert() 호출.

        버그 재현: state에 _thread_id 없으면 notifier가 uuid를 새로 생성 →
        app.py의 config thread_id와 다른 값이 DB에 저장 → resume 실패.
        """
        from agents.notifier import _handle_hitl
        from datetime import datetime, timedelta, timezone

        _FUTURE = datetime.now(timezone.utc) + timedelta(days=7)
        parsed = EventJSON(title="중요 미팅", event_datetime=_FUTURE)
        expected_thread_id = "fixed-thread-id-from-app"

        mock_hitl = MagicMock()
        mock_hitl.is_email_pending.return_value = False
        mock_hitl.insert.return_value = True

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.send_hitl_message.return_value = {"ts": "1234567890.123456"}

        with (
            patch("agents.notifier.get_hitl_store", return_value=mock_hitl),
            patch("agents.notifier.send_hitl_message", return_value={"ts": "1234567890.123456"}),
            patch("mcp_servers.slack_mcp.build_slack_client", return_value=MagicMock()),
            patch.dict("os.environ", {"SLACK_CHANNEL_ID": "C_TEST"}),
            patch("agents.notifier.interrupt", side_effect=Exception("interrupted")),
        ):
            try:
                _handle_hitl({
                    "email_id": "msg_hitl",
                    "parsed_event": parsed,
                    "confidence": 0.65,
                    "conflicts": [],
                    "_thread_id": expected_thread_id,
                })
            except Exception:
                pass

        # hitl.insert()의 두 번째 인자(thread_id)가 state의 _thread_id여야 함
        call_args = mock_hitl.insert.call_args
        actual_thread_id = call_args[0][1]  # positional: (slack_ts, thread_id, email_id, ...)
        assert actual_thread_id == expected_thread_id, (
            f"thread_id mismatch: expected {expected_thread_id!r}, got {actual_thread_id!r}\n"
            "이 버그가 있으면 HITL resume이 잘못된 그래프 스레드를 찾습니다."
        )

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
