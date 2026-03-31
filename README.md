# dairinin (代理人)

---

## 🇯🇵 日本語

メールが届いたら、自動でGoogle Calendarに登録してくれる個人スケジュールAIエージェント。

> "何もしていないのに、カレンダーにもう登録されてた。"

### アーキテクチャ

![Architecture](docs/LangGraph.drawio.png)

### 処理フロー

```mermaid
sequenceDiagram
    participant Gmail
    participant App as App (FastAPI)
    participant Detector as EventDetector
    participant Parser as Parser Agent<br/>(GPT-4o)
    participant Scheduler as Scheduler Agent<br/>(Google Calendar)
    participant Conflict as Conflict Agent
    participant Notifier as Notifier Agent
    participant Slack
    participant User as 人間 (User)
    participant Calendar as Google Calendar
    participant Memory as mem0<br/>(Memory)
    participant DB as PostgreSQL<br/>(Checkpoint)

    Note over App,Gmail: 1. メール受信 (15秒ポーリング)

    App->>Gmail: fetch_emails() — 未読メール取得
    Gmail-->>App: [{id, subject, from, snippet}]

    Note over App,Detector: 2. 前処理フィルター (LLMコスト削減)

    App->>Detector: should_process_email(text)
    Detector->>Detector: 1段階: 日付/時刻/キーワード regex
    alt キーワードなし
        Detector-->>App: False → skip
    end
    Detector->>Detector: 2段階: LLM yes/no (gpt-4o-mini)
    Detector-->>App: True / False

    Note over App,DB: 3. LangGraphパイプライン開始

    App->>DB: thread_id生成 + checkpoint初期化
    App->>Parser: parse_email_node(email)

    Parser->>Memory: 過去パターン照会 (mem0)
    Memory-->>Parser: 類似メール処理履歴
    Parser->>Parser: GPT-4oでイベント抽出<br/>(title, datetime, location)
    Parser->>Parser: confidence計算 (ルールベース)
    Parser-->>DB: 状態保存 (parsed_event, confidence)

    Note over Parser,Conflict: 4. スケジュール衝突確認

    Parser->>Scheduler: schedule_check_node()
    Scheduler->>Calendar: 該当時間帯の既存予定照会
    Calendar-->>Scheduler: conflicts[]
    Scheduler-->>DB: 状態保存 (conflicts)

    Scheduler->>Conflict: conflict_decision_node()

    alt event_datetime なし
        Conflict-->>Notifier: action = skip
    else confidence < 0.8 OR 衝突あり
        Conflict-->>Notifier: action = hitl_required
    else confidence ≥ 0.8 AND 衝突なし
        Conflict-->>Notifier: action = auto_register
    end

    Note over Notifier,Calendar: 5a. 自動登録パス

    alt action = auto_register
        Notifier->>Calendar: create_event(title, datetime, location)
        Calendar-->>Notifier: event_id
        Notifier->>Memory: 成功パターン保存 (mem0)
        Notifier-->>DB: 完了 checkpoint
    end

    Note over Notifier,User: 5b. 人間確認パス (HITL)

    alt action = hitl_required
        Notifier->>Slack: send_hitl_message()<br/>confidence, 衝突情報, ボタン付き
        Slack-->>User: 📩 予定確認依頼 [✅ 登録] [❌ 無視]
        Notifier->>DB: interrupt() — グラフ一時停止

        User->>Slack: ボタンクリック (approve / reject)
        Slack->>App: POST /webhook/slack/interact

        App->>DB: slack_ts → thread_id照会 (HitlStore)
        App->>DB: Command(resume=approve/reject)

        alt approve
            DB->>Calendar: create_event() — グラフ再開
            Calendar-->>DB: event_id
            DB->>Memory: HITL承認パターン保存
            DB->>Slack: ✅ 登録完了メッセージに置換
        else reject
            DB->>Memory: 却下パターン保存
            DB->>Slack: ❌ 無視されましたメッセージに置換
        end
    end

    Note over Memory,Calendar: 6. 学習 → 次サイクルに反映
    Memory->>Parser: 次回類似メール処理時にパターン参照
```

### 主な機能

| 機能 | 説明 |
|------|------|
| **自動イベント解析** | GPT-4o-miniがメールを読んでタイトル・日時・場所・参加者を抽出 |
| **信頼度による分岐** | confidence ≥ 0.8で自動登録、未達なら確認依頼 |
| **スケジュール衝突検出** | 既存の予定と重複する場合、自動登録からHITLへ切替 |
| **Slack HITL** | 不確かなイベントはSlackボタン(✅/❌)で人間が最終判断 |
| **グラフ再開** | サーバー再起動後もSlackボタン押下で中断地点から正確に再開 |
| **パターン学習** | 自動承認されたイベントをmem0に保存 → 繰り返しパターンを認識 |
| **DRY_RUNモード** | 実際の登録前にログで精度検証が可能 |

### 技術スタック

| コンポーネント | 技術 |
|----------|------|
| オーケストレーション | LangGraph |
| LLM | GPT-4o-mini (LangChain) |
| APIサーバー | FastAPI + uvicorn |
| 外部連携 | FastMCP (Gmail, Calendar, Slack, mem0) |
| パターン学習 | mem0 + pgvector + Neo4j |
| DB | PostgreSQL (LangGraph checkpointer + HITL mapping) |
| デプロイ | Railway |
| パッケージ管理 | uv |

### 実行方法

```bash
git clone https://github.com/ias-kim/dairinin.git
cd dairinin

uv sync
cp .env.example .env
# .env にキーを入力

python scripts/get_gmail_token.py
docker compose up -d

uv run uvicorn app:app --reload
```

```bash
uv run pytest -v   # 63件、~9秒
```

---

## 🇰🇷 한국어

이메일이 오면 자동으로 Google Calendar에 등록해주는 개인 스케줄 AI 에이전트.

> "아무것도 안 했는데 캘린더에 이미 등록됨."

### 아키텍처

![Architecture](docs/LangGraph.drawio.png)

### 처리 흐름

```mermaid
sequenceDiagram
    participant Gmail
    participant App as App (FastAPI)
    participant Detector as EventDetector
    participant Parser as Parser Agent<br/>(GPT-4o)
    participant Scheduler as Scheduler Agent<br/>(Google Calendar)
    participant Conflict as Conflict Agent
    participant Notifier as Notifier Agent
    participant Slack
    participant User as 사람 (User)
    participant Calendar as Google Calendar
    participant Memory as mem0<br/>(Memory)
    participant DB as PostgreSQL<br/>(Checkpoint)

    Note over App,Gmail: 1. 이메일 수신 (15초 폴링)

    App->>Gmail: fetch_emails() — 안 읽은 메일 조회
    Gmail-->>App: [{id, subject, from, snippet}]

    Note over App,Detector: 2. 전처리 필터 (LLM 절약)

    App->>Detector: should_process_email(text)
    Detector->>Detector: 1차: 날짜/시간/키워드 regex
    alt 키워드 없음
        Detector-->>App: False → skip
    end
    Detector->>Detector: 2차: LLM yes/no (gpt-4o-mini)
    Detector-->>App: True / False

    Note over App,DB: 3. LangGraph 파이프라인 시작

    App->>DB: thread_id 생성 + checkpoint 초기화
    App->>Parser: parse_email_node(email)

    Parser->>Memory: 과거 패턴 조회 (mem0)
    Memory-->>Parser: 유사 이메일 처리 이력
    Parser->>Parser: GPT-4o로 이벤트 추출<br/>(title, datetime, location)
    Parser->>Parser: confidence 계산 (규칙 기반)
    Parser-->>DB: 상태 저장 (parsed_event, confidence)

    Note over Parser,Conflict: 4. 일정 충돌 확인

    Parser->>Scheduler: schedule_check_node()
    Scheduler->>Calendar: 해당 시간대 기존 일정 조회
    Calendar-->>Scheduler: conflicts[]
    Scheduler-->>DB: 상태 저장 (conflicts)

    Scheduler->>Conflict: conflict_decision_node()

    alt event_datetime 없음
        Conflict-->>Notifier: action = skip
    else confidence < 0.8 OR 충돌 있음
        Conflict-->>Notifier: action = hitl_required
    else confidence ≥ 0.8 AND 충돌 없음
        Conflict-->>Notifier: action = auto_register
    end

    Note over Notifier,Calendar: 5a. 자동 등록 경로

    alt action = auto_register
        Notifier->>Calendar: create_event(title, datetime, location)
        Calendar-->>Notifier: event_id
        Notifier->>Memory: 성공 패턴 저장 (mem0)
        Notifier-->>DB: 완료 checkpoint
    end

    Note over Notifier,User: 5b. 사람 확인 경로 (HITL)

    alt action = hitl_required
        Notifier->>Slack: send_hitl_message()<br/>confidence, 충돌 정보, 버튼 포함
        Slack-->>User: 📩 일정 확인 요청 [✅ 등록] [❌ 무시]
        Notifier->>DB: interrupt() — 그래프 일시 정지

        User->>Slack: 버튼 클릭 (approve / reject)
        Slack->>App: POST /webhook/slack/interact

        App->>DB: slack_ts → thread_id 조회 (HitlStore)
        App->>DB: Command(resume=approve/reject)

        alt approve
            DB->>Calendar: create_event() — 그래프 재개
            Calendar-->>DB: event_id
            DB->>Memory: HITL 승인 패턴 저장
            DB->>Slack: ✅ 등록 완료 메시지로 교체
        else reject
            DB->>Memory: 거절 패턴 저장
            DB->>Slack: ❌ 무시됨 메시지로 교체
        end
    end

    Note over Memory,Calendar: 6. 학습 → 다음 사이클에 반영
    Memory->>Parser: 다음 유사 이메일 처리 시 패턴 참조
```

### 주요 기능

| 기능 | 설명 |
|------|------|
| **자동 이벤트 파싱** | GPT-4o-mini가 이메일을 읽고 제목, 날짜, 장소, 참석자를 추출 |
| **신뢰도 기반 분기** | confidence ≥ 0.8이면 자동 등록, 미달이면 사람에게 확인 요청 |
| **충돌 감지** | 기존 캘린더 일정과 겹치면 자동 등록 대신 HITL로 전환 |
| **Slack HITL** | 불확실한 이벤트는 Slack 버튼(✅/❌)으로 사람이 최종 결정 |
| **그래프 재개** | 서버 재시작 후에도 Slack 버튼 클릭 시 중단된 지점부터 재개 |
| **패턴 학습** | 자동 승인된 이벤트를 mem0에 저장 → 반복 패턴 인식 |
| **DRY_RUN 모드** | 실제 등록 전 로그로 정확도 검증 가능 |

### 기술 스택

| 컴포넌트 | 기술 |
|----------|------|
| 오케스트레이션 | LangGraph |
| LLM | GPT-4o-mini (LangChain) |
| API 서버 | FastAPI + uvicorn |
| 외부 연동 | FastMCP (Gmail, Calendar, Slack, mem0) |
| 패턴 학습 | mem0 + pgvector + Neo4j |
| DB | PostgreSQL (LangGraph checkpointer + HITL 매핑) |
| 배포 | Railway |
| 패키지 관리 | uv |

### 프로젝트 구조

```
dairinin/
├── app.py                  FastAPI + 폴링 루프 + Slack webhook
├── graph/
│   ├── state.py            ScheduleState
│   └── orchestrator.py     StateGraph (노드 연결 + 분기)
├── agents/
│   ├── parser.py           이메일 → EventJSON (LLM)
│   ├── scheduler.py        캘린더 충돌 체크
│   ├── conflict.py         auto / hitl / skip 판단
│   └── notifier.py         실행 + interrupt/resume
├── mcp_servers/
│   ├── gmail_mcp.py        Gmail API
│   ├── calendar_mcp.py     Google Calendar API
│   ├── slack_mcp.py        Slack HITL 메시지
│   └── memory_mcp.py       mem0 패턴 저장
├── db/
│   └── hitl_store.py       slack_ts ↔ thread_id 매핑
├── utils/
│   ├── models.py           EventJSON (Pydantic)
│   └── confidence.py       신뢰도 계산
└── tests/                  63개 테스트
```

### 실행 방법

```bash
git clone https://github.com/ias-kim/dairinin.git
cd dairinin

uv sync
cp .env.example .env
# .env에 키 입력

python scripts/get_gmail_token.py   # OAuth refresh_token 발급
docker compose up -d                # PostgreSQL + mem0

uv run uvicorn app:app --reload
```

```bash
uv run pytest -v   # 63개, ~9초
```

### 테스트 현황

```
63개 테스트 | ~9초 | Python 3.12

test_confidence.py       8개   신뢰도 계산
test_gmail_mcp.py        5개   Gmail fetch + mark_read
test_calendar_mcp.py    10개   일정 조회 + 충돌 감지 + 등록
test_memory_mcp.py       5개   패턴 저장/조회/격리
test_slack_mcp.py        3개   HITL 메시지 전송
test_parser_agent.py     4개   LLM 파싱 (mock)
test_scheduler_agent.py  4개   충돌 체크 + 타임존
test_conflict_agent.py   4개   판단 분기
test_notifier_agent.py   6개   auto/hitl/skip + interrupt
test_orchestrator.py     3개   전체 파이프라인 통합
test_hitl_store.py       8개   dedup + TTL + PostgreSQL 분기
test_app.py              3개   process_single_email
```
