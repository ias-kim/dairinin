"""
slack-mcp 테스트.

HITL 메시지를 Slack 채널에 전송.
Slack SDK를 mock해서 API 호출 없이 테스트.
"""

from unittest.mock import MagicMock, patch


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
