"""
이메일을 5가지 카테고리로 분류.

전체 시스템에서의 역할:
    event_detector가 "일정 메일인가 아닌가"만 판단했다면,
    email_classifier는 "어떤 종류의 메일인가"를 판단해 각각 다른 처리를 한다.

        classify_email(text)
            → "calendar"    : 일정/미팅 → LangGraph 파이프라인
            → "spam"        : 스팸      → Gmail 아카이브
            → "newsletter"  : 뉴스레터  → Gmail 라벨 붙이고 skip
            → "important"   : 중요      → Slack 알림 (HITL 아님)
            → "other"       : 기타      → skip

LLM 프롬프트:
    카테고리 5개를 명확히 정의해서 LLM이 하나만 선택하게 함.
    max_tokens=10으로 비용 최소화.

에러 처리:
    LLM 오류 또는 알 수 없는 카테고리 → "other" 반환 (안전 기본값).
    스팸 오탐보다 누락이 낫기 때문.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"calendar", "spam", "newsletter", "important", "other"}


def classify_email_llm(email_text: str) -> str:
    """LLM으로 이메일 카테고리를 분류.

    테스트에서 이 함수만 mock하면 LLM 호출 없이 classify_email 테스트 가능.

    Args:
        email_text: 이메일 본문 (앞 500자만 사용)

    Returns:
        "calendar" | "spam" | "newsletter" | "important" | "other"
    """
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=10)

    truncated = email_text[:500]
    response = llm.invoke(
        [
            {
                "role": "system",
                "content": (
                    "Classify the email into exactly one category. "
                    "Reply with only the category name.\n\n"
                    "Categories:\n"
                    "- calendar: meeting, appointment, event invitation, schedule request\n"
                    "- spam: unsolicited ads, phishing, lottery, suspicious offers\n"
                    "- newsletter: regular digest, blog updates, product news\n"
                    "- important: urgent issues, alerts, critical business matters\n"
                    "- other: everything else (receipts, thanks, casual conversation)\n\n"
                    "Reply with only one word: calendar, spam, newsletter, important, or other."
                ),
            },
            {"role": "user", "content": truncated},
        ]
    )

    return response.content.strip().lower()


def classify_email(email_text: str) -> str:
    """이메일을 카테고리로 분류.

    Args:
        email_text: 이메일 본문 텍스트

    Returns:
        "calendar" | "spam" | "newsletter" | "important" | "other"
        오류 또는 알 수 없는 카테고리 → "other"
    """
    try:
        category = classify_email_llm(email_text)
        if category not in VALID_CATEGORIES:
            logger.warning(f"Unknown category from LLM: '{category}', falling back to 'other'")
            return "other"
        return category
    except Exception as e:
        logger.warning(f"Email classification failed, defaulting to 'other': {e}")
        return "other"
