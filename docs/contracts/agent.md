# Agent 계약 (State·Graph·API 연결)

> 담당: **R2** · 상태: 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. 승인(interrupt)·안내문·연락 업무 관련 State와 노드는 삭제 대상이고, 그래프는 새로 정한다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.

## 4-5. Agent State (`agent/state.py`)

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

## 4-6. Graph 노드 (`agent/graph.py`)

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

## 4-7. API ↔ Graph 연결 약속 (A 구현 기준으로 C가 맞춤)

API(`api/graph_adapter.py`의 `RealGraphAdapter`)는 A의 **`agent.AgentRuntime`**으로 Graph를 부른다. 자세한 경계는 `docs/contracts/agent.md`.
LLM에게: 이 절과 `docs/contracts/agent.md`가 다르면 docs/contracts/agent.md(A)가 맞다. 체크되지 않은 항목은 담당자 확인이 필요하다고 사용자에게 알려라.
검증: `tests/api/test_runtime_adapter.py` (A의 가짜 deps로 API → AgentRuntime → 승인·재개·재시작을 확인).

**API가 Graph를 부르는 방법 (C 구현 완료)**
- `open_runtime(deps, data/checkpoints.db)`를 한 번 열고 서버 종료 시 닫는다. `thread_id = rcept_no`.
- 시작 `runtime.start(rcept_no)`, 재개 `runtime.resume(rcept_no, ApprovalBatch, PBContext)`.
  - C 형식 `{action: approve|reject|rewrite, edited_draft}` → A 형식 `{status: approved|rejected|rewrite, edited_message}`로 바꾼다.
  - `PBContext.allowed_customer_ids`는 토큰의 PB가 DB에서 담당하는 고객으로 C가 만든다.
  - 결정 API는 202 전에 `validate_batch`로 먼저 검사한다: PermissionError → 403, ValueError(가드레일 실패한 수정안 등) → 422.
- 진행 이벤트: `deps.harness.event`의 `node_started`·`node_finished`를 SSE로 보낸다. `waiting_approval`·`done`·`failed`는 실행이 끝난 뒤 State(`status`)로 C가 만든다.
- 화면 데이터: `runtime.get_state` → `assessment_id, status, disclosure, risk_level, score_result, ai_explanation, evidence, affected_customers, drafts, guardrail_flags, manual_drafts, decisions, task_ids, error`. PB별 필터는 API 라우트가 한다.

**A (Agent) — 구현으로 확인됨**
- [x] PB 1명씩 결정 묶음, `create_tasks` 후 남은 고객이 있으면 다시 승인 대기 (`finalize` → `prepare_approval`)
- [x] interrupt 전에 `persistence.save(state, "WAITING_APPROVAL")` → `State.assessment_id`, 끝나면 `DONE`
- [x] 결정 누적은 `State.decisions = {customer_id: {status, edited_message, reject_reason, pb_id, decided_at}}`
- [x] 반려 고객은 업무 없음(`save_rejection`이 감사 기록), interrupt 노드 앞 DB 쓰기 없음
- [x] **`agent.Dependencies` 조립**은 B가 `agent/assembly.py`의 `build_dependencies()`로 만들었다 (tools=`MCPTools`, model=`StructuredLLM`, harness=D 모듈을 async로 연결, persistence=`AssessmentStore`, create_task=`ToolTaskCreator`). `api/graph_adapter.default_dependencies()`가 이를 부른다. 실제 실행에는 business-mcp(8001)·rag-mcp(8002)와 `.env`의 `ANTHROPIC_API_KEY`·`OPENAI_API_KEY`가 필요하다. 모델만 가짜로 바꾼 시나리오 A 통합 테스트: `tests/mcp_servers/test_assembly.py`.
- [x] `langchain-mcp-adapters 0.3.1` + `mcp 2.3.0` import 오류 → B가 `mcp>=1.12,<2`, `fastmcp>=2.12,<3`로 고정하고 `uv.lock`을 갱신했다 (feat/b-risk-score). 실제 서버 두 개에 `MCPTools.connect`가 붙는 것을 확인했다.

**B (MCP·shared) 확인 항목**
- [x] `shared/config.py` Settings에 `GRAPH_ADAPTER: str = "fake"`(fake | real), `FAKE_GRAPH_DELAY: float = 0.4`를 추가한다. 지금은 API가 `os.environ`에서 임시로 읽는다.
- [x] `save_assessment`는 **rcept_no 기준 upsert**다. 같은 공시를 다시 저장하거나 `signals`·`score_result`를 비우고 status만 보내도(WAITING_APPROVAL → DONE) `assessment_id`가 유지된다. 점수 계산 전 실패(RUNNING·FAILED·MANUAL_CHECK)도 점수 없이 저장된다.
- [ ] (C 결정 필요) `PATCH /tasks`는 지금 API가 DB를 직접 고친다. `update_task_status` 도구는 같은 규칙(`rules.yaml`의 `task_transitions`: 승인·보류 → 연락완료·보류)으로 준비돼 있다. 규칙은 "승인·보류 상태에서만 → 연락완료·보류"다. `update_task_status` 도구를 쓰도록 바꿀지 정한다.
- [x] `calculate_risk_score`의 `breakdown[i]`에 `quote`와 `evidence`(`{source, quote, rcept_no, location}` 목록)가 실린다. 입력 `new_signals[i].evidence`를 그대로 돌려주고, 과거 신호는 `source_chunks`도 붙는다 (`docs/contracts/mcp_business.md`).

**D (데이터·Harness·문서) 확인 항목**
- [ ] `seed_db.py`의 비밀번호는 bcrypt 해시로 저장한다. `from api.auth import hash_password`를 쓰거나 `bcrypt.hashpw(pw.encode(), bcrypt.gensalt())`를 쓴다. 이메일은 소문자로 저장한다.
- [ ] `.env.example`에 `GRAPH_ADAPTER=fake`, `FAKE_GRAPH_DELAY=0.4`를 추가하고, `JWT_SECRET`은 32자 이상을 권장한다고 적는다.
- [ ] README 실행 방법: API `uv run uvicorn api.main:app --reload --port 8000`, 화면 `cd web && npm install && npm run dev`, 점검 `uv run python scripts/demo/smoke.py --rcept-no <접수번호>`
- [ ] (선택) `harness/guardrails.py`에 `check_message(text) -> list[str]`를 열어 주면, PB가 직접 고친 안내문도 API에서 금지 표현을 검사한다.

---

---

## 경계 상세 (docs/contracts/agent.md에서 옮김)

# Agent 실행·연동 안내

A 구현 범위는 `agent/`, `tests/agent/`다. 공통 스키마·타 담당 폴더·의존성 잠금 파일은 변경하지 않았다.

## 테스트 실행

```bash
uv sync --frozen
uv run pytest tests/agent -q
```

테스트는 네트워크·실제 키·다른 담당 서버 없이 실행된다. 합성 공시를 사용하며 실제 공시의 정확성이나 조기경보 성능을 검증하지 않는다. 고객 기대 우선순위는 기존 `data/seed/customers.json`을 참조한다. `FakeHarness`는 D의 운영 구현이 아니다.

## C: Python 호출 경계

```python
from agent import open_runtime, PBContext, ApprovalBatch

# deps는 아래 B·D 계약을 실제 구현에 연결한 Dependencies 객체다.
# API lifespan에서 한 번 열고 종료할 때 닫는다.
async with open_runtime(deps, "data/checkpoints.db") as runtime:
    state = await runtime.start(rcept_no)

    # C가 JWT 검증과 DB 소유권 조회를 완료한 뒤 내부에서 만든다.
    pb = PBContext(pb_id=authenticated_pb_id,
                   allowed_customer_ids=owned_customer_ids)
    review = await runtime.get_review(rcept_no, pb)
    state = await runtime.resume(rcept_no, ApprovalBatch(decisions=[
        {"customer_id": "C001", "status": "approved",
         "edited_message": "{고객명}님, 공시 내용을 확인하시고 상담 부탁드립니다."},
    ]), pb)
```

- pb_id·allowed_customer_ids를 클라이언트 요청에서 그대로 복사하면 안 된다.
- 고객별 status는 `approved`, `rejected`, `rewrite`. 반려 시 reject_reason이 필수다.
- `start`는 같은 스레드를 다시 실행하지 않고 기존 상태를 반환한다.
- 잘못된 결정·타 고객 결정·금지 문구가 있는 수정안은 interrupt를 소비하기 전에 거부한다.
- `resume`은 PB별 고객 결정을 누적한다. 승인 업무 생성 후 미결정 고객이 남으면 다시 대기한다.
- 같은 승인·반려 요청 재전송은 기존 결과를 반환한다. 기존 결정을 다른 내용으로 바꾸는 요청은 거부한다. 다시 작성은 새로 요청한 동작으로 취급한다.
- 생성 실패 시 `retry_tasks(rcept_no, pb)`로 이미 승인된 미완료 업무만 명시적으로 재시도할 수 있다. 실패 노드가 create_tasks인 경우만 허용한다. 새 승인·재분석은 하지 않는다.
- `get_state`, `start`, `resume` 반환은 서버 내부용 전체 State다. HTTP에 그대로 반환하지 않는다. PB 화면에는 `get_review`를 사용한다. 원시 interrupt·Graph 이벤트에도 타 PB 고객이 포함될 수 있어 그대로 브라우저에 전달하지 않는다.
- C에서 PermissionError→403, 입력 오류→적절한 4xx로 변환한다. assessment_id→rcept_no 조회는 C/B가 제공한다.
- 단일 runtime 내 동일 스레드 요청은 잠금으로 직렬화한다. 여러 process를 사용하면 C에서 동일 thread_id의 분산 직렬화를 보장해야 한다. 초기 데모는 단일 worker를 권장한다.
- PB 대기 시간은 실행 예산에서 제외한다. 시작·재개·업무 재시도 구간마다 120초·AI 생성 최대 8회를 적용한다. 그래프 recursion_limit은 별도로 256이다.

## B: MCP 연결

`MCPTools.connect(settings)`로 두 서버의 도구를 읽는다. LLM에 도구 선택권을 주지 않고 코드가 호출한다. update_task_status는 허용하지 않는다. 아래 상세 형식은 A 내부 adapter 계약이며, B 출력이 다르면 경계에서 변환한다.

| 응답 | A가 받는 상세 형식 |
| --- | --- |
| 공시 조회 | rcept_no, corp_code, title, filed_at; 본문은 State·모델 입력에서 제외 |
| 회사 조회 | stock_code, name |
| 공시 RAG | 조각 배열 또는 `{chunks: [...]}`. 각 조각의 source, text, rcept_no, location은 문자열 |
| 고객 영향도 | 고객 배열 또는 `{customers: [...]}`. 각 항목은 shared.CustomerImpact |
| 점수 계산 | shared.ScoreResult 필드 + 규칙이 계산한 신규 signals 배열 |
| 점수 내역 | breakdown 원소마다 evidence 배열. 과거 근거는 검증 가능한 source_chunks도 제공 |
| 평가 저장 | `{assessment_id: int}`. rcept_no 기준 upsert, 상태 갱신 시 ID 유지 |
| 업무 생성 | `{task_id: int}`. 평가·고객 기준 멱등성 보장 |

근거는 `{source, quote, rcept_no, location}`이다. source_chunks 필드는 RAG 조각과 같다. 실제 원문에 인용이 포함되어야 한다. 각 점수 항목의 근거가 없으면 설명을 지어내지 않고 MANUAL_CHECK로 종료한다.

`Dependencies.signal_input`은 검증된 사실을 B의 new_signals로 변환하는 함수다. LLM에서 점수를 받지 않는다. 현재 shared.Signal.points는 필수이므로 B의 신호 계산·반환 계약 확인이 필요하다. 임의 0점을 채우는 우회는 없다. B adapter는 계산 결과의 signals를 shared.Signal 배열로 제공해야 한다.

과거 신호는 조회하되 new_signals에 다시 합치지 않는다. B 계산 도구가 공시일 as_of 기준으로 합산한다. 분류는 주입된 classifier로 실행한다. rules.load_classifier의 YAML 구조는 제안이며 B와 다르면 loader를 조정하거나 B의 함수를 바인딩한다.

### 저장 연결

- `PersistenceBindings(save=..., audit=...)`로 실제 저장 함수를 연결한다. save는 전체 State와 상태를 받아 upsert한다. 계산 전에 실패한 평가의 저장도 처리해야 한다.
- 기존 도구의 upsert가 확인되면 `ToolAssessmentStore(tools, audit, harness, upsert_confirmed=True)`를 사용한다. shared 입력 필드만 전달한다. 이 adapter만으로는 미계산 평가 저장·초안/결정 별도 DB 조회를 지원하지 않는다. 전체 상태·초안은 체크포인트에 저장되어 runtime에서 조회할 수 있다.
- audit 함수는 trace_id, actor, action, target, payload를 실제 로그에 저장한다. 고객별 결정의 idempotency_key로 재실행 중복을 막는다. 반려는 감사 기록에 남기고 contact_tasks 행은 만들지 않는다.
- `ToolTaskCreator(tools, harness, authorize, idempotency_confirmed=True)`를 Dependencies.create_task에 넣을 수 있다. authorize는 `(pb_id, customer_id, assessment_id)`를 받는 async 소유권 검사다. 도구에는 shared.CreateContactTaskInput 필드만 전달한다. B는 평가·고객별 유일성과 응답 유실 후 재호출 시 기존 task_id 반환을 보장해야 한다.

## D: Harness 연결

HarnessBindings에 아래 async 함수를 모두 주입한다. 없는 모듈을 자동으로 가짜 구현으로 교체하지 않는다.

| 함수 | 계약 |
| --- | --- |
| prepare_input(payload) → dict | 개인정보 마스킹, 도구 결과 4,000자·입력 6,000토큰 예산 관리. 원문 검증용 구조는 보존하고 모델 문맥만 축약 |
| check_message(message) → list[str] | 금지 문구·개인정보 검사. 통과 시 빈 배열 |
| recover_llm(primary, fallback) | 429/503만 1초·3초 간격으로 2회 재시도 후 fallback. 구조 검증 오류는 구분 |
| recover_tool(operation) | ToolResponse 반환. TIMEOUT만 1회 재시도, 나머지 오류는 그대로 반환 |
| event(name, payload) | C 이벤트·D 관측 연결. 개인정보 없는 이벤트 전달 |

초기 안내문 7개는 한 번의 구조화 호출로 생성한다. 실패 고객만 묶어 최대 두 번 재작성한다. 기본 AI 생성은 사실 추출·설명·초안의 3회이며 고객 수 때문에 8턴을 넘지 않는다. 모델 장애 재시도는 D recovery에서 계측한다.

A는 구간 내 도구명+동일 인자 반복을 최대 3회로 제한한다. 서로 다른 고객의 정상 업무 생성은 별개 입력이다. 이 해석은 D와 확인해야 한다. 실제 저장 adapter도 도구 호출로 집계한다. 실패 기록에는 최대 15초의 별도 정리 예산을 둔다.

LangSmith root의 trace_id와 전달 헤더를 체크포인트에 저장한다. 재개는 원래 trace의 child로 연결한다. Graph·LLM은 LangChain tracing을 사용하며 모델·프롬프트 버전을 metadata에 남긴다. D는 tracing 환경·프로젝트·도구 관측을 설정한다. 실제 서버 전송·토큰·비용·화면 캡처는 실제 키로 확인해야 한다.

## 현재 외부 의존성 제한

2026-10-20 `uv sync --frozen`으로 설치한 조합은 langchain-mcp-adapters 0.3.1, mcp 2.3.0이다. 이 환경에서 `from langchain_mcp_adapters.client import MultiServerMCPClient`가 `mcp.shared.context.RequestContext` ImportError를 낸다. 서버 연결 전의 패키지 호환 문제다.

의존성 담당자가 호환 버전을 확인하고 pyproject.toml·uv.lock을 함께 조정해야 한다. A에서는 잠금 파일을 변경하지 않았고 준비 오류를 명시한다.

실제 공시·MCP·D Harness·C 인증/API·모델 키가 아직 연결되지 않았다. 현재 테스트 결과는 A의 흐름·검증·저장 경계에 대한 결과이며, 실제 계산·공시 사실성·HTTP 권한·실제 저장 멱등성은 병합 후 통합 검증 대상이다.
