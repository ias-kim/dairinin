"""
HITL Store 테스트.
"""

import pytest
from unittest.mock import MagicMock, patch

from db.hitl_store import HitlStore
import agents.notifier as notifier_module


@pytest.fixture(autouse=True)
def reset_singletons(monkeypatch):
    """각 테스트: DATABASE_URL 제거 + notifier 싱글톤 초기화."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    notifier_module._hitl_store = None
    notifier_module._memory_store = None
    yield
    notifier_module._hitl_store = None
    notifier_module._memory_store = None


class TestHitlStore:

    def test_insert_and_lookup(self):
        store = HitlStore()
        store.insert("1234.5678", "thread-1", "msg_1")

        result = store.lookup_by_slack_ts("1234.5678")
        assert result["thread_id"] == "thread-1"
        assert result["email_id"] == "msg_1"

    def test_duplicate_email_rejected(self):
        store = HitlStore()
        assert store.insert("1111", "thread-1", "msg_1") is True
        assert store.insert("2222", "thread-2", "msg_1") is False  # 같은 email_id

    def test_is_email_pending(self):
        store = HitlStore()
        store.insert("1111", "thread-1", "msg_1")

        assert store.is_email_pending("msg_1") is True
        assert store.is_email_pending("msg_2") is False

    def test_remove(self):
        store = HitlStore()
        store.insert("1111", "thread-1", "msg_1")
        store.remove("1111")

        assert store.lookup_by_slack_ts("1111") is None
        assert store.is_email_pending("msg_1") is False

    def test_cleanup_expired(self):
        store = HitlStore()
        store.insert("1111", "thread-1", "msg_1")

        # 0시간 TTL → 즉시 만료
        expired = store.cleanup_expired(ttl_hours=0)
        assert expired == 1
        assert store.lookup_by_slack_ts("1111") is None


class TestHitlStorePostgres:
    """PostgreSQL 모드 테스트 — DB 없이 mock으로."""

    def test_uses_postgres_when_database_url_set(self):
        """DATABASE_URL 있으면 PostgreSQL 모드로 초기화."""
        with patch("db.hitl_store.psycopg") as mock_psycopg:
            mock_conn = MagicMock()
            mock_psycopg.connect.return_value = mock_conn

            store = HitlStore(database_url="postgresql://test/db")
            assert store._use_postgres is True

    def test_insert_calls_postgres(self):
        """insert() → SQL INSERT 호출."""
        with patch("db.hitl_store.psycopg") as mock_psycopg:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_psycopg.connect.return_value = mock_conn

            store = HitlStore(database_url="postgresql://test/db")
            store.insert("1234.5", "thread-1", "msg_1")

            mock_cursor.execute.assert_called()

    def test_fallback_to_memory_on_connect_error(self):
        """DB 연결 실패 → 인메모리 fallback."""
        with patch("db.hitl_store.psycopg") as mock_psycopg:
            mock_psycopg.connect.side_effect = Exception("connection refused")

            store = HitlStore(database_url="postgresql://test/db")
            assert store._use_postgres is False

            # 인메모리로 정상 동작
            store.insert("1111", "thread-1", "msg_1")
            assert store.lookup_by_slack_ts("1111")["thread_id"] == "thread-1"
