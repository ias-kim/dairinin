"""
FastAPI 앱 — 폴링 루프 + 전체 파이프라인 통합.

모든 조각을 하나의 프로세스에서 실행:
    - asyncio background task: 15초마다 Gmail 폴링
    - FastAPI HTTP: Slack webhook 수신 (Week 2-3)

실행: uvicorn app:app --reload
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request

from graph.orchestrator import build_graph

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("dairinin")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

# 동시 실행 방지 플래그
_processing = False


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
                logger.info(f"Processing: [{email.get('subject')}] from {email.get('from')}")

                result = process_single_email(email)

                if result is None:
                    logger.warning(f"  → Failed to process {email['id']}")
                    continue

                confidence = result.get("confidence", 0.0)
                parsed = result.get("parsed_event")

                if parsed is None:
                    logger.info(f"  → Not an event, skipping")
                elif confidence >= 0.8:
                    # Week 1: 로그만 (DRY_RUN)
                    logger.info(
                        f"  → AUTO_REGISTER (confidence={confidence}): "
                        f"{parsed.title} at {parsed.event_datetime}"
                    )
                else:
                    # Week 1: console HITL (Slack 전)
                    logger.warning(
                        f"  → HITL REQUIRED (confidence={confidence}): "
                        f"{parsed.title} at {parsed.event_datetime}"
                    )

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
    return {"status": "ok", "service": "dairinin"}


@app.get("/health/db")
async def health_db():
    """DB 연결 상태 확인.

    DATABASE_URL 환경변수로 PostgreSQL에 접속해 SELECT 1을 실행.
    연결 성공 시 {"status": "ok", "database": "connected"},
    실패 시 {"status": "error", "database": "disconnected", "error": "..."}.
    """
    import psycopg
    from fastapi.responses import JSONResponse

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "database": "disconnected",
                "error": "DATABASE_URL environment variable is not set",
            },
        )

    try:
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        logger.warning(f"DB health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "database": "disconnected",
                "error": str(e),
            },
        )


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
    from fastapi.responses import JSONResponse

    body = await request.json()

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
    from fastapi.responses import JSONResponse

    form = await request.form()
    payload = json_mod.loads(form.get("payload", "{}"))

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
