# R4 Pre-merge 지원 구현 — 2026-10-23

## 작업 기준

- 시작: `Pre-merge-P01-v01`, 로컬 HEAD `788e07c`.
- 작업: `codex/r4-premerge-integration`. 사용자 요청으로 현재 브랜치에서 새 브랜치를 생성했다.
- 순서: 사실 저장/조회 → 재무/설정 → 판정/검토/작업 모델 → 체크포인터/실행기 → 주입/API → 검증.
- R2 Graph/runtime/prompts, R3 collector/RAG, R5 화면 소스는 직접 수정하지 않았다. 새 진입점과 저장 인계를 해당 담당자가 연결해야 한다. 외부 PR 댓글·메시지를 보내지 않았다.
- 사용자 .env/개발 DB/원격 브랜치를 수정하지 않았다. 이번 변경은 아직 커밋·푸시하지 않았다.

## 요청별 반영

| 요청 | R4 구현 | 연결 상태 |
| --- | --- | --- |
| 1 구조화 사실 | nullable JSON 컬럼, 스키마 v1, 엄격한 값/식별자/시간대 검증, store_facts, get_disclosure_facts 등록 및 MCP 허용 목록 | R3가 jobs/repo에서 store_facts를 호출해야 함 |
| 2 자본잠식 OFS | 최신 기간 OFS만 선택. CFS/과거 OFS 대체 금지. 다른 지표는 CFS 우선 | 실제 수집 지표·출처 연결은 R3와 확인 |
| 3 금융비용 지표 | YAML enabled:false로 경계값 확정 전 자동 가점 중지. 값/정책 보류 기록 유지. 분모<=0 산정 불가 유지 | R1 경계값 확정 후 규칙 갱신 |
| 4 체크포인터 | R4 open_checkpointer에서 SQLite/PostgreSQL 생성/setup/종료. R2에는 saver 객체 주입 | 기존 AgentRuntime 생성자는 객체 주입 가능. 새 public/private 진입점은 R2 필요 |
| 5 DB 실행기 | 원자 선점, lease/token, 갱신, 만료 복구, 늦은 결과 차단, 최초 포함 3회, 수동 확인 자동 반복 제외 | 명시적 R2 executor를 주입해야 실제 분석 실행 가능 |
| 6 조직 분리 | analysis_jobs/public_assessments/organization_reviews, 고정 thread ID, 조직/PB별 조회·결정, 승인 후 기존 contact_tasks 생성 | R2가 새 문맥을 사용하는 executor를 제공해야 함. 기존 UI는 새 API 미연결 |
| 7 의존성/설정 등 | httpx 운영 이동, COLLECT/DART 설정, UPSTREAM_ERROR, 기존 collect_cursors 재사용, purge 전환 계약 | 최신 collector.md는 tenacity/APScheduler 불필요로 명시해 추가하지 않음 |

## 주요 설계와 구현

### 구조화 사실

- `shared/facts.py`: `schema_version="1"`, PRESENT/MISSING/NOT_APPLICABLE/NEEDS_CHECK. `0`·`false`는 PRESENT일 때 정상 값이다. 결측은 null 및 사유를 요구한다.
- 접수번호/기업 코드 일치, 출처 접수번호, 항목별 행 키 중복, dtype, 유한한 숫자, 시간대를 검증한다.
- NULL은 UNAVAILABLE이며 수집 실패/미시도를 추측하지 않는다. 정상 빈 목록이나 위험 없음으로 반환하지 않는다.
- 저장 후 필수 수집 전체가 준비됐을 때 같은 트랜잭션의 `mark_ready`로 인계한다. 목록만 저장된 공시는 분석 작업으로 가져가지 않는다.

### 판정과 실행

- 입력 manifest는 기준일까지의 공시/구조화 사실/재무 버전/과거 신호를 보존하며 원문 전문을 포함하지 않는다. 현재 공시의 파생 signals는 입력 hash에서 제외해 자체 저장이 무한 재등록을 만들지 않게 한다.
- job key는 종목/접수번호/기준일/입력 hash/규칙 digest를 포함한다. 같은 요청은 멱등이다. 명시적 재분석 UUID는 요청 버전으로 포함하고 기존 실패 이력을 덮어쓰지 않는다.
- 공개 판정 저장 전 입력·규칙 일치, lease, 기대 상태 버전을 검사하고 실제 calculate_risk_score를 다시 호출해 판정과 대조한다. executor가 점수를 임의 변경할 수 없다.
- 공개 판정/현재 상태/분석 성공 시각/조직별 검토 준비를 같은 트랜잭션에 저장한다. 실패한 결과는 공개 판정으로 발행하지 않는다.
- MANUAL_CHECK는 processed_at을 성공 시각으로 기록하지 않으며 자동 재시도하지 않는다. 오류는 제한된 코드만 기록하고 외부 예외 전문/URL/원문을 저장하지 않는다.
- 공개 결과는 source_chunks/body_text/고객 초안·결정 등을 제외한다. 조직별 고객 실행은 공개 분석 thread와 분리한다.
- 재무 수치 입력은 DB 스냅샷만 사용하도록 계산 도구를 보완했다. 호출자가 CAPITAL_IMPAIRMENT를 보내 OFS 선택을 우회할 수 없다.
- R3 original_rcept_no는 후보일 수 있으므로 event_id가 확정되지 않은 후보만으로 자동 병합하지 않는다. R4의 신뢰된 confirm_event_link 경계를 추가했다. 후보 탐지·실제 근거 확인을 자동 완성한 것은 아니다.

### 조직별 검토

- 공개 판정 완료와 초안/승인 대기는 별도 상태다. 한 조직의 완료는 다른 조직 상태를 변경하지 않는다.
- 검토 생성/초안 저장/승인에서 최신 공개 판정과 조직/PB 범위를 검사한다. 새 판정이 발행되면 이전 대기 검토를 SUPERSEDED 또는 RESOLVED로 닫고 과거 업무는 보존한다.
- R4 backend는 검증된 초안 저장·결정·업무 생성을 트랜잭션으로 처리한다. R2의 새 executor와 이 backend를 연결하는 최종 Graph 계약은 확인이 필요하다. 기존 R2 승인 interrupt를 새 조직별 실행에 자동 연결했다고 주장하지 않는다.
- 재작성은 PENDING으로 돌리고 이미 결정한 고객 문안과 생성된 업무를 보존한다.
- `/reviews` 응답은 담당 고객 문안만 반환하며 검사 실패 문안을 노출/승인하지 않는다. 기존 `/tasks`가 새 검토 업무도 읽을 수 있도록 기존 contact_tasks에 연결한다.

## 새 파일과 연결 지점

- DB/공통: `shared/facts.py`, `shared/analysis.py`, `shared/events.py`, `shared/models/execution.py`.
- R4 backend: `mcp_servers/business/publication.py`, `mcp_servers/business/reviews.py`, `tools/get_disclosure_facts.py`.
- 실행: `api/worker/analysis.py`, `api/review_executor.py`, `agent/adapters/executors.py`, `scripts/ops/run_analysis_worker.py`.
- API: `api/routes/execution.py`. 상세 경로와 입력은 contracts/api.md의 Pre-merge 절.
- 추가 마이그레이션: `20261023_03`, `20261023_04`. 01/02는 변경하지 않았다. 04 스키마는 현 ORM을 참조하지 않는 고정 정의다.
- Docker: integration collector를 실제 `--schedule` 진입점으로 변경하고 API 마이그레이션 완료 후 시작한다. Compose 설정 검증만 수행했다.

## R2/R3에 전달할 정확한 인계

### R3

1. 공시·필요 스냅샷 저장 트랜잭션에서 `store_facts(session,rcept_no,payload)` 호출.
2. 필수 수집 완료 후 `mark_ready(session,rcept_no)` 호출. API 행 부재는 MISSING, 호출 실패는 NULL/수집 오류로 유지한다.
3. R4의 사실 스키마와 R3 생성 값 fixture를 맞춘다. 실제 API 근거와 문서 인용을 서로 위장하지 않는다.
4. 원문 해시/정규화 규약·동시 색인 사용·새 판정의 일일 재등록을 연결한다.

### R2

`factory(deps,initialized_checkpointer)`가 다음 비동기 메서드를 제공해야 한다.

- `analyze_public(AnalysisContext)` → `{status,score_result,ai_explanation}`. 공개 분석만 실행하고 자체 저장/고객 작업을 하지 않는다. 신뢰된 기준일·입력/규칙 버전·thread ID를 사용하고 실제 계산 도구의 signals/CAS 메타데이터를 보존한다.
- `draft_review(ReviewContext)` → `{drafts}`. 서버 tenant/판정/고객 목록·기존 결정·재작성 대상만 사용한다. 공개 분석 재실행/임의 업무 생성은 하지 않는다.

현재 AgentRuntime.start(rcept_no)는 위 계약을 충족하지 않는다. 잘못된 fallback 대신 설정 미지정 또는 503으로 남겼다. `PHASE1_RUNTIME_FACTORY` 설정 시 R4가 체크포인터와 실행기를 연결한다. R2의 StageResult/Phase1Bindings·문서/API 근거 DTO·고객 실행 문맥 수정 및 실제 도구 연결은 여전히 담당자 작업이다.

## 원문 수명/구형 경로

- 새 공개 발행은 purge를 호출하지 않고 공개 분석 성공 시각을 기록한다.
- 기존 purge/release 호출은 구형 Graph/테스트 때문에 보존했다. 기존 호출자를 삭제하지 않은 상태에서 도구만 제거하지 않았다.
- 새 실행기 테스트는 원문 전문/불필요 조각 미저장을 확인한다. R2 State/trace 및 R3 메모리 색인 전체 수명의 완료 검증은 별도다.
- 기존 /runs/SSE/구형 승인과 신규 /analysis/jobs·/reviews는 별도 경로다. R5 화면 전환과 전체 서비스 완료를 뜻하지 않는다.
- 기존 일일 refresh_states는 호환 경로다. 새 불변 판정에는 새 기준일의 작업 등록이 필요하며 스케줄 인계는 아직 연결하지 않았다.

## 검증 결과

- 변경 전: **459 passed, 4 skipped**.
- 최종 전체 실행기: **480 passed, 6 skipped**, Python compileall 통과.
- 마지막 만료 작업 처리/판정 변조 방지 보완 이후 `tests/business/test_premerge_execution.py`: **11 passed**.
- 임시 PostgreSQL: 기존 3개 + 새 2개(동시 선점·만료 token / 실제 PostgreSQL 체크포인터 재시작 복원) 통과. 실행은 4 passed 및 2 passed이며 선점 테스트 1개가 중복 포함된다. 독립 스키마만 생성·정리했고 임시 서버는 종료했다.
- SQLite: 기존 사람 데이터 보존, 02→head 업그레이드 반복 적용, JSON 컬럼·새 테이블 검증 통과.
- 주입 테스트: 공개 작업 완료와 조직별 초안 대기 분리, 타 조직 접근 차단, 담당 PB 승인/중복 업무 방지, 오래된 판정 차단, 미연결 503, 구형 runtime fallback 거부를 확인했다.
- `docker compose config --quiet`, `git diff --check` 통과. Docker 이미지 빌드/전체 서비스 기동은 수행하지 않았다.
- 실제 DART 호출·유료 모델 호출·실제 5개 회사 새 정책 평가는 수행하지 않았다. 내부 fixture는 새 공시 사실을 주장하지 않는다.

## 현재 판정

**R4 저장·도구·실행·검토 backend는 구현했으나 #31 전체 통합 완료/머지 준비 완료는 아니다.** R3 저장 인계, R2 새 진입점과 실제 근거 계약, R5 화면 연결을 마친 뒤 종목 1개의 전체 흐름을 검증해야 한다. 사용자 결정으로 제외한 항목 간 중복 합산·보유/PB 변경 대응·재발 전용 감지는 추가하지 않았다.
