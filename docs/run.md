# DARTCH R4 실행 안내 (2026-10-22)

이 절은 새 R4 구현 기준이다. 아래 이전 프로젝트 실행 기록은 참고용으로 유지한다.

## 준비와 검증

```bash
uv sync --frozen
uv run python scripts/ops/verify_all.py
```

검증은 임시 테스트 DB를 사용하고 실제 모델을 호출하지 않는다. `uv` 캐시 권한 오류가 있는 환경에서는 `UV_CACHE_DIR=/tmp/dartch-uv-cache`를 지정한다.

## PostgreSQL

```bash
docker compose up -d postgres
DB_URL=postgresql+psycopg://dartch:dartch@localhost:5432/dartch uv run alembic upgrade head
```

API와 business-mcp를 로컬에서 실행할 경우 `.env`의 DB_URL을 위 URL로, CHECKPOINT_DB를 `postgresql://dartch:dartch@localhost:5432/dartch`로 설정한다. 기존 SQLite는 자동으로 PostgreSQL에 복사하지 않는다. 초기 마이그레이션은 연결한 DB 안의 기존 행·테이블을 보존하고 새 스키마를 추가한다. 실제 운영 데이터 이전은 별도 백업·이관 절차가 필요하다.

```bash
docker compose up --build api business
# 화면 개발 서버가 필요할 때
# docker compose --profile web up web
```

기본 GRAPH_ADAPTER=fake는 이전 시연용 그래프다. 새 위험 상태 전체 흐름이 구현됐다는 의미가 아니다. QUEUE_ENABLED는 false로 둔다.

## 새 API

- 로그인 후 `GET /states?alert=true`, `GET /states/{stock_code}`, `POST /states/{stock_code}/ack`를 사용한다.
- 확인함 요청은 화면에서 받은 버전을 `{ "version": 1 }` 형태로 보낸다.
- 회사 관리자는 PB 행의 role=ADMIN으로 지정한다. 별도 계정 생성·승격 API는 만들지 않는다.
- `GET /collect/status`는 수집 이력을 조회한다. `POST /collect/run`은 관리자 전용이며, R3의 `app.state.collect_dispatcher`가 미연결이면 503이다.
- 수집 dispatcher는 async `(run_id, stock_codes)` 계약이다. 실제 실행 결과와 종료 상태를 collect_runs에 기록하고, 성공한 종목만 `shared.collection.finish_stock(..., success_at=...)`으로 커서를 전진시킨다. 실패·취소 시 예약은 해제한다.
- `uv run python -m scripts.ops.refresh_states`는 기존 위험 상태를 오늘(KST) 기준으로 재계산한다. R3 스케줄러는 일일 수집 후 이 함수를 호출해야 한다.

## 평가와 연동 경계

```bash
uv run python eval/run_eval.py --cases eval/cases
```

실제 사례 JSON이 없으면 평가 미실행을 출력한다. 사례 형식은 case_id, as_of, signals, expected_stage, sources와 선택 history다. 실제 공시와 출처만 사용한다.

R2는 새 signal_type·stage 분기, 회사별 승인 실행, 승인 대기 전 원문 조각 제거를 연결해야 한다. R3는 수집 dispatcher·스케줄러·재무 스냅샷·공시 사건 연결·메모리 RAG를 연결해야 한다. 이 연동 전에는 default 외 회사의 구형 실행·승인·SSE를 503으로 차단한다. 새 states/customers 조회와 수집 권한은 회사별로 분리한다.

`integration` Compose 프로필은 R3 진입점·RAG 바인딩 연동용 초안이다. 현재 완성된 전체 서비스 배포로 취급하지 않는다. 상세 상태는 [구현 로그](log/2026-10-22-r4-implementation.md)를 참고한다.

---

> 담당: R4 (실행 방법·환경 변수). 이전 프로젝트(Capstone)의 README를 그대로 옮긴 것이다. 연락·승인 관련 설명은 구조 정리 이후 단계에서 DARTCH 기준으로 고친다.

# 공시 조기경보 에이전트 (SKALA AI Agent Capstone)

증권사 PB를 위한 에이전트. 공시 속 **숨은 위험 신호를 점수로 쌓아** 악재가 터지기 전에 경보를 띄우고,
해당 종목을 가진 고객의 **연락 우선순위와 안내문 초안**을 만든 뒤, **PB 승인**을 받아 연락 업무를 만든다.

- 점수 계산과 등급은 **규칙(코드)**이 정한다. 공시 문장 해석, 근거 설명, 안내문은 **AI**가 쓴다. AI는 점수를 바꿀 수 없다.
- 근거를 못 찾으면 추측하지 않고 "수동 확인 필요"(MANUAL_CHECK)로 남긴다.
- PB 승인 전에는 연락 업무가 만들어지지 않는다. 안내문에 매수·매도 권유는 넣지 않는다.

설계와 팀 약속(테이블, 도구, State)은 [CLAUDE.md](CLAUDE.md), Agent 연동 경계는 [docs/contracts/agent.md](docs/contracts/agent.md),
MCP 도구 사용법은 [docs/contracts/mcp_business.md](docs/contracts/mcp_business.md)와 [docs/contracts/mcp_rag.md](docs/contracts/mcp_rag.md)를 본다.

## 흐름

```
공시 1건 ──▶ receive → classify ──(해당 없음)──▶ 끝
                          └▶ extract_facts(AI) → score_risk(규칙) → explain_risk(AI)
                               ├─ Low·Medium ─▶ 저장하고 끝 (일일 요약)
                               └─ High·Critical ─▶ find_customers(규칙) → draft_messages(AI) → guardrail_check
                                                    → pb_approval ⏸ PB 승인·수정·반려 → create_tasks → 끝
```

| 프로세스 | 포트 | 하는 일 |
| --- | --- | --- |
| `api` (FastAPI) | 8000 | 로그인(JWT), 공시 실행(`/runs`), 진행 이벤트(SSE), 경보·결정·업무 API |
| `business-mcp` (FastMCP) | 8001 | 조회·규칙 계산·저장 도구 10개 (점수, 영향도, 평가·업무 저장) |
| `rag-mcp` (FastMCP) | 8002 | 공시 원문·참고 문서 검색 도구 2개 (Chroma) |
| `web` (Vue 3 + Vite) | 5173 | PB 화면: 경보 목록, 상세, 안내문 검토, 연락 업무 |

폴더와 담당: `agent/`(A) · `mcp_servers/`, `shared/`(B) · `api/`, `web/`(C) · `data/`, `rag_docs/`, `scripts/`, `harness/`, `eval/`(D).

아키텍처 설계(구성도·시퀀스·Harness·ISO/IEC 25010 품질 특성별 설계와 검증 근거): [docs/architecture.md](docs/architecture.md)

## 빠른 시작

### 1. 준비 (처음 한 번)
```bash
git pull origin main
uv sync                                  # Python 3.11 의존성 (uv.lock으로 고정)
cp .env.example .env                     # 키를 채운다 (아래 "환경 변수"). 진짜 모드는 OPENAI_API_KEY가 필요하다
uv run python scripts/ops/doctor.py          # 준비가 됐는지 점검 (고칠 것은 ✗ 아래에 방법이 나온다)
```
`.env`, `data/app.db`, `data/chroma/`, `data/checkpoints.db`는 git에 없다. **노트북마다 따로 만든다.**
`run_stack.py`가 없는 것(DB, 색인, `npm install`)을 알아서 만들어 주므로 위 네 줄이면 된다.

### 2. 서버 전체를 한 명령으로
```bash
uv run python scripts/ops/run_stack.py            # 진짜 모드: business-mcp 8001 · rag-mcp 8002 · api 8000 · web 5173
uv run python scripts/ops/run_stack.py --fake     # 가짜 모드: api 8000 · web 5173 (키·MCP 서버 없이 화면과 API만)
uv run python scripts/ops/run_stack.py --no-web   # 화면 없이
```
`준비됨`이 나오면 http://localhost:5173 에서 로그인한다. 끄려면 `Ctrl+C` 한 번이면 서버가 모두 꺼진다.
- **진짜 모드**는 진짜 MCP 도구와 진짜 모델을 쓴다. 공시 1건 처리에 모델 비용이 약간 든다(10~15원 안팎).
- **가짜 모드**는 시나리오 A를 흉내 낸 가짜 Graph라 화면과 API를 개발할 때 쓴다.

화면: **경보**(경보 목록·상세·안내문 승인) · **고객 현황**(내 담당 고객의 보유 종목, 경보 종목 노출, 연락 이력 — 조회 전용) · **연락 업무**(연락완료·보류 처리).

데모 로그인: `p01@demo.local`(김하늘) / `p02@demo.local`(박서준), 비밀번호 `demo1234` (가짜 계정).

<details>
<summary>서버를 하나씩 직접 띄우려면</summary>

```bash
uv run python -m mcp_servers.business.server                     # 터미널 1 (8001)
uv run python -m mcp_servers.rag.server                          # 터미널 2 (8002)
GRAPH_ADAPTER=real uv run uvicorn api.main:app --port 8000       # 터미널 3 (8000). 가짜 모드는 GRAPH_ADAPTER 없이
cd web && npm install && npm run dev                             # 화면 (5173)
uv run python scripts/data/seed_db.py --reset                         # DB가 없을 때
uv run python -m mcp_servers.rag.ingest                          # 색인이 없을 때 (OpenAI 임베딩, 약 20초)
```
API는 처음 공시를 처리할 때 MCP 서버에 연결하므로 두 MCP 서버가 먼저 떠 있어야 한다.
</details>

## 전체를 한 번에 검증 (`scripts/ops/verify_all.py`)

테스트부터 시나리오 A, 안전장치, 장애, 관측까지 **명령 하나로** 순서대로 돌리고 ✓/✗ 표로 보여 준다.
**내 `data/app.db`와 켜져 있는 서버는 건드리지 않는다**(임시 DB와 별도 포트 18100번대에서 실행). `.env`에 `OPENAI_API_KEY`가 있어야 한다.
```bash
uv run python scripts/ops/verify_all.py            # 테스트 + 시나리오 A + 안전장치 + 장애 + LangSmith (2~10분, 모델 비용 소액)
uv run python scripts/ops/verify_all.py --with-b   # + 위니아·오스템·CGV·SK (30분 이상)
uv run python scripts/ops/verify_all.py --ui       # 끝나면 같은 데이터로 화면을 띄운다 (http://localhost:5180, Ctrl+C로 종료)
```
| 단계 | 통과 기준 |
| --- | --- |
| 1. 자동 테스트 | `pytest` 전체 통과 |
| 2~3. 환경·서버 | 키, 임시 DB(회사 5·공시 594), 색인, 서버 3개 기동 |
| 4. 시나리오 A | 사건 전 첫 High, 사건 Critical, 영향 고객 7명(P1 5·P2 2), 점수 내역 원문 인용, 가드레일 통과, 안내문 `{고객명}` 유지 |
| 5. 안전장치 | 금지 표현 수정안 422, **서버 재시작 후에도 승인 대기 복원·이어서 실행**, 타 PB 고객 승인 403, 승인·반려·업무 생성·감사 로그, 고객 현황 |
| 6. 장애 | `FAKE_LLM_ERROR=503`에서 재시도(1초·3초) → 대체 모델 → 정상 완료 |
| 7. 관측 | LangSmith에 한 공시의 trace가 노드·토큰·비용과 함께 올라옴 (키가 없으면 건너뜀) |

결과는 `eval/results/verify_all.md`에 저장된다. 화면의 모양(진행 단계, 점수 내역 표, 안내문 검토, 고객 현황)은 사람이 눈으로 봐야 해서 `--ui`로 확인한다.

## 시나리오 A(금양)를 한 번에 점검

서버를 끈 상태에서 DB를 초기화하고, 터미널 두 개로 진행한다:
```bash
# (서버를 끈 상태에서) 깨끗하게 시작
uv run python scripts/data/seed_db.py --reset
rm -f data/checkpoints.db data/checkpoints.db-wal data/checkpoints.db-shm
uv run python scripts/ops/run_stack.py --no-web      # 터미널 1
uv run python scripts/demo/check_scenario_a.py        # 터미널 2
```
사건 전 공시 재생 → 감사의견 거절 공시 → Critical → 영향 고객 7명(P1 5, P2 2) → P01 승인(C001 문장 수정) → P02 반려·승인까지 출력한다.
몇 분 걸리고 모델 비용이 든다. 단계별로 직접 보려면:
```bash
uv run python scripts/demo/replay_demo.py --stock 001570 --until-event   # 사건 전 공시를 날짜순으로 (첫 High에서 멈춤)
```
그다음 화면(5173)에서 접수번호 `20250321802001`을 실행하고, P01·P02로 로그인해 경보 상세 → 안내문 검토 → 승인·반려를 누른다.

> 한 번 처리한 공시를 다시 실행하면 409가 난다. 다시 시연하려면 위처럼 `seed_db.py --reset`과 `data/checkpoints.db` 삭제를 먼저 한다.
> 모델이 어떤 공시를 신호로 볼지는 실행마다 조금 달라서, 첫 High 날짜가 04-26 ~ 05-21 사이로 바뀔 수 있다 (모두 사건 약 10개월 전).

## 나머지 회사(시나리오 B)와 전체 결과

위니아·오스템임플란트·CJ CGV·SK를 같은 방식으로 재생하고 기대 결과와 비교한다 (서버가 떠 있어야 한다):
```bash
uv run python scripts/demo/replay_all.py                 # 금양을 뺀 4개 회사
uv run python scripts/demo/replay_all.py --all           # 금양까지 5개
uv run python scripts/demo/replay_all.py --stock 048260  # 한 회사만
```
회사마다 사건 전 공시 → 사건 공시 → 기대 결과 비교(✓/✗)를 하고 `eval/results/scenario_b.md`에 표를 저장한다. 처리한 공시는 화면(경보 목록)에 그대로 나타난다.
최근 실행(진짜 모델, 실제 DART 공시) 결과는 5개 회사 모두 기대와 일치했다: 금양 사건 2~3개월 전 High(실행마다 12월~1월)·사건 Critical·고객 7명(P1 5, P2 2),
위니아 사건 174일 전 Critical, 오스템 사건 전 경보 없음·횡령 Critical·고객 3명, CGV 최고 Medium, SK 최고 Low (자세한 표는 `eval/results/scenario_b.md`).

> **같은 공시는 한 서버 프로세스에서 한 번만 실행된다**(서버가 메모리에 기억한다). 다시 실행하려면 DB 기록을 지우는 것만으로는 안 되고 **서버를 다시 시작**해야 한다.

## 환경 변수 (`.env`)

| 변수 | 설명 |
| --- | --- |
| `OPENAI_API_KEY` | **진짜 모드에 필요.** 임베딩(`text-embedding-3-small`)과 모델(`gpt-5.4-mini`) |
| `ANTHROPIC_API_KEY` | 있으면 기본 모델이 Claude(`LLM_PRIMARY=claude-haiku-5-5`). **없으면 OpenAI 모델이 기본·대체를 모두 맡는다** (이때 503 → 대체 모델 전환은 같은 모델로 간다) |
| `LLM_PRIMARY` / `LLM_FALLBACK` | `claude…` 또는 `gpt…`. `gpt…`로 두면 Anthropic 키 없이 그 OpenAI 모델을 기본으로 쓴다 |
| `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT` | 있으면 한 공시의 노드·도구·LLM 호출·토큰·비용이 한 trace로 LangSmith에 올라간다 (코드가 환경 변수로 내보낸다) |
| `DART_API_KEY` | `fetch_dart.py`, `compute_base_score.py`용. 시연만 할 때는 필요 없다 |
| `GRAPH_ADAPTER` | `fake`(기본) \| `real` |
| `RAG_INDEX_MODE` | `persistent`(기본, 데모·평가): `rag_docs/`를 `ingest`로 미리 색인해 둔다. `ephemeral`(운영): 공시 1건을 처리할 때 그 원문만 임시로 색인하고(DB 본문 → rag_docs 파일 → DART 순서로 원문을 찾음), 경보를 저장한 직후 색인과 DB 본문을 지운다. 해시·처리 시각만 남는다. 참고 문서 4개의 색인은 그대로 둔다 |
| `FAKE_LLM_ERROR` | `503`이면 기본 모델 호출만 실패시킨다 (재시도 → 대체 모델 시연) |
| `JWT_SECRET` | 32자 이상 권장 |
| `DB_URL`, `CHECKPOINT_DB`, `CHROMA_DIR` | 기본값 `data/app.db`, `data/checkpoints.db`, `data/chroma` |
| `RULES_PATH` | 다른 규칙표를 쓸 때(평가용 복사본). 기본은 `risk_engine/rules.yaml` |

## 점수와 경보 기준

모든 숫자는 [`risk_engine/rules.yaml`](risk_engine/rules.yaml)에만 있다. 총점 = 기본 점수 + 최근 12개월 숨은 신호 × 시간 반영률(6개월 이내 100%, 6~12개월 50%).

| 총점 | 등급 | 하는 일 |
| --- | --- | --- |
| 0~3 | Low | 기록만 |
| 4~6 | Medium | 일일 요약에만 포함 |
| 7~9 | High | 경보, 고객 영향도 계산, 안내문 초안, PB 승인 대기 |
| 10 이상 | Critical | 긴급 경보 (같은 절차) |

- **확정 악재**(감사의견 거절·부적정, 거래정지, 관리종목, 상장폐지 사유, 큰 횡령·배임, 전액 자본잠식)는 점수와 상관없이 Critical이다.
- **조합 규칙**: 12개월 안에 서로 다른 신호가 3종류 이상이면 등급이 한 단계 오른다.
- **고객 영향도**: 보유 비중·평가금액·투자성향·신용거래 점수 → 낮음/보통/높음, 회사 등급과 합쳐 P1·P2·P3. P1·P2만 안내문과 연락 업무를 만든다.
- 같은 종류의 신호는 공시마다 계속 더해진다 (상한 없음, 문서 5-2장 그대로). 다만 아래는 점수에서 뺀다 (`rules.yaml`에 설정):
  - **정정 공시**(`[기재정정]`·`[첨부정정]`): 같은 유형의 신호가 이미 있으면 점수를 다시 더하지 않는다. 같은 유형을 3번 이상 정정하면 "같은 공시 3회 이상 정정"(+2). 첨부만 올리는 정정은 분석하지 않는다.
  - **자회사·종속회사 공시**(유상증자, CB·BW, 감사의견): 이 회사 주식을 희석시키거나 이 회사의 확정 악재가 아니므로 점수·등급에 반영하지 않는다.
  - **최대주주 담보**: 근거에 `설정`·`체결`·`신규`·`제공`이 있을 때(담보가 새로 더해질 때)만 점수가 붙는다. 변경·갱신·연장은 0점.
  - **행정적 거래정지**(회사분할·합병·액면분할 등)와 같은 날 해제된 거래정지는 Critical로 보지 않는다.
- 횡령·배임 5%(코스닥 3%), 자본잠식 50% 기준과 사업보고서 기본 점수의 감사보고서 본문 항목은 `TODO(확인 필요)`.

## 데이터

| 데이터 | 출처 |
| --- | --- |
| 공시 목록·접수번호·제목·접수일 (`data/disclosures/`) | **실제 DART 데이터** (OpenDART). 금양 104건은 DART 조회와 전부 일치함을 확인 |
| 공시 원문 (`rag_docs/companies/`) | **실제 DART 공시 원문** |
| 사업보고서 기본 점수 (`data/demo_companies.json`) | DART 재무제표 API 수치로 계산 (`scripts/data/compute_base_score.py`). 금양 1, 위니아 2, 오스템 0, CGV 2, SK 2(D 확정) |
| PB·고객·보유 종목·평가금액 (`data/seed/customers.json`) | **가짜 데이터.** 실제 사람·계좌와 무관 |
| 사건일·기대 결과 (`demo_companies.json`) | 팀이 정한 평가 정답 |

```bash
uv run python scripts/data/fetch_dart.py              # OpenDART → data/disclosures/<종목코드>.json
uv run python scripts/data/fetch_dart.py --with-text  # 원문 텍스트까지 (느림)
uv run python scripts/data/compute_base_score.py      # 기본 점수 계산 결과 보기 (--write로 기록)
```
- 공시 내용과 접수번호는 OpenDART에서 받은 것만 쓴다. 직접 만들지 않는다.
- 회사별 원문 md는 `rag_docs/companies/<종목코드>_<회사명>/<YYYYMMDD>_<공시종류>.md` (머리말 형식은 CLAUDE.md 7장).

## Harness (`harness/`)
| 파일 | 구성 요소 | 하는 일 |
| --- | --- | --- |
| `loop_limits.py` | 5 Agent Loop | 8턴 · 120초 · 같은 도구 3회. 한도 값의 단일 출처이고 Agent(`agent/ports.py`, `agent/execution.py`)가 가져와 시행한다. 넘으면 MANUAL_CHECK |
| `guardrails.py` | 6 Guardrails | 안내문 금지 표현 검사, 재작성 2회 한도 |
| `masking.py` | 6 Guardrails | 고객 이름 `고객 C001`, 전화·계좌 `***` (날짜는 가리지 않음) |
| `recovery.py` | 7 Recovery | LLM 429/503 → 재시도(1초, 3초) → 대체 모델 → FAILED, 도구 TIMEOUT 1회 재시도 |
| `context.py` | 3 Context | 도구 결과 4,000자 자르기, 입력 6,000토큰 예산, 오래된 메시지 요약, RAG 상위 5개 |
| `tracing.py` | 8 Observability | LangSmith 메타데이터(rcept_no, corp_name, risk_level, prompt_version, model), audit_logs 기록 |

이 모듈들을 Agent에 연결하는 조립은 [`agent/assembly.py`](agent/assembly.py)에 있다 (`build_dependencies()`). 실패 시나리오는 `FAKE_LLM_ERROR=503`으로 재현한다.

## 평가
```bash
uv run python eval/run_eval.py            # eval/results/*.md 에 전/후 표 저장
uv run python eval/capture_signals.py      # (선택) combo·rag_k·model 실험의 입력 신호를 진짜 모델로 다시 저장 (수 분, 수백 원)
uv run python eval/run_eval.py --only guardrail
```
`guardrail`, `context`는 지금 돈다. `combo`, `rag_k`, `model`은 `EXPERIMENTS`에 함수를 채운다 (`combo`는 `RULES_PATH`로 `combo.enabled: false`인 규칙표 복사본을 쓰면 된다).

## 테스트
```bash
uv run pytest -q                          # 전체 (네트워크·키 불필요)
uv run pytest tests/mcp_servers tests/agent tests/api tests/harness -q
```
테스트는 가짜 임베딩과 가짜 모델을 쓴다. `tests/mcp_servers/test_assembly.py`는 진짜 도구 함수·DB·검색·Harness를 묶어 시나리오 A 4~12단계를 서버 없이 확인한다.

## 문제 해결
| 증상 | 원인과 해결 |
| --- | --- |
| `POST /runs`가 409 | 이미 처리한 공시. `seed_db.py --reset`과 `data/checkpoints.db` 삭제 후 다시 |
| 공시를 실행하면 `agent.Dependencies 조립 실패` | business-mcp(8001)·rag-mcp(8002)가 안 떠 있거나 `.env`에 `OPENAI_API_KEY`가 없다 |
| `search_disclosure_text`가 NOT_FOUND | `python -m mcp_servers.rag.ingest`를 안 했거나 본문이 없는 공시 |
| 색인과 임베딩 설정이 다르다는 오류 | `EMBEDDING_PROVIDER`를 바꿨다. `ingest --reset`으로 다시 만든다 |
| `MultiServerMCPClient` import 오류 | `mcp`가 2.x로 올라갔다. `uv sync`로 `uv.lock`(mcp 1.x 고정)에 맞춘다 |
| 상태가 MANUAL_CHECK | AI가 근거를 못 찾았거나 한도 초과. `audit_logs`(action `agent_failure`)에서 원인을 본다 |
| LangSmith에 trace가 안 보임 | `LANGSMITH_API_KEY`와 `LANGSMITH_TRACING=true`를 확인. 테스트(pytest)에서는 전송하지 않는다 |

## 알려진 한계
- Anthropic 키가 없으면 대체 모델 전환이 같은 OpenAI 모델 사이에서 일어난다.
- 같은 신호가 공시마다 쌓여 경보가 사건보다 일찍 울리고, 이후 비슷한 공시마다 경보가 반복될 수 있다 (개선 과제).
- 사건 날짜 기준 경보 시점은 모델이 어떤 공시를 신호로 보는지에 따라 실행마다 며칠~몇 주 달라질 수 있다 (등급과 사건 공시 결과는 안정적이다).
- SQLite 파일 하나를 서버 두 개가 쓰므로 동시에 여러 PB가 결정하는 경우는 시험하지 않았다.

## 검증 후 운영 보완 (2026-10-22)

`uv run alembic upgrade head`로 20261022_02 마이그레이션을 적용한다. 위험 판정 기준일·버전과 수집 임대 컬럼을 추가하며 이전 마이그레이션은 수정하지 않는다.

수집 실행자는 `shared.collection.heartbeat_run(session, run_id)`가 false이면 만료된 작업이므로 중단한다. 정상 실행 중 COLLECTION_LEASE_SECONDS보다 짧은 주기로 heartbeat하고, 각 종목을 finish_stock으로 마친 후 finish_run으로 실행을 종료한다. `uv run python -m scripts.ops.recover_collections`는 만료 작업을 실패 처리하고 예약을 해제한다. 수동 수집 요청도 만료 복구를 수행한다. 스케줄러에서의 호출 연결은 R3 담당이다.

새 공시 분류와 모델 결과 필터는 R4의 Phase1 adapter에 연결했다. 과거 근거는 실제 RAG 조각을 다시 조회·검증하고 검증된 인용 부분만 반환한다. RAG 재조회가 불가능하면 수동 확인으로 남긴다. R2의 프롬프트 품질 검증 및 회사별 Graph 실행은 여전히 별도 연동이 필요하다.

## Pre-merge R4 실행 지원 (2026-10-23)

- `uv run alembic upgrade head`: 기존 01/02는 유지하고 03(구조화 사실/준비 표시), 04(분석 작업/불변 판정/조직 검토)를 추가한다. 사용자 데이터 자동 삭제·원문 자동 이관은 하지 않는다.
- 구조화 사실은 `schema_version: "1"`, 상태는 PRESENT/MISSING/NOT_APPLICABLE/NEEDS_CHECK다. 재무 indicators의 OK/MISSING과 혼용하지 않는다.
- R3는 `store_facts`와 `mark_ready`를 수집 완료 트랜잭션에 연결해야 한다. 현재 collector/jobs.py를 R4가 대신 수정하지 않았다.
- 수동 수집은 `COLLECT_DISPATCH_ENABLED=true`일 때 R3 dispatcher를 연결한다. 기본 false이며 DART 실호출 없이 오프라인 검증한다. Compose integration의 collector는 `python -m collector --schedule`로 실행한다.
- R3 현 구현에는 httpx만 필요하다. 이를 운영 의존성으로 옮겼으며 미사용 tenacity/APScheduler는 추가하지 않았다. COLLECT_TIME(09:05 KST), COLLECT_BACKFILL_MONTHS, DART_TIMEOUT_SECONDS, DART_RETRY_MAX, DART_MAX_RPS, DART_MAX_CONCURRENCY를 설정한다. DART_RETRY_MAX는 R3 구현상 **재시도 횟수**(최초 별도)이며 R4 작업 전체 최대 3회와 다르다. MAX_RPS 기본 4는 공식 한도 보장이 아니다.
- 체크포인터는 R4 `open_checkpointer`가 생성·종료하며 R2에는 객체를 주입한다. 구형 R2 open_runtime 편의 함수는 호환용으로 보존했다.
- `PHASE1_RUNTIME_FACTORY`가 비어 있으면 새 공개/조직 Graph는 실행하지 않는다. R2가 계약에 맞는 factory를 제공한 뒤 설정하면 API가 DB 분석 worker와 private executor를 연결한다. 같은 factory를 `python -m scripts.ops.run_analysis_worker --factory module:factory [--once]`로 별도 호스트에 주입할 수도 있다. 실제 존재하지 않는 factory 예시를 실행하면 안 된다.
- ANALYSIS_LEASE_SECONDS와 ANALYSIS_RETRY_DELAY_SECONDS는 수집 lease와 별도다. 큐는 false를 유지한다. ANALYSIS 작업은 PENDING/RUNNING/RETRY_WAIT/DONE/MANUAL_CHECK/FAILED로 관리한다.
- `processed_at`은 새 공개 판정 저장 성공 시각이다. 승인 완료·색인 해제 시각이 아니다. MANUAL_CHECK는 성공 시각을 기록하지 않으며 명시적 재분석 요청으로만 재실행한다.
- 기존 purge_disclosure_text와 release_disclosure_index는 구형 호출자 때문에 보존했다. 새 공개 저장은 purge를 호출하지 않는다. R2 원문 수명 전환·R3 메모리 동시 사용 정책 확인 후 기존 호출 제거/도구 정리를 진행한다.
- 기존 refresh_states는 구형 상태 재계산 경로다. 새 불변 공개 판정의 일일 재계산은 새 기준일로 분석 작업을 등록하는 경로에 연결해야 한다. 스케줄러에서 이 인계를 연결하기 전 자동 갱신 완료로 판단하지 않는다.

## R5 상태 요약 필드 (2026-10-23)

추가 마이그레이션 20261023_05가 필요하다. 종목 상세의 changes는 저장된 직전 상태와의 차이며 comparison_available=false이면 과거 상태를 추측해 표시하지 않는다. version(알림), change_version(판정 변화), review의 version(검토)은 구분한다. 신규 검토 링크는 review_backend/ID 종류를 확인하며 legacy Graph 조회 불가의 null은 대기 없음과 다르다. 자세한 변경은 docs/log/2026-10-23-r4-r5-api-followup.md를 참고한다.
