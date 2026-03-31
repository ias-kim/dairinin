"""
event_detector 테스트.

has_schedule_keywords(): 정규식 기반 1차 필터
is_calendar_event_llm(): LLM 기반 2차 검증 (테스트에서 mock)
should_process_email(): 두 단계 조합 (공개 API)

테스트 시나리오:
  has_schedule_keywords:
    1. 날짜 패턴 포함 → True
    2. 시간 패턴 포함 → True
    3. 일정 키워드 포함 → True
    4. 아무 패턴 없음 → False
    5. 뉴스레터/광고 → False

  should_process_email:
    6. 키워드 없음 → False (LLM 호출 안 함)
    7. 키워드 있음 + LLM True → True
    8. 키워드 있음 + LLM False → False
    9. LLM 에러 → False (graceful degradation)
"""

from unittest.mock import MagicMock, patch

import pytest

from utils.event_detector import has_schedule_keywords, should_process_email


class TestHasScheduleKeywords:
    """has_schedule_keywords() — 정규식 기반 1차 필터."""

    # --- 1. 날짜 패턴 ---
    def test_date_korean_pattern(self):
        """'3월 31일' 같은 한국어 날짜 패턴 감지."""
        assert has_schedule_keywords("다음 주 3월 31일에 미팅이 있습니다") is True

    def test_date_slash_pattern(self):
        """'3/31' 슬래시 날짜 패턴 감지."""
        assert has_schedule_keywords("Meeting on 3/31 at the office") is True

    # --- 2. 시간 패턴 ---
    def test_time_24h_pattern(self):
        """'14:00' 같은 24시간 패턴 감지."""
        assert has_schedule_keywords("회의는 14:00에 시작합니다") is True

    def test_time_ampm_korean(self):
        """'오전 10시' 한국어 오전/오후 패턴 감지."""
        assert has_schedule_keywords("오후 3시에 통화 가능하신가요?") is True

    # --- 3. 일정 키워드 ---
    def test_schedule_keyword_korean(self):
        """'미팅', '회의' 같은 한국어 일정 키워드 감지."""
        assert has_schedule_keywords("다음 주 팀 미팅 일정을 잡고 싶습니다") is True

    def test_schedule_keyword_english(self):
        """'meeting', 'appointment' 같은 영어 일정 키워드 감지."""
        assert has_schedule_keywords("Can we schedule a meeting for next week?") is True

    # --- 4. 패턴 없음 ---
    def test_no_schedule_pattern(self):
        """일정 관련 패턴이 전혀 없는 일반 이메일."""
        assert has_schedule_keywords("안녕하세요. 프로젝트 진행 상황을 공유드립니다.") is False

    # --- 5. 뉴스레터/광고 ---
    def test_newsletter_no_pattern(self):
        """뉴스레터 형태의 이메일 — 일정 키워드 없음."""
        assert has_schedule_keywords("이번 달 신제품 소식을 전해드립니다. 많은 관심 부탁드립니다.") is False

    # --- 일본어 패턴 ---
    def test_japanese_date_pattern(self):
        """'3月31日' 같은 일본어 날짜 패턴 감지."""
        assert has_schedule_keywords("3月31日にミーティングがあります") is True

    def test_japanese_time_pattern(self):
        """'午後3時' 같은 일본어 시간 패턴 감지."""
        assert has_schedule_keywords("午後3時に打ち合わせはいかがでしょうか？") is True

    def test_japanese_keyword(self):
        """'アポ', '会議' 같은 일본어 일정 키워드 감지."""
        assert has_schedule_keywords("来週アポを入れたいのですが") is True


class TestShouldProcessEmail:
    """should_process_email() — 2단계 필터 조합."""

    # --- 6. 키워드 없음 → LLM 호출 안 함 ---
    def test_no_keywords_skips_llm(self):
        """키워드 필터 실패 시 LLM을 호출하지 않고 바로 False."""
        with patch("utils.event_detector.is_calendar_event_llm") as mock_llm:
            result = should_process_email("그냥 안부 인사 이메일입니다")
            assert result is False
            mock_llm.assert_not_called()

    # --- 7. 키워드 있음 + LLM True → True ---
    def test_keywords_match_llm_true(self):
        """키워드 통과 + LLM이 일정 있다고 판단 → True."""
        with patch("utils.event_detector.is_calendar_event_llm", return_value=True):
            result = should_process_email("내일 오후 2시에 미팅 어떠세요?")
            assert result is True

    # --- 8. 키워드 있음 + LLM False → False ---
    def test_keywords_match_llm_false(self):
        """키워드 통과했지만 LLM이 일정 아니라고 판단 → False.
        예: '지난 회의 결과를 공유합니다' — 과거 이벤트 언급.
        """
        with patch("utils.event_detector.is_calendar_event_llm", return_value=False):
            result = should_process_email("지난 14:00 회의 결과 공유드립니다")
            assert result is False

    # --- 9. LLM 에러 → False (graceful degradation) ---
    def test_llm_error_returns_false(self):
        """LLM 호출 실패 시 스팸 방지를 위해 False 반환."""
        with patch(
            "utils.event_detector.is_calendar_event_llm",
            side_effect=Exception("API error"),
        ):
            result = should_process_email("내일 오전 10시 미팅 확인 부탁드립니다")
            assert result is False
