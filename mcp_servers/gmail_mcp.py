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
    build_gmail_service()       → Gmail API 클라이언트 생성 (OAuth2 인증 포함)
    fetch_emails_logic()        → 안 읽은 이메일 가져오기 (순수 로직, 테스트용)
    mark_read_logic()           → 이메일 읽음 처리 (순수 로직, 테스트용)
    get_or_create_label()       → 라벨 ID 조회, 없으면 자동 생성 (순수 로직)
    add_label_logic()           → 이메일에 라벨 추가 (순수 로직, 테스트용)
    fetch_emails()              → MCP 툴 (FastMCP @mcp.tool 데코레이터)
    mark_read()                 → MCP 툴 (FastMCP @mcp.tool 데코레이터)
    add_label()                 → MCP 툴 (FastMCP @mcp.tool 데코레이터)

왜 logic 함수와 MCP 툴을 분리하는가:
    MCP 툴은 FastMCP 프레임워크에 의존 → 테스트 시 MCP 서버를 띄워야 함.
    logic 함수는 순수 Python → mock만으로 테스트 가능.
    logic 함수에서 실제 로직을 처리하고, MCP 툴은 logic 함수를 호출만 함.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

load_dotenv()

# MCP 서버 인스턴스 — 툴들이 여기에 등록됨
mcp = FastMCP("gmail")

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


_REPLY_PATTERNS = re.compile(
    r'\nOn .{10,500}?wrote:'      # Gmail "On <date>, <name> wrote:"
    r'|\n-{5,}'                   # "-----Original Message-----"
    r'|\n_{5,}',                  # "___________" separators
    re.DOTALL,
)


def _extract_text_body(payload: dict) -> str:
    """Gmail payload에서 text/plain 본문을 재귀적으로 추출하고 base64 디코딩."""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    elif mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            result = _extract_text_body(part)
            if result:
                return result

    return ""


def _strip_quoted_content(text: str) -> str:
    """이메일 본문에서 인용된 이전 스레드 내용 제거. 최신 메시지만 반환."""
    match = _REPLY_PATTERNS.search(text)
    if match:
        return text[: match.start()].strip()
    return text.strip()


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
                .get(userId="me", id=msg_ref["id"], format="full")
                .execute()
            )

            # headers에서 From, Subject 추출
            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
                if h["name"] in ("From", "Subject")
            }

            # 전체 본문 추출 → 인용 스레드 제거 → snippet 폴백
            raw_body = _extract_text_body(msg.get("payload", {}))
            body = _strip_quoted_content(raw_body) if raw_body else msg.get("snippet", "")

            emails.append(
                {
                    "id": msg["id"],
                    "from": headers.get("From", ""),
                    "subject": headers.get("Subject", ""),
                    "snippet": msg.get("snippet", ""),
                    "body": body,
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


def send_reply_logic(service: Any, thread_id: str, body: str, to: str) -> bool:
    """이메일에 답장을 보낸다.

    Args:
        service: Gmail API 클라이언트
        thread_id: 답장할 스레드 ID
        body: 답장 본문
        to: 수신자 이메일

    Returns:
        True: 성공, False: 실패
    """
    import base64
    from email.mime.text import MIMEText
    from email.utils import parseaddr

    try:
        _, addr = parseaddr(to)
        message = MIMEText(body)
        message["to"] = addr or to
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id},
        ).execute()
        return True

    except Exception as e:
        logger.error(f"send_reply failed for thread {thread_id}: {e}")
        return False


def archive_email_logic(service: Any, email_id: str) -> bool:
    """이메일을 아카이브한다 (INBOX 라벨 제거).

    Args:
        service: Gmail API 클라이언트
        email_id: Gmail 메시지 ID

    Returns:
        True: 성공, False: 실패
    """
    try:
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()
        return True

    except Exception as e:
        logger.error(f"archive_email failed for {email_id}: {e}")
        return False


def get_or_create_label(service: Any, label_name: str) -> str:
    """라벨 이름으로 라벨 ID를 반환한다. 라벨이 없으면 먼저 생성한다.

    Gmail API는 사용자 정의 라벨(예: "NEWSLETTER")이 존재하지 않으면
    messages().modify() 호출 시 "Invalid label" 에러를 반환한다.
    이 함수는 라벨 존재 여부를 확인하고, 없으면 자동으로 생성한 뒤
    라벨 ID를 반환해 add_label_logic()이 안전하게 라벨을 적용할 수 있게 한다.

    Args:
        service: Gmail API 클라이언트
        label_name: 라벨 이름 (예: "NEWSLETTER")

    Returns:
        라벨 ID 문자열 (기존 라벨이면 기존 ID, 새로 생성하면 새 ID)

    Raises:
        Exception: Gmail API 호출 실패 시 그대로 전파
    """
    # 기존 라벨 목록에서 이름이 일치하는 라벨 검색
    response = service.users().labels().list(userId="me").execute()
    for label in response.get("labels", []):
        if label["name"].upper() == label_name.upper():
            return label["id"]

    # 라벨이 없으면 새로 생성
    logger.info(f"Label '{label_name}' not found — creating it")
    created = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    return created["id"]


def add_label_logic(service: Any, email_id: str, label_name: str) -> bool:
    """이메일에 라벨을 추가한다.

    라벨이 Gmail 계정에 존재하지 않으면 자동으로 생성한 뒤 적용한다.
    Gmail API는 존재하지 않는 라벨 ID를 사용하면 "Invalid label" 에러를
    반환하므로, get_or_create_label()로 라벨 ID를 먼저 확보한다.

    Args:
        service: Gmail API 클라이언트
        email_id: Gmail 메시지 ID
        label_name: 추가할 라벨 이름 (예: "NEWSLETTER")

    Returns:
        True: 성공, False: 실패
    """
    try:
        label_id = get_or_create_label(service, label_name)
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"addLabelIds": [label_id]},
        ).execute()
        return True

    except Exception as e:
        logger.error(f"add_label failed for {email_id}: {e}")
        return False


# ──────────────────────────────────────────────
# FastMCP 툴 — logic 함수를 MCP 프로토콜로 노출
# ──────────────────────────────────────────────

@mcp.tool
def fetch_emails(max_results: int = 10) -> list[dict]:
    """안 읽은 이메일 목록을 가져온다.

    왜 @mcp.tool로 감싸는가:
        logic 함수는 service 객체를 인자로 받음 → LLM이 직접 호출 불가.
        MCP 툴은 인자 없이 호출 가능 → LLM이 "fetch_emails 해줘" 한 마디로 실행.
    """
    service = build_gmail_service()
    return fetch_emails_logic(service, max_results)


@mcp.tool
def mark_read(email_id: str) -> bool:
    """이메일을 읽음 처리한다."""
    service = build_gmail_service()
    return mark_read_logic(service, email_id)


@mcp.tool
def archive_email(email_id: str) -> bool:
    """이메일을 아카이브한다 (INBOX 라벨 제거)."""
    service = build_gmail_service()
    return archive_email_logic(service, email_id)


@mcp.tool
def add_label(email_id: str, label_name: str) -> bool:
    """이메일에 라벨을 추가한다. 라벨이 없으면 자동으로 생성한다."""
    service = build_gmail_service()
    return add_label_logic(service, email_id, label_name)


if __name__ == "__main__":
    # uvx fastmcp run mcp_servers/gmail_mcp.py 로 실행
    mcp.run()
