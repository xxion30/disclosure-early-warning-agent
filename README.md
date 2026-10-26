# DARTCH
한 줄 소개: **증권사 PB가 고객 보유 종목의 공시 위험을 조기에 확인하고, 근거와 고객 안내문을 검토한 뒤 연락 업무를 관리하도록 돕는 서비스.**

## 실행 방법

### 준비 사항

- Python 3.11
- uv
- Node.js: `20.19 이상인 20.x` 또는 `22.12 이상`

### 설치 및 데이터 적재

```bash
# 소스코드 내려받기
git clone --branch main https://github.com/xxion30/disclosure-early-warning-agent.git
cd disclosure-early-warning-agent

# Python 가상환경 생성 및 의존성 설치
uv sync --frozen

# 환경 변수 파일 생성
# 실제 모델·수집 기능을 사용할 때만 .env에 API 키 입력
cp .env.example .env

# 데모 계정·고객·회사·공시 데이터 적재
uv run python scripts/data/seed_db.py

# 프론트엔드 의존성 설치
npm --prefix web ci
```

### API 실행

프로젝트 루트에서 실행합니다.

```bash
# 실제 AI 호출 없이 처리 흐름을 확인하는 fake 모드
# 다른 프로그램과의 충돌을 피하기 위해 API 포트를 8010으로 지정
PYTHONUTF8=1 \
GRAPH_ADAPTER=fake \
PHASE1_RUNTIME_FACTORY= \
COLLECT_DISPATCH_ENABLED=false \
QUEUE_ENABLED=false \
uv run uvicorn api.main:app --host 127.0.0.1 --port 8010
```

### 웹 화면 실행

API 터미널을 유지하고, 새 터미널에서 프로젝트 루트로 이동한 후 실행합니다.

```bash
cd web
VITE_API_TARGET=http://127.0.0.1:8010 npm run dev
```

| 항목 | 주소·계정 |
| --- | --- |
| 웹 화면 | http://localhost:5173 |
| API 문서 | http://127.0.0.1:8010/docs |
| 상태 확인 | http://127.0.0.1:8010/health |
| 데모 이메일 | `p01@demo.local` 또는 `p02@demo.local` |
| 데모 비밀번호 | `demo1234` |
| 종료 방법 | 각 터미널에서 `Ctrl+C` |

### 데모 사용 순서

1. 데모 계정으로 로그인합니다.
2. 공시 직접 처리에 접수번호 `20250321802001`을 입력합니다.
3. 처리 진행과 경보 상세를 확인합니다.
4. 담당 고객의 안내문을 검토하고 승인·반려·재작성합니다.
5. 승인 후 생성된 연락 업무를 확인합니다.

fake 모드는 금양 시연 흐름을 모사합니다. 실제 공시 분석 정확도나 모델 성능을 검증하는 실행은 아닙니다. 동일 접수번호를 다시 처리하면 실행 이력에 따라 `409` 오류가 발생할 수 있습니다.

## 구조
구성도와 처리 흐름은 [docs/architecture.md](docs/architecture.md)를 참고합니다.

```text
agent/                   AI 분석·근거 검증·안내문 생성
api/                     인증·분석 실행·조회·승인 API
collector/               DART 공시·재무 데이터 수집
risk_engine/             위험 점수·단계 판정
shared/                  공통 설정·스키마·DB 모델
mcp_servers/business/    업무·위험 계산 도구
mcp_servers/rag/         공시 원문·참고 문서 검색
harness/                 가드레일·마스킹·복구·관측
web/                     PB 웹 화면
data/                    데모·수집 데이터
rag_docs/                검색용 테스트 원문·참고 문서
tests/                   자동 테스트
eval/                    평가 사례·실험·결과
docs/                    설계·계약·실행·검증 문서
```

공개 공시 분석과 조직별 고객 대응을 분리합니다. 위험 판정 기준은 `risk_engine/rules.yaml`에서 관리합니다.

## 환경 변수

`.env.example`을 `.env`로 복사해 설정합니다. 실제 키가 포함된 `.env`는 저장소에 올리지 않습니다.

아래 값은 저장소 기본 설정이며, 개인 환경에서 사용하는 실제 값과 다를 수 있습니다.

| 이름 | 설명 | 기본값·예시 |
| --- | --- | --- |
| `OPENAI_API_KEY` | OpenAI 모델·임베딩 API 키 | 개인 키 입력 |
| `ANTHROPIC_API_KEY` | Anthropic 모델 API 키 | 개인 키 입력 |
| `DART_API_KEY` | OpenDART 수집 API 키 | 개인 키 입력 |
| `LLM_PRIMARY` | 기본 모델 | `claude-haiku-5-5` |
| `LLM_FALLBACK` | 대체 모델 | `gpt-5.4-mini` |
| `EMBEDDING_PROVIDER` | 임베딩 제공 방식 | `openai` |
| `EMBEDDING_MODEL` | 임베딩 모델 | `text-embedding-3-small` |
| `DB_URL` | 업무 DB 연결 주소 | `sqlite:///data/app.db` |
| `CHECKPOINT_DB` | Graph 체크포인트 저장소 | `data/checkpoints.db` |
| `CHROMA_DIR` | 영구 검색 색인 경로 | `data/chroma` |
| `JWT_SECRET` | 로그인 토큰 서명 비밀값 | 개인 비밀값으로 변경 |
| `BUSINESS_MCP_URL` | 업무 MCP 주소 | `http://localhost:8001/mcp` |
| `RAG_MCP_URL` | 검색 MCP 주소 | `http://localhost:8002/mcp` |
| `MCP_HOST` | MCP 서버 바인딩 주소 | `127.0.0.1` |
| `GRAPH_ADAPTER` | 기존 분석 실행 방식 | `fake` / `real` |
| `RAG_INDEX_MODE` | 원문 색인 방식 | `persistent` / `ephemeral` |
| `PHASE1_RUNTIME_FACTORY` | 새 분석·조직 검토 실행기 | 기본 빈 값 |
| `COLLECT_DISPATCH_ENABLED` | API 수동 수집 연결 여부 | `false` |
| `COLLECT_TIME` | 일일 수집 시각, KST | `09:05` |
| `COLLECT_BACKFILL_MONTHS` | 과거 데이터 수집 기간, 개월 | `12` |
| `COLLECTION_LEASE_SECONDS` | 수집 작업 임대 시간, 초 | `600` |
| `DART_TIMEOUT_SECONDS` | 수집 요청 제한 시간, 초 | `30` |
| `DART_RETRY_MAX` | 최초 호출을 제외한 재시도 횟수 | `3` |
| `DART_MAX_RPS` | 초당 요청 제한 | `4` |
| `DART_MAX_CONCURRENCY` | 동시 수집 요청 수 | `3` |
| `ANALYSIS_LEASE_SECONDS` | 분석 작업 임대 시간, 초 | `120` |
| `ANALYSIS_RETRY_DELAY_SECONDS` | 분석 재시도 대기 시간, 초 | `30` |
| `QUEUE_ENABLED` | 기존 RabbitMQ 큐 사용 여부 | `false` |
| `RABBITMQ_URL` | RabbitMQ 연결 주소 | `amqp://guest:guest@localhost:5672/` |
| `LANGSMITH_TRACING` | 관측 설정 | `true` |
| `LANGSMITH_API_KEY` | LangSmith API 키 | 개인 키 입력 |
| `LANGSMITH_PROJECT` | 관측 프로젝트 이름 | `disclosure-early-warning` |
| `FAKE_GRAPH_DELAY` | 데모 단계별 지연 시간, 초 | `0.4` |
| `FAKE_LLM_ERROR` | 모델 오류 주입 시연 | 기본 빈 값 |
| `FAKE_UNSAFE_DRAFT` | 가드레일 시연용 문안 주입 | `false` |

웹 프록시의 목적지는 별도 환경 변수 `VITE_API_TARGET`으로 지정합니다. 미설정 시 `http://localhost:8000`을 사용하며, 위 실행 예시는 `http://127.0.0.1:8010`을 사용합니다.

## API

[docs/api.md](docs/api.md)에서 다음 내용을 확인할 수 있습니다.

- 인증·권한
- 엔드포인트별 요청·응답
- 상태 코드·예외 처리
- SSE 이벤트
- 기존 직접 처리와 새 분석·조직 검토 경로의 사용 조건

## 데이터 출처

[data/SOURCES.md](data/SOURCES.md)에 데이터별 출처와 공개·합성 여부를 정리합니다.

| 데이터 | 출처 | 구분 |
| --- | --- | --- |
| 공시 목록·원문·재무 데이터 | OpenDART | 공개 |
| PB·고객·보유 내역 | 팀 작성 가상 데이터 | 합성 |
| 공시·회계·안내문 참고 자료 | 팀 작성 설명 자료 | 공개 자료 기반 가공·자체 작성 |
| 평가 사례 | 공개 공시·재무 데이터와 팀 기대값 | 공개 기반 가공 |

## 평가 결과

평가 대상은 `eval/cases/r3_cases.json` 44건과 `eval/cases/r5_cases.json` 6건으로, 총 50건입니다.

아래는 저장된 사례를 이용한 **규칙 엔진 평가**이며, 전체 AI 서비스의 정확도를 의미하지 않습니다.

| 평가 항목 | 현재 결과 |
| --- | --- |
| 경보 대상 기간 사례 적중 | 3/3 |
| 정상 기간 사례 오탐 | 1/6 |
| 단건 단계 일치 | 16/18 |
| 타임라인 단계 일치 | 5/5 |
| 기간 사례 계산 일치 | 3/3 |

재현 명령:

```bash
uv run --frozen python scripts/demo/eval_cases.py
```

- 2026-10-26, 코드 기준 `60f1c3a`로 재채점해 [검증 결과서](docs/verification.md)의 평가표·기준값 비교표를 갱신했습니다. 단건 결과는 `16/18`이며, 남은 두 불일치는 `r3-red-012`·`r3-red-013`입니다. 규칙과 기대값은 변경하지 않았습니다.
- 두 JSON의 `schema_version=1`은 데이터 형식 버전이며, 평가셋 릴리스 버전 v1로 간주하지 않습니다.
- 최종 수치는 검증 결과서·실험 기록과 동일하게 맞춰야 합니다.
- 금양 최초 전체 경보의 359일 선행 결과는 당시 원본 재무 데이터 검증 대기이므로 확정 성과로 사용하지 않습니다.

### 제출용 D2 평가 명세와 개선 전후 측정 상태

Notion 검증서의 **D2-v2 (2026-10-26)**는 위 50건 전체와 구별되는 제출용 점검 명세입니다. [문항 목록·기대 결과·재현 명령](docs/verification.md#제출용-d2-평가-명세-d2-v2-2026-10-26)을 함께 기록했습니다.

| 범위 | 문항 수 | 현재 결과 | 해석 |
| --- | --- | --- | --- |
| 기존 No.1~10 규칙 사례 표본 | 10 | 8건 채점 중 7건 충족, **7/8 (87.5%)** | 답 불가 2건 제외. 전체 50건이나 AI 서비스의 정확도가 아님 |
| 추가 No.11~13 실패 유도 | 3 | **3/3 통과** | 금지 문구 반복·타 PB 고객 승인·원문에 없는 인용을 주입한 제어 시험. 가짜 모델·검색 또는 fake graph 사용 |
| Phase 2 개선 전후 비교 | 같은 D2-v2 명세 사용 예정 | **개선 전 미측정, 개선율 미산출** | 기준선 `phase1-end` (`d6f3191`)과 비교할 실행 기록이 필요 |

두 범위의 결과를 합산해 하나의 AI 정확도로 표시하지 않습니다. 현재 규칙의 기준값 변경 비교는 설정 민감도 실험이며 Phase 2 개선 전후 성과가 아닙니다. `eval/results/guardrail.md`의 금지 표현 4→0은 2026-10-20 합성 초안 7개에 대한 **Phase 1 참고 실험**으로만 인용합니다.

## 사용 도구

| 영역 | 기술 |
| --- | --- |
| AI 흐름 | LangGraph, LangChain |
| 모델 연결 | OpenAI, Anthropic |
| API·인증 | FastAPI, Uvicorn, JWT, bcrypt |
| 도구 연결 | FastMCP, MCP |
| 데이터 저장 | SQLAlchemy, SQLite, PostgreSQL, Alembic |
| 검색 | Chroma, 임베딩 |
| 프론트엔드 | Vue 3, Vite, Vue Router, Pinia, Axios |
| 수집 | httpx, requests, OpenDART |
| 테스트·실행 | pytest, uv, Docker Compose |
| 관측 | LangSmith, 감사 로그 |
| 선택 큐 | RabbitMQ, aio-pika |

### AI 코딩 도구

| 도구 | 사용 범위 | 확인 방법 |
| --- | --- | --- |
| Claude Code | 일부 PR에 사용 표기 존재 | 관련 코드의 테스트·실행·리뷰 기록 대조 |
| Codex | 이번 README 작성의 코드·문서 조사 | 소스 대조, API 목록·설정·평가 결과 확인 |

## 재사용 코드

샘플·Mini-project ① 코드를 재사용한 경우 출처와 사용 범위를 기록합니다.

## Team & Contribution

| 이름 | 역할 | 담당 모듈 · 문서 | 주요 PR |
| --- | --- | --- | --- |
| 양시온 | R1 PM · PO | `docs/`, `.github/`, `CLAUDE.md`, `README.md` | [#26](https://github.com/xxion30/disclosure-early-warning-agent/pull/26), [#68](https://github.com/xxion30/disclosure-early-warning-agent/pull/68) |
| 윤찬웅 | R2 AI Engineer (흐름) | `agent/`, `harness/`의 context·guardrails·loop_limits·recovery_llm | [#48](https://github.com/xxion30/disclosure-early-warning-agent/pull/48) |
| 한예빈 | R3 AI Engineer (RAG · Data) | `collector/`, `mcp_servers/rag/`, `rag_docs/`, `data/` | [#49](https://github.com/xxion30/disclosure-early-warning-agent/pull/49) |
| 양경환 | R4 Backend · Tool | `shared/`, `risk_engine/`, `mcp_servers/business/`, `api/` | [#71](https://github.com/xxion30/disclosure-early-warning-agent/pull/71) |
| 박세호 | R5 Frontend · Demo (R6 QA · Eval 겸임) | `web/`, `scripts/demo/`, `tests/demo/`, `docs/verification.md` | [#61](https://github.com/xxion30/disclosure-early-warning-agent/pull/61) |

## 알려진 한계

| 한계 | 원인 · 근거 | 다음 단계 |
| --- | --- | --- |
| 기존 직접 처리와 새 분석 경로가 함께 존재 | 화면 직접 처리는 `/runs`를 사용하며 새 `/analysis/jobs`로 자동 전환되지 않음 | 최종 시연 경로 확정·종단 검증 |
| 일부 공시는 수동 확인 필요 | 구조화 사실·필수 수치·검증 가능한 근거 누락 | 사실·근거 연결 보완 |
| 실제 전체 서비스 검증 범위 제한 | 일부 연결 검증은 가짜 모델·검색 응답 사용 | 실제 DART·모델·PostgreSQL 환경 검증 |
| 단건 기대값과 계산의 불일치 2건 | r3-red-012·r3-red-013은 기대 RED, 계산 WATCH로 단건 16/18 | R1이 기간 변경·상장폐지 사유 정책을 확인한 뒤 R4 규칙 또는 R3 기대값 반영·재평가 |
| Phase 2 개선 전 결과 미측정 | 같은 D2-v2 명세의 phase1-end 기준선 실행 기록 없음 | 같은 입력·기대값·채점 기준을 고정해 기준선과 개선본 재실행 |
| 금양 최초 전체 경보 359일은 검증 대기 | 당시 원본 재무 값 확인 필요 | 원본 검증 후 결과 확정 |
| 정상 회사 과경보 가능 | 현재 기준에 따른 연결 재무 등의 위험 신호 | 근거를 제시하고 PB 검토, 정책 개선 시 재평가 |
| 공개 원문의 개인정보 검수 필요 | 일부 테스트 공시에 개인 식별 정보 포함 | 공개 제출 범위·마스킹 기준 확정 |

## 참고
Phase 1(Capstone) 종료 시점은 PR [#23](https://github.com/xxion30/disclosure-early-warning-agent/pull/23) 병합 커밋 `d6f3191`(2026-10-21 11:50)이며, 이 커밋에 phase1-end 태그를 달았다. 태그는 10/25에 소급해서 생성했고, Phase 2는 이 태그 이후의 변경만 대상으로 한다.
