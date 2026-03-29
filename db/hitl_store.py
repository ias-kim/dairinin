"""
HITL 매핑 저장소.

Slack message_ts ↔ LangGraph thread_id ↔ email_id 매핑.
Slack webhook에서 reaction 오면 thread_id로 그래프를 resume.

두 가지 모드:
    - 인메모리 (기본): 테스트/개발용
    - PostgreSQL (DATABASE_URL 설정 시): 영속 저장
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


class HitlStore:
    """hitl_pending 매핑 관리."""

    def __init__(self):
        self._store: dict[str, dict] = {}  # slack_ts → {thread_id, email_id, created_at}

    def insert(self, slack_ts: str, thread_id: str, email_id: str) -> bool:
        """HITL 매핑 저장.

        Returns:
            True: 성공, False: 이미 존재 (중복 방지)
        """
        if email_id in {v["email_id"] for v in self._store.values()}:
            logger.warning(f"HITL already pending for email_id={email_id}")
            return False

        self._store[slack_ts] = {
            "thread_id": thread_id,
            "email_id": email_id,
            "created_at": datetime.now(KST),
        }
        return True

    def lookup_by_slack_ts(self, slack_ts: str) -> Optional[dict]:
        """Slack message_ts로 thread_id 조회."""
        return self._store.get(slack_ts)

    def remove(self, slack_ts: str) -> bool:
        """처리 완료된 매핑 삭제."""
        if slack_ts in self._store:
            del self._store[slack_ts]
            return True
        return False

    def is_email_pending(self, email_id: str) -> bool:
        """이미 HITL 대기 중인 이메일인지 확인 (dedup guard)."""
        return email_id in {v["email_id"] for v in self._store.values()}

    def cleanup_expired(self, ttl_hours: int = 24) -> int:
        """TTL 만료된 매핑 정리.

        24시간 지나도 반응 없으면 skip 처리.
        Returns: 삭제된 건수
        """
        now = datetime.now(KST)
        expired = [
            ts for ts, v in self._store.items()
            if now - v["created_at"] > timedelta(hours=ttl_hours)
        ]
        for ts in expired:
            logger.info(f"HITL expired: email_id={self._store[ts]['email_id']}")
            del self._store[ts]
        return len(expired)
