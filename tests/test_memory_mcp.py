"""
memory-mcp 테스트.

인메모리 fallback으로 테스트. mem0 서버 불필요.
"""

import pytest
from mcp_servers.memory_mcp import MemoryStore


class TestMcpTools:

    @pytest.mark.asyncio
    async def test_tools_are_registered(self):
        """write_pattern, query_patterns 툴 등록 확인."""
        from fastmcp import Client
        from mcp_servers.memory_mcp import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "write_pattern" in tool_names
            assert "query_patterns" in tool_names


class TestMemoryStore:

    def test_write_and_query_pattern(self):
        """패턴 저장 후 조회.

        write → query → 관련 패턴 반환.
        실제로는 "화요일 팀미팅 자동 승인"을 기억해뒀다가
        다음에 비슷한 이벤트 올 때 찾아주는 것.
        """
        store = MemoryStore()
        store.write_pattern(
            user_id="gwan",
            pattern="화요일 2시 팀미팅 자동 승인",
            metadata={"title": "팀미팅", "day": "Tuesday", "time": "14:00"},
        )

        results = store.query_patterns("gwan", "화요일 미팅")
        assert len(results) >= 1
        assert "팀미팅" in results[0]["pattern"]

    def test_empty_query(self):
        """저장된 패턴 없으면 빈 리스트."""
        store = MemoryStore()
        results = store.query_patterns("gwan", "점심 약속")
        assert results == []

    def test_multiple_patterns(self):
        """여러 패턴 저장 후 관련된 것만 조회."""
        store = MemoryStore()
        store.write_pattern("gwan", "화요일 팀미팅 자동 승인", {"day": "Tuesday"})
        store.write_pattern("gwan", "금요일 점심 자동 승인", {"day": "Friday"})

        results = store.query_patterns("gwan", "금요일")
        assert any("금요일" in r["pattern"] for r in results)

    def test_user_isolation(self):
        """다른 user_id의 패턴은 안 보임.

        개인용이라 지금은 의미 없지만,
        멀티유저 확장 시 데이터 격리 보장.
        """
        store = MemoryStore()
        store.write_pattern("gwan", "팀미팅 패턴", {})
        store.write_pattern("kim", "김씨 패턴", {})

        gwan_results = store.query_patterns("gwan", "팀미팅")
        kim_results = store.query_patterns("kim", "팀미팅")

        assert len(gwan_results) == 1
        assert len(kim_results) == 0

    def test_pattern_count(self):
        """특정 패턴의 누적 횟수 조회.

        Conflict Agent가 "이 패턴이 10번 이상 승인됐는가?"
        확인할 때 사용.
        """
        store = MemoryStore()
        for i in range(5):
            store.write_pattern("gwan", "화요일 팀미팅", {"count": i})

        count = store.get_pattern_count("gwan", "화요일 팀미팅")
        assert count == 5
