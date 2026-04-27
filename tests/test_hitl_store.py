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

    def test_reconnects_on_stale_connection(self):
        """conn.closed == False지만 서버가 연결을 끊은 경우(stale) 재연결해야 한다.

        Railway idle timeout 시나리오:
        - psycopg3에서 conn.closed는 클라이언트가 마지막으로 사용한 이후
          서버가 끊어도 0(False)으로 남는다.
        - 따라서 _ensure_conn()은 재연결하지 않고, 이후 쿼리가 OperationalError를 던진다.
        - insert() / lookup_by_slack_ts()는 OperationalError 시 재연결 후 재시도해야 한다.
        """
        class _FakeOperationalError(Exception):
            pass

        with patch("db.hitl_store.psycopg") as mock_psycopg:
            mock_psycopg.OperationalError = _FakeOperationalError

            # ── 초기 연결 (init 시 _setup_table 성공) ──
            init_cursor = MagicMock()
            first_conn = MagicMock()
            first_conn.closed = False
            first_conn.cursor.return_value.__enter__ = MagicMock(return_value=init_cursor)
            first_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            # ── idle timeout 후 재연결 (insert 재시도 성공) ──
            good_cursor = MagicMock()
            good_cursor.fetchone.return_value = None  # is_email_pending → False
            second_conn = MagicMock()
            second_conn.closed = False
            second_conn.cursor.return_value.__enter__ = MagicMock(return_value=good_cursor)
            second_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            mock_psycopg.connect.side_effect = [first_conn, second_conn]

            store = HitlStore(database_url="postgresql://test/db")
            assert store._use_postgres is True

            # 초기화 이후 idle timeout: 다음 쿼리(insert)에서 OperationalError 발생
            stale_cursor = MagicMock()
            stale_cursor.execute.side_effect = _FakeOperationalError("SSL connection closed")
            first_conn.cursor.return_value.__enter__ = MagicMock(return_value=stale_cursor)

            # insert() → stale connection → 재연결 후 재시도 → True
            result = store.insert("ts_stale", "thread-stale", "email_stale")

        assert result is True, "stale connection 후 insert가 성공해야 합니다"
        assert mock_psycopg.connect.call_count == 2, (
            "stale connection(conn.closed=False, OperationalError) 시 재연결이 없음 — "
            "_ensure_conn()만으로는 부족합니다. OperationalError 재시도 로직이 필요합니다."
        )

    def test_reconnects_when_connection_closed(self):
        """연결이 끊기면 자동으로 재연결 후 작업이 성공해야 한다.

        시나리오: Railway idle timeout → conn.closed == True
        → 다음 insert() 호출 시 재연결해야 함.
        """
        with patch("db.hitl_store.psycopg") as mock_psycopg:
            # 첫 번째 연결 (초기화용)
            first_conn = MagicMock()
            first_conn.closed = False
            first_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
            first_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            # 두 번째 연결 (재연결용)
            second_conn = MagicMock()
            second_conn.closed = False
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None  # is_email_pending → False
            second_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            second_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            mock_psycopg.connect.side_effect = [first_conn, second_conn]

            store = HitlStore(database_url="postgresql://test/db")
            assert store._use_postgres is True

            # idle timeout 시뮬레이션: 연결 끊김
            first_conn.closed = True

            # 재연결 없이 insert하면 실패해야 하는 상황 → _ensure_conn()이 있으면 성공
            store.insert("1234.5", "thread-1", "msg_reconnect")

        # psycopg.connect가 2회 호출되어야 함 (초기 1 + 재연결 1)
        assert mock_psycopg.connect.call_count == 2, (
            f"connect 호출 횟수: {mock_psycopg.connect.call_count}. "
            "연결 끊김 후 재연결을 시도하지 않습니다."
        )
