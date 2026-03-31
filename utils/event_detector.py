"""
이메일에 캘린더 일정이 있는지 판단한다.

전체 시스템에서의 역할:
    Parser Agent 앞단에서 실행되는 2단계 사전 필터.
    일정 없는 이메일을 조기에 걸러내 Slack 스팸과 불필요한 LLM 비용을 방지.

        should_process_email(email_text)
            → True  : 일정 있음 → Parser Agent로 전달
            → False : 일정 없음 → mark_read 후 무시

필터 구조:
    1단계: has_schedule_keywords() — 정규식 기반, 비용 0, ~0ms
    2단계: is_calendar_event_llm() — LLM yes/no, 저비용, ~500ms
    두 단계 모두 통과해야 True.
    1단계 실패 시 2단계 호출 안 함.

왜 2단계인가:
    키워드만으로는 "지난 14:00 회의 결과 공유" 같은 과거 이벤트 언급을 걸러내지 못함.
    LLM만으로는 모든 이메일에 API 비용이 발생함.
    조합하면 비용 최소화 + 정확도 확보.
"""

import logging
import re

logger = logging.getLogger(__name__)

# 날짜/시간 정규식 패턴
_DATE_PATTERNS = [
    r"\d{1,2}월\s*\d{1,2}일",   # 3월 31일 (한국어)
    r"\d{1,2}/\d{1,2}",         # 3/31
    r"\d{4}-\d{2}-\d{2}",       # 2026-03-31
    r"\d{1,2}:\d{2}",           # 14:00, 3:30
    r"(오전|오후)\s*\d{1,2}시",  # 오전 10시, 오후 3시 (한국어)
    r"\d{1,2}月\s*\d{1,2}日",   # 3月31日 (일본어)
    r"(午前|午後)\s*\d{1,2}時",  # 午前10時, 午後3時 (일본어)
]

# 일정 관련 키워드
_SCHEDULE_KEYWORDS = [
    # 한국어
    "미팅", "회의", "면접", "약속", "일정", "스케줄", "예약", "참석",
    # 일본어
    "ミーティング", "打ち合わせ", "面接", "予約", "スケジュール",
    "日程", "参加", "アポ", "会議",
    # 영어
    "meeting", "appointment", "schedule", "interview", "call at",
    "invite", "calendar", "join us",
]

_DATE_REGEX = re.compile("|".join(_DATE_PATTERNS), re.IGNORECASE)
_KEYWORD_REGEX = re.compile("|".join(_SCHEDULE_KEYWORDS), re.IGNORECASE)


def has_schedule_keywords(email_text: str) -> bool:
    """이메일 본문에 날짜/시간 패턴 또는 일정 키워드가 있는지 확인.

    정규식 기반이므로 LLM 호출 없이 즉시 실행.
    False이면 should_process_email()이 LLM 호출을 건너뜀.

    Args:
        email_text: 이메일 본문 텍스트 (한국어/일본어/영어)

    Returns:
        날짜/시간 패턴 또는 일정 키워드가 있으면 True
    """
    return bool(_DATE_REGEX.search(email_text) or _KEYWORD_REGEX.search(email_text))


def is_calendar_event_llm(email_text: str) -> bool:
    """LLM으로 이메일이 실제 캘린더 일정 요청인지 판단.

    gpt-4o-mini를 사용해 yes/no만 판단 — 저비용, 빠름.
    과거 이벤트 언급, 뉴스레터 등 키워드 기반 오탐을 걸러냄.

    테스트에서 이 함수만 mock하면 LLM 호출 없이 should_process_email 테스트 가능.

    Args:
        email_text: 이메일 본문 텍스트 (앞 500자만 사용 — 비용 절감)

    Returns:
        미래 일정 요청이면 True, 아니면 False
    """
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=5)

    truncated = email_text[:500]
    response = llm.invoke(
        [
            {
                "role": "system",
                "content": (
                    "You are a classifier. "
                    "Answer only 'yes' or 'no'. "
                    "Does this email contain a request to schedule a future meeting or appointment?"
                ),
            },
            {"role": "user", "content": truncated},
        ]
    )

    answer = response.content.strip().lower()
    return answer.startswith("yes")


def should_process_email(email_text: str) -> bool:
    """이메일을 Parser Agent로 넘길지 결정하는 2단계 필터.

    1단계 (키워드): 빠른 사전 필터 — 실패 시 즉시 False
    2단계 (LLM):   정확한 의도 판단 — 오탐 제거

    에러 발생 시 False 반환 (스팸 방지 우선).

    Args:
        email_text: 이메일 본문 텍스트 (한국어/일본어/영어)

    Returns:
        Parser Agent로 넘겨야 하면 True, 무시해도 되면 False
    """
    if not has_schedule_keywords(email_text):
        return False

    try:
        return is_calendar_event_llm(email_text)
    except Exception as e:
        logger.warning(f"LLM event detection failed, skipping email: {e}")
        return False
