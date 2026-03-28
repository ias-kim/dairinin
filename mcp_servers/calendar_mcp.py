"""
Google Calendar MCP 서버.

전체 아키텍처에서의 역할:
    Google Calendar API를 래핑해서 3개 MCP 툴 제공.

    ┌──────────────────────────────────────┐
    │  calendar-mcp (port 8002)            │
    │                                      │
    │  get_events(date) ───────────────────│──→ Scheduler Agent
    │  check_conflicts(start, end) ────────│──→ Conflict Agent
    │  create_event(event) ────────────────│──→ Notifier Agent
    │    └── DRY_RUN=true면 로그만          │
    └──────────────────────────────────────┘
              │
              ▼
        Google Calendar API (OAuth2)

의존성:
    build_calendar_service() → gmail-mcp와 같은 OAuth2 인증 패턴
    get_events_logic()       → check_conflicts_logic()이 내부적으로 호출 (DRY)
    create_event_logic()     → DRY_RUN 플래그로 안전장치

함수 호출 체인:
    Scheduler Agent
        → check_conflicts_logic(events, start, end)
            → 시간 겹침 계산 (순수 함수)

    Notifier Agent
        → create_event_logic(service, summary, start, end, dry_run)
            → dry_run=True: 로그만 반환
            → dry_run=False: events().insert() 호출
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from dateutil.parser import isoparse
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

load_dotenv()


# ──────────────────────────────────────────────
# Calendar API 클라이언트 생성
# ──────────────────────────────────────────────


def build_calendar_service():
    """Google Calendar API 클라이언트 생성.

    gmail-mcp의 build_gmail_service()와 동일한 OAuth2 패턴.
    같은 refresh_token으로 Gmail과 Calendar 모두 접근 가능
    (OAuth scope에 둘 다 포함되어 있으면).

    나중에 공통 함수로 추출 가능 — 지금은 DRY보다 명시성 우선.
    """
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    )

    if not creds.valid:
        creds.refresh(Request())

    return build("calendar", "v3", credentials=creds)


# ──────────────────────────────────────────────
# 순수 로직 함수 (테스트 가능)
# ──────────────────────────────────────────────


def get_events_logic(service, date: str, calendar_id: str = "primary") -> list[dict]:
    """특정 날짜의 캘린더 이벤트를 가져온다.

    Args:
        service: Calendar API 클라이언트
        date: "2026-03-29" 형태의 날짜 문자열
        calendar_id: 조회할 캘린더 ID (기본: primary)

    Returns:
        [{"id", "summary", "start", "end"}, ...]
        에러 시 빈 리스트

    Calendar API 호출:
        events().list(timeMin, timeMax) → 해당 날짜 00:00~23:59 이벤트

    eng review TODO: calendar_id를 설정 가능하게
        (학교 캘린더, 개인 캘린더, 한국 공휴일 등)
    """
    try:
        # 날짜 → 시간 범위 변환 (해당 날짜의 00:00 ~ 다음날 00:00)
        day_start = datetime.fromisoformat(f"{date}T00:00:00+09:00")
        day_end = day_start + timedelta(days=1)

        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,  # 반복 이벤트를 개별 인스턴스로 펼침
                orderBy="startTime",
            )
            .execute()
        )

        return response.get("items", [])

    except Exception as e:
        logger.error(f"get_events failed for {date}: {e}")
        return []


def check_conflicts_logic(
    existing_events: list[dict],
    new_start: str,
    new_end: str,
) -> list[dict]:
    """새 이벤트와 시간이 겹치는 기존 이벤트를 찾는다.

    Args:
        existing_events: get_events_logic()의 반환값
        new_start: "2026-03-29T14:00:00+09:00" (ISO 8601)
        new_end: "2026-03-29T15:00:00+09:00"

    Returns:
        겹치는 이벤트 리스트. 빈 리스트 = 충돌 없음.

    시간 겹침 판단:
        기존.start < 새.end AND 기존.end > 새.start

        ──기존──        ──기존──
        |      |        |      |
        ──────→    vs   ──────→
           ──새──           ──새──
           |    |           |    |
           겹침!            겹침!

        ──기존──
        |      |
        ──────→
                ──새──
                |    |
                안 겹침 (인접)

    이 함수는 순수 함수 — service 객체 불필요.
    get_events_logic()으로 이벤트를 가져온 뒤 이 함수에 넘기는 구조.
    """
    new_s = isoparse(new_start)
    new_e = isoparse(new_end)

    conflicts = []
    for event in existing_events:
        # Calendar API 이벤트의 start/end 형태
        evt_start_str = event.get("start", {}).get("dateTime", "")
        evt_end_str = event.get("end", {}).get("dateTime", "")

        if not evt_start_str or not evt_end_str:
            continue  # 종일 이벤트 등은 스킵

        evt_s = isoparse(evt_start_str)
        evt_e = isoparse(evt_end_str)

        # 겹침 조건: 기존.start < 새.end AND 기존.end > 새.start
        if evt_s < new_e and evt_e > new_s:
            conflicts.append(event)

    return conflicts


def create_event_logic(
    service,
    summary: str,
    start: str,
    end: str,
    dry_run: bool = True,
    location: str | None = None,
    description: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
) -> dict:
    """캘린더에 이벤트를 생성한다.

    Args:
        service: Calendar API 클라이언트
        summary: 이벤트 제목
        start: 시작 시간 (ISO 8601)
        end: 종료 시간 (ISO 8601)
        dry_run: True면 API 호출 안 함 (안전장치)
        location: 장소 (optional)
        description: 설명 (optional)
        attendees: 참석자 이메일 리스트 (optional)
        calendar_id: 등록할 캘린더 ID

    Returns:
        {"status": "dry_run"|"created"|"error", ...}

    DRY_RUN 모드가 왜 필요한가:
        Parser Agent가 "치과 예약"을 "치과 에약"으로 파싱하면?
        DRY_RUN=true 상태에서 로그를 보고 정확도를 검증한 후에야
        실제 등록을 켜야 함. 설계 문서: "10개 이메일로 정확도 검증 후 해제."
    """
    event_body = {
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }

    if location:
        event_body["location"] = location
    if description:
        event_body["description"] = description
    if attendees:
        event_body["attendees"] = [{"email": a} for a in attendees]

    # DRY_RUN: 로그만 남기고 실제 API 호출 안 함
    if dry_run:
        logger.info(f"[DRY_RUN] Would create event: {summary} ({start} ~ {end})")
        return {
            "status": "dry_run",
            "summary": summary,
            "start": start,
            "end": end,
            "body": event_body,
        }

    # 실제 등록
    try:
        created = (
            service.events()
            .insert(calendarId=calendar_id, body=event_body)
            .execute()
        )

        logger.info(f"Created event: {created.get('htmlLink')}")
        return {
            "status": "created",
            "id": created["id"],
            "summary": created.get("summary", summary),
            "link": created.get("htmlLink", ""),
        }

    except Exception as e:
        logger.error(f"create_event failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "summary": summary,
        }
