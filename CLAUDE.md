# CLAUDE.md — 공시 조기경보 에이전트 (SKALA AI Agent Capstone)

이 파일은 팀원 4명이 각자 Claude Code로 작업할 때 **모두가 따르는 약속**이다.
여기 적힌 이름(폴더, 테이블, 도구, State)을 바꾸려면 먼저 팀 채팅에 알리고 이 파일을 고친다.
설계 배경과 기준을 정한 이유는 팀 설계안 문서(Claude Docs "공시 조기경보 에이전트 설계안")에 있다.

---

## 0. 한 줄 소개

증권사 PB를 위한 B2B 에이전트. 공시 속 **숨은 위험 신호를 점수로 쌓아** 악재가 터지기 전에 경보를 띄우고,
해당 종목을 가진 고객을 찾아 **연락 우선순위와 안내문 초안**을 만든 뒤, **PB 승인**을 받아 연락 업무를 만든다.

- 점수 계산·등급 결정 = **규칙(코드)**. 공시 문장 해석·근거 설명·안내문 = **AI**.
- AI는 점수를 바꿀 수 없다. 근거를 못 찾으면 추측하지 않고 "수동 확인 필요"로 남긴다.
- PB 승인 전에는 어떤 연락 업무도 만들어지지 않는다.
- 안내문에 매수·매도 권유는 절대 넣지 않는다.

마감: 2026-10-21(수) 아침. **기능을 늘리지 말고 데모 시나리오 하나(9장)가 끝까지 도는 것**이 최우선이다.

---

## 1. 기술 스택 (확정)

| 영역 | 선택 |
| --- | --- |
| 언어 | Python 3.11 (패키지 관리: uv, `uv.lock`으로 버전 고정) |
| Agent | LangGraph + langgraph-checkpoint-sqlite |
| LLM 연결 | langchain-anthropic(기본), langchain-openai(대체 모델) |
| MCP 서버 | FastMCP, Streamable HTTP 방식 (서버마다 별도 프로세스) |
| MCP 클라이언트 | langchain-mcp-adapters (MultiServerMCPClient) |
| API 서버 | FastAPI + Uvicorn, 진행 이벤트는 SSE |
| 인증 | JWT (PyJWT) + bcrypt 비밀번호 해시 |
| DB | SQLite (`data/app.db`), SQLAlchemy 2 |
| 벡터 DB | Chroma (로컬 파일, `data/chroma/`) |
| 화면 | Vue 3 + Vite + Vue Router + Pinia + axios |
| 관측 | LangSmith |
| 큐 (가산점) | RabbitMQ (docker compose) + aio-pika |

### 모델 (`.env`로 바꿀 수 있게)

| 용도 | 모델 | 이유 |
| --- | --- | --- |
| 기본 LLM | `claude-haiku-5-5` | 입력 $0.10 / 출력 $0.50 (100만 토큰당). 한국어 공시 읽기와 도구 호출에 충분하고 가장 저렴한 편 |
| 대체 LLM (Recovery) | `gpt-5.4-mini` | 다른 회사 모델이라 한쪽 서버 장애(503) 때 넘어갈 수 있음 |
| 임베딩 | `text-embedding-3-small` (OpenAI) | 아래 참고 |

임베딩을 `text-embedding-3-small`로 정한 이유:
- RAG 문서가 회사 5개 공시 + 참고 문서 4개 정도라 양이 적다. 전부 임베딩해도 비용이 몇십 원 수준이다.
- 대체 LLM 때문에 OpenAI 키가 어차피 필요하다. 키 하나로 해결된다.
- 로컬 모델(bge-m3 등)은 한국어 성능은 좋지만 2GB 넘게 내려받아야 하고 노트북마다 속도가 달라서, 이틀 안에 4명 환경을 맞추기 어렵다.
- OpenAI 키를 쓸 수 없게 되면 `EMBEDDING_PROVIDER=local`로 `BAAI/bge-m3`를 쓰도록 코드에 분기만 만들어 둔다.

---

## 2. 폴더 구조와 담당

**각자 자기 폴더만 고친다.** 다른 사람 폴더를 고쳐야 하면 그 담당자에게 요청한다.
`shared/`는 모두가 import하는 계약이라 **바꾸기 전에 반드시 팀에 알린다.**

```
capstone/
├── CLAUDE.md                 # 이 파일 (모두)
├── README.md                 # 실행 방법 (D)
├── .env.example              # 환경 변수 목록 (D)
├── docker-compose.yml        # RabbitMQ (C)
├── pyproject.toml / uv.lock  # 의존성 (처음 만든 사람, 이후 추가 시 알림)
│
├── shared/                   # [B] 공통 계약 — 바꾸면 팀에 알림
│   ├── config.py             # .env 읽기 (pydantic-settings)
│   ├── db.py                 # SQLAlchemy 엔진, 세션
│   ├── models.py             # 4-1장 테이블 정의
│   └── schemas.py            # 도구 입출력, 공통 응답 형식, 오류 코드
│
├── mcp_servers/              # [B] MCP 담당
│   ├── business/
│   │   ├── server.py         # FastMCP, 포트 8001
│   │   ├── tools/            # 4-2장 도구 (파일 하나에 도구 하나)
│   │   └── rules/
│   │       ├── rules.yaml    # 점수표·경보 기준·영향도 기준 (숫자는 전부 여기)
│   │       ├── risk_rules.py # calculate_risk_score 계산 로직
│   │       └── impact_rules.py
│   └── rag/
│       ├── server.py         # FastMCP, 포트 8002
│       └── ingest.py         # rag_docs/ → Chroma 적재
│
├── agent/                    # [A] Agent 담당
│   ├── state.py              # 4-5장 State
│   ├── graph.py              # 노드·분기·interrupt 연결
│   ├── nodes/                # 노드마다 파일 하나
│   ├── mcp_client.py         # MultiServerMCPClient 설정
│   ├── llm.py                # 모델 생성 (기본/대체)
│   └── prompts/              # Harness 1 Instructions — *.md
│
├── harness/                  # [D] Harness 담당 (agent가 import)
│   ├── loop_limits.py        # 5 Agent Loop
│   ├── guardrails.py         # 6 금지 표현 검사
│   ├── masking.py            # 6 개인정보 마스킹
│   ├── recovery.py           # 7 재시도·모델 교체
│   ├── context.py            # 3 토큰 예산·요약·결과 자르기
│   └── tracing.py            # 8 LangSmith 메타데이터
│
├── api/                      # [C] 서버·인증 담당
│   ├── main.py               # FastAPI, 포트 8000
│   ├── auth.py               # 로그인, JWT
│   ├── routes/               # alerts, assessments, approvals, tasks, events(SSE)
│   └── worker/               # 큐 소비자 (가산점)
│
├── web/                      # [C] 화면 담당 — Vue 3, 포트 5173
│   └── src/views/            # Login, AlertList, AlertDetail, DraftReview, TaskList
│
├── data/                     # [D] 데이터 담당
│   ├── demo_companies.json   # 데모 회사 5개 + 기대 결과
│   ├── customers.json        # 가짜 PB 2명, 고객 20명, 보유 종목
│   ├── disclosures/          # 회사별 공시 목록 JSON (scripts/fetch_dart.py가 생성)
│   ├── app.db                # 생성물, git 제외
│   └── chroma/               # 생성물, git 제외
│
├── rag_docs/                 # [D] RAG 원문 (md)
│   ├── companies/<종목코드>_<회사명>/   # 회사별 공시 원문
│   └── reference/            # 상장규정, 공시해설, 회계용어, 안내문가이드
│
├── scripts/                  # [D]
│   ├── fetch_dart.py         # OpenDART에서 공시 목록·원문 받기
│   ├── seed_db.py            # JSON → SQLite
│   └── replay_demo.py        # 데모 회사 공시를 날짜순으로 흘려보내기
│
├── eval/                     # [D] Harness 평가 (전/후 결과표)
│   ├── run_eval.py
│   └── results/
│
└── tests/                    # 담당별 테스트 (tests/<폴더명>/)
```

| 담당 | 폴더 |
| --- | --- |
| A — Agent | `agent/` |
| B — MCP | `mcp_servers/`, `shared/` |
| C — 서버·화면·인증 | `api/`, `web/`, `docker-compose.yml` |
| D — 데이터·Harness·문서 | `data/`, `rag_docs/`, `scripts/`, `harness/`, `eval/`, `README.md` |

포트: api 8000 / business-mcp 8001 / rag-mcp 8002 / web 5173 / RabbitMQ 5672(관리 화면 15672)

---

## 3. 환경 변수 (`.env`, git에 올리지 않음)

```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
LLM_PRIMARY=claude-haiku-5-5
LLM_FALLBACK=gpt-5.4-mini
EMBEDDING_PROVIDER=openai          # openai | local
EMBEDDING_MODEL=text-embedding-3-small
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=disclosure-early-warning
DART_API_KEY=                       # scripts/fetch_dart.py용
JWT_SECRET=change-me
DB_URL=sqlite:///data/app.db
CHECKPOINT_DB=data/checkpoints.db
CHROMA_DIR=data/chroma
BUSINESS_MCP_URL=http://localhost:8001/mcp
RAG_MCP_URL=http://localhost:8002/mcp
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
QUEUE_ENABLED=false                 # 가산점 기능 켜기
```

---

## 4. 연결 약속

### 4-1. DB 테이블 (SQLite)

| 테이블 | 컬럼 |
| --- | --- |
| companies | corp_code(PK), stock_code, name, market, base_score, base_reasons(JSON) |
| disclosures | rcept_no(PK), corp_code, title, disclosure_type, filed_at, url, body_text, processed(bool) |
| signals | signal_id(PK), corp_code, rcept_no, signal_type, points, detail(JSON), filed_at |
| risk_assessments | assessment_id(PK), corp_code, rcept_no, total_score, level, combo_upgraded, reasons(JSON), ai_explanation, status, created_at |
| pbs | pb_id(PK), name, email, password_hash |
| customers | customer_id(PK), pb_id, name, risk_profile, total_assets |
| holdings | customer_id, stock_code, eval_amount, on_credit(bool) — PK(customer_id, stock_code) |
| contact_tasks | task_id(PK), assessment_id, customer_id, pb_id, priority, impact_score, draft_message, status, reject_reason, result_note, created_at, updated_at |
| audit_logs | log_id(PK), actor, action, target, payload(JSON), trace_id, created_at |

- `risk_assessments.status`: RUNNING, WAITING_APPROVAL, DONE, FAILED, MANUAL_CHECK
- `contact_tasks.status`: 승인대기, 승인, 반려, 연락완료, 보류
- `level`: Low, Medium, High, Critical / `priority`: P1, P2, P3
- 날짜는 ISO 문자열(`2025-04-01`, `2025-04-01T09:00:00+09:00`)
- LangGraph 체크포인트는 `data/checkpoints.db`에 따로 저장

### 4-2. business-mcp 도구 (포트 8001)

| 도구 | 입력 | 출력 | 종류 |
| --- | --- | --- | --- |
| list_new_disclosures | since: str | 미처리 공시 목록 | 조회 |
| get_disclosure | rcept_no: str | 제목·종류·날짜·원문 | 조회 |
| get_company_profile | corp_code: str | 회사 정보, base_score, base_reasons | 조회 |
| get_signal_history | corp_code: str, months: int = 12 | 과거 신호 목록 | 조회 |
| calculate_risk_score | corp_code: str, new_signals: list, as_of: str | total_score, level, combo_upgraded, 점수 내역 | 조회(규칙 계산) |
| get_holders | stock_code: str | 보유 고객과 보유 내역 (이름은 마스킹) | 조회 |
| compute_customer_impact | stock_code: str, level: str | 고객별 impact_score, impact_level, priority, 요소별 점수 | 조회(규칙 계산) |
| save_assessment | corp_code, rcept_no, signals, score_result, ai_explanation, status | assessment_id | 변경 |
| create_contact_task | assessment_id, customer_id, priority, draft_message | task_id | 변경 (PB 승인 후에만) |
| update_task_status | task_id, status, note | 바뀐 task | 변경 (PB가 화면에서만) |

### 4-3. rag-mcp 도구 (포트 8002)

| 도구 | 입력 | 출력 |
| --- | --- | --- |
| search_disclosure_text | rcept_no: str, query: str, k: int = 5 | 해당 공시 원문 조각 + 위치 |
| search_reference | query: str, category: str, k: int = 5 | 참고 문서 조각 + 출처 |

- `category`: 상장규정, 공시해설, 회계용어, 안내문가이드
- 결과마다 출처(문서명, rcept_no, 조각 위치)를 꼭 돌려준다. 근거 인용에 쓴다.
- 조각 크기 800자, 겹침 100자.

### 4-4. 공통 응답·오류 형식 (모든 도구)

```json
{"ok": true, "data": {}}
{"ok": false, "error": {"code": "NOT_FOUND", "message": "..."}}
```

- 오류 코드: NOT_FOUND, INVALID_INPUT, DB_ERROR, TIMEOUT
- 입력 검사는 도구 안에서 한다 (없는 rcept_no, 허용되지 않은 status 등).
- 예외를 밖으로 던지지 말고 위 형식으로 돌려준다.

### 4-5. Agent State (`agent/state.py`)

| 항목 | 형식 | 채우는 노드 |
| --- | --- | --- |
| disclosure | {rcept_no, corp_code, stock_code, title, filed_at} | receive |
| disclosure_type | str | classify |
| extracted_facts | dict | extract_facts |
| evidence | list[{source, quote, rcept_no}] | extract_facts |
| retry_count | int | extract_facts, draft_messages |
| tool_call_counts | dict[str, int] | 모든 노드 (Harness 5) |
| signals | list[{signal_type, points, detail}] | score_risk |
| total_score / risk_level / combo_upgraded | int / str / bool | score_risk |
| ai_explanation | str | explain_risk |
| affected_customers | list[{customer_id, impact_score, impact_level, priority, reasons}] | find_customers |
| drafts | dict[customer_id, str] | draft_messages |
| guardrail_flags | list[str] | guardrail_check |
| approval | {status, pb_id, edited_drafts, reject_reason, decided_at} | pb_approval |
| task_ids | list[int] | create_tasks |
| error | str 또는 None | 모든 노드 |

- 공시 1건 = 스레드 1개, `thread_id = rcept_no`. 승인 대기 후 같은 thread_id로 재개한다.

### 4-6. Graph 노드 (`agent/graph.py`)

```
receive → classify ─(해당없음)→ END
            └→ extract_facts ⟲(근거 부족, 최대 2회) → score_risk → explain_risk
               → route_by_level ─(Low·Medium)→ save_and_summarize → END
                                └(High·Critical)→ find_customers → draft_messages
               → guardrail_check ─(실패, 최대 2회)→ draft_messages
                                └(통과)→ pb_approval [interrupt]
               → (반려) save_rejection → END
               → (승인) create_tasks → END
```

| 노드 | 담당 | 하는 일 |
| --- | --- | --- |
| receive | 코드 | get_disclosure로 공시 가져오기 |
| classify | 규칙 | rules.yaml의 제목 키워드 표로 disclosure_type 결정 |
| extract_facts | AI | search_disclosure_text로 원문을 읽고 조건 값 + 인용 문장 반환 (프롬프트 `extract_facts.md`) |
| score_risk | 규칙 | get_signal_history + calculate_risk_score |
| explain_risk | AI | 점수 내역 항목마다 원문 인용을 붙여 설명 (`explain_risk.md`) |
| route_by_level | 코드 | 등급으로 분기 |
| save_and_summarize | 코드 | save_assessment, 일일 요약 대상으로 표시 |
| find_customers | 규칙 | compute_customer_impact, P1·P2만 다음 단계로 |
| draft_messages | AI | 고객별 안내문 초안 (`draft_message.md`), 고객 이름 대신 `{고객명}` 자리표시 |
| guardrail_check | 규칙 | harness/guardrails.py로 금지 표현 검사 |
| pb_approval | 사람 | `interrupt()`로 멈춤 → PB 결정 받아 재개 |
| create_tasks | 코드 | 승인된 고객마다 create_contact_task |

---

## 5. 위험도 규칙 (`mcp_servers/business/rules/rules.yaml`에 그대로 옮긴다)

숫자는 코드에 직접 쓰지 말고 yaml에서 읽는다. 회계 담당이 조정할 수 있게 하기 위해서다.

### 5-1. 확정 악재 (A그룹) — 점수와 상관없이 등급 고정

| 공시 | 조건 | 등급 |
| --- | --- | --- |
| 감사의견 거절·부적정 | - | Critical |
| 거래정지·관리종목 지정·상장폐지 사유 | - | Critical |
| 횡령·배임 | 자기자본의 5% 이상 (코스닥 3%) | Critical |
| 자본잠식 | 전액 잠식 | Critical |
| 감사의견 한정 | - | High |
| 횡령·배임 | 위 기준 미만 | High |
| 자본잠식 | 50% 이상 | High |

(횡령·배임, 자본잠식 % 기준은 거래소 상장규정 원문으로 확인 후 확정)

### 5-2. 숨은 신호 (B그룹)

| signal_type | 신호 | 기본 | 추가 |
| --- | --- | --- | --- |
| FILING_DELAY | 정기보고서 제출 지연 | 4 | - |
| AUDITOR_CHANGE | 감사인 변경 | 3 | 주기적 지정제 교체는 0점 |
| GOOD_NEWS_CANCEL | 호재 공시 정정·해지 | 3 | - |
| CB_BW | CB·BW 발행 | 2 | 리픽싱 +1, 12개월 내 2회 이상 +1 |
| RIGHTS_OFFERING | 유상증자 (발행 주식 수의 20% 이상) | 2 | 50% 이상 또는 제3자배정 +1 |
| PLEDGE | 최대주주 주식 담보 | 2 | 보유 주식의 50% 이상 +1 |
| REPEATED_CORRECTION | 같은 공시 3회 이상 정정 | 2 | - |

- 시간 반영: 최근 6개월은 100%, 6~12개월은 50%, 12개월 넘으면 제외
- 조합 규칙: 12개월 안에 서로 다른 signal_type이 3개 이상이면 등급 한 단계 상향

### 5-3. 사업보고서 기본 점수 (최대 6점, `companies.base_score`)

| 항목 | 점수 |
| --- | --- |
| 계속기업 불확실성 문단 | 4 |
| 영업손실 2년 연속 | 2 |
| 순이익 흑자 + 영업현금흐름 적자 | 2 |
| 최근 3년 회사 본인 회계 관련 제재 | 2 |
| 강조사항 있음 | 1 |
| 영업이익 3년 연속 감소 | 1 |
| 순이익 중 일회성 이익 30% 이상 | 1 |
| 핵심감사사항 | 0 |

### 5-4. 경보 기준

| 총점 | 등급 | 하는 일 |
| --- | --- | --- |
| 0~3 | Low | 기록만 |
| 4~6 | Medium | 일일 요약에만 포함 |
| 7~9 | High | 경보, 고객 영향도 계산 |
| 10 이상 | Critical | 긴급 경보 |

### 5-5. 고객 영향도

| 요소 | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| 보유 비중 (평가금액 ÷ 총자산) | 5% 미만 | 5% 이상 15% 미만 | 15% 이상 30% 미만 | 30% 이상 |
| 평가금액 | 1천만 원 미만 | 1천만 이상 5천만 미만 | 5천만 이상 2억 미만 | 2억 이상 |
| 투자성향 | 공격투자형 | 적극투자형·위험중립형 | 안정추구형 | 안정형 |
| 신용거래 | 없음 | | | 있음 |

- 합계 0~3 낮음, 4~6 보통, 7~12 높음. **신용거래가 있으면 무조건 높음.**

| 회사 등급 | 높음 | 보통 | 낮음 |
| --- | --- | --- | --- |
| Critical | P1 | P1 | P2 |
| High | P1 | P2 | P3 |

- P1·P2만 안내문과 연락 업무를 만든다. P3는 목록에만.

---

## 6. Harness (수업 자료의 8개 구성 요소)

> Agent = 모델 + Harness. 모델은 "다음에 뭘 할지" 제안만 하고, 실행·제한·기록·복구는 Harness가 맡는다.
> 같은 모델을 쓰면 팀마다 차이가 없고, **Harness에서 팀마다 차이가 난다.**

### 모델에 들어가는 입력을 다루는 4개

**1. Instructions — 시스템 프롬프트와 규칙**
- 역할, 도구 사용 규칙, 금지 사항, 출력 형식을 `agent/prompts/*.md`로 따로 두고 버전 관리한다. **코드 안에 프롬프트를 직접 쓰지 않는다.**
- 파일: `system.md`, `extract_facts.md`, `explain_risk.md`, `draft_message.md`, `rewrite_message.md`
- 각 파일 맨 위에 `version: v1` 같은 줄을 두고, 바꿀 때마다 올린다. trace 메타데이터에 버전을 남긴다.
- 출력은 JSON 형식으로 받고 Pydantic으로 검사한다 (`with_structured_output`).

**2. Tools — MCP Client로 가져온 도구**
- `langchain-mcp-adapters`로 business-mcp, rag-mcp 도구를 가져온다.
- 도구 결과가 4,000자를 넘으면 잘라서 넘기고 "(이하 생략, 전체 N자)"를 붙인다 (`harness/context.py`).
- 도구 오류(`ok: false`)는 예외로 멈추지 말고 ToolMessage로 모델에게 돌려준다.

**3. Context — 모델에 넣는 정보의 양 관리**
- LLM 호출 1회당 입력 토큰 예산 6,000. 넘으면 오래된 메시지를 요약해서 줄인다.
- RAG 검색 결과는 상위 5개 조각만 넣는다. 공시 원문 전체를 통째로 넣지 않는다.

**4. State·Memory — 상태 저장**
- `SqliteSaver` 체크포인터, `thread_id = rcept_no`.
- PB 승인 대기에서 멈췄다가, 서버를 재시작해도 같은 thread_id로 그대로 이어서 실행할 수 있어야 한다.

### 실행을 통제하고 기록하는 4개

**5. Agent Loop — 반복 한도**
- 최대 8턴(LangGraph `recursion_limit` + AI 노드 내부 도구 호출 루프), 작업당 120초, 같은 도구는 3회까지.
- 한도를 넘으면 status를 MANUAL_CHECK로 저장하고 종료한다.

**6. Guardrails — 안전장치**
- 데이터를 바꾸는 도구(`create_contact_task`)는 실행 전에 멈추고(interrupt) PB 승인을 받는다. `save_assessment`는 모델이 아니라 코드만 호출한다. `update_task_status`는 PB가 화면에서만 호출한다.
- 도구 결과의 개인정보를 가린다: 고객 이름은 `고객 C001` 형태로, 전화번호·계좌번호는 `***`로. **LLM은 실제 고객 이름을 보지 않는다.** 이름은 화면에 보여줄 때만 채운다.
- 안내문 금지 표현 검사: "매도하세요", "매수하세요", "비중을 줄이", "반드시", "확실히", "손실을 피하" 등 (`harness/guardrails.py`의 목록). 걸리면 `rewrite_message.md`로 다시 쓰고, 2회 실패하면 PB에게 "직접 작성 필요"로 넘긴다.

**7. Recovery — 실패 대응**
- LLM 429(요청 과다)·503(서버 오류): 2회 재시도(1초, 3초 대기) → 대체 모델(`LLM_FALLBACK`)로 교체 → 그래도 실패하면 status FAILED.
- MCP 도구 TIMEOUT: 1회 재시도. 나머지 오류는 State.error에 남기고 MANUAL_CHECK.
- 모든 실패는 audit_logs에 trace_id와 함께 남긴다.

**8. Observability — 기록과 관측**
- LangSmith로 trace_id 하나에 노드, 도구, 토큰, 지연, 비용이 모이게 한다.
- trace 메타데이터: rcept_no, corp_name, risk_level, prompt_version, model.
- 사람의 결정(승인·반려·수정)은 audit_logs에 남긴다.

### 증빙 (발표 장표용)
- **관측:** 금양 시나리오 1회 실행의 LangSmith trace 화면 캡처 (노드별 토큰·지연·비용이 보이게).
- **평가:** `eval/run_eval.py`로 설정을 바꾸기 전/후 결과표를 만든다. 예:
  - 조합 규칙 켬/끔 → 첫 경보가 사건보다 며칠 먼저 울렸는지
  - 가드레일 켬/끔 → 금지 표현이 들어간 안내문 수
  - RAG k=3 / k=5 → 근거 인용 정확도, 토큰 수
  - 기본 모델 / 대체 모델 → 비용, 지연
- 결과는 `eval/results/*.md` 표로 저장한다.

---

## 7. 데이터

- `data/demo_companies.json`: 데모 회사 5개, 사건, **기대 결과**(평가 정답).
- `data/customers.json`: PB 2명, 고객 20명, 보유 종목. 실제 사람과 무관한 가짜 데이터다.
- `data/disclosures/<종목코드>.json`: `scripts/fetch_dart.py`가 OpenDART에서 받아 만든다. **공시 내용과 접수번호를 직접 지어내지 않는다.**
- `rag_docs/companies/<종목코드>_<회사명>/<YYYYMMDD>_<공시종류>.md`: 공시 원문. 파일 맨 위에 아래 머리말을 둔다.

```
---
rcept_no: "20250401000123"
corp_name: 금양
stock_code: "001570"
filed_at: 2025-04-01
title: 감사보고서제출
url: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...
---
```

- `rag_docs/reference/`: `listing_rules.md`(상장규정), `disclosure_guide.md`(공시해설), `accounting_terms.md`(회계용어), `message_guide.md`(안내문가이드).

---

## 8. PB 화면 (Vue)

| 화면 | 경로 | 내용 |
| --- | --- | --- |
| 로그인 | /login | JWT 발급 |
| 경보 목록 | /alerts | 회사명, 등급 배지, 점수, 영향 고객 수, P1 수, 상태 / 등급·상태 필터 / 일일 요약 탭 |
| 경보 상세 | /alerts/:assessmentId | 진행 단계 표시(SSE), 점수 내역 표, AI 설명과 원문 인용, 영향 고객 목록(우선순위순) |
| 안내문 검토 | /alerts/:assessmentId/drafts | 고객별 초안, 가드레일 결과, 승인 / 수정 후 승인 / 다시 작성 / 반려(사유 필수) |
| 연락 업무 | /tasks | 내 업무 목록, 연락완료·보류 처리, 결과 메모 |

- PB는 자기 담당 고객의 안내문만 승인할 수 있다 (토큰의 pb_id와 customers.pb_id 비교).
- SSE 이벤트 이름: `node_started`, `node_finished`, `waiting_approval`, `done`, `failed`.

API 주요 경로 (C 담당이 확정 후 이 표를 갱신):

| 메서드 | 경로 | 하는 일 |
| --- | --- | --- |
| POST | /auth/login | 로그인 |
| POST | /runs | 공시 1건 처리 시작 (body: rcept_no) |
| GET | /runs/{rcept_no}/events | SSE 진행 이벤트 |
| GET | /alerts | 경보 목록 |
| GET | /alerts/{assessment_id} | 경보 상세 |
| POST | /alerts/{assessment_id}/decision | PB 결정 → Graph 재개 |
| GET | /tasks | 연락 업무 목록 |
| PATCH | /tasks/{task_id} | 상태 변경 |

---

## 9. 데모 시나리오와 완료 기준 (이게 돌면 완성)

### 시나리오 A — 금양: 조기경보 + 확정 악재 (주인공)

1. `scripts/seed_db.py`로 DB를 초기화한다. 금양 base_score를 계산해 넣는다.
2. `scripts/replay_demo.py --stock 001570`로 사건 전 12개월 공시를 날짜순으로 흘려보낸다.
3. **확인:** 숨은 신호가 쌓이면서 사건일(감사의견 거절 공시일)보다 앞선 날짜에 처음으로 High 이상이 나온다. 그 날짜와 "사건보다 며칠 먼저"를 출력한다.
4. 감사의견 거절 공시를 넣는다.
5. **확인:** 등급이 Critical(A그룹 규칙)이고, 점수 내역에 원문 인용이 1개 이상 붙어 있다.
6. **확인:** 영향 고객이 **7명**, P1 **5명**(C001, C003, C005, C006, C007), P2 **2명**(C002, C004)이다.
7. 안내문 초안 7개가 생성되고 모두 가드레일을 통과한다. 고객 이름은 `{고객명}`으로 남아 있다.
8. Graph가 `pb_approval`에서 멈추고 status가 WAITING_APPROVAL이다. 화면 경보 목록에 "승인 대기"로 보인다.
9. PB P01(김하늘)로 로그인 → 경보 상세에서 근거 확인 → C001 안내문을 한 문장 고쳐서 승인, 나머지 P01 고객 3명은 그대로 승인.
10. **확인:** contact_tasks에 4건(C001, C003, C005, C007)이 status "승인"으로 생기고, C001은 수정한 문장이 저장돼 있다.
11. PB P02(박서준)로 로그인 → C002는 반려(사유 입력), C004·C006은 승인.
12. **확인:** contact_tasks에 2건이 추가되고, C002 반려 사유가 저장된다. P01은 P02 고객을 승인할 수 없다(403).
13. **확인:** audit_logs에 결정 기록이 남고, LangSmith에 하나의 trace로 전체 흐름이 보인다.

### 시나리오 B — 다른 회사 (기대 결과, 평가 정답)

| 회사 | 기대 결과 |
| --- | --- |
| 위니아 | 사건일 전에 High 이상, 감사의견 거절 공시에서 Critical |
| 오스템임플란트 | 사건 전 경보 없음(정상), 횡령 공시에서 즉시 Critical → 보유 고객 C005, C011, C012 |
| CJ CGV | 유상증자 공시 1건으로는 Medium 이하, 경보 없음 |
| SK㈜ | base_score 2점, Low |

### 실패 시나리오 (Harness 증빙)
- LLM 503을 흉내 내는 설정(`FAKE_LLM_ERROR=503`)에서 재시도 2회 → 대체 모델로 성공하는 trace.
- 금지 표현이 섞인 초안을 강제로 넣었을 때 가드레일이 잡고 다시 쓰는 trace.
- 서버를 멈췄다 다시 켠 뒤 같은 thread_id로 승인하면 그대로 이어서 실행된다.

---

## 10. 작업 순서 (Claude Code에 한 단계씩 맡긴다)

0. **[B, 가장 먼저 30분]** 폴더 뼈대, `pyproject.toml`, `shared/`(config, db, models, schemas), `.env.example`를 만들어 main 브랜치에 올린다. 나머지는 이걸 받은 뒤 시작한다.
1. [D] `scripts/seed_db.py` + `data/*.json` 적재, `scripts/fetch_dart.py` 실행
2. [B] business-mcp 조회 도구 → 규칙 계산 도구(rules.yaml) → 변경 도구, 도구별 테스트
3. [B] rag-mcp + `ingest.py`
4. [A] State, Graph를 **가짜 도구(mock)**로 먼저 끝까지 돌리기 → 진짜 MCP로 교체
5. [C] FastAPI 인증, `/runs`, SSE, 결정 API → Vue 화면 5개
6. [D] `harness/` 모듈 → A가 노드에 연결
7. **[모두, 화요일 오후 3시] 1차 합치기 — 시나리오 A가 처음부터 끝까지 돌면 성공**
8. [D] `eval/run_eval.py` 전/후 결과표, LangSmith trace 캡처
9. [C] 가산점: 큐 (기본 기능이 화요일 저녁 8시까지 돌 때만)

## 11. 작업 규칙

- 브랜치: `feat/<담당>-<기능>` (예: `feat/b-risk-score`). main에는 PR로 합친다.
- 다른 사람 폴더의 코드는 고치지 않는다. 필요한 게 있으면 그 담당자에게 요청한다.
- 계약(4장)과 다르게 만들어야 하면 코드를 쓰기 전에 팀 채팅에 먼저 말한다.
- 비밀 키는 `.env`에만. 커밋 전에 `.env`가 빠졌는지 확인한다.
- 공시 내용, 접수번호, 숫자를 지어내지 않는다. 모르면 `TODO(확인 필요)`로 남긴다.
- 새 기능을 추가하기 전에 시나리오 A가 깨지지 않는지 먼저 확인한다.
