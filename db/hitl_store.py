"""
HITL 매핑 저장소.

Slack message_ts ↔ LangGraph thread_id ↔ email_id 매핑.
Slack webhook에서 reaction 오면 thread_id로 그래프를 resume.

두 가지 모드:
    - 인메모리 (기본): 테스트/개발용
    - PostgreSQL (database_url 전달 또는 DATABASE_URL 환경변수): 영속 저장
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

try:
    import psycopg
except ImportError:
    psycopg = None  # type: ignore


class HitlStore:
    """hitl_pending 매핑 관리."""

    def __init__(self, database_url: str | None = None):
        url = database_url or os.getenv("DATABASE_URL")

        self._use_postgres: bool = False
        self._url: Optional[str] = url
        self._conn: Optional[Any] = None
        self._store: dict[str, dict] = {}

        if url and psycopg:
            try:
                self._conn = psycopg.connect(url)
                self._use_postgres = True
                self._setup_table()
                logger.info("HitlStore: PostgreSQL mode")
            except Exception as e:
                logger.warning(f"HitlStore: DB connect failed, falling back to in-memory: {e}")
                self._use_postgres = False
        else:
            logger.info("HitlStore: in-memory mode")

    def _ensure_conn(self) -> None:
        """연결이 명시적으로 닫힌 경우 재연결."""
        if self._conn is not None and self._conn.closed and self._url and psycopg:
            logger.info("HitlStore: reconnecting to PostgreSQL")
            self._conn = psycopg.connect(self._url)

    def _reconnect(self) -> None:
        """서버 측 idle timeout 등으로 연결이 끊긴 경우 강제 재연결."""
        if self._url and psycopg:
            logger.info("HitlStore: reconnecting after OperationalError")
            self._conn = psycopg.connect(self._url)

    def _run(self, fn):
        """DB 작업 실행. OperationalError 시 재연결 후 1회 재시도.

        conn.closed == False이지만 서버가 idle timeout으로 연결을 끊은 경우
        (stale connection)를 방어한다.
        """
        self._ensure_conn()
        try:
            return fn()
        except psycopg.OperationalError:
            self._reconnect()
            return fn()

    def _setup_table(self):
        """hitl_pending 테이블 생성 (없으면) + 컬럼 마이그레이션."""
        with self._conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hitl_pending (
                    slack_ts   TEXT PRIMARY KEY,
                    thread_id  TEXT NOT NULL,
                    email_id   TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # subject/sender 컬럼 추가 (기존 테이블 마이그레이션)
            cur.execute("ALTER TABLE hitl_pending ADD COLUMN IF NOT EXISTS subject TEXT DEFAULT ''")
            cur.execute("ALTER TABLE hitl_pending ADD COLUMN IF NOT EXISTS sender  TEXT DEFAULT ''")
        self._conn.commit()

    def insert(self, slack_ts: str, thread_id: str, email_id: str, subject: str = "", sender: str = "") -> bool:
        """HITL 매핑 저장.

        Returns:
            True: 성공, False: 이미 존재 (중복 방지)
        """
        if self.is_email_pending(email_id):
            logger.warning(f"HITL already pending for email_id={email_id}")
            return False

        if self._use_postgres:
            def _do():
                with self._conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO hitl_pending (slack_ts, thread_id, email_id, subject, sender)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (slack_ts) DO NOTHING
                        """,
                        (slack_ts, thread_id, email_id, subject, sender),
                    )
                self._conn.commit()
            self._run(_do)
        else:
            self._store[slack_ts] = {
                "thread_id": thread_id,
                "email_id": email_id,
                "subject": subject,
                "sender": sender,
                "created_at": datetime.now(KST),
            }
        return True

    def lookup_by_slack_ts(self, slack_ts: str) -> Optional[dict]:
        """Slack message_ts로 thread_id 조회."""
        if self._use_postgres:
            def _do():
                with self._conn.cursor() as cur:
                    cur.execute(
                        "SELECT thread_id, email_id, created_at FROM hitl_pending WHERE slack_ts = %s",
                        (slack_ts,),
                    )
                    return cur.fetchone()
            row = self._run(_do)
            if not row:
                return None
            return {"thread_id": row[0], "email_id": row[1], "created_at": row[2]}
        return self._store.get(slack_ts)

    def remove(self, slack_ts: str) -> bool:
        """처리 완료된 매핑 삭제."""
        if self._use_postgres:
            def _do():
                with self._conn.cursor() as cur:
                    cur.execute("DELETE FROM hitl_pending WHERE slack_ts = %s", (slack_ts,))
                    deleted = cur.rowcount
                self._conn.commit()
                return deleted
            return self._run(_do) > 0
        if slack_ts in self._store:
            del self._store[slack_ts]
            return True
        return False

    def is_email_pending(self, email_id: str) -> bool:
        """이미 HITL 대기 중인 이메일인지 확인 (dedup guard)."""
        if self._use_postgres:
            def _do():
                with self._conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM hitl_pending WHERE email_id = %s LIMIT 1",
                        (email_id,),
                    )
                    return cur.fetchone() is not None
            return self._run(_do)
        return email_id in {v["email_id"] for v in self._store.values()}

    def list_pending(self) -> list[dict]:
        """대기 중인 HITL 목록 전체 반환."""
        if self._use_postgres:
            def _do():
                with self._conn.cursor() as cur:
                    cur.execute(
                        "SELECT slack_ts, thread_id, email_id, subject, sender, created_at FROM hitl_pending ORDER BY created_at DESC"
                    )
                    return cur.fetchall()
            rows = self._run(_do)
            return [
                {
                    "slack_ts": r[0],
                    "thread_id": r[1],
                    "email_id": r[2],
                    "subject": r[3] or "",
                    "sender": r[4] or "",
                    "created_at": r[5].isoformat() if r[5] else None,
                }
                for r in rows
            ]
        return [
            {
                "slack_ts": ts,
                "thread_id": v["thread_id"],
                "email_id": v["email_id"],
                "subject": v.get("subject", ""),
                "sender": v.get("sender", ""),
                "created_at": v["created_at"].isoformat() if hasattr(v["created_at"], "isoformat") else str(v["created_at"]),
            }
            for ts, v in self._store.items()
        ]

    def cleanup_expired(self, ttl_hours: int = 24) -> int:
        """TTL 만료된 매핑 정리.

        Returns: 삭제된 건수
        """
        if self._use_postgres:
            def _do():
                with self._conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM hitl_pending WHERE created_at < NOW() - INTERVAL '%s hours'",
                        (ttl_hours,),
                    )
                    deleted = cur.rowcount
                self._conn.commit()
                return deleted
            return self._run(_do)

        now = datetime.now(KST)
        expired = [
            ts for ts, v in self._store.items()
            if now - v["created_at"] > timedelta(hours=ttl_hours)
        ]
        for ts in expired:
            logger.info(f"HITL expired: email_id={self._store[ts]['email_id']}")
            del self._store[ts]
        return len(expired)
