"""
Gmail MCP 서버.

전체 아키텍처에서의 역할:
    Gmail API를 래핑해서 LangGraph 에이전트에게 두 개의 툴을 제공.
    에이전트는 Gmail SDK를 모름 — "fetch_emails", "mark_read"만 호출.

    ┌────────────────────────┐
    │  gmail-mcp (port 8001) │
    │                        │
    │  fetch_emails() ───────│──→ Orchestrator가 15s마다 호출
    │  mark_read()    ───────│──→ 이벤트 처리 완료 후 호출
    └────────────────────────┘
          │
          ▼
    Google Gmail API (OAuth2)

의존성:
    google-api-python-client → Gmail API SDK
    google-auth             → OAuth2 인증 + 자동 토큰 갱신
    fastmcp                 → MCP 서버 프레임워크

함수 구조:
    build_gmail_service()   → Gmail API 클라이언트 생성 (OAuth2 인증 포함)
    fetch_emails_logic()    → 안 읽은 이메일 가져오기 (순수 로직, 테스트용)
    mark_read_logic()       → 이메일 읽음 처리 (순수 로직, 테스트용)
    fetch_emails()          → MCP 툴 (FastMCP @mcp.tool 데코레이터)
    mark_read()             → MCP 툴 (FastMCP @mcp.tool 데코레이터)

왜 logic 함수와 MCP 툴을 분리하는가:
    MCP 툴은 FastMCP 프레임워크에 의존 → 테스트 시 MCP 서버를 띄워야 함.
    logic 함수는 순수 Python → mock만으로 테스트 가능.
    logic 함수에서 실제 로직을 처리하고, MCP 툴은 logic 함수를 호출만 함.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

load_dotenv()

# ──────────────────────────────────────────────
# Gmail API 클라이언트 생성
# ──────────────────────────────────────────────


def build_gmail_service():
    """Gmail API 클라이언트를 OAuth2 인증으로 생성.

    refresh_token 기반이라 access_token이 1시간 후 만료되어도
    자동으로 갱신됨 (eng review issue #7).

    호출 체인:
        FastAPI startup → build_gmail_service() → Credentials 생성
        → 이후 fetch_emails_logic(), mark_read_logic()에서 사용
    """
    creds = Credentials(
        token=None,  # 첫 호출 시 자동 갱신됨
        refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    )

    # 토큰이 만료됐으면 즉시 갱신
    if not creds.valid:
        creds.refresh(Request())

    return build("gmail", "v1", credentials=creds)


# ──────────────────────────────────────────────
# 순수 로직 함수 (테스트 가능)
# ──────────────────────────────────────────────


def fetch_emails_logic(service: Any, max_results: int = 10) -> list[dict]:
    """안 읽은 이메일 목록을 가져온다.

    Args:
        service: Gmail API 클라이언트 (build_gmail_service()의 반환값)
        max_results: 최대 가져올 이메일 수

    Returns:
        [{"id": "msg_1", "from": "...", "subject": "...", "snippet": "..."}, ...]
        에러 시 빈 리스트 (폴링 루프가 죽으면 안 되니까)

    Gmail API 호출 순서:
        1. messages().list(q="is:unread") → 안 읽은 이메일 ID 목록
        2. messages().get(id=...) → 각 이메일의 상세 정보 (제목, 보낸 사람, 본문 미리보기)

    왜 두 번 호출하는가:
        list()는 ID만 반환. 제목, 본문 등은 get()으로 따로 가져와야 함.
        Gmail API의 설계 — 리스트는 가볍게, 상세는 필요할 때만.
    """
    try:
        response = (
            service.users()
            .messages()
            .list(userId="me", q="is:unread", maxResults=max_results)
            .execute()
        )

        # Gmail API는 결과 없을 때 "messages" 키 자체가 없음
        message_ids = response.get("messages", [])
        if not message_ids:
            return []

        emails = []
        for msg_ref in message_ids:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_ref["id"], format="metadata")
                .execute()
            )

            # headers에서 From, Subject 추출
            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
                if h["name"] in ("From", "Subject")
            }

            emails.append(
                {
                    "id": msg["id"],
                    "from": headers.get("From", ""),
                    "subject": headers.get("Subject", ""),
                    "snippet": msg.get("snippet", ""),
                }
            )

        return emails

    except Exception as e:
        logger.error(f"fetch_emails failed: {e}")
        return []


def mark_read_logic(service: Any, email_id: str) -> bool:
    """이메일을 읽음 처리한다.

    Args:
        service: Gmail API 클라이언트
        email_id: Gmail 메시지 ID

    Returns:
        True: 성공, False: 실패 (실패해도 시스템은 계속 동작)

    Gmail에서 "읽음" = UNREAD 라벨 제거.
    addLabelIds가 아니라 removeLabelIds를 써야 함 — 직관과 반대.

    실패해도 치명적이지 않음:
        mark_read 실패 → 다음 폴링에서 같은 이메일 다시 잡힘
        → dedup 로직이 처리 (processed_emails 테이블)
    """
    try:
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        return True

    except Exception as e:
        logger.error(f"mark_read failed for {email_id}: {e}")
        return False
