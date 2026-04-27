"""
Parser Agent 테스트.

Parser Agent의 역할:
    이메일 텍스트를 받아서 EventJSON으로 변환.
    LLM(GPT-4o)이 실제 파싱을 수행.
    compute_confidence()로 신뢰도를 계산해서 state에 저장.

    강의 ch2/03 Prompt Chaining과 동일한 패턴:
        raw_email → [LLM 파싱] → EventJSON → [confidence 계산] → state 업데이트

테스트 전략:
    LLM 호출을 mock. 이유:
    - 실제 API 호출 = 느리고 비용 발생
    - 응답이 비결정적 (같은 입력이어도 다른 출력)
    - mock하면 "LLM이 이런 결과를 반환했을 때" 시나리오를 정확히 테스트 가능

    실제 LLM 품질 테스트는 나중에 eval로 별도 수행.
"""

from datetime import datetime, date
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from graph.state import ScheduleState
from utils.models import EventJSON


class TestParserAgent:
    """Parser Agent 노드 테스트."""

    def test_parses_meeting_email(self):
        """회의 요청 이메일 → EventJSON + confidence.

        시나리오: "Can we meet Tuesday at 2pm?"
        LLM이 title, event_datetime을 파싱 → confidence ≥ 0.8 → auto_register 가능
        """
        from agents.parser import parse_email_node

        # LLM이 반환할 mock EventJSON
        mock_event = EventJSON(
            title="Meeting",
            event_datetime=datetime(2026, 3, 31, 14, 0),
            attendees=["kim@example.com"],
        )

        with patch("agents.parser.parse_with_llm", return_value=mock_event):
            state: ScheduleState = {
                "email_id": "msg_1",
                "raw_email": "Can we meet Tuesday at 2pm?",
                "subject": "Meeting request",
                "sender": "kim@example.com",
            }

            result = parse_email_node(state)

        assert result["parsed_event"] is not None
        assert result["parsed_event"].title == "Meeting"
        assert result["confidence"] >= 0.8  # 필수 필드 모두 있음

    def test_returns_none_for_non_event_email(self):
        """일정과 무관한 이메일 → parsed_event=None.

        시나리오: "Here's the report you asked for"
        LLM이 이벤트를 찾지 못함 → parsed_event=None → END
        """
        from agents.parser import parse_email_node

        with patch("agents.parser.parse_with_llm", return_value=None):
            state: ScheduleState = {
                "email_id": "msg_2",
                "raw_email": "Here's the quarterly report.",
                "subject": "Q1 Report",
                "sender": "lee@example.com",
            }

            result = parse_email_node(state)

        assert result["parsed_event"] is None
        assert result["confidence"] == 0.0

    def test_handles_partial_parse(self):
        """부분적으로만 파싱된 이메일 → low confidence.

        시나리오: "Let's meet sometime next week"
        title은 있지만 정확한 시간 없음 → confidence < 0.8 → HITL
        """
        from agents.parser import parse_email_node

        mock_event = EventJSON(
            title="Meeting",
            event_datetime=None,  # 시간 모름
        )

        with patch("agents.parser.parse_with_llm", return_value=mock_event):
            state: ScheduleState = {
                "email_id": "msg_3",
                "raw_email": "Let's meet sometime next week",
                "subject": "Meeting",
                "sender": "park@example.com",
            }

            result = parse_email_node(state)

        assert result["parsed_event"] is not None
        assert result["confidence"] < 0.8  # datetime 누락 → HITL

    def test_prompt_contains_current_date(self):
        """parse_with_llm 호출 시 시스템 프롬프트에 오늘 날짜가 포함되어야 한다.

        Re: Re: 이메일 스레드에서 과거 날짜(2024-05-08)를 잘못 추출하는 근본 원인:
        LLM이 '지금'이 언제인지 몰라서 이메일에 명시된 날짜를 그대로 쓴다.
        """
        from agents.parser import parse_with_llm

        captured_messages = []

        class MockStructuredLLM:
            def invoke(self, messages):
                captured_messages.extend(messages)
                return EventJSON(title="面接", event_datetime=datetime(2026, 5, 8, 14, 0))

        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = MockStructuredLLM()

        today = date.today().isoformat()

        with patch("langchain_openai.ChatOpenAI", return_value=mock_llm):
            parse_with_llm(
                raw_email="Re: 一次面接のご案内 (2024-05-08 14:00)",
                subject="Re: Re: 【Finatextグループ】次のステップのご案内",
            )

        system_messages = [m for m in captured_messages if m.get("role") == "system"]
        assert system_messages, "시스템 메시지가 없습니다"
        assert today in system_messages[0]["content"], (
            f"시스템 프롬프트에 오늘 날짜({today})가 없습니다"
        )

    def test_prompt_has_reply_chain_instruction(self):
        """프롬프트에 답장 스레드 처리 지시가 포함되어야 한다.

        Re: Re: 이메일의 인용된 과거 내용에서 날짜를 추출하지 않도록
        LLM에게 명시적으로 지시해야 한다.
        """
        from agents.parser import PARSER_SYSTEM_PROMPT

        lower = PARSER_SYSTEM_PROMPT.lower()
        has_reply_instruction = (
            "quoted" in lower
            or "reply" in lower
            or "thread" in lower
            or "re:" in lower
        )
        assert has_reply_instruction, (
            "프롬프트에 답장 스레드(quoted/reply/thread) 처리 지시가 없습니다"
        )

    def test_handles_llm_error(self):
        """LLM 호출 실패 → graceful degradation.

        API timeout, rate limit 등에서 시스템이 죽으면 안 됨.
        parsed_event=None, confidence=0.0 반환 → skip 처리됨.
        """
        from agents.parser import parse_email_node

        with patch("agents.parser.parse_with_llm", side_effect=Exception("API timeout")):
            state: ScheduleState = {
                "email_id": "msg_4",
                "raw_email": "Some email",
                "subject": "Test",
                "sender": "test@example.com",
            }

            result = parse_email_node(state)

        assert result["parsed_event"] is None
        assert result["confidence"] == 0.0
