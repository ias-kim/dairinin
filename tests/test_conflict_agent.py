"""
Conflict Agent 테스트.

confidence + conflicts로 최종 action 결정.
"""

from datetime import datetime

from utils.models import EventJSON


class TestConflictAgent:

    def test_auto_register_high_confidence_no_conflict(self):
        """confidence ≥ 0.8 + 충돌 없음 → auto_register."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": EventJSON(title="치과", event_datetime=datetime(2026, 4, 1, 15, 0)),
            "confidence": 0.9,
            "conflicts": [],
        })

        assert result["action"] == "auto_register"

    def test_hitl_when_conflict_exists(self):
        """충돌 있으면 confidence 높아도 → hitl_required."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": EventJSON(title="미팅", event_datetime=datetime(2026, 4, 1, 14, 0)),
            "confidence": 0.95,
            "conflicts": [{"summary": "기존 미팅", "id": "evt_1"}],
        })

        assert result["action"] == "hitl_required"

    def test_hitl_when_low_confidence(self):
        """confidence < 0.8 → hitl_required (충돌 무관)."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": EventJSON(title="뭔가"),
            "confidence": 0.5,
            "conflicts": [],
        })

        assert result["action"] == "hitl_required"

    def test_skip_when_no_event(self):
        """parsed_event 없으면 → skip."""
        from agents.conflict import conflict_decision_node

        result = conflict_decision_node({
            "parsed_event": None,
            "confidence": 0.0,
            "conflicts": [],
        })

        assert result["action"] == "skip"
