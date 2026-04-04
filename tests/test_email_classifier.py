"""tests/test_email_classifier.py — email_classifier TDD (RED → GREEN)."""

from unittest.mock import patch

import pytest


class TestEmailClassifier:
    """classify_email() 분류 테스트."""

    def test_classify_calendar(self):
        """일정 관련 메일 → 'calendar'."""
        from utils.email_classifier import classify_email

        with patch("utils.email_classifier.classify_email_llm", return_value="calendar"):
            result = classify_email("3월 31일 14:00 미팅 참석 부탁드립니다")

        assert result == "calendar"

    def test_classify_spam(self):
        """스팸 메일 → 'spam'."""
        from utils.email_classifier import classify_email

        with patch("utils.email_classifier.classify_email_llm", return_value="spam"):
            result = classify_email("당신이 당첨되었습니다! 지금 바로 클릭하세요")

        assert result == "spam"

    def test_classify_newsletter(self):
        """뉴스레터 → 'newsletter'."""
        from utils.email_classifier import classify_email

        with patch("utils.email_classifier.classify_email_llm", return_value="newsletter"):
            result = classify_email("주간 기술 뉴스레터 vol.42")

        assert result == "newsletter"

    def test_classify_important(self):
        """중요 메일 → 'important'."""
        from utils.email_classifier import classify_email

        with patch("utils.email_classifier.classify_email_llm", return_value="important"):
            result = classify_email("긴급: 서버 장애 발생 즉시 확인 바랍니다")

        assert result == "important"

    def test_classify_other(self):
        """기타 메일 → 'other'."""
        from utils.email_classifier import classify_email

        with patch("utils.email_classifier.classify_email_llm", return_value="other"):
            result = classify_email("감사합니다. 잘 받았습니다.")

        assert result == "other"

    def test_classify_llm_error_returns_other(self):
        """LLM 오류 시 'other' 반환 (안전 기본값)."""
        from utils.email_classifier import classify_email

        with patch("utils.email_classifier.classify_email_llm", side_effect=Exception("API error")):
            result = classify_email("어떤 메일")

        assert result == "other"

    def test_classify_empty_email_returns_other(self):
        """빈 메일 → 'other'."""
        from utils.email_classifier import classify_email

        with patch("utils.email_classifier.classify_email_llm", return_value="other"):
            result = classify_email("")

        assert result == "other"

    def test_valid_categories(self):
        """LLM이 알 수 없는 카테고리 반환 시 'other'로 폴백."""
        from utils.email_classifier import classify_email

        with patch("utils.email_classifier.classify_email_llm", return_value="unknown_category"):
            result = classify_email("무슨 메일")

        assert result == "other"
