"""
Slack MCP 서버 — HITL 메시지 전송.

Block Kit 버튼으로 ✅등록 / ❌무시 선택.
이메일 본문 snippet 포함.
"""

from __future__ import annotations

import json
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
    sender: str = "",
    snippet: str = "",
) -> Optional[dict]:
    """HITL 메시지를 Block Kit 버튼과 함께 전송.

    Returns:
        {"ok": True, "ts": "...", "channel": "..."} 또는 None
    """
    # 충돌 텍스트
    conflict_text = ""
    if conflicts:
        conflict_text = "\n".join(f"• {c}" for c in conflicts)

    # Block Kit 구성
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📅 일정 확인 요청", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*제목*\n{title}"},
                {"type": "mrkdwn", "text": f"*일시*\n{datetime_str}"},
                {"type": "mrkdwn", "text": f"*신뢰도*\n{confidence:.0%}"},
                {"type": "mrkdwn", "text": f"*보낸 사람*\n{sender or '알 수 없음'}"},
            ],
        },
    ]

    # 이메일 본문
    if snippet:
        display_snippet = snippet[:300] + ("..." if len(snippet) > 300 else "")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*이메일 내용*\n```{display_snippet}```"},
        })

    # 충돌 정보
    if conflict_text:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"⚠️ *충돌 일정*\n{conflict_text}"},
        })

    blocks.append({"type": "divider"})

    # 버튼
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✅ 등록", "emoji": True},
                "style": "primary",
                "action_id": "hitl_approve",
                "value": json.dumps({"email_id": email_id, "action": "approve"}),
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "❌ 무시", "emoji": True},
                "style": "danger",
                "action_id": "hitl_reject",
                "value": json.dumps({"email_id": email_id, "action": "reject"}),
            },
        ],
    })

    # fallback text (알림용)
    fallback = f"일정 확인: {title} ({datetime_str}) - 신뢰도 {confidence:.0%}"

    try:
        response = client.chat_postMessage(
            channel=channel,
            text=fallback,
            blocks=blocks,
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
