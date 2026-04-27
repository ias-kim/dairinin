"""
Parser Agent — 이메일을 EventJSON으로 변환.

LangGraph StateGraph의 첫 번째 노드.
gmail-mcp가 가져온 이메일을 LLM으로 파싱해서 구조화된 이벤트 데이터로 변환.

함수 구조:
    parse_with_llm(raw_email, subject, sender)
        → LLM 호출 → EventJSON 반환 (테스트 시 mock 대상)

    parse_email_node(state: ScheduleState)
        → LangGraph 노드 함수
        → parse_with_llm 호출 → confidence 계산 → state 업데이트
"""

from __future__ import annotations

import logging
from typing import Optional

from graph.state import ScheduleState
from utils.confidence import compute_confidence
from utils.models import EventJSON

logger = logging.getLogger(__name__)

PARSER_SYSTEM_PROMPT = """You are an email parser that extracts calendar event information.

Given an email, extract the following if present:
- title: event name or meeting topic
- event_datetime: date and time (ISO 8601 format)
- attendees: list of email addresses or names
- location: meeting place
- duration: duration in minutes
- description: brief description

If the email does NOT contain a calendar event or meeting request, return null for all fields.

Important:
- For relative dates like "next Tuesday", use the current date as reference.
- If time is ambiguous, leave event_datetime as null.
- Only extract events the sender is proposing/requesting, not past events being referenced.
"""


def parse_with_llm(
    raw_email: str,
    subject: str = "",
    sender: str = "",
) -> Optional[EventJSON]:
    """LLM으로 이메일을 파싱해서 EventJSON 반환.

    테스트에서 이 함수만 mock하면 LLM 호출 없이
    parse_email_node의 로직을 테스트할 수 있음.
    """
    from langchain_openai import ChatOpenAI

    logger.info(f"Parser input: subject='{subject}', sender='{sender}'")
    logger.debug(f"Parser raw_email snippet: {raw_email[:200]}...")  # First 200 chars

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
    )

    structured_llm = llm.with_structured_output(EventJSON)

    user_message = f"""From: {sender}
Subject: {subject}

{raw_email}"""

    logger.debug(f"Parser user_message: {user_message[:300]}...")  # First 300 chars

    try:
        result = structured_llm.invoke(
            [
                {"role": "system", "content": PARSER_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ]
        )

        logger.info(f"Parser result: title='{result.title if result else None}', datetime='{result.event_datetime if result else None}'")

        if result and (result.title or result.event_datetime):
            return result

        return None
    except Exception as e:
        logger.error(f"Parser LLM call failed: {e}", exc_info=True)
        return None


def parse_email_node(state: ScheduleState) -> dict:
    """LangGraph 노드: 이메일 파싱 + confidence 계산.

    1. state에서 raw_email 읽기
    2. parse_with_llm() → EventJSON 또는 None
    3. compute_confidence() → float
    4. state 업데이트 반환
    """
    email_id = state.get("email_id", "unknown")

    try:
        parsed_event = parse_with_llm(
            raw_email=state.get("raw_email", ""),
            subject=state.get("subject", ""),
            sender=state.get("sender", ""),
        )
    except Exception as e:
        logger.error(f"Parser failed for {email_id}: {e}")
        parsed_event = None

    if parsed_event:
        confidence = compute_confidence(parsed_event)
        logger.info(f"Parser node result for {email_id}: title='{parsed_event.title}', datetime='{parsed_event.event_datetime}', confidence={confidence}")
    else:
        confidence = 0.0
        logger.info(f"Parser node result for {email_id}: no event parsed")

    return {
        "parsed_event": parsed_event,
        "confidence": confidence,
    }
