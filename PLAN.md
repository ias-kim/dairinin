<!-- /autoplan restore point: /Users/gwan/.gstack/projects/ias-kim-dairinin/main-autoplan-restore-20260404-123852.md -->
# dairinin (代理人) — System Architecture Plan

**Branch:** main | **Date:** 2026-04-04 | **Status:** Active Development

---

## What This Is

개인 이메일을 자동으로 Google Calendar에 등록해주는 멀티 에이전트 시스템.

Gmail 폴링 → 이벤트 탐지 → LangGraph 파이프라인 → 캘린더 자동 등록 or HITL(Slack 확인)

---

## Current Architecture

```
Gmail (15s polling)
    │
    ▼
EventDetector (utils/event_detector.py)
    │  1단계: regex (날짜/시간 키워드)
    │  2단계: LLM yes/no (gpt-4o-mini)
    │
    ▼
EmailClassifier (utils/email_classifier.py)
    │  calendar / spam / newsletter / important / other
    │
    ▼
LangGraph Pipeline (graph/orchestrator.py)
    │
    ├─ parser_node (agents/parser.py)
    │      GPT-4o → EventJSON(title, event_datetime, location, ...)
    │      compute_confidence() → 0.0~1.0
    │
    ├─ scheduler_node (agents/scheduler.py)
    │      Google Calendar API → conflicts[]
    │
    ├─ conflict_node (agents/conflict.py)
    │      confidence ≥ 0.8 + 충돌 없음 → auto_register
    │      confidence < 0.8 or 충돌 → hitl_required
    │      datetime 없음 or 과거 → skip
    │
    └─ notifier_node (agents/notifier.py)
           auto_register → create_event() + send_reply() + Slack 알림 + mark_read()
           hitl_required → Slack HITL 메시지 + interrupt() 대기
           skip          → mark_read() only
```

## MCP Servers

| 서버 | 역할 |
|------|------|
| `mcp_servers/gmail_mcp.py` | fetch_emails, mark_read, send_reply, archive, add_label |
| `mcp_servers/calendar_mcp.py` | get_events, create_event, check_conflicts |
| `mcp_servers/slack_mcp.py` | send_hitl_message, send_reply_notification |
| `mcp_servers/memory_mcp.py` | mem0 패턴 저장/조회 |

## Infrastructure

- **FastAPI** (app.py): 폴링 루프 + Slack webhook 수신
- **PostgreSQL**: LangGraph checkpoint (Railway 배포)
- **LangGraph**: 멀티에이전트 오케스트레이션 + HITL interrupt/resume

---

## Current State

### Done
- [x] Gmail 폴링 루프 (15초)
- [x] EventDetector (regex + LLM 2단계)
- [x] EmailClassifier (5개 카테고리)
- [x] Parser Agent (GPT-4o structured output)
- [x] Scheduler Agent (충돌 확인)
- [x] Conflict Agent (auto/hitl/skip 결정)
- [x] Notifier Agent (캘린더 등록 + HITL)
- [x] Gmail MCP (fetch, mark_read, send_reply, archive, add_label)
- [x] Slack HITL (Block Kit 버튼 + webhook resume)
- [x] send_reply + send_reply_notification (이메일 답장 + Slack 알림)
- [x] PostgreSQL checkpoint (Railway)
- [x] 95/100 테스트 통과

### Known Issues

#### 1. 테스트 하드코딩 날짜 (5개 FAIL)
- `tests/test_conflict_agent.py`: `datetime(2026, 4, 1, ...)` → 이미 과거
- `tests/test_orchestrator.py`: 동일 문제
- **원인**: conflict_decision_node의 과거 날짜 skip 로직에 걸림
- **해결**: `datetime.now() + timedelta(days=7)` 등 상대 날짜로 교체

#### 2. EmailClassifier 파이프라인 미연결
- `utils/email_classifier.py` 구현 완료 + 테스트 완료
- `app.py`의 폴링 루프에 아직 연결 안 됨
- 현재: 모든 이메일이 LangGraph 파이프라인으로 들어감
- 목표: spam → archive, newsletter → label+skip, important → Slack 알림, calendar만 파이프라인

#### 3. CLAUDE.md 스킬 라우팅 룰 없음
- `/autoplan` 등 gstack 스킬을 자동 호출하지 않음

---

## Next Steps (Priority Order)

### Priority 0: 보안 + Dedup (Critical)
- `app.py` `/webhook/slack`, `/webhook/slack/interact`에 `X-Slack-Signature` HMAC 검증 추가
- `app.py` 폴링 루프에 `processed_email_ids` 세트 추가 (재시작 간 영속화는 Priority 3에서)
- TDD 적용: 테스트 먼저 → RED → 구현 → GREEN

### Priority 1: 테스트 수정 (5개 FAIL → GREEN)
- `tests/test_conflict_agent.py`: 하드코딩 날짜를 `datetime.now(timezone.utc) + timedelta(days=N)`으로 교체
- `tests/test_orchestrator.py`: 동일 수정

### Priority 2: EmailClassifier → 폴링 루프 연결
- `app.py`의 `process_single_email()`에 classify_email() 통합
- spam: archive_email_logic()
- newsletter: add_label_logic("NEWSLETTER") + skip 파이프라인
- important: Slack 알림 (send_reply_notification 변형)
- calendar: 기존 LangGraph 파이프라인

### Priority 3: DRY_RUN 모드 기본값 검토
- 현재 `DRY_RUN=true`가 기본 → 실제 캘린더 미등록
- 배포 환경에서 `DRY_RUN=false` 확인 필요

### Priority 4: mem0 실제 연동 검증
- `mcp_servers/memory_mcp.py`가 Railway에서 실제로 동작하는지 확인
- 로컬 fallback과 prod mem0 분리

---

## Tech Stack

| 컴포넌트 | 기술 |
|----------|------|
| LLM | GPT-4o (파싱), GPT-4o-mini (분류/탐지) |
| 오케스트레이션 | LangGraph 0.4+ |
| 메모리 | mem0ai |
| DB | PostgreSQL (psycopg3) |
| API 서버 | FastAPI + uvicorn |
| 배포 | Railway |
| 테스트 | pytest (TDD) |

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |

**VERDICT:** APPROVED — /autoplan 완료. P0 보안 → P1 테스트 → P2 classifier → P3 DRY_RUN → P4 mem0 순서로 진행.

---

## Decision Audit Trail

<!-- AUTONOMOUS DECISION LOG -->

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|----------------|-----------|-----------|---------|
| 1 | CEO | Mode: SELECTIVE EXPANSION | Mechanical | P1+P3 | 전략 변경 없이 즉시 실용적 개선 집중 | SCOPE EXPANSION |
| 2 | CEO | Priority 순서 유지 (1→2→3→4) | Mechanical | P3 | 검증된 코드 활용, 즉시 실용적 | C: 전체 리팩토링 |
| 3 | CEO | Gmail Push API → TODOS.md | Mechanical | P2+P3 | 폴링도 동작하고, Push API는 별도 인프라 필요 | 즉시 교체 |
| 4 | CEO | DRY_RUN 환경변수 문서화 추가 | Mechanical | P2 | blast radius 1 파일, 즉각적 가치 | 그냥 무시 |
| 5 | CEO | UI 없음 → Phase 2 Design 스킵 | Mechanical | P5 | PLAN.md에 UI 관련 키워드 없음 | Design 강제 실행 |
| 6 | Eng | HITL thread_id "없음" → 실제 구현됨 확인 | Mechanical | P5 | db/hitl_store.py PostgreSQL 저장 구현 확인 | 재구현 |
| 7 | Eng | Gmail 토큰 "파일 의존" → 환경변수 기반 확인 | Mechanical | P5 | GOOGLE_REFRESH_TOKEN 환경변수 사용 중 | 재구현 |
| 8 | Eng | Slack HMAC 검증 → PLAN 추가 Priority | Taste | P1 | Security critical, blast radius 1 파일 | 무시 |
| 9 | Eng | 이메일 dedup → PLAN 추가 | Taste | P1+P2 | 중복 등록 방지, blast radius app.py+test | 무시 |
