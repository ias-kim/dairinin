"""
FastAPI 앱 — 폴링 루프 + 전체 파이프라인 통합.

모든 조각을 하나의 프로세스에서 실행:
    - asyncio background task: 15초마다 Gmail 폴링
    - FastAPI HTTP: Slack webhook 수신 (Week 2-3)

실행: uvicorn app:app --reload
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from graph.orchestrator import build_graph
from mcp_servers.gmail_mcp import archive_email_logic, add_label_logic, build_gmail_service
from mcp_servers.slack_mcp import build_slack_client, send_reply_notification
from utils.email_classifier import classify_email

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("dairinin")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

# 동시 실행 방지 플래그
_processing = False

# ──────────────────────────────────────────────
# 이메일 중복 처리 방지
# ──────────────────────────────────────────────

_processed_emails: set[str] = set()


def is_already_processed(email_id: str) -> bool:
    return email_id in _processed_emails


def mark_processed(email_id: str) -> None:
    _processed_emails.add(email_id)


def reset_processed_emails() -> None:
    """테스트용 초기화."""
    _processed_emails.clear()


# ──────────────────────────────────────────────
# Slack HMAC 서명 검증
# ──────────────────────────────────────────────

SLACK_SIGNATURE_MAX_AGE = 300  # 5분


def _verify_slack_signature(request_body: bytes, timestamp: str, signature: str) -> bool:
    """Slack X-Slack-Signature HMAC 검증.

    Slack 공식 검증 방식:
        1. 타임스탬프가 5분 이내인지 확인 (리플레이 공격 방어)
        2. sig_basestring = "v0:{timestamp}:{body}"
        3. HMAC-SHA256(signing_secret, sig_basestring)
        4. "v0=" + hex_digest 와 비교
    """
    signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        return True  # 개발 환경: 검증 스킵

    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False

    if abs(time.time() - ts) > SLACK_SIGNATURE_MAX_AGE:
        return False

    sig_base = f"v0:{timestamp}:{request_body.decode()}".encode()
    mac = hmac.new(signing_secret.encode(), sig_base, hashlib.sha256)
    expected = "v0=" + mac.hexdigest()

    return hmac.compare_digest(expected, signature)


def route_email(email: dict) -> None:
    """EmailClassifier로 분류 후 카테고리별 처리.

    calendar  → process_single_email (LangGraph 파이프라인)
    spam      → archive
    newsletter→ label + skip
    important → Slack 알림
    other     → skip
    classifier 오류 → pipeline으로 폴백 (안전 기본값)
    """
    text = f"{email.get('subject', '')} {email.get('snippet', '')}"
    email_id = email["id"]

    try:
        category = classify_email(text)
    except Exception as e:
        logger.warning(f"EmailClassifier failed for {email_id}, falling back to pipeline: {e}")
        process_single_email(email)
        return

    if category == "spam":
        logger.info(f"  → SPAM: archiving {email_id}")
        try:
            service = build_gmail_service()
            archive_email_logic(service, email_id)
        except Exception as e:
            logger.warning(f"archive failed: {e}")

    elif category == "newsletter":
        logger.info(f"  → NEWSLETTER: labeling {email_id}")
        try:
            service = build_gmail_service()
            add_label_logic(service, email_id, "NEWSLETTER")
        except Exception as e:
            logger.warning(f"add_label failed: {e}")

    elif category == "important":
        logger.info(f"  → IMPORTANT: Slack 알림 {email_id}")
        slack_channel = os.getenv("SLACK_CHANNEL_ID", "")
        if slack_channel:
            try:
                client = build_slack_client()
                send_reply_notification(
                    client,
                    slack_channel,
                    email.get("subject", ""),
                    email.get("from", ""),
                )
            except Exception as e:
                logger.warning(f"Slack notification failed: {e}")

    elif category == "calendar":
        logger.info(f"  → CALENDAR: pipeline {email_id}")
        process_single_email(email)

    else:  # other
        logger.info(f"  → OTHER: skipping {email_id}")


def process_single_email(email: dict) -> Optional[dict]:
    """이메일 1건을 LangGraph 파이프라인으로 처리.

    Args:
        email: {"id", "from", "subject", "snippet"}

    Returns:
        graph 실행 결과 dict, 실패 시 None
    """
    import uuid

    try:
        thread_id = str(uuid.uuid4())
        graph = build_graph()
        result = graph.invoke(
            {
                "email_id": email["id"],
                "raw_email": email.get("snippet", ""),
                "subject": email.get("subject", ""),
                "sender": email.get("from", ""),
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        return result
    except Exception as e:
        logger.error(f"Failed to process email {email['id']}: {e}")
        return None


async def poll_gmail_loop():
    """15초마다 Gmail을 폴링해서 파이프라인 실행.

    동작:
        1. fetch_emails로 안 읽은 이메일 가져오기
        2. 각 이메일에 대해 process_single_email 실행
        3. confidence 결과에 따라 로그 출력
           (Week 1: console HITL — Slack 전에 로그로 확인)
        4. 에러 발생해도 루프 계속

    동시 실행 방지:
        이전 사이클이 아직 실행 중이면 이번 사이클 스킵.
        15초 간격인데 LLM 호출이 오래 걸릴 수 있으므로.
    """
    global _processing

    while True:
        await asyncio.sleep(10)
        try:
            if _processing:
                logger.debug("Previous cycle still running, skipping")
                await asyncio.sleep(POLL_INTERVAL)
                continue

            _processing = True

            # Gmail에서 안 읽은 이메일 가져오기
            from mcp_servers.gmail_mcp import build_gmail_service, fetch_emails_logic

            try:
                service = build_gmail_service()
                emails = fetch_emails_logic(service)
            except Exception as e:
                logger.error(f"Gmail fetch failed: {e}")
                emails = []

            if emails:
                logger.info(f"Found {len(emails)} unread emails")

            for email in emails:
                email_id = email["id"]
                if is_already_processed(email_id):
                    logger.debug(f"Skipping already processed: {email_id}")
                    continue

                logger.info(f"Processing: [{email.get('subject')}] from {email.get('from')}")
                mark_processed(email_id)

                route_email(email)

        except Exception as e:
            logger.error(f"Poll loop error: {e}")

        finally:
            _processing = False

        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 라이프사이클.

    앱 시작 시 폴링 루프를 background task로 실행.
    앱 종료 시 task 취소.
    """
    logger.info(f"dairinin starting (poll every {POLL_INTERVAL}s)")
    task = asyncio.create_task(poll_gmail_loop())
    yield
    task.cancel()
    logger.info("dairinin stopped")


app = FastAPI(title="dairinin (代理人)", lifespan=lifespan)


@app.get("/health")
async def health():
    import os
    db_status = "disconnected"
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            import psycopg
            conn = psycopg.connect(database_url)
            conn.close()
            db_status = "connected"
        except Exception as e:
            db_status = f"error: {e}"
    return {"status": "ok", "service": "dairinin", "db": db_status}


@app.get("/")
async def root():
    return {"service": "dairinin (代理人)", "status": "running"}


# ──────────────────────────────────────────────
# Slack Events API webhook — HITL 반응 수신
# ──────────────────────────────────────────────

@app.post("/webhook/slack")
async def slack_webhook(request: Request):
    """Slack Events API webhook 수신."""
    import json as json_mod

    raw_body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(raw_body, timestamp, signature):
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    body = json_mod.loads(raw_body)

    # URL verification (Slack 앱 설정 시 1회)
    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body.get("challenge", "")})

    # Event callback
    if body.get("type") == "event_callback":
        event = body.get("event", {})

        if event.get("type") == "reaction_added":
            reaction = event.get("reaction", "")
            message_ts = event.get("item", {}).get("ts", "")

            if reaction in ("white_check_mark", "heavy_check_mark"):
                _resume_hitl(message_ts, "approve")
            elif reaction == "x":
                _resume_hitl(message_ts, "reject")

    return {"ok": True}


@app.post("/webhook/slack/interact")
async def slack_interact(request: Request):
    """Slack Interactivity webhook — Block Kit 버튼 클릭 수신.

    Slack은 버튼 클릭을 application/x-www-form-urlencoded로 보냄.
    payload 필드 안에 JSON이 들어있음.
    """
    import json as json_mod
    from urllib.parse import parse_qs

    raw_body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(raw_body, timestamp, signature):
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    # application/x-www-form-urlencoded 직접 파싱
    parsed = parse_qs(raw_body.decode())
    payload_str = parsed.get("payload", ["{}"])[0]
    payload = json_mod.loads(payload_str)

    actions = payload.get("actions", [])
    message_ts = payload.get("message", {}).get("ts", "")

    for action in actions:
        action_id = action.get("action_id", "")
        value = json_mod.loads(action.get("value", "{}"))

        if action_id == "hitl_approve":
            _resume_hitl(message_ts, "approve")
            # 버튼을 "✅ 등록됨"으로 교체
            return JSONResponse({
                "replace_original": True,
                "text": f"✅ 등록 완료: {value.get('email_id', '')}",
            })
        elif action_id == "hitl_reject":
            _resume_hitl(message_ts, "reject")
            return JSONResponse({
                "replace_original": True,
                "text": f"❌ 무시됨: {value.get('email_id', '')}",
            })

    return JSONResponse({"ok": True})


def _resume_hitl(slack_ts: str, decision: str):
    """HITL 그래프를 resume.

    slack_ts로 thread_id를 찾고, LangGraph를 resume.
    """
    from agents.notifier import get_hitl_store
    from langgraph.types import Command

    hitl = get_hitl_store()
    mapping = hitl.lookup_by_slack_ts(slack_ts)

    if not mapping:
        logger.warning(f"No HITL mapping for slack_ts={slack_ts}")
        return

    thread_id = mapping["thread_id"]
    email_id = mapping["email_id"]

    logger.info(f"HITL resume: {decision} for email_id={email_id}")

    try:
        graph = build_graph()
        graph.invoke(
            Command(resume=decision),
            config={"configurable": {"thread_id": thread_id}},
        )
        hitl.remove(slack_ts)
    except Exception as e:
        logger.error(f"HITL resume failed: {e}")
