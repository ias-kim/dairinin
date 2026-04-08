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
