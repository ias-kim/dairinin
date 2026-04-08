"""
Memory MCP 서버 — 사용자 일정 패턴 학습.

auto_register된 이벤트를 패턴으로 저장하고,
다음에 비슷한 이벤트가 올 때 조회해서 Conflict Agent의
threshold 판단에 활용.

두 가지 모드:
    - 인메모리 (기본): dict 기반. 테스트/개발용.
    - mem0 (MEM0_API_URL 설정 시): 자연어 검색 지원.

인메모리 모드의 검색 로직:
    단순 문자열 포함 매칭. mem0는 임베딩 기반 유사도 검색.
    인메모리는 "화요일"로 검색하면 "화요일" 포함된 것만.
    mem0는 "Tuesday meeting"으로 검색해도 "화요일 미팅" 찾아줌.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("memory")


class MemoryStore:
    """사용자 패턴 저장소.

    Conflict Agent가 threshold 판단 시 사용:
        patterns = store.query_patterns(user_id, event_title)
        if len(patterns) >= 10:
            threshold = 0.6  (자주 승인하는 패턴 → 완화)
    """

    def __init__(self, use_mem0: bool | None = None):
        # None이면 환경변수에서 자동 감지
        if use_mem0 is None:
            use_mem0 = bool(os.getenv("MEM0_API_URL"))

        self._use_mem0 = use_mem0

        if use_mem0:
            self._init_mem0()
        else:
            # 인메모리 fallback: {user_id: [{"pattern": ..., "metadata": ...}]}
            self._store: dict[str, list[dict]] = defaultdict(list)

    def _init_mem0(self):
        """mem0 클라이언트 초기화."""
        try:
            from mem0 import MemoryClient

            api_url = os.getenv("MEM0_API_URL", "http://localhost:8888")
            self._mem0 = MemoryClient(api_key="local", host=api_url)
            logger.info(f"mem0 connected: {api_url}")
        except Exception as e:
            logger.warning(f"mem0 init failed, falling back to in-memory: {e}")
            self._use_mem0 = False
            self._store = defaultdict(list)

    def write_pattern(
        self,
        user_id: str,
        pattern: str,
        metadata: dict | None = None,
    ) -> None:
        """패턴 저장.

        Args:
            user_id: 사용자 식별자
            pattern: 자연어 패턴 ("화요일 2시 팀미팅 자동 승인")
            metadata: 구조화된 부가 정보 (title, day, time 등)
        """
        if self._use_mem0:
            self._mem0.add(
                messages=pattern,
                user_id=user_id,
                metadata=metadata or {},
            )
        else:
            self._store[user_id].append({
                "pattern": pattern,
                "metadata": metadata or {},
            })

    def query_patterns(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> list[dict]:
        """관련 패턴 조회.

        Args:
            user_id: 사용자 식별자
            query: 검색 쿼리 ("화요일 미팅")
            limit: 최대 반환 수

        Returns:
            [{"pattern": "...", "metadata": {...}}, ...]
        """
        if self._use_mem0:
            try:
                results = self._mem0.search(
                    query=query,
                    user_id=user_id,
                    limit=limit,
                )
                return [
                    {"pattern": r.get("memory", ""), "metadata": r.get("metadata", {})}
                    for r in results
                ]
            except Exception as e:
                logger.error(f"mem0 search failed: {e}")
                return []
        else:
            # 인메모리: 쿼리의 각 단어가 하나라도 포함되면 매칭
            user_patterns = self._store.get(user_id, [])
            query_words = query.split()
            matched = [
                p for p in user_patterns
                if any(word in p["pattern"] for word in query_words)
            ]
            return matched[:limit]

    def get_pattern_count(self, user_id: str, query: str) -> int:
        """특정 패턴의 누적 횟수.

        Conflict Agent가 "10번 이상 승인했으면 threshold 낮추기"에 사용.
        """
        return len(self.query_patterns(user_id, query, limit=100))


# ──────────────────────────────────────────────
# FastMCP 툴 — 싱글톤 MemoryStore 인스턴스 공유
# ──────────────────────────────────────────────

_store = MemoryStore()


@mcp.tool
def write_pattern(user_id: str, pattern: str, metadata: dict | None = None) -> None:
    """사용자 일정 패턴을 저장한다."""
    _store.write_pattern(user_id, pattern, metadata)


@mcp.tool
def query_patterns(user_id: str, query: str, limit: int = 10) -> list[dict]:
    """관련 패턴을 조회한다."""
    return _store.query_patterns(user_id, query, limit)


if __name__ == "__main__":
    mcp.run()
