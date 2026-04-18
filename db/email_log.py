"""
이메일 처리 이력 저장소.

이메일이 처리될 때마다 결과를 기록 → 대시보드에서 조회.
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


class EmailLogStore:
    """email_logs 테이블 관리."""

    def __init__(self, database_url: str | None = None):
        url = database_url or os.getenv("DATABASE_URL")

        self._use_postgres: bool = False
        self._conn: Optional[Any] = None
        self._store: list[dict] = []

        if url and psycopg:
            try:
                self._conn = psycopg.connect(url)
                self._use_postgres = True
                self._setup_table()
                logger.info("EmailLogStore: PostgreSQL mode")
            except Exception as e:
                logger.warning(f"EmailLogStore: DB connect failed, falling back to in-memory: {e}")
        else:
            logger.info("EmailLogStore: in-memory mode")

    def _setup_table(self):
        with self._conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS email_logs (
                    id         SERIAL PRIMARY KEY,
                    email_id   TEXT NOT NULL,
                    subject    TEXT,
                    sender     TEXT,
                    category   TEXT,
                    action     TEXT,
                    confidence FLOAT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        self._conn.commit()

    def log(
        self,
        email_id: str,
        subject: str = "",
        sender: str = "",
        category: str = "other",
        action: str = "skip",
        confidence: float | None = None,
    ) -> None:
        if self._use_postgres:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO email_logs (email_id, subject, sender, category, action, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (email_id, subject, sender, category, action, confidence),
                )
            self._conn.commit()
        else:
            self._store.append({
                "id": len(self._store) + 1,
                "email_id": email_id,
                "subject": subject,
                "sender": sender,
                "category": category,
                "action": action,
                "confidence": confidence,
                "created_at": datetime.now(KST).isoformat(),
            })

    def list_logs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        if self._use_postgres:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, email_id, subject, sender, category, action, confidence, created_at
                    FROM email_logs
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "email_id": r[1],
                    "subject": r[2],
                    "sender": r[3],
                    "category": r[4],
                    "action": r[5],
                    "confidence": r[6],
                    "created_at": r[7].isoformat() if r[7] else None,
                }
                for r in rows
            ]
        return list(reversed(self._store))[offset : offset + limit]

    def get_stats(self) -> dict:
        if self._use_postgres:
            with self._conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM email_logs")
                total = cur.fetchone()[0]
                cur.execute("SELECT action, COUNT(*) FROM email_logs GROUP BY action")
                by_action = {row[0]: row[1] for row in cur.fetchall()}
                cur.execute("SELECT category, COUNT(*) FROM email_logs GROUP BY category")
                by_category = {row[0]: row[1] for row in cur.fetchall()}
            return {"total": total, "by_action": by_action, "by_category": by_category}
        total = len(self._store)
        by_action: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for row in self._store:
            by_action[row["action"]] = by_action.get(row["action"], 0) + 1
            by_category[row["category"]] = by_category.get(row["category"], 0) + 1
        return {"total": total, "by_action": by_action, "by_category": by_category}


_email_log_store: EmailLogStore | None = None


def get_email_log_store() -> EmailLogStore:
    global _email_log_store
    if _email_log_store is None:
        _email_log_store = EmailLogStore()
    return _email_log_store
