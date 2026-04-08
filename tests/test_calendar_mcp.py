"""
calendar-mcp 단위 테스트.

calendar-mcp의 역할:
    Google Calendar API를 래핑해서 3개 MCP 툴 제공:
    - get_events(date): 해당 날짜 일정 조회
    - check_conflicts(start, end): 시간 충돌 확인
    - create_event(event): 이벤트 생성 (DRY_RUN 지원)

    Scheduler Agent → check_conflicts 호출
    Notifier Agent  → create_event 호출

의존성 흐름:
    check_conflicts → get_events_logic 내부 호출 (DRY)
    create_event → DRY_RUN 플래그에 따라 실제 API 호출 여부 결정
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from mcp_servers.calendar_mcp import (
    check_conflicts_logic,
    create_event_logic,
    get_events_logic,
)


class TestMcpTools:
    """FastMCP @mcp.tool 레이어 테스트."""

    @pytest.mark.asyncio
    async def test_tools_are_registered(self):
        """get_events, check_conflicts, create_event 툴 등록 확인."""
        from fastmcp import Client
        from mcp_servers.calendar_mcp import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_events" in tool_names
            assert "check_conflicts" in tool_names
            assert "create_event" in tool_names


class TestGetEvents:
    """get_events 툴 테스트."""

    def test_returns_events_for_date(self):
        """특정 날짜의 일정을 반환.

        Calendar API 응답 구조:
            events().list(timeMin, timeMax) → {"items": [{...}, ...]}
        """
        mock_service = MagicMock()
        mock_service.events().list().execute.return_value = {
            "items": [
                {
                    "id": "evt_1",
                    "summary": "팀 미팅",
                    "start": {"dateTime": "2026-03-29T14:00:00+09:00"},
                    "end": {"dateTime": "2026-03-29T15:00:00+09:00"},
                },
                {
                    "id": "evt_2",
                    "summary": "점심",
                    "start": {"dateTime": "2026-03-29T12:00:00+09:00"},
                    "end": {"dateTime": "2026-03-29T13:00:00+09:00"},
                },
            ]
        }

        result = get_events_logic(mock_service, "2026-03-29")

        assert len(result) == 2
        assert result[0]["summary"] == "팀 미팅"
        assert result[1]["summary"] == "점심"

    def test_returns_empty_list_when_no_events(self):
        """일정 없는 날 → 빈 리스트.

        Calendar API는 items가 비어있으면 빈 리스트 반환.
        Gmail처럼 키가 없어지진 않지만 방어적으로 처리.
        """
        mock_service = MagicMock()
        mock_service.events().list().execute.return_value = {"items": []}

        result = get_events_logic(mock_service, "2026-04-01")
        assert result == []

    def test_handles_api_error(self):
        """Calendar API 에러 → 빈 리스트 + 로그.

        gmail-mcp와 같은 패턴: 에러가 시스템을 죽이면 안 됨.
        """
        mock_service = MagicMock()
        mock_service.events().list().execute.side_effect = Exception("API error")

        result = get_events_logic(mock_service, "2026-03-29")
        assert result == []


class TestCheckConflicts:
    """check_conflicts 툴 테스트."""

    def test_finds_overlapping_event(self):
        """새 이벤트 시간과 겹치는 기존 이벤트를 찾음.

        시나리오: 14:00-15:00 미팅이 있는데 14:30-15:30 이벤트를 넣으려 함
        → 충돌 감지.

        시간 비교 로직:
            기존.start < 새.end AND 기존.end > 새.start → 겹침
        """
        existing_events = [
            {
                "id": "evt_1",
                "summary": "팀 미팅",
                "start": {"dateTime": "2026-03-29T14:00:00+09:00"},
                "end": {"dateTime": "2026-03-29T15:00:00+09:00"},
            },
        ]

        conflicts = check_conflicts_logic(
            existing_events,
            new_start="2026-03-29T14:30:00+09:00",
            new_end="2026-03-29T15:30:00+09:00",
        )

        assert len(conflicts) == 1
        assert conflicts[0]["summary"] == "팀 미팅"

    def test_no_conflict_when_adjacent(self):
        """인접 시간은 충돌 아님.

        기존: 14:00-15:00, 새: 15:00-16:00 → 겹치지 않음.
        end == start인 경우는 연속이지 겹침이 아님.
        """
        existing_events = [
            {
                "id": "evt_1",
                "summary": "팀 미팅",
                "start": {"dateTime": "2026-03-29T14:00:00+09:00"},
                "end": {"dateTime": "2026-03-29T15:00:00+09:00"},
            },
        ]

        conflicts = check_conflicts_logic(
            existing_events,
            new_start="2026-03-29T15:00:00+09:00",
            new_end="2026-03-29T16:00:00+09:00",
        )

        assert conflicts == []

    def test_no_conflict_when_no_events(self):
        """기존 일정 없으면 충돌 없음."""
        conflicts = check_conflicts_logic(
            [],
            new_start="2026-03-29T14:00:00+09:00",
            new_end="2026-03-29T15:00:00+09:00",
        )
        assert conflicts == []

    def test_multiple_conflicts(self):
        """여러 이벤트와 동시에 충돌 가능.

        새 이벤트가 3시간짜리면 기존 2개와 겹칠 수 있음.
        """
        existing_events = [
            {
                "id": "evt_1",
                "summary": "오전 미팅",
                "start": {"dateTime": "2026-03-29T10:00:00+09:00"},
                "end": {"dateTime": "2026-03-29T11:00:00+09:00"},
            },
            {
                "id": "evt_2",
                "summary": "점심",
                "start": {"dateTime": "2026-03-29T12:00:00+09:00"},
                "end": {"dateTime": "2026-03-29T13:00:00+09:00"},
            },
        ]

        # 09:30~12:30 → 오전 미팅(10-11)과 점심(12-13) 모두 겹침
        conflicts = check_conflicts_logic(
            existing_events,
            new_start="2026-03-29T09:30:00+09:00",
            new_end="2026-03-29T12:30:00+09:00",
        )

        assert len(conflicts) == 2


class TestCreateEvent:
    """create_event 툴 테스트."""

    def test_dry_run_does_not_call_api(self):
        """DRY_RUN=true → API 호출 안 함, 로그만.

        Parser 정확도 검증 전 안전장치.
        mock_service의 insert()가 호출되지 않아야 함.
        """
        mock_service = MagicMock()

        result = create_event_logic(
            mock_service,
            summary="치과 예약",
            start="2026-04-01T15:00:00+09:00",
            end="2026-04-01T16:00:00+09:00",
            dry_run=True,
        )

        # API 호출 안 됨
        mock_service.events().insert.assert_not_called()
        assert result["status"] == "dry_run"
        assert result["summary"] == "치과 예약"

    def test_creates_event_when_not_dry_run(self):
        """DRY_RUN=false → 실제 Calendar API 호출.

        events().insert(calendarId, body) 형태로 호출되어야 함.
        """
        mock_service = MagicMock()
        mock_service.events().insert().execute.return_value = {
            "id": "new_evt_1",
            "summary": "치과 예약",
            "htmlLink": "https://calendar.google.com/event?eid=xxx",
        }

        result = create_event_logic(
            mock_service,
            summary="치과 예약",
            start="2026-04-01T15:00:00+09:00",
            end="2026-04-01T16:00:00+09:00",
            dry_run=False,
        )

        assert result["status"] == "created"
        assert result["id"] == "new_evt_1"

    def test_create_event_api_error(self):
        """Calendar 쓰기 실패 → status="error".

        eng review: failed_events 테이블에 로깅해서 나중에 수동 처리.
        여기서는 에러 반환까지만 — DB 로깅은 Notifier Agent 책임.
        """
        mock_service = MagicMock()
        mock_service.events().insert().execute.side_effect = Exception("403 Forbidden")

        result = create_event_logic(
            mock_service,
            summary="치과 예약",
            start="2026-04-01T15:00:00+09:00",
            end="2026-04-01T16:00:00+09:00",
            dry_run=False,
        )

        assert result["status"] == "error"
        assert "403" in result["error"]
