"""
compute_confidence() 테스트.

이 함수는 전체 시스템의 분기점:
  ≥ 0.8 → auto_register (자동 캘린더 등록)
  < 0.8 → hitl_required (Slack으로 사람에게 확인)

REQUIRED_FIELDS: title, datetime (없으면 큰 감점)
OPTIONAL_FIELDS: attendees, location, duration, description (없으면 작은 감점)

테스트 시나리오:
  1. 모든 필드 있음 → 1.0
  2. 필수만 있음 → 0.8 (optional 4개 누락: 1.0 - 0.2)
  3. 필수 1개 누락 → 0.7 이하
  4. 필수 모두 누락 → 0.4 이하
  5. 빈 문자열은 "없음"과 동일 (Pydantic 버그 방지)
  6. 빈 리스트도 "없음"과 동일
  7. 솔로 이벤트 (attendees 없음) → 여전히 ≥ 0.8 가능
  8. 최소값 0.0 아래로 안 내려감
"""

from datetime import datetime

from utils.confidence import compute_confidence
from utils.models import EventJSON


class TestComputeConfidence:
    """compute_confidence() 단위 테스트."""

    # --- 1. Happy path: 모든 필드 있음 ---
    def test_all_fields_present_returns_1(self):
        """필수 + 선택 필드 모두 채워지면 완벽한 신뢰도."""
        event = EventJSON(
            title="팀 미팅",
            event_datetime=datetime(2026, 3, 29, 14, 0),
            attendees=["kim@example.com"],
            location="회의실 A",
            duration=60,
            description="주간 회의",
        )
        assert compute_confidence(event) == 1.0

    # --- 2. 필수만 있음 ---
    def test_required_only_returns_08(self):
        """필수 필드만 있으면 0.8.
        optional 4개 누락 → 0.2 * (4/4) = 0.2 감점 → 1.0 - 0.2 = 0.8.
        이 점수가 auto_register 경계값(threshold).
        """
        event = EventJSON(
            title="치과 예약",
            event_datetime=datetime(2026, 3, 30, 10, 0),
        )
        assert compute_confidence(event) == 0.8

    # --- 3. 필수 1개 누락 (datetime 없음) ---
    def test_missing_one_required_below_threshold(self):
        """datetime 누락 → 큰 감점. HITL 트리거됨.
        required_penalty = 0.6 * (1/2) = 0.3
        optional_penalty = 0.2 * (4/4) = 0.2
        score = 1.0 - 0.3 - 0.2 = 0.5
        """
        event = EventJSON(title="뭔가 약속")
        assert compute_confidence(event) == 0.5

    # --- 4. 필수 모두 누락 ---
    def test_missing_all_required(self):
        """title도 datetime도 없음. 최저 수준 신뢰도.
        required_penalty = 0.6 * (2/2) = 0.6
        optional_penalty = 0.2 * (4/4) = 0.2
        score = 1.0 - 0.6 - 0.2 = 0.2
        """
        event = EventJSON()
        assert compute_confidence(event) == 0.2

    # --- 5. 빈 문자열은 "없음"과 동일 (eng review issue #4) ---
    def test_empty_string_treated_as_missing(self):
        """Pydantic 모델에서 title=""은 None이 아님.
        하지만 빈 문자열은 실질적으로 파싱 실패.
        bool("") == False이므로 missing으로 처리해야 함.
        """
        event = EventJSON(
            title="",
            event_datetime=datetime(2026, 3, 29, 14, 0),
        )
        # title이 빈 문자열 → missing 취급
        # required_penalty = 0.6 * (1/2) = 0.3
        # optional_penalty = 0.2 * (4/4) = 0.2
        # score = 0.5
        assert compute_confidence(event) == 0.5

    # --- 6. 빈 리스트도 "없음"과 동일 ---
    def test_empty_list_treated_as_missing(self):
        """attendees=[] 는 실질적으로 참석자 없음.
        bool([]) == False이므로 missing으로 처리.
        """
        event = EventJSON(
            title="미팅",
            event_datetime=datetime(2026, 3, 29, 14, 0),
            attendees=[],  # 빈 리스트
        )
        # required 모두 있음 → 0
        # optional: attendees=[], location=None, duration=None, description=None → 4개 누락
        # score = 1.0 - 0.2 = 0.8
        assert compute_confidence(event) == 0.8

    # --- 7. 솔로 이벤트 (eng review: attendees는 optional) ---
    def test_solo_event_can_auto_register(self):
        """치과 예약, 혼자 하는 일정 — attendees 없어도 auto_register 가능.
        attendees가 REQUIRED였다면 여기서 0.7 나와서 HITL 트리거됨.
        OPTIONAL이므로 0.8 유지 → auto_register.
        """
        event = EventJSON(
            title="치과",
            event_datetime=datetime(2026, 4, 1, 15, 0),
            location="서울치과의원",
        )
        # required 모두 있음 → 0
        # optional: attendees=None, duration=None, description=None → 3개 누락
        # optional_penalty = 0.2 * (3/4) = 0.15
        # score = 1.0 - 0.15 = 0.85
        assert compute_confidence(event) == 0.85

    # --- 8. 바닥값 테스트 ---
    def test_floor_at_zero(self):
        """score가 0.0 아래로 내려가면 안 됨.
        max(0.0, ...) 로 보장.
        """
        event = EventJSON()  # 모든 필드 없음
        score = compute_confidence(event)
        assert score >= 0.0
