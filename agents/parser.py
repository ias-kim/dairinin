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
from datetime import date
from typing import Optional

from graph.state import ScheduleState
from utils.confidence import compute_confidence
from utils.models import EventJSON

logger = logging.getLogger(__name__)

PARSER_SYSTEM_PROMPT = """You are an email parser that extracts calendar event information.

Current date: {current_date}

Given an email, extract the following if present:
- title: event name or meeting topic
- event_datetime: date and time (ISO 8601 format)
- attendees: list of email addresses or names
- location: video conference URL (Google Meet, Zoom, Teams link) or physical address. Prefer the video URL if both exist.
- duration: duration in minutes
- description: key details the attendee needs — participants/interviewers names, agenda, emergency contact, any special instructions. Do NOT include the date/time (already in event_datetime).

If the email does NOT contain a calendar event or meeting request, return null for all fields.

Important date parsing rules:
- For dates without explicit year (e.g., "4月17日", "April 17"), assume the current year ({current_year})
- For relative dates like "next Tuesday", calculate from the current date ({current_date})
- If time is ambiguous, leave event_datetime as null
- Only extract events the sender is proposing/requesting, not past events being referenced
- For reply-chain emails (Re: or Fwd: in subject): only extract dates from the NEWEST message at the top. Ignore quoted/historical thread content that appears after "On ... wrote:", "-----Original Message-----", or similar reply markers.
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

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
    )

    structured_llm = llm.with_structured_output(EventJSON)

    today = date.today()
    system_prompt = PARSER_SYSTEM_PROMPT.format(
        current_date=today.isoformat(),
        current_year=today.year,
    )

    logger.info(f"Parser input: subject='{subject}', sender='{sender}'")
    logger.debug(f"Parser raw_email: {raw_email[:200]}")

    user_message = f"""From: {sender}
Subject: {subject}

{raw_email}"""

    try:
        result = structured_llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )
        logger.info(
            f"Parser result: title='{result.title if result else None}', "
            f"datetime='{result.event_datetime if result else None}'"
        )
    except Exception as e:
        logger.error(f"Parser LLM call failed: {e}", exc_info=True)
        return None

    if result and (result.title or result.event_datetime):
        return result

    return None


def parse_email_node(state: ScheduleState) -> dict:
    """LangGraph 노드: 이메일 파싱 + confidence 계산.

    1. state에서 raw_email 읽기
    2. parse_with_llm() → EventJSON 또는 None
    3. compute_confidence() → float
    4. state 업데이트 반환
    """
    try:
        parsed_event = parse_with_llm(
            raw_email=state.get("raw_email", ""),
            subject=state.get("subject", ""),
            sender=state.get("sender", ""),
        )
    except Exception as e:
        logger.error(f"Parser failed for {state.get('email_id')}: {e}")
        parsed_event = None

    if parsed_event:
        confidence = compute_confidence(parsed_event)
    else:
        confidence = 0.0

    return {
        "parsed_event": parsed_event,
        "confidence": confidence,
    }
