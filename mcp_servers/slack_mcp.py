"""
Slack MCP 서버 — HITL 메시지 전송.

Notifier Agent가 hitl_required일 때 호출.
사용자에게 이벤트 정보 + ✅/❌ 이모지 안내를 전송.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def build_slack_client():
    """Slack WebClient 생성."""
    from slack_sdk import WebClient

    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN not set")
    return WebClient(token=token)


def send_hitl_message(
    client,
    channel: str,
    title: str,
    datetime_str: str,
    confidence: float,
    conflicts: list[str],
    email_id: str,
) -> Optional[dict]:
    """HITL 메시지를 Slack 채널에 전송.

    Args:
        client: Slack WebClient
        channel: 채널 ID
        title: 이벤트 제목
        datetime_str: 일시 문자열
        confidence: 신뢰도 점수
        conflicts: 충돌 일정 이름 리스트
        email_id: Gmail 메시지 ID (webhook에서 매핑용)

    Returns:
        {"ok": True, "ts": "...", "channel": "..."} 또는 None
    """
    conflict_text = ""
    if conflicts:
        conflict_lines = "\n".join(f"  • {c}" for c in conflicts)
        conflict_text = f"\n⚠️ 충돌:\n{conflict_lines}"

    text = (
        f"📅 *일정 확인 요청*\n"
        f"제목: *{title}*\n"
        f"일시: {datetime_str}\n"
        f"신뢰도: {confidence:.0%}"
        f"{conflict_text}\n\n"
        f"✅ 등록  |  ❌ 무시\n"
        f"_email_id: {email_id}_"
    )

    try:
        response = client.chat_postMessage(
            channel=channel,
            text=text,
            unfurl_links=False,
        )
        logger.info(f"HITL message sent: {response['ts']}")
        return {
            "ok": response.get("ok", False),
            "ts": response.get("ts", ""),
            "channel": response.get("channel", channel),
        }
    except Exception as e:
        logger.error(f"Slack send failed: {e}")
        return None
