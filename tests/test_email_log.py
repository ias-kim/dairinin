"""
EmailLogStore 테스트.
"""

import pytest
from unittest.mock import MagicMock, patch

from db.email_log import EmailLogStore


@pytest.fixture(autouse=True)
def no_db_env(monkeypatch):
    """DATABASE_URL 제거 → in-memory 모드 강제."""
    monkeypatch.delenv("DATABASE_URL", raising=False)


class TestEmailLogStore:

    def test_log_and_list(self):
        store = EmailLogStore()
        store.log("msg_1", subject="Meeting", sender="a@a.com", category="calendar", action="auto_register")
        logs = store.list_logs()
        assert len(logs) == 1
        assert logs[0]["email_id"] == "msg_1"

    def test_get_stats(self):
        store = EmailLogStore()
        store.log("msg_1", action="auto_register")
        store.log("msg_2", action="skip")
        stats = store.get_stats()
        assert stats["total"] == 2
        assert stats["by_action"]["auto_register"] == 1
        assert stats["by_action"]["skip"] == 1


class TestEmailLogStoreIsProcessed:
    """is_processed() — 재시작 내구성 핵심 메서드."""

    def test_returns_false_for_unknown_email(self):
        """로그에 없는 이메일 → False."""
        store = EmailLogStore()
        assert store.is_processed("unknown_email_id") is False

    def test_returns_true_after_log(self):
        """log() 후 → True."""
        store = EmailLogStore()
        store.log("msg_dup", action="auto_register")
        assert store.is_processed("msg_dup") is True

    def test_not_affected_by_other_emails(self):
        """다른 이메일 log해도 미처리 이메일은 False."""
        store = EmailLogStore()
        store.log("msg_a", action="skip")
        assert store.is_processed("msg_b") is False


class TestEmailLogStorePostgres:
    """PostgreSQL 모드 테스트 — DB 없이 mock으로."""

    def test_fallback_to_memory_on_connect_error(self):
        """DB 연결 실패 → 인메모리 fallback."""
        with patch("db.email_log.psycopg") as mock_psycopg:
            mock_psycopg.connect.side_effect = Exception("connection refused")

            store = EmailLogStore(database_url="postgresql://test/db")
            assert store._use_postgres is False

            store.log("msg_1", action="auto_register")
            assert store.is_processed("msg_1") is True

    def test_reconnects_when_connection_closed(self):
        """연결이 끊기면 자동으로 재연결 후 log()가 성공해야 한다.

        시나리오: Railway idle timeout → conn.closed == True
        """
        with patch("db.email_log.psycopg") as mock_psycopg:
            first_conn = MagicMock()
            first_conn.closed = False
            first_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
            first_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            second_conn = MagicMock()
            second_conn.closed = False
            second_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
            second_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            mock_psycopg.connect.side_effect = [first_conn, second_conn]

            store = EmailLogStore(database_url="postgresql://test/db")
            assert store._use_postgres is True

            # idle timeout 시뮬레이션
            first_conn.closed = True

            store.log("msg_reconnect", action="auto_register")

        assert mock_psycopg.connect.call_count == 2, (
            f"connect 호출 횟수: {mock_psycopg.connect.call_count}. "
            "연결 끊김 후 재연결을 시도하지 않습니다."
        )
