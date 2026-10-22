# CLAUDE.md — DARTCH (임시 이름) 팀 약속

이 파일은 팀원 5명이 각자 Claude Code로 작업할 때 **모두가 따르는 약속**이다. 길게 쓰지 않는다.
담당이 다른 코드끼리 만나는 곳의 **상세 계약은 `docs/contracts/`**에 있고, 바꾸려면 그 문서를 먼저 고친다.

---

## 0. 한 줄 소개

증권사 PB가 고객 보유 종목의 위험을 공시로 판단하던 일(노가다)을 AI가 대신하고, 경보와 그 **근거**를 웹 화면으로 알려 주는 B2B2E 서비스.
(이전 프로젝트 "공시 조기경보 에이전트"를 DARTCH Phase 1 MVP로 바꾸는 중이다.)

- **점수·단계는 규칙(코드)이 계산한다. AI는 공시에서 사실을 뽑고 근거를 인용해 설명만 한다. AI는 점수를 바꿀 수 없다.**
- 근거를 못 찾으면 추측하지 않고 "수동 확인 필요"로 남긴다.
- 손실 방지가 목적이다. 주가 상승 가능성은 보지 않는다.
- **고객에게 연락하는 기능(안내문 초안, PB 승인, 연락 업무)은 없다.** 이미 삭제했고, 다시 만들지 않는다.
- 모델에 고객 실명을 보내지 않는다. 설명문에 매수·매도 권유를 넣지 않는다.

## 1. 문서 지도

| 문서 | 내용 |
| --- | --- |
| [docs/dartch_phase1_mvp.md](docs/dartch_phase1_mvp.md) | **Phase 1 MVP 설계서** — 무엇을 만드는가 (STEP 01~06, 위험 항목, 판정 규칙, 결정 사항) |
| [docs/dartch_file_plan.md](docs/dartch_file_plan.md) | 파일 구성 계획 — 폴더와 파일의 담당 |
| [docs/contracts/](docs/contracts/) | 계약: `db.md` `api.md` `mcp_business.md` `mcp_rag.md` `agent.md` `risk_rules.md` `harness.md` `data_sources.md` (이전 프로젝트 계약을 옮긴 것이며, DARTCH에서 바뀌는 곳은 각 문서 머리에 적혀 있다) |
| [docs/architecture.md](docs/architecture.md) | 아키텍처와 품질 목표 (이전 프로젝트 기준, R1이 갱신 예정) |
| [docs/run.md](docs/run.md) | 실행 방법·환경 변수 (R4) |
| [docs/verification.md](docs/verification.md) | 검증 결과·시연 절차 (R5) |
| [docs/legacy/](docs/legacy/) | 이전 프로젝트 기록 (참고용) |

## 2. 역할과 담당

| 역할 | 담당자 | 맡는 곳 |
| --- | --- | --- |
| R1 PM·PO | 양시온 | `CLAUDE.md`, `README.md`, `docs/`(설계서·정책·아키텍처), `.github/` |
| R2 AI 흐름 | 윤찬웅 | `agent/`(`adapters/` 제외), `harness/`의 context·guardrails·loop_limits·recovery_llm, AI 평가 실험, `tests/agent/` |
| R3 RAG·데이터 | 한예빈 | `collector/`, `mcp_servers/rag/`, `rag_docs/`, `data/`, `scripts/data/`, `shared/models/collect.py`, `tests/{collector,rag}/` |
| R4 백엔드·도구 | 양경환 | `shared/`, `migrations/`, `risk_engine/`, `mcp_servers/business/`, `api/`, `agent/adapters/`, `harness/`의 masking·tracing·recovery_tools, `docker/`, 의존성(`pyproject.toml`·`.env.example`), `scripts/ops/`, `eval/run_eval.py`, `tests/{business,risk_engine,api,wiring}/` |
| R5 프론트·데모 | 박세호 | `web/`, `scripts/demo/`, `tests/demo/`, `docs/verification.md` |

- **파일 단위 담당은 `.github/CODEOWNERS`가 정본이다.** 위 표와 다르면 CODEOWNERS를 따르고, 둘을 함께 고친다.
- 공동 폴더: `eval/cases/`(R3·R5) — 파일 이름에 담당(`r3_`, `r5_`)을 붙여 서로의 파일을 고치지 않는다.

## 3. 폴더 구조

```
shared/        config·db·schemas, models/(base core tenant risk collect)        [R4]
risk_engine/   규칙 엔진(rules.yaml, 판정). MCP 없이 쓰는 라이브러리              [R4]
collector/     DART 수집기 (뼈대만 있음)                                         [R3]
mcp_servers/   business/(R4)  rag/(R3)
agent/         graph·state·runtime·nodes·prompts·model_adapter·harness_llm·rules [R2], adapters/ [R4]
harness/       context·guardrails·loop_limits·recovery_llm [R2], masking·tracing·recovery_tools [R4]
api/           FastAPI, routes/, worker/                                         [R4]
web/           Vue 3                                                             [R5]
eval/          run_eval.py(R4), experiments/(파일별 담당), cases/, capture_signals.py(R2)
scripts/       ops/(R4)  data/(R3)  demo/(R5)
tests/         소스와 같은 모양, 같은 담당
```

## 4. 공동 작업 규칙

1. **다른 사람의 파일은 고치지 않는다.** 필요하면 담당자에게 이슈나 PR 코멘트로 요청한다. 공동 파일은 수정할 함수·클래스와 목적을 PR 설명에 적는다.
2. **계약이 먼저다.** DB·API·MCP 도구·State·규칙 구조·수집 데이터를 바꾸려면 `docs/contracts/`의 해당 문서를 먼저 고치고 상대 담당의 승인을 받는다.
3. **의존 방향**: 아래가 위를 import하지 않는다.
   - `web` → (HTTP) `api` → `agent.runtime` → (MCP) `mcp_servers` → `risk_engine`, `shared`
   - `agent`는 `risk_engine`·`collector`·`api`를 직접 import하지 않는다. `collector`와 `risk_engine`은 `shared`만 import한다.
   - 수집기와 규칙 엔진은 **DB 표로만** 만난다 (재무 지표는 R3가 계산해 넣고, 점수는 R4가 그 값을 읽어 계산한다).
4. **점수 숫자는 `risk_engine/rules.yaml` 한 곳에만 둔다.** 코드에 직접 쓰지 않는다. 값은 R1이 확정(`docs/risk_policy.md`)하고 R4가 반영한다.
5. **공시 내용·접수번호·숫자를 지어내지 않는다.** 테스트 사례는 실제 DART 과거 사례만 쓴다. 모르면 `TODO(확인 필요)`로 남긴다.
6. **브랜치와 PR**: `feat/r<번호>-<기능>` (예: `feat/r3-collector`). main에는 PR로 합치고, 해당 파일의 CODEOWNERS가 리뷰한다. 다른 PR과 같은 파일을 고치게 되면 먼저 알린다.
7. **비밀 키는 `.env`에만.** 커밋 전에 `.env`가 빠졌는지 확인한다. 라이브러리·환경 변수 추가는 R4에게 요청한다.
8. **테스트는 구현과 함께.** `uv run pytest -q`가 통과해야 PR을 합친다. 새 기능을 넣기 전에 기존 테스트가 깨지지 않는지 먼저 확인한다.
9. **구조를 바꾸는 일(이동·분리·삭제)은 기능 개발과 섞지 않는다.** 별도 PR로 한 번에 한 사람이 한다.

## 5. 지금 상태 (2026-10-22 구조 정리 직후)

- 구조 정리 완료: 규칙 엔진 `risk_engine/`, `shared/models/` 패키지, `assembly.py`·`recovery.py`·`run_eval.py` 분리, 스크립트·테스트 재배치, 문서 계약 분리.
- **고객 연락 기능 삭제 완료**: 안내문·승인·연락 업무 노드·도구·API·화면, `ContactTask` 표 모델, 영향도(P1~P3) 규칙. 공시 1건은 분석 → 설명 → 저장 → `done`으로 끝난다.
- **아직 이전 프로젝트 그대로인 것(이후 개발에서 바꾼다)**: 위험 규칙(A·B그룹, Low~Critical)은 🔴/🟠/🟡로, 데모 데이터(`data/demo_companies.json`, `data/disclosures/`, `rag_docs/companies/`)는 수집기와 테스트 사례(`eval/cases/`)로, RAG 색인은 메모리 임시 색인으로, 체크포인트·DB는 PostgreSQL로.
- **돌지 않는 옛 스크립트**: `scripts/ops/verify_all.py`, `scripts/demo/check_scenario_a.py`, `scripts/demo/smoke.py`는 연락·승인 시나리오 기준이라 다시 써야 한다 (파일 머리에 표시했다).
