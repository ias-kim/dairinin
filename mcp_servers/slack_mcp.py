"""
Slack MCP 서버 — HITL 메시지 전송.

Block Kit 버튼으로 ✅등록 / ❌무시 선택.
이메일 본문 snippet 포함.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("slack")


def format_datetime_kr(dt: datetime) -> str:
    """datetime을 한국어 형식으로 포맷. 예: '2026년 5월 8일 (금) 오후 2:00'"""
    WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
    weekday = WEEKDAYS[dt.weekday()]
    hour = dt.hour
    minute = dt.minute
    if hour >= 12:
        am_pm = "오후"
        h = hour - 12 if hour > 12 else hour
    else:
        am_pm = "오전"
        h = hour if hour > 0 else 12
    time_str = f"{am_pm} {h}:{minute:02d}"
    return f"{dt.year}년 {dt.month}월 {dt.day}일 ({weekday}) {time_str}"


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


def send_auto_register_notification(
    client,
    channel: str,
    parsed_event,
    sender: str = "",
) -> bool:
    """auto_register 완료 후 Slack으로 등록된 일정 상세 알림.

    Args:
        client: Slack WebClient
        channel: 전송할 채널 ID
        parsed_event: EventJSON (title, event_datetime, location 포함)
        sender: 이메일 발신자

    Returns:
        True: 성공, False: 실패
    """
    title = parsed_event.title or "제목 없음"
    dt = parsed_event.event_datetime
    dt_str = format_datetime_kr(dt) if dt else "일시 미정"
    location = parsed_event.location or ""

    fields = [
        {"type": "mrkdwn", "text": f"*제목*\n{title}"},
        {"type": "mrkdwn", "text": f"*일시*\n{dt_str}"},
    ]
    if location:
        fields.append({"type": "mrkdwn", "text": f"*장소/URL*\n{location}"})
    if sender:
        fields.append({"type": "mrkdwn", "text": f"*보낸 사람*\n{sender}"})

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "✅ 일정 등록 완료", "emoji": True},
        },
        {"type": "section", "fields": fields},
    ]

    fallback = f"✅ 일정 등록 완료: {title} ({dt_str})"

    try:
        client.chat_postMessage(
            channel=channel,
            text=fallback,
            blocks=blocks,
            unfurl_links=False,
        )
        logger.info(f"Auto-register notification sent: {title}")
        return True
    except Exception as e:
        logger.error(f"send_auto_register_notification failed: {e}")
        return False


def send_reply_notification(client, channel: str, subject: str, sender: str) -> bool:
    """메일 답장 완료 알림을 Slack에 전송.

    Args:
        client: Slack WebClient
        channel: 전송할 채널 ID
        subject: 답장한 이메일 제목
        sender: 답장 대상 이메일 주소

    Returns:
        True: 성공, False: 실패
    """
    text = f"✅ 메일 답장 완료\n*제목*: {subject}\n*수신자*: {sender}"

    try:
        client.chat_postMessage(
            channel=channel,
            text=text,
            unfurl_links=False,
        )
        logger.info(f"Reply notification sent: {subject} → {sender}")
        return True
    except Exception as e:
        logger.error(f"send_reply_notification failed: {e}")
        return False


# ──────────────────────────────────────────────
# FastMCP 툴
# ──────────────────────────────────────────────

@mcp.tool
def send_hitl(
    channel: str,
    title: str,
    datetime_str: str,
    confidence: float,
    conflicts: list[str],
    email_id: str,
    sender: str = "",
    snippet: str = "",
) -> Optional[dict]:
    """HITL 메시지를 Slack Block Kit 버튼과 함께 전송."""
    client = build_slack_client()
    return send_hitl_message(client, channel, title, datetime_str, confidence, conflicts, email_id, sender, snippet)


@mcp.tool
def send_reply_notification_tool(channel: str, subject: str, sender: str) -> bool:
    """메일 답장 완료 알림을 Slack에 전송."""
    client = build_slack_client()
    return send_reply_notification(client, channel, subject, sender)


if __name__ == "__main__":
    mcp.run()
