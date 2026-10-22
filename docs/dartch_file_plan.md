# DARTCH 파일 구성 계획 (초안 — 아직 실행하지 않음)

> 목적: R1~R5가 **서로 같은 파일을 고치지 않고** 병렬로 개발할 수 있게 폴더와 파일을 정리하는 계획이다.
> 이 문서는 계획만 담고 있고, 파일은 하나도 옮기거나 지우지 않았다.
> 기준: [DARTCH Phase 1 MVP 설계서](dartch_phase1_mvp.md), 팀이 합의한 역할 분담(R1 양시온, R2 윤찬웅, R3 한예빈, R4 양경환, R5 박세호).
> 범례: **유지** 그대로 / **수정** 내용만 바꿈 / **이동** 위치만 바꿈 / **분리** 파일을 여러 개로 쪼갬 / **삭제** / **신규**

---

## 1. 원칙 (충돌을 줄이는 다섯 가지)

1. **파일 하나에 담당 한 명.** 한 파일을 둘 이상이 고쳐야 하면 파일을 쪼갠다. 쪼갠 뒤에는 폴더 단위가 아니라 **파일 단위**로 담당을 정하고, `.github/CODEOWNERS`로 리뷰어가 자동 지정되게 한다.
2. **계약은 문서로 먼저.** 담당이 다른 코드끼리 만나는 곳(DB, API, MCP 도구, State, 수집 데이터)은 `docs/contracts/`에 적고, 바꿀 때는 양쪽 담당이 같이 승인한다.
3. **의존 방향을 정한다.** 아래쪽이 위쪽을 import하지 않는다(6장).
4. **구조 정리(이동·분리·삭제)는 기능 개발보다 먼저, 한 번에, 한 사람이.** 그동안 다른 사람은 기다린다. 정리가 끝나고 테스트가 통과한 뒤에 각자 브랜치를 딴다.
5. **테스트는 소스와 같은 모양, 같은 담당.** `tests/<폴더명>/`의 담당은 소스 폴더 담당과 같다.

---

## 2. 목표 구조 (DARTCH Phase 1)

담당 표시: [R1]~[R5]. 파일 단위 담당이 폴더 담당과 다르면 파일 옆에 따로 적었다.

```
dartch/
├── .github/CODEOWNERS                      # 경로별 리뷰어 [R1 작성, R4 검토]
├── CLAUDE.md                               # 공통 규칙·담당표·계약 링크만 (약 100줄) [R1]
├── README.md                               # 요약과 링크 [R1]
├── .env.example  pyproject.toml  uv.lock   # [R4] (다른 역할은 변경을 R4에게 요청)
├── docker-compose.yml  docker/             # postgres, api, collector, mcp 서버, web [R4]
│
├── docs/
│   ├── architecture.md  dartch_phase1_mvp.md  risk_policy.md   # [R1] 위험 점수표 값·기준 확정본
│   ├── contracts/
│   │   ├── api.md  db.md  mcp_business.md  risk_rules.md       # [R4]
│   │   ├── agent.md                                            # [R2]
│   │   └── mcp_rag.md  data_sources.md  collector.md           # [R3]
│   ├── run.md                                                  # [R4] 실행 방법·환경 변수
│   └── verification.md                                         # [R5] 검증 결과·시연 절차
│
├── shared/                                 # [R4]
│   ├── config.py  db.py  schemas.py
│   └── models/  core.py  tenant.py  risk.py  collect.py([R3]·R4 검토)
├── migrations/                             # Alembic [R4]
│
├── risk_engine/                            # 규칙 엔진, MCP 없이 쓰는 라이브러리 [R4]
│   ├── rules.yaml  loader.py               # 값은 R1이 확정, 파일은 R4가 수정
│   ├── red.py  orange.py  yellow.py        # 🔴 / 🟠 점수 / 🟡 점수
│   ├── stage.py                            # 경보·복합 위험·관찰 중 판정
│   └── states.py                           # 종목별 상태: 반복·해소·확인함
│
├── collector/                              # DART 수집 [R3]
│   ├── dart_client.py                      # httpx, 재시도, 호출 속도 제한
│   ├── watchlist.py                        # 보유 종목 합집합, ETF 제외, 회사 코드 변환
│   ├── cursor.py  jobs.py  backfill.py     # 증분 커서, 종목별 작업, 신규 종목 백필
│   ├── indicators.py                       # 🟡 재무 지표 계산
│   ├── repo.py  scheduler.py  __main__.py  # DB 저장, 매일 09:05(APScheduler), 실행 진입점
│
├── mcp_servers/
│   ├── business/                           # 업무 MCP (규칙 엔진을 부르는 얇은 도구) [R4]
│   └── rag/                                # 참고 문서·공시 원문 임시 색인 [R3]
│
├── agent/                                  # Agent [R2]
│   ├── graph.py  state.py  runtime.py  execution.py  ports.py  llm.py
│   ├── evidence.py  tool_loop.py  rules.py            # rules.py: 분류·신호 연결
│   ├── model_adapter.py  harness_llm.py               # assembly.py에서 분리 [R2]
│   ├── nodes/  prompts/
│   └── adapters/                                      # 연결 코드 [R4]
│       ├── assembly.py  mcp_client.py  store.py  harness_tools.py  bindings.py
│
├── harness/                                # 파일 단위 담당
│   ├── context.py  guardrails.py  loop_limits.py  recovery_llm.py     # [R2]
│   └── masking.py  tracing.py  recovery_tools.py                      # [R4]
│
├── api/                                    # [R4]
│   ├── main.py  auth.py  deps.py  tenancy.py  graph_adapter.py  run_manager.py  audit.py
│   ├── routes/  auth.py  states.py  customers.py  collect.py  events.py  runs.py
│   └── worker/                             # 큐 (Phase 1은 꺼 둠)
│
├── web/                                    # [R5]
├── rag_docs/reference/                     # 위험 항목 해설 문서 [R3]
├── data/seed/                              # 가짜 회사·PB·고객·보유 종목 시드 [R3]
│
├── eval/
│   ├── run_eval.py                         # 실행기 [R4]
│   ├── experiments/  rag_k.py [R3]  model.py  guardrail.py  context.py [R2]  stage_thresholds.py [R4]
│   ├── capture_signals.py                  # [R2]
│   ├── cases/                              # 테스트 사례 세트 [R3·R5 공동 폴더]
│   └── results/                            # 생성물
│
├── scripts/
│   ├── ops/   run_stack.py  doctor.py  verify_all.py       # [R4]
│   ├── data/  seed_db.py                                    # [R3]
│   └── demo/  replay_demo.py  replay_all.py  check_scenario.py  smoke.py   # [R5]
│
└── tests/                                  # 소스와 같은 담당
    ├── agent/ [R2]   harness/ (파일별)   collector/ [R3]   rag/ [R3]
    ├── business/ [R4]   risk_engine/ [R4]   api/ [R4]   wiring/ [R4·R2]   demo/ [R5]
```

---

## 3. 파일별 변경표 (현재 → 계획)

### 루트·문서
| 현재 | 변경 | 계획 | 담당 |
| --- | --- | --- | --- |
| `CLAUDE.md` (571줄) | 수정·분리 | 공통 규칙·담당표·링크만 남기고, 4장 "연결 약속"은 `docs/contracts/`로 옮김 | R1 |
| `README.md` | 수정 | 요약·역할·범위·링크만. 실행 방법은 `docs/run.md`, 검증은 `docs/verification.md` | R1 (각 문서는 R4·R5) |
| `.env.example`, `pyproject.toml`, `uv.lock`, `docker-compose.yml`, `.gitignore` | 수정 | PostgreSQL, collector, 새 환경 변수 | R4 |
| — | 신규 | `.github/CODEOWNERS` | R1 |
| `docs/architecture.md` | 수정 | DARTCH 구조로 갱신 | R1 |
| `docs/dartch_phase1_mvp.md` | 유지 | 설계서 | R1 |
| `docs/evidence/traces.md` | 삭제 | 이전 프로젝트 증빙(새 trace는 나중에) | — |
| — | 신규 | `docs/risk_policy.md`, `docs/contracts/*`, `docs/run.md`, `docs/verification.md` | 각 담당 |
| `agent/INTEGRATION.md` | 이동 | `docs/contracts/agent.md` | R2 |
| `agent/IMPLEMENTATION_PLAN.md`, `mcp_servers/*/README.md` | 삭제·통합 | 이전 계획서는 삭제, MCP README는 `docs/contracts/mcp_*.md`로 합침 | R2·R3·R4 |

### shared·마이그레이션·규칙 엔진
| 현재 | 변경 | 계획 | 담당 |
| --- | --- | --- | --- |
| `shared/config.py`, `db.py` | 수정 | PostgreSQL, 새 설정 | R4 |
| `shared/schemas.py` | 유지 | 도구 응답·오류 형식 | R4 |
| `shared/models.py` | **분리** | `models/core.py`(회사·공시·신호), `tenant.py`(회사·PB·고객·보유), `risk.py`(위험 상태·평가·감사), `collect.py`(재무 스냅샷·수집 이력, R3 주담당). `contact_tasks` 삭제 | R4 |
| — | 신규 | `migrations/` (Alembic) | R4 |
| `mcp_servers/business/rules/rules.yaml` | **이동·재작성** | `risk_engine/rules.yaml` — 🔴/🟠/🟡 구조. 🔴에 거래정지·관리종목·국내 상장폐지·횡령·배임 규칙 이식. 숫자는 이 파일에만 | R4 (값 R1) |
| `rules/loader.py` | 이동 | `risk_engine/loader.py` | R4 |
| `rules/risk_rules.py` | **분리** | `red.py`, `orange.py`, `yellow.py`, `stage.py`. 시간 반영·정정 처리 코드는 재사용 | R4 |
| `rules/impact_rules.py` | 삭제 | 연락 우선순위 폐지 | — |
| — | 신규 | `risk_engine/states.py` (반복·해소·확인함) | R4 |

### collector (신규, R3)
| 신규 파일 | 하는 일 |
| --- | --- |
| `dart_client.py` | DART API 호출 (httpx, 재시도, 호출 속도 제한). 현재 `scripts/fetch_dart.py`와 `mcp_servers/rag/dart_source.py`의 호출 코드를 통합 |
| `watchlist.py` | 보유 종목 합집합(ETF 제외), 종목코드 → 회사 코드 |
| `cursor.py`, `jobs.py`, `backfill.py` | 증분 커서, 종목별 수집 작업, 새 종목 최근 12개월 채우기 |
| `indicators.py` | 자본잠식·현금흐름·이자보상·단기채무 지표 (현재 `compute_base_score.py`의 재무 호출 로직을 재사용) |
| `repo.py`, `scheduler.py`, `__main__.py` | DB 저장, 매일 09:05 실행, 진입점 |

### mcp_servers
| 현재 | 변경 | 계획 | 담당 |
| --- | --- | --- | --- |
| `business/server.py`, `tools/_common.py` | 수정·유지 | 새 도구 목록 등록 | R4 |
| `tools/calculate_risk_score.py` | 수정 | 단계 판정 결과를 돌려줌 (이름 변경 여부는 계약 변경이라 합의) | R4 |
| `tools/get_company_profile.py`, `get_disclosure.py`, `get_holders.py`, `get_signal_history.py`, `list_new_disclosures.py`, `save_assessment.py` | 수정 | 새 모델(tenant, risk_states)에 맞춤 | R4 |
| `tools/compute_customer_impact.py`, `create_contact_task.py`, `update_task_status.py`, `purge_disclosure_text.py` | **삭제** | 연락·원문 저장 폐지 | — |
| — | 신규 | `get_risk_state`, `save_risk_state`, `get_financial_snapshot` (예상) | R4 |
| `rag/server.py`, `tools.py` | 수정 | `release_disclosure_index` 삭제 | R3 |
| `rag/store.py` | 수정 | Chroma 영속 → 메모리 임시 색인 | R3 |
| `rag/filing.py` | 수정 | 원문은 DART에서만(DB·파일 우선순위 제거) | R3 |
| `rag/dart_source.py` | **삭제** | `collector/dart_client.py`로 통합 | R3 |
| `rag/ingest.py` | 수정 | 참고 문서만 적재 | R3 |
| `rag/embedding.py` | 유지 | | R3 |

### agent
| 현재 | 변경 | 계획 | 담당 |
| --- | --- | --- | --- |
| `graph.py` | 재작성 | 승인·초안 없는 새 그래프 | R2 |
| `state.py` | 수정 | 승인·초안·결정 항목 제거, 단계 항목 추가 | R2 |
| `runtime.py` | 수정 | interrupt 제거, Postgres 체크포인트 | R2 (연결 R4) |
| `execution.py`, `evidence.py`, `tool_loop.py`, `llm.py` | 유지·수정 | | R2 |
| `ports.py` | 수정 | 승인·업무 생성 포트 제거 | R2 |
| `rules.py` | 수정 | 분류·신호 연결 (`assembly.py`의 `signal_input`, `make_classifier` 이동 받음) | R2 |
| `nodes/classify.py`, `receive.py`, `explain_risk.py`, `handle_failure.py` | 수정·유지 | | R2 |
| `nodes/extract_facts.py` | 수정·이름 변경 | `extract_flags.py` | R2 |
| `nodes/score_risk.py` | **분리** | `score_orange.py`, `score_yellow.py` | R2 |
| `nodes/save_and_summarize.py` | 수정 | `save_state.py` | R2 |
| `nodes/create_tasks.py`, `draft_messages.py`, `find_customers.py`, `guardrail_check.py`, `pb_approval.py`, `save_rejection.py` | **삭제** | 연락·승인 폐지 | — |
| — | 신규 | `nodes/mark_red.py`, `judge_stage.py` | R2 |
| `prompts/system.md`, `explain_risk.md`, `select_tools.md` | 수정·유지 | | R2 |
| `prompts/extract_facts.md` | 수정·이름 변경 | `extract_flags.md` | R2 |
| `prompts/draft_message.md`, `rewrite_message.md` | **삭제** | | — |
| `assembly.py` (423줄) | **분리** | 아래 표 참고 | R2·R4 |
| `harness_adapter.py` | 이동 | `adapters/bindings.py` | R4 |
| `mcp_client.py` | 이동 | `adapters/mcp_client.py` | R4 |
| `persistence.py` | 이동·통합 | `adapters/store.py`. `ToolTaskCreator` 삭제 | R4 |

**`agent/assembly.py` 분리 방식 (가장 중요)**
| 지금 있는 것 | 새 위치 | 담당 |
| --- | --- | --- |
| `build_model`, `_openai_chat`, `KnownSignalsModel`, `snap_quote`, `best_chunk` | `agent/model_adapter.py` (`UnsafeDraftInjector`는 안내문이 없어져 삭제) | R2 |
| `prepare_input`, `check_message`, `recover_llm` (+ `_shrink_strings`, `_longest_string`) | `agent/harness_llm.py` | R2 |
| `signal_input`, `make_classifier` | `agent/rules.py` | R2 |
| `recover_tool`, `event`, `default_harness`, `_trace_id`, `_audit_system` | `agent/adapters/harness_tools.py` | R4 |
| `AssessmentStore`, `audit`, `_write_audit` | `agent/adapters/store.py` (`authorize_task`, `_owns`, `_release_after_save`는 삭제) | R4 |
| `enable_langsmith` | `harness/tracing.py` | R4 |
| `build_dependencies` | `agent/adapters/assembly.py` (얇은 조립만) | R4 |

### harness
| 현재 | 변경 | 계획 | 담당 |
| --- | --- | --- | --- |
| `context.py`, `loop_limits.py` | 유지 | | R2 |
| `guardrails.py` | 수정 | 안내문이 아니라 **설명문**의 투자 권유·풍문 단정 표현 검사 | R2 |
| `recovery.py` (103줄) | **분리** | `recovery_llm.py`(LLM 429·503, 대체 모델, 가짜 오류 주입) [R2] / `recovery_tools.py`(도구 TIMEOUT 재시도, 감사 기록) [R4] | R2·R4 |
| `masking.py`, `tracing.py` | 수정·유지 | 고객 이름 마스킹 유지 | R4 |

### api
| 현재 | 변경 | 계획 | 담당 |
| --- | --- | --- | --- |
| `main.py`, `deps.py`, `graph_adapter.py`, `run_manager.py`, `audit.py` | 수정·유지 | 회사 단위 필터 적용 | R4 |
| `auth.py` | 수정 | 사용자에 회사 소속 저장 (SSO 없음) | R4 |
| — | 신규 | `tenancy.py` (로그인한 사용자의 회사로 자동 필터) | R4 |
| `routes/alerts.py` | 재작성·이름 변경 | `routes/states.py` (종목별 위험 상태) | R4 |
| `routes/customers.py` | 수정 | 연락 이력·우선순위 제거, 보유·노출만 | R4 |
| `routes/events.py`, `runs.py` | 유지·수정 | | R4 |
| `routes/approvals.py`, `tasks.py` | **삭제** | | — |
| — | 신규 | `routes/collect.py` (수집 상태, 수동 실행) | R4 |
| `worker/` | 유지 | 큐는 Phase 1에서 끔 | R4 |
| `smoke.py` | 이동 | `scripts/demo/smoke.py` | R5 |

### web (R5)
| 현재 | 변경 | 계획 |
| --- | --- | --- |
| `views/Login.vue`, `CustomerList.vue` | 유지·수정 | 고객 현황은 연락 이력 제거 |
| `views/AlertList.vue` | 이름 변경·수정 | `RiskList.vue` |
| `views/AlertDetail.vue` | 이름 변경·수정 | `RiskDetail.vue` |
| `views/DraftReview.vue`, `TaskList.vue` | **삭제** | |
| `components/AlertSummary.vue`, `LevelBadge.vue` | 이름 변경·수정 | `RiskSummary.vue`, `StageBadge.vue` |
| `components/ScoreBreakdown.vue`, `StatusBadge.vue`, `RunProgress.vue` | 수정 | 새 점수 내역·상태 |
| `router/`, `stores/`, `api/client.js`, `labels.js`, `composables/` | 수정 | 새 경로·단계 이름 |

### 데이터·평가·스크립트
| 현재 | 변경 | 계획 | 담당 |
| --- | --- | --- | --- |
| `rag_docs/companies/` (140개) | **삭제** | 원문은 저장하지 않음 | R3 |
| `rag_docs/reference/*` | 수정 | 위험 항목 해설로 재작성. `message_guide.md` 삭제 | R3 |
| `data/customers.json` | 수정·이동 | `data/seed/` (회사·PB·고객·보유 종목) | R3 |
| `data/demo_companies.json`, `data/disclosures/*.json` | **삭제** | 새 테스트 사례는 `eval/cases/`로 | R3 |
| `scripts/fetch_dart.py`, `build_rag_docs.py`, `compute_base_score.py` | **삭제** | `collector/`로 통합 | R3 |
| `scripts/seed_db.py` | 이동·수정 | `scripts/data/seed_db.py` (회사 시드 추가) | R3 |
| `scripts/run_stack.py`, `doctor.py`, `verify_all.py` | 이동·수정 | `scripts/ops/` | R4 |
| `scripts/replay_demo.py`, `replay_all.py`, `check_scenario_a.py` | 이동·재작성 | `scripts/demo/` (새 시나리오) | R5 |
| `eval/run_eval.py` (295줄) | **분리** | 실행기만 남기고 실험은 `eval/experiments/`로 | R4 |
| `eval/capture_signals.py` | 수정 | | R2 |
| `eval/fixtures/signals.json`, `eval/results/*` | 삭제 | 이전 프로젝트 결과 | — |
| — | 신규 | `eval/cases/` (실제 과거 사례, R3·R5 공동) | R3·R5 |

### tests
| 현재 | 변경 | 계획 | 담당 |
| --- | --- | --- | --- |
| `tests/agent/test_approval.py` | **삭제** | 승인 폐지 | — |
| `tests/agent/*` 나머지 | 수정 | 새 그래프 | R2 |
| `tests/harness/test_harness.py` | **분리** | R2 쪽(문맥·가드레일·LLM 복구)과 R4 쪽(마스킹·도구 복구) | R2·R4 |
| `tests/mcp_servers/test_tools.py` | 이동 | `tests/business/` | R4 |
| `tests/mcp_servers/test_rules.py` | 이동·재작성 | `tests/risk_engine/` | R4 |
| `tests/mcp_servers/test_rag.py`, `test_ephemeral_index.py` | 이동·수정 | `tests/rag/` | R3 |
| `tests/mcp_servers/test_assembly.py` | 이동·수정 | `tests/wiring/` | R4 (R2 협업) |
| `tests/mcp_servers/test_replay_all.py` | 이동 | `tests/demo/` | R5 |
| `tests/api/*` | 수정 | 승인·업무 테스트 삭제, 상태·회사 분리 테스트 추가 | R4 |
| — | 신규 | `tests/collector/`, `tests/risk_engine/` | R3, R4 |

---

## 4. 역할별 담당 (파일 단위)

| 역할 | 맡는 것 |
| --- | --- |
| **R1 PM·PO** | `CLAUDE.md`(공통 규칙), `README.md`(요약), `docs/architecture.md`, `dartch_phase1_mvp.md`, `risk_policy.md`, `.github/CODEOWNERS`. 점수 값·합격 기준 확정, 일정, 발표 |
| **R2 AI 흐름** | `agent/`(R4의 `adapters/` 제외), `harness/{context, guardrails, loop_limits, recovery_llm}.py`, `eval/experiments/{model, guardrail, context}.py`, `eval/capture_signals.py`, `tests/agent/`, `docs/contracts/agent.md` |
| **R3 RAG·데이터** | `collector/`, `mcp_servers/rag/`, `rag_docs/reference/`, `data/seed/`, `scripts/data/`, `shared/models/collect.py`(R4 검토), `eval/experiments/rag_k.py`, `eval/cases/`(R5와 공동), `tests/{collector, rag}/`, `docs/contracts/{mcp_rag, data_sources, collector}.md` |
| **R4 백엔드·도구** | `shared/`(`collect.py` 제외), `migrations/`, `risk_engine/`, `mcp_servers/business/`, `api/`, `agent/adapters/`, `harness/{masking, tracing, recovery_tools}.py`, `docker/`, `docker-compose.yml`, `pyproject.toml`, `.env.example`, `scripts/ops/`, `eval/run_eval.py`·`stage_thresholds.py`, `tests/{business, risk_engine, api, wiring}/`, `docs/contracts/{api, db, mcp_business, risk_rules}.md`, `docs/run.md` |
| **R5 프론트·데모** | `web/`, `scripts/demo/`, `eval/cases/`(R3와 공동), `tests/demo/`, `docs/verification.md`, 시연 영상 |

---

## 5. 공동 파일과 변경 절차
| 파일·계약 | 주담당 | 같이 보는 사람 | 절차 |
| --- | --- | --- | --- |
| `shared/models/*`, DB 계약 | R4 | R3(collect), R2·R5(읽기) | 칸 추가는 이슈로 요청 → R4가 모델·마이그레이션 수정 |
| `risk_engine/rules.yaml` 값 | R4 | R1(값 확정), R3(근거 수치) | R1이 값을 이슈로 제안 → R4 반영 |
| MCP 도구 입출력 | R4(business), R3(rag) | R2 | 계약 문서를 먼저 고치고 양쪽 승인 |
| `agent/state.py`, `ports.py` | R2 | R4 | 형식 변경은 R4와 합의 |
| API 응답 형식 | R4 | R5 | `docs/contracts/api.md`를 먼저 확정, R5는 그 명세로 가짜 데이터 개발 |
| `eval/cases/` | R3·R5 공동 | R1 | 파일 이름에 담당 표시, 사례는 실제 DART 사례만 |
| `pyproject.toml`, `.env.example` | R4 | 모두 | 라이브러리·환경 변수 추가는 R4에게 PR 요청 |

## 6. 의존 방향 (아래가 위를 import하지 않는다)
```
web  ──HTTP──▶  api ──▶ agent.runtime(graph_adapter) ──▶ MCP(business, rag) ──▶ risk_engine, shared
                 └────▶ shared                          collector ──▶ shared
agent ──▶ harness            (agent는 risk_engine·collector·api를 직접 import하지 않는다)
mcp_servers/business ──▶ risk_engine, shared      mcp_servers/rag ──▶ collector.dart_client, shared
collector ──▶ shared 만       risk_engine ──▶ shared 만 (collector를 import하지 않는다)
```
- 수집기와 규칙 엔진은 **DB 표(`financial_snapshots`, `risk_states` 등)** 로만 만난다. 재무 지표 값은 R3가 계산해 넣고, 점수는 R4가 그 값을 읽어 계산한다.

## 7. 진행 순서 (아직 실행하지 않음)
| 단계 | 내용 | 누가 | 끝났다는 기준 |
| --- | --- | --- | --- |
| **0. 시작 위치** | 새 저장소 또는 장기 브랜치 결정 | R1 | 위치 확정 |
| **1. 구조 정리 PR (한 번에)** | 이동·분리·삭제만 하고 기능은 바꾸지 않음. `assembly.py`·`recovery.py`·`models.py`·`run_eval.py` 분리, `risk_engine/` 이동, 연락 기능 삭제, CODEOWNERS 추가 | R4 주도, R2 검토 | 기존 테스트(삭제한 것 제외)가 통과 |
| **2. 계약 확정** | `docs/contracts/*` 작성: 위험 항목 표, 규칙 구조, State, API 응답, DB 스키마 | 각 담당 + R1 승인 | 모든 계약 문서가 합의됨 |
| **3. 병렬 개발** | R3 수집기, R4 규칙·DB·API, R2 그래프·프롬프트, R5 화면(가짜 데이터) | 각자 | 각자 폴더와 테스트 |
| **4. 통합** | 종목 1개로 수집 → 판정 → 화면 | R4 총괄 | 통합 시나리오 통과 |
| **5. 확장·평가** | 🔴🟠🟡 전체, 복합·관찰·해소, 테스트 사례 평가, 영상 | 전원 | 평가표 작성 |

## 8. 확인이 필요한 것
1. 새 저장소 / 장기 브랜치 중 어디에서 시작할지.
2. `risk_engine/` 분리(MCP 서버 밖으로)를 할지. 안 하면 `mcp_servers/business/rules/`에 두고, 담당 표는 그대로 쓴다.
3. 1단계 구조 정리를 한 사람이 한꺼번에 하는 방식으로 동의하는지 (그동안 다른 사람은 대기).
4. `.github/CODEOWNERS`에 쓸 GitHub 아이디.
