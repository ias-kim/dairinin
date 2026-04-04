"""
Conflict Agent 테스트.

confidence + conflicts로 최종 action 결정.
"""

from datetime import datetime, timedelta, timezone

from utils.models import EventJSON

_FUTURE = datetime.now(timezone.utc) + timedelta(days=7)


class TestConflictAgent:

    def test_auto_register_high_confidence_no_conflict(self):
        """confidence ≥ 0.8 + 충돌 없음 → auto_register."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": EventJSON(title="치과", event_datetime=_FUTURE),
            "confidence": 0.9,
            "conflicts": [],
        })

        assert result["action"] == "auto_register"

    def test_hitl_when_conflict_exists(self):
        """충돌 있으면 confidence 높아도 → hitl_required."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": EventJSON(title="미팅", event_datetime=_FUTURE),
            "confidence": 0.95,
            "conflicts": [{"summary": "기존 미팅", "id": "evt_1"}],
        })

        assert result["action"] == "hitl_required"

    def test_hitl_when_low_confidence(self):
        """confidence < 0.8 → hitl_required (충돌 무관)."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": EventJSON(title="뭔가", event_datetime=_FUTURE),
            "confidence": 0.5,
            "conflicts": [],
        })

        assert result["action"] == "hitl_required"

    def test_skip_when_no_event_datetime(self):
        """event_datetime 없으면 → skip (Slack 올려봤자 등록 불가)."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": EventJSON(title="설명회 안내"),
            "confidence": 0.7,
            "conflicts": [],
        })

        assert result["action"] == "skip"

    def test_skip_when_no_event(self):
        """parsed_event 없으면 → skip."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": None,
            "confidence": 0.0,
            "conflicts": [],
        })

        assert result["action"] == "skip"

    def test_skip_when_past_event(self):
        """event_datetime이 과거 → skip (캘린더 등록 불필요)."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": EventJSON(
                title="지난 미팅",
                event_datetime=datetime(2024, 3, 16, 15, 0, tzinfo=timezone.utc),
            ),
            "confidence": 0.95,
            "conflicts": [],
        })

        assert result["action"] == "skip"

    def test_skip_when_past_event_naive_datetime(self):
        """timezone 없는 과거 datetime도 → skip."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": EventJSON(
                title="옛날 설명회",
                event_datetime=datetime(2024, 1, 1, 10, 0),
            ),
            "confidence": 0.9,
            "conflicts": [],
        })

        assert result["action"] == "skip"
