"""
gmail-mcp 단위 테스트.

gmail-mcp의 역할:
    Gmail API를 래핑해서 두 개의 MCP 툴을 제공:
    - fetch_emails(): 안 읽은 이메일 목록 가져오기
    - mark_read(email_id): 이메일을 읽음 처리

    LangGraph Orchestrator가 이 툴들을 호출.
    에이전트는 Gmail SDK를 직접 모름 — 이 MCP 서버가 중간 다리.

테스트 전략:
    Google Gmail API를 mock해서 테스트.
    실제 API 호출 없이 함수 로직만 검증.

왜 mock하는가:
    - 실제 Gmail 계정 필요 없음
    - 네트워크 없이 테스트 가능
    - CI에서도 돌릴 수 있음
    - 테스트가 0.1초 안에 끝남
"""

from unittest.mock import MagicMock, patch
import pytest

from mcp_servers.gmail_mcp import fetch_emails_logic, mark_read_logic


class TestFetchEmails:
    """fetch_emails 툴 테스트."""

    def test_returns_unread_emails(self):
        """안 읽은 이메일이 있으면 id + snippet 리스트 반환.

        Gmail API 응답 구조:
            messages().list() → {"messages": [{"id": "abc123"}, ...]}
            messages().get()  → {"id": "abc123", "snippet": "내용 미리보기", "payload": {...}}
        """
        mock_service = MagicMock()

        # list: 안 읽은 이메일 2개
        mock_service.users().messages().list().execute.return_value = {
            "messages": [{"id": "msg_1"}, {"id": "msg_2"}]
        }

        # get: 각 이메일의 상세 정보
        def mock_get(userId, id, format):
            data = {
                "msg_1": {
                    "id": "msg_1",
                    "snippet": "Can we meet Tuesday at 2pm?",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "kim@example.com"},
                            {"name": "Subject", "value": "Meeting request"},
                        ]
                    },
                },
                "msg_2": {
                    "id": "msg_2",
                    "snippet": "점심 같이 할까요?",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "lee@example.com"},
                            {"name": "Subject", "value": "점심 약속"},
                        ]
                    },
                },
            }
            mock = MagicMock()
            mock.execute.return_value = data[id]
            return mock

        mock_service.users().messages().get = mock_get

        result = fetch_emails_logic(mock_service)

        assert len(result) == 2
        assert result[0]["id"] == "msg_1"
        assert result[0]["subject"] == "Meeting request"
        assert result[0]["from"] == "kim@example.com"
        assert result[0]["snippet"] == "Can we meet Tuesday at 2pm?"

    def test_returns_empty_list_when_no_unread(self):
        """안 읽은 이메일 없으면 빈 리스트.

        Gmail API는 결과 없을 때 "messages" 키 자체가 없음.
        이걸 처리 안 하면 KeyError.
        """
        mock_service = MagicMock()
        mock_service.users().messages().list().execute.return_value = {}
        # "messages" 키 없음 — resultSizeEstimate만 있는 경우

        result = fetch_emails_logic(mock_service)
        assert result == []

    def test_handles_api_error_gracefully(self):
        """Gmail API가 에러 던지면 빈 리스트 + 에러 로그.

        네트워크 장애, 인증 만료 등에서 시스템이 죽으면 안 됨.
        폴링 루프가 계속 돌아야 하니까.
        """
        mock_service = MagicMock()
        mock_service.users().messages().list().execute.side_effect = Exception("API error")

        result = fetch_emails_logic(mock_service)
        assert result == []


class TestMarkRead:
    """mark_read 툴 테스트."""

    def test_marks_email_as_read(self):
        """이메일을 읽음 처리 → Gmail API modify 호출 확인.

        Gmail에서 "읽음"은 UNREAD 라벨을 제거하는 것.
        addLabelIds가 아니라 removeLabelIds에 "UNREAD" 넣어야 함.
        """
        mock_service = MagicMock()
        mock_service.users().messages().modify().execute.return_value = {"id": "msg_1"}

        result = mark_read_logic(mock_service, "msg_1")

        # modify가 올바른 인자로 호출됐는지 확인
        mock_service.users().messages().modify.assert_called_with(
            userId="me",
            id="msg_1",
            body={"removeLabelIds": ["UNREAD"]},
        )
        assert result is True

    def test_returns_false_on_error(self):
        """mark_read 실패해도 시스템 죽으면 안 됨.

        eng review: mark_read 실패는 "이메일이 다음 폴링에서 다시 잡힘" 정도.
        치명적이지 않으므로 로그만 남기고 계속.
        """
        mock_service = MagicMock()
        mock_service.users().messages().modify().execute.side_effect = Exception("API error")

        result = mark_read_logic(mock_service, "msg_1")
        assert result is False
