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


class TestConflictAgentMemoryThreshold:
    """mem0 패턴 학습 기반 threshold 동적 조정 테스트."""

    def test_auto_register_when_frequent_pattern(self):
        """패턴 10회 이상 → threshold 0.6, confidence=0.7 → auto_register."""
        from unittest.mock import MagicMock, patch
        from agents.conflict import conflict_decision_node

        mock_store = MagicMock()
        mock_store.get_pattern_count.return_value = 10

        with patch("agents.conflict.get_memory_store", return_value=mock_store):
            result = conflict_decision_node({
                "parsed_event": EventJSON(title="팀미팅", event_datetime=_FUTURE),
                "confidence": 0.7,
                "conflicts": [],
            })

        assert result["action"] == "auto_register"

    def test_hitl_when_few_patterns(self):
        """패턴 9회 이하 → threshold 0.8 유지, confidence=0.7 → hitl_required."""
        from unittest.mock import MagicMock, patch
        from agents.conflict import conflict_decision_node

        mock_store = MagicMock()
        mock_store.get_pattern_count.return_value = 9

        with patch("agents.conflict.get_memory_store", return_value=mock_store):
            result = conflict_decision_node({
                "parsed_event": EventJSON(title="팀미팅", event_datetime=_FUTURE),
                "confidence": 0.7,
                "conflicts": [],
            })

        assert result["action"] == "hitl_required"

    def test_default_threshold_when_mem0_fails(self):
        """mem0 예외 발생 시 기본 threshold 0.8 사용 → confidence=0.7 → hitl_required."""
        from unittest.mock import MagicMock, patch
        from agents.conflict import conflict_decision_node

        mock_store = MagicMock()
        mock_store.get_pattern_count.side_effect = Exception("mem0 unavailable")

        with patch("agents.conflict.get_memory_store", return_value=mock_store):
            result = conflict_decision_node({
                "parsed_event": EventJSON(title="팀미팅", event_datetime=_FUTURE),
                "confidence": 0.7,
                "conflicts": [],
            })

        assert result["action"] == "hitl_required"

    def test_frequent_pattern_still_hitl_when_conflict_exists(self):
        """패턴 많아도 충돌 있으면 hitl_required."""
        from unittest.mock import MagicMock, patch
        from agents.conflict import conflict_decision_node

        mock_store = MagicMock()
        mock_store.get_pattern_count.return_value = 20

        with patch("agents.conflict.get_memory_store", return_value=mock_store):
            result = conflict_decision_node({
                "parsed_event": EventJSON(title="팀미팅", event_datetime=_FUTURE),
                "confidence": 0.9,
                "conflicts": [{"summary": "기존 일정", "id": "evt_1"}],
            })

        assert result["action"] == "hitl_required"

    def test_boundary_at_learned_threshold_is_auto_register(self):
        """confidence 정확히 0.6 + count≥10 → auto_register (threshold 이상)."""
        from unittest.mock import MagicMock, patch
        from agents.conflict import conflict_decision_node

        mock_store = MagicMock()
        mock_store.get_pattern_count.return_value = 10

        with patch("agents.conflict.get_memory_store", return_value=mock_store):
            result = conflict_decision_node({
                "parsed_event": EventJSON(title="팀미팅", event_datetime=_FUTURE),
                "confidence": 0.6,
                "conflicts": [],
            })

        assert result["action"] == "auto_register"

    def test_boundary_below_learned_threshold_is_hitl(self):
        """confidence 0.59 + count≥10 → hitl_required (threshold 미만)."""
        from unittest.mock import MagicMock, patch
        from agents.conflict import conflict_decision_node

        mock_store = MagicMock()
        mock_store.get_pattern_count.return_value = 10

        with patch("agents.conflict.get_memory_store", return_value=mock_store):
            result = conflict_decision_node({
                "parsed_event": EventJSON(title="팀미팅", event_datetime=_FUTURE),
                "confidence": 0.59,
                "conflicts": [],
            })

        assert result["action"] == "hitl_required"
