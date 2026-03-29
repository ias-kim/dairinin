# dairinin (代理人)

Personal Schedule Multi-Agent System — 이메일이 오면 자동으로 Google Calendar에 등록.

"아무것도 안 했는데 캘린더에 이미 등록됨."

## 아키텍처

```
Gmail (15s 폴링)
    │
    ▼
[Parser Agent]        LLM(GPT-4o-mini)이 이메일 → EventJSON 변환
    │                 compute_confidence()로 신뢰도 계산
    ▼
[Scheduler Agent]     Google Calendar에서 충돌 확인
    │
    ▼
[Conflict Agent]      규칙 기반 판단 (LLM 안 씀)
    │
    ├── confidence ≥ 0.8 + 충돌 없음 → auto_register
    ├── confidence < 0.8 or 충돌     → hitl_required
    └── 이벤트 아님                    → skip
    │
    ▼
[Notifier Agent]
    ├── auto_register → Calendar 등록 + mem0 패턴 저장 + 이메일 읽음 처리
    ├── hitl_required → Slack 전송 + interrupt() → ✅/❌ 반응으로 resume
    └── skip          → 이메일 읽음 처리
```

## 기술 스택

| 컴포넌트 | 기술 | 왜 |
|----------|------|-----|
| 오케스트레이션 | LangGraph | HITL interrupt/resume + PostgreSQL 상태 영속 |
| LLM | GPT-4o-mini (via LangChain) | 이메일 파싱. 비용 효율. |
| MCP 서버 | FastMCP | 외부 서비스(Gmail, Calendar, Slack, mem0) 분리 |
| 패턴 학습 | mem0 | 사용자 승인 패턴 기억 → threshold 동적 조정 |
| API 서버 | FastAPI | 폴링 루프 + Slack webhook 동시 처리 |
| DB | PostgreSQL | LangGraph checkpointer + hitl_pending |
| 벡터 저장 | pgvector | mem0 임베딩 저장 |
| 그래프 DB | Neo4j | mem0 엔티티 관계 |

## 프로젝트 구조

```
dairinin/
├── app.py                        FastAPI + 폴링 루프 + Slack webhook
├── graph/
│   ├── state.py                  ScheduleState (이메일 1건의 상태)
│   └── orchestrator.py           StateGraph (노드 연결 + 분기)
├── agents/
│   ├── parser.py                 Parser Agent (LLM → EventJSON)
│   ├── scheduler.py              Scheduler Agent (충돌 체크)
│   ├── conflict.py               Conflict Agent (auto/hitl/skip 판단)
│   └── notifier.py               Notifier Agent (실행 + interrupt)
├── mcp_servers/
│   ├── gmail_mcp.py              Gmail API (fetch + mark_read)
│   ├── calendar_mcp.py           Calendar API (get + check + create)
│   ├── slack_mcp.py              Slack API (HITL 메시지)
│   └── memory_mcp.py             mem0 (패턴 학습)
├── db/
│   └── hitl_store.py             HITL 매핑 (slack_ts ↔ thread_id)
├── utils/
│   ├── models.py                 EventJSON (Pydantic)
│   └── confidence.py             compute_confidence()
├── tests/                        60개 테스트
├── scripts/
│   └── get_gmail_token.py        OAuth refresh_token 발급
├── docker/
│   └── mem0.Dockerfile           mem0 서버 커스텀 이미지
├── docker-compose.yml            PostgreSQL + mem0 + pgvector + Neo4j
└── pyproject.toml                Python >=3.12,<3.14
```

## 실행 방법

### 1. 사전 준비

- Python 3.12+
- Docker Desktop
- Google Cloud 프로젝트 (Gmail API + Calendar API 활성화)
- OpenAI API 키
- Slack App (HITL용, 선택)

### 2. 환경 설정

```bash
git clone https://github.com/YOUR_USERNAME/dairinin.git
cd dairinin

# Python 환경
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 환경변수
cp .env.example .env
# .env에 키 입력:
#   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, OPENAI_API_KEY
#   (선택) SLACK_BOT_TOKEN, SLACK_CHANNEL_ID

# OAuth refresh_token 발급
python scripts/get_gmail_token.py

# Docker 서비스 시작
docker compose up -d
```

### 3. 테스트

```bash
pytest -v    # 60개 테스트, ~8초
```

### 4. 실행

```bash
# DRY_RUN 모드 (캘린더에 실제 등록 안 함)
DRY_RUN=true uvicorn app:app --reload

# 실제 모드
DRY_RUN=false uvicorn app:app --reload
```

15초마다 Gmail을 폴링하고, 이벤트 이메일을 발견하면 자동 처리.

## 핵심 설계 결정

**왜 LLM 자기 평가를 안 쓰는가?**
LLM이 "이거 잘 파싱한 것 같아"라고 하는 건 miscalibrated. 대신 `compute_confidence()`가 필수 필드(title, datetime) 완성도를 규칙 기반으로 측정. 예측 가능하고 테스트 가능.

**왜 LangGraph인가?**
HITL이 서비스 재시작을 넘어 살아남아야 하기 때문. `interrupt()`로 그래프를 일시정지하면 PostgreSQL에 상태가 저장되고, Slack reaction이 오면 `Command(resume=...)`으로 정확히 중단된 지점부터 재개.

**왜 MCP 서버를 분리하는가?**
에이전트는 "fetch_emails"만 알면 됨. Gmail이든 Outlook이든 MCP 서버만 교체하면 에이전트 코드는 그대로.

**왜 DRY_RUN 모드가 있는가?**
Parser 정확도를 검증하기 전에 이상한 이벤트가 캘린더에 등록되면 안 됨. 10개 이메일로 정확도 검증 후 해제.

## 테스트 현황

```
60개 테스트 | ~8초 | Python 3.12

test_confidence.py      8개   compute_confidence 전체 경로
test_gmail_mcp.py       5개   fetch_emails + mark_read
test_calendar_mcp.py   10개   get_events + check_conflicts + create_event
test_memory_mcp.py      5개   패턴 저장/조회/격리/TTL
test_slack_mcp.py       3개   HITL 메시지 전송
test_parser_agent.py    4개   LLM 파싱 (mock)
test_scheduler_agent.py 4개   충돌 체크 + 타임존
test_conflict_agent.py  4개   판단 분기 전체
test_notifier_agent.py  6개   auto/hitl/skip + interrupt
test_orchestrator.py    3개   전체 파이프라인 통합
test_hitl_store.py      5개   dedup + TTL 만료
test_app.py             3개   process_single_email
```
