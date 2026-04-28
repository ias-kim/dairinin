"""
slack-mcp 테스트.

HITL 메시지를 Slack 채널에 전송.
Slack SDK를 mock해서 API 호출 없이 테스트.
"""
import pytest
import pytest
from unittest.mock import MagicMock, patch


class TestMcpTools:
    """FastMCP @mcp.tool 레이어 테스트."""

    @pytest.mark.asyncio
    async def test_send_hitl_tool_is_registered(self):
        from fastmcp import Client
        from mcp_servers.slack_mcp import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "send_hitl" in tool_names

    @pytest.mark.asyncio
    async def test_send_reply_notification_tool_is_registered(self):
        from fastmcp import Client
        from mcp_servers.slack_mcp import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "send_reply_notification_tool" in tool_names


class TestSlackMcp:

    def test_send_hitl_message(self):
        """HITL 메시지 전송 → Slack API 호출 확인."""
        from mcp_servers.slack_mcp import send_hitl_message

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {
            "ok": True,
            "ts": "1234567890.123456",
            "channel": "C0123",
        }

        result = send_hitl_message(
            client=mock_client,
            channel="C0123",
            title="팀 미팅",
            datetime_str="2026-03-31 14:00",
            confidence=0.65,
            conflicts=["기존 미팅 14:00-15:00"],
            email_id="msg_1",
        )

        mock_client.chat_postMessage.assert_called_once()
        assert result["ok"] is True
        assert result["ts"] == "1234567890.123456"

    def test_send_message_without_conflicts(self):
        """충돌 없이 confidence만 낮을 때도 전송."""
        from mcp_servers.slack_mcp import send_hitl_message

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "111.222"}

        result = send_hitl_message(
            client=mock_client,
            channel="C0123",
            title="점심 약속",
            datetime_str="다음 주",
            confidence=0.5,
            conflicts=[],
            email_id="msg_2",
        )

        assert result["ok"] is True

    def test_handles_slack_error(self):
        """Slack API 에러 → None 반환."""
        from mcp_servers.slack_mcp import send_hitl_message

        mock_client = MagicMock()
        mock_client.chat_postMessage.side_effect = Exception("channel_not_found")

        result = send_hitl_message(
            client=mock_client,
            channel="C0123",
            title="미팅",
            datetime_str="내일",
            confidence=0.7,
            conflicts=[],
            email_id="msg_3",
        )

        assert result is None


class TestSendAutoRegisterNotification:
    """send_auto_register_notification — 일정 등록 완료 Slack 알림 테스트.

    auto_register 완료 후 사용자에게 "어떤 일정이 등록됐는지" 알려줘야 한다.
    기존 send_reply_notification은 "메일 답장 완료" 텍스트만 보여줬는데,
    실제로는 답장을 안 보내므로 일정 상세를 보여주는 새 함수가 필요하다.
    """

    def test_sends_event_title_and_datetime(self):
        """등록된 일정의 제목과 일시가 Slack 메시지에 포함돼야 한다."""
        from datetime import datetime
        from mcp_servers.slack_mcp import send_auto_register_notification
        from utils.models import EventJSON

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "111.222"}

        parsed = EventJSON(
            title="1차 면접",
            event_datetime=datetime(2026, 5, 8, 14, 0),
        )

        send_auto_register_notification(
            client=mock_client,
            channel="C0123",
            parsed_event=parsed,
            sender="hr@company.com",
        )

        call_kwargs = mock_client.chat_postMessage.call_args[1]
        text = call_kwargs.get("text", "") + str(call_kwargs.get("blocks", ""))
        assert "1차 면접" in text
        assert "2026" in text or "05-08" in text or "5월" in text

    def test_includes_location_url(self):
        """Google Meet / Zoom URL이 있으면 Slack 메시지에 포함돼야 한다."""
        from datetime import datetime
        from mcp_servers.slack_mcp import send_auto_register_notification
        from utils.models import EventJSON

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "111.222"}

        parsed = EventJSON(
            title="팀 미팅",
            event_datetime=datetime(2026, 5, 8, 14, 0),
            location="https://meet.google.com/abc-defg-hij",
        )

        send_auto_register_notification(
            client=mock_client,
            channel="C0123",
            parsed_event=parsed,
            sender="manager@company.com",
        )

        call_kwargs = mock_client.chat_postMessage.call_args[1]
        text = call_kwargs.get("text", "") + str(call_kwargs.get("blocks", ""))
        assert "meet.google.com" in text

    def test_shows_registered_status(self):
        """메시지에 '등록' 완료 표시가 있어야 한다 (답장이 아님을 명확히)."""
        from datetime import datetime
        from mcp_servers.slack_mcp import send_auto_register_notification
        from utils.models import EventJSON

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "111.222"}

        parsed = EventJSON(title="미팅", event_datetime=datetime(2026, 5, 8, 14, 0))

        send_auto_register_notification(
            client=mock_client,
            channel="C0123",
            parsed_event=parsed,
            sender="x@x.com",
        )

        call_kwargs = mock_client.chat_postMessage.call_args[1]
        text = call_kwargs.get("text", "")
        assert "등록" in text

    def test_handles_slack_error_gracefully(self):
        """Slack API 에러 → False 반환, 예외 전파 안 함."""
        from datetime import datetime
        from mcp_servers.slack_mcp import send_auto_register_notification
        from utils.models import EventJSON

        mock_client = MagicMock()
        mock_client.chat_postMessage.side_effect = Exception("channel_not_found")

        parsed = EventJSON(title="미팅", event_datetime=datetime(2026, 5, 8, 14, 0))

        result = send_auto_register_notification(
            client=mock_client,
            channel="C0123",
            parsed_event=parsed,
            sender="x@x.com",
        )

        assert result is False


class TestHitlMessageDatetime:
    """HITL 메시지에 datetime이 사람이 읽기 좋은 형태로 표시되는지 테스트."""

    def test_datetime_formatted_in_korean(self):
        """HITL 메시지에 ISO 8601 repr 대신 '5월 8일 14:00' 형태로 표시."""
        from mcp_servers.slack_mcp import format_datetime_kr

        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        dt = datetime(2026, 5, 8, 14, 0, tzinfo=KST)

        result = format_datetime_kr(dt)

        assert "5월" in result or "05" in result
        assert "8일" in result or "08" in result
        assert "2:00" in result or "14:00" in result  # 오후 2:00 또는 14:00

    def test_formats_naive_datetime(self):
        """timezone 없는 datetime도 처리."""
        from mcp_servers.slack_mcp import format_datetime_kr
        from datetime import datetime

        dt = datetime(2026, 5, 8, 9, 30)
        result = format_datetime_kr(dt)

        assert "5월" in result or "05" in result
        assert "9" in result or "09" in result


class TestSendReplyNotification:

    def test_send_reply_notification_success(self):
        """답장 완료 Slack 알림 전송."""
        from mcp_servers.slack_mcp import send_reply_notification

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "999.111"}

        result = send_reply_notification(
            client=mock_client,
            channel="C0123",
            subject="Meeting request",
            sender="kim@example.com",
        )

        mock_client.chat_postMessage.assert_called_once()
        assert result is True

    def test_send_reply_notification_failure_returns_false(self):
        """Slack 에러 → False 반환."""
        from mcp_servers.slack_mcp import send_reply_notification

        mock_client = MagicMock()
        mock_client.chat_postMessage.side_effect = Exception("channel_not_found")

        result = send_reply_notification(
            client=mock_client,
            channel="C0123",
            subject="미팅",
            sender="kim@example.com",
        )

        assert result is False
