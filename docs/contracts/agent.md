# Agent 계약 — R2 구현·통합 기준

> 전체 통합(2026-10-24): UNAVAILABLE 사실은 pinned facts_version이 null일 때 원문 분석으로 이어진다. numeric_facts는 원문에 직접 기재된 백분율과 검증 인용만 허용하며 모델 계산은 금지한다. financial 근거는 스냅샷 식별자·실제 값·source_hash를 verify_financial_evidence로 대조한다. 과거 API 근거는 각 접수번호의 사실을 따로 검증한다. 공개 판정과 조직별 초안·승인은 hosted runtime/DB 경로를 사용한다.

> 재무 결측 보완: R4가 재무 스냅샷 항목의 결측만 남았다고 확인하면 검증된 위험은 공개하고 manual_check·결측 안내를 유지한다. 감사의견은 financial 스냅샷 근거일 때만 이 예외에 포함한다. 불확실한 원문 조건·정정 연결은 계속 중단한다. 성공 processed_at은 쓰지 않고 작업은 MANUAL_CHECK로 유지한다. 수치 전사만 요청된 경우에도 추출 검증 완료 여부로 최대 3회 재시도를 판단한다.

작성일: 2026-10-23 · 담당: R2 윤찬웅 · 검토: R3·R4

> R2 측에서 채택한 구현 기준이다. 타 역할에 해당하는 변경은 담당자가 검토·반영한다. 문서 작성은 코드 구현·통합 검증 완료를 뜻하지 않는다. 기존 CLAUDE.md·업무 카드·R1 정책을 유지한다.

## 1. 목적과 범위

공시에서 위험 조건을 추출하고, 코드가 계산한 위험 단계와 검증된 근거를 설명해 PB의 판단을 지원한다. 내부 점수는 계산·정렬에 사용하지만 PB용 응답과 설명·안내문 모델에는 전달하지 않는다.

- RED / ORANGE / YELLOW / COMPOSITE: 설명·저장 후 담당 고객 전원에 대한 초안·검사·PB 승인·연락 업무로 연결한다.
- WATCH / NONE: 설명·저장 후 종료한다. WATCH는 새 위험 경보로 발송하지 않는다.
- MANUAL_CHECK: 위험 단계가 아니라 분석 처리 상태다. 정보 부족을 NONE이나 0점으로 바꾸지 않는다.
- 직접 발송, 매수·매도 권유, 고객 영향도 P1~P3, 뉴스·SNS 수집은 이번 범위에 포함하지 않는다.

## 2. 책임과 의존 방향

| 담당 | 책임 |
| --- | --- |
| R3 | DART 원천 사실, 재무 지표 계산, 출처·버전, 원문 검색·메모리 색인 |
| R2 | Graph, 텍스트 조건 추출, AI 출력·인용 검증, 설명·안내문, 승인 중단·재개 |
| R4 adapter | MCP 연결, 합의된 사실·조건의 계산 입력 변환, 저장, 실행 context·체크포인터 주입 |
| R4 규칙 엔진 | 점수·임계값·최종 단계·사건 상태 계산 |

agent는 risk_engine·collector·api를 직접 import하지 않는다. R4가 카탈로그·읽기 도구·계산·저장 인터페이스를 주입한다. 체크포인터 생성과 종료는 R4가 담당하고 R2 런타임은 주입받은 객체를 사용한다. R2는 agent/adapters/·shared/·api/·의존성 파일을 수정하지 않는다.

## 3. 처리 흐름과 계산 도구

1. 접수번호와 신뢰된 실행 context를 받는다.
2. 공시 메타데이터·구조화 사실·유효 재무 지표를 조회한다.
3. 카탈로그에 따라 관련 항목을 분류하고 필요한 원문만 검색한다.
4. AI가 허용된 텍스트 조건을 추출하고 R2가 근거를 검증한다.
5. R4 adapter가 입력을 변환해 calculate_risk_score를 호출한다.
6. 검증된 계산 결과로 설명을 생성·검사하고 공개 분석 결과를 저장한다.
7. 경보이면 조직별 고객 조회·초안·검사·PB 승인·연락 업무를 진행한다.

**외부 계산 도구는 calculate_risk_score를 유지한다.** 기존 R2의 mark_red / score_orange / score_yellow / judge_stage 이름은 내부 노드·레거시 연결 이름일 수 있으며, 각각 새 MCP 도구를 만들라는 요구가 아니다. R2 Phase1Bindings와 결과 DTO는 실제 R4 도구 계약에 맞게 수정한다.

- AI 출력에는 점수·최종 단계·임의 event_id를 받지 않는다.
- R3가 계산한 재무 지표를 AI가 재계산하거나 누락을 추정하지 않는다.
- R4의 expected_state_version·corp_code·as_of 등 신뢰된 제어 메타데이터를 DTO 변환 중 버리지 않는다. 모델이 생성한 값으로 대체하지 않는다.
- 도구의 공통 ok/data/error 형식을 유지한다. 상세 입력·응답의 정본은 mcp_business.md다.
- 주입 미완료로 기존 Graph가 선택된 경우 새 Graph 검증 완료로 기록하지 않는다.

## 4. 구조화 사실과 충돌

별도 disclosure_facts 테이블 없이 disclosures.structured_facts JSON을 사용한다. 조회는 R4의 get_disclosure_facts(rcept_no)를 통한다. 도구·컬럼은 구현되어 있다. R2는 ToolResponse.data의 availability/rcept_no/corp_code/structured_facts/facts_hash를 받아 structured_facts.facts를 검증한다. v1 dtype은 string/number/integer/boolean이며 number를 decimal로 바꾸지 않는다. 값·단위·상태·출처를 보존한다. 정식 버전 키 facts_hash는 정규화된 저장 payload 전체(시각 포함)의 해시다. R2가 해당 payload의 해시를 확인한다. 원문 content_hash·전체 입력 input_version과 혼용하지 않는다. 최상위 facts/data_version 등은 호환용이며 R2는 사용하지 않는다. 호환 facts도 number를 유지한다. UNAVAILABLE은 structured_facts/facts_hash=null이며 정상 빈 목록과 구분한다.

| 상태 | 의미 | 처리 |
| --- | --- | --- |
| PRESENT | 정제·검증된 값 존재 | 근거·단위·기간 확인 후 사용 |
| MISSING | 조회 성공, 필요한 행·값 없음 | 필요한 경우 원문 검색으로 보완 |
| NOT_APPLICABLE | 해당 항목이 적용되지 않음 | 제외 사유 보존, 0으로 변환 금지 |
| NEEDS_CHECK | 정제 실패·모순·식별 불확실 | 해당 조건을 확정 계산에 사용하지 않음 |

수집 실패는 structured_facts=NULL과 collect_runs.error로 구분한다. NULL만 보고 정상 빈 결과·위험 없음으로 판단하지 않는다. 재무 지표의 기존 OK/MISSING 어휘는 별도 계약이며 이 상태와 혼용하지 않는다.

구조화 값에는 스키마 버전, 고정 항목 코드, 원천 API·필드·행 식별자, 값·단위·기간·재무 기준, 수집 시각·자료 버전을 포함한다. 세부 JSON 키는 collector.md와 R4 공통 스키마를 따른다.

API 값과 AI 추출값이 다르면 동일 사건·기간·단위·정정 버전인지 먼저 확인한다. 실제 충돌은 양쪽 값·출처를 기록하고 NEEDS_CHECK로 둔다. 다른 독립 항목은 계속 처리하되, 누락·충돌이 결론을 바꿀 수 있거나 필수 근거이면 판정을 수동 확인으로 남긴다.

## 5. 검색과 근거 검증

- 검색 후보 수와 모델 입력 수를 구분한다. 모델 입력 한 번에 전달하는 공시 조각은 모든 검색 호출을 합쳐 최대 5개다. 참고 문서도 전체 토큰 예산 안에서 전달한다.
- 같은 공시·자료 버전·위치의 중복 조각은 제거한다. 부족하면 무관한 문서로 수량을 채우지 않는다.
- 문서 근거: source, rcept_no, location, quote와 원문 content_sha256·정규화 버전을 연결한다. 인용문은 해당 정규화 원문의 검색 조각과 일치해야 한다.
- API 근거: API·필드·행 식별자·실제 값·단위·기간·OFS/CFS·데이터 버전으로 검증한다. API 값을 가짜 문서 인용으로 만들지 않는다.
- R2가 검증된 근거 묶음을 만들고 R4는 계산·저장 경계에서 필수 정보와 버전 일치를 확인한다. 단순히 AI가 verified=true라고 낸 값은 신뢰하지 않는다.
- 과거 근거 재검색은 R2/R4 중 한 경로에서만 수행한다. R4 EvidenceTools를 걷어낼 때 검증 없이 우회하지 말고 동등한 검사와 테스트를 먼저 연결한다.
- 과거 평가는 기준 시점까지 공개된 자료·정정본만 사용한다. 공개 시점을 확인할 수 없는 이후 조회 값은 과거 평가 근거로 사용하지 않는다.
- 해시·문자열 일치는 문서 식별·인용 일치 검사이며 재무적 해석의 타당성까지 보장하지 않는다.

## 6. 사건·정정·후속과 초안

R3가 원본 후보와 연결 근거를 제공하고 R4가 event_id를 확정한다. 기업이 같거나 제목·날짜가 비슷하다는 이유만으로 자동 연결하지 않는다. 후보가 없거나 여러 개이면 원본 관계를 확정하지 않는다.

R1 정책에 따라 신규 위험 사건만 새 RED로 취급한다. 후속은 기존 사건 타임라인을 갱신하며 금액·상태·해소 변화는 반영한다. 정정은 자동 해소가 아니다. 확인된 철회 등 해소 근거를 규칙에 반영한다.

판정·근거가 바뀌면 이전 승인 대기 초안을 차단한다. 위험이 유지되면 새 판정 기준으로 재작성·재승인하고, 해소되면 해당 승인 대기를 종료한다. 과거 승인·반려·연락 업무 이력은 삭제하지 않는다. 이미 생성된 미완료 업무의 변경은 R4 업무 정책으로 명시적으로 처리하고 조용히 삭제하지 않는다.

## 7. 공개 분석·조직별 실행·버전

tenant는 공시 발행 기업이 아닌 서비스 이용 증권사 등 조직이다.

| 식별자 | 의미 |
| --- | --- |
| assessment_id / 판정 버전 | 종목, 입력 공시·정정본·재무 버전, 규칙 버전, 평가 기준 시점으로 식별되는 판정 |
| 실행 ID / 시도 횟수 | 동일 판정 작업의 실행과 실패 재시도 |
| 조직별 검토 ID | tenant_id와 판정 ID에 연결된 고객·초안·승인 흐름 |
| 알림 버전 | 실제 위험 변화로 PB에게 새로 알릴 때의 버전 |

이 이름은 논리적 의미다. 실제 DB/API 필드는 R4 계약으로 매핑한다. 판정 버전과 알림 버전을 같은 것으로 가정하지 않는다.

- 공시·구조화 사실과 공개 위험 분석은 조직 간 공유한다. 고객·보유 정보·초안·결정·연락 업무는 조직별 접근을 적용한다.
- 공개 분석이 완료돼도 다른 조직의 고객 업무까지 완료된 것으로 보지 않는다.
- 동일 판정의 일시 실패는 같은 작업에 재연결한다. 새 입력·규칙·시간 감쇠 평가이면 새 판정으로 구분한다.
- 공개 분석 thread와 조직별 검토 thread를 분리하고, 후자는 신뢰된 tenant와 판정 ID를 포함한다. 접수번호 단독 thread_id를 운영 기준으로 쓰지 않는다.
- tenant·PB·판정 ID·허용 고객은 인증·DB·dispatcher에서 받는다. 사용자 입력이나 모델 출력으로 정하지 않는다.
- 승인 시 tenant·담당 고객·현재 판정 버전을 R4에서 재검사한다.

## 8. 실행·완료·재시도 연결

Phase 1은 DB 미처리 작업을 R4 실행 관리가 원자적으로 선점하고 R2 Graph를 실행한다. R3 수집기가 agent를 직접 호출하지 않는다. 수집 커서·수집 lease는 분석 잠금과 다르다.

논리적 상태는 PENDING → RUNNING → DONE / RETRY_WAIT / MANUAL_CHECK / FAILED다. 실제 열거형은 R4 DB 계약에 매핑하며 승인 대기 상태와 구분한다.

- 공개 분석 DONE은 판정·설명·재현 입력·필수 근거가 저장된 시점이다. 고객 초안 실패가 이미 성공한 공개 분석을 실패로 되돌리지 않는다.
- processed_at은 공개 분석 성공 완료 시각이다. MANUAL_CHECK에는 성공 시각을 새로 찍지 않고 별도 종료 상태·시각을 남긴다.
- 수동 확인은 자동 미처리 조회에서 제외한다. 명시적 재분석 요청으로만 다시 처리한다.
- 작업 전체 최대 3회(최초 포함), 일시 오류만 재시도한다. 세부 예산·복구는 harness.md를 따른다.
- 원자적 선점·잠금·만료·갱신·늦은 결과 차단·중복 저장은 R4 책임이다.

## 9. 근거·원문 수명과 공개 출력

정규화·해시는 collector.dart_client.normalize_document 한 곳을 사용하고 R2는 직접 다른 해시를 만들지 않는다. 원문 해시와 구조화 입력 해시는 서로 다른 대상이다.

검색 원문은 모델 호출 구간의 일시 데이터로 처리한다. Graph 영속 State에는 원문 전문·불필요한 조각·전체 도구 응답을 넣지 않는다. 검증에 필요한 최소 인용·출처·해시·구조화 사실·판정 참조만 보존한다. 점수는 내부 보존하되 불필요한 원문이 score_result의 source_chunks에 포함되지 않게 투영한다.

마지막 원문 검증과 결과 저장 뒤 색인을 해제한다. 설명 재작성·승인 재개는 저장된 근거를 사용한다. 부족하면 원문을 재확보하는 명시적 재분석으로 전환한다. 해시가 달라지면 과거 체크포인트에 새 원문을 조용히 혼합하지 않는다.

설명·안내문 모델에는 public_risk_context의 허용 필드만 전달한다. get_state/start/resume과 원시 interrupt는 서버 내부용이며, PB용 get_review/API/SSE는 점수 없는 투영과 tenant·PB 필터를 적용한다.

## 10. 구현 상태와 통합 완료 조건

이 문서는 목표 규약이다. 기존 R2 코드의 Phase1Bindings 분리 계산 인터페이스, 접수번호 단독 thread, 최대 8개 검색 조각, 원문 조각이 포함된 State는 변경·검증 대상이다. R4의 새 Graph 주입·실제 도구·조직 context 연결도 완료 여부를 확인해야 한다.

- [ ] 실제 calculate_risk_score를 사용하는 새 Graph가 선택된다.
- [ ] 정상·RED·조건부·정정·결측 사례에서 계산 입력과 결과가 검증된다.
- [ ] 실패 재시도·재시작·승인 재개가 중복 결과나 업무를 만들지 않는다.
- [ ] 조직 간 조회·승인·체크포인트가 섞이지 않는다.
- [ ] 모델 입력·PB API·SSE·체크포인트·trace에서 비공개 경계를 검사한다.
- [ ] 같은 실제 사례로 개선 전후를 측정한다. 오프라인 테스트와 실제 모델 평가를 구분한다.

상세: [Harness](harness.md), [수집기](collector.md), [RAG](mcp_rag.md), [업무 도구](mcp_business.md), [DB](db.md), [API](api.md), [위험 규칙](risk_rules.md).

## 11. R2 코드 반영 상태 (2026-10-23)

이 절은 이번 R2 변경의 실제 연결 API를 설명한다. 앞 절의 팀 목표와 R4 미구현 연결을 구분한다.

### 새 경로 활성화

- `Dependencies.unified: UnifiedBindings | None`를 추가했다. `unified`가 있으면 단일 계산 도구 Graph를 선택한다.
- 기존 `phase1: Phase1Bindings`와 legacy Graph는 호환용이다. 이를 새 단일 도구 통합 완료로 보지 않는다.
- `UnifiedBindings`는 `classify(title) -> list[RiskRoute]`, `prepare_signals(payload) -> list[dict]`, `await ensure_current(payload) -> bool`을 R4에서 주입받는다.
- `classify`의 route는 `{bucket, item, text_flags}`다. `text_flags`는 승인된 비수치 조건만 지정한다. RED·YELLOW에서도 명시된 텍스트 조건을 추출할 수 있다. R2가 분류 정책이나 점수를 새로 계산하지 않는다.
- `prepare_signals` 입력은 `disclosure/structured_facts/flags/evidence/routes/as_of`다. R4가 API 필드 매핑·단위·정정 관계·API/AI 충돌 처리를 수행한다. 미해결 필수 충돌은 `AgentError`로 중단하거나 규칙의 수동 확인으로 전달한다. 확인 필요 값을 버린 뒤 NONE으로 반환하지 않는다.
- `ensure_current`는 `context/assessment_id/rcept_no/customer_ids`를 받는다. DB의 최신 판정·tenant·고객 소유권을 확인하고 명시적인 bool을 반환한다. 승인·업무 생성의 원자적 최종 검사는 R4 저장 도구에서도 필요하다.

R4 조립에서 기존 `Phase1Model`·`EvidenceTools` 중복 검증을 연결 정리한 뒤 다음 방식으로 주입한다. 아래 `approved_routes`, `convert_signals`, `check_current`, `record_budget`은 R4가 실제 함수로 연결해야 하는 자리이며 이 이름의 구현을 R2가 만들었다는 뜻이 아니다.

```python
from dataclasses import replace
from agent.integration import UnifiedBindings
from agent.runtime import AgentRuntime

bindings = UnifiedBindings(
    classify=approved_routes,
    prepare_signals=convert_signals,
    ensure_current=check_current,
    record_budget=record_budget,
)
deps = replace(deps, phase1=None, unified=bindings)
runtime = AgentRuntime(deps, checkpointer)  # 생성·종료는 R4 소유
```

### 실행 API

`ExecutionContext`는 R4 인증·dispatcher 내부에서 생성한다. 공개 HTTP 요청 DTO로 그대로 노출하지 않는다.

| 필드 | 의미 |
| --- | --- |
| `assessment_key` | R4가 확정한 공개 판정 ID/버전 |
| `as_of` | 평가 기준일 YYYY-MM-DD |
| `rules_version` | 계산 응답의 rules_digest와 일치하는 규칙 해시 |
| `input_version` | 공시·정정·재무·이력까지 포함하는 분석 입력 manifest의 R4 버전 |
| `facts_version` | 작업 input_manifest에 고정된 이번 공시 structured_facts의 해시. 조회 facts_hash와 비교한다. R4 신규 실행은 명시적으로 전달하며 전체 input_version·원문 content_hash로 대체하지 않는다. NULL은 사실 미수집이다. R2는 유효한 facts_version 누락 시 중단한다. |
| `run_id`, `attempt` | R4 선점된 작업과 최초 포함 1~3 시도 |
| `kind`, `tenant_id` | public 또는 review. review는 이용 조직 필수 |
| `model_requests_used`, `active_seconds_used` | R4가 영속 예산 장부에서 복원한 누적 사용량 |

- `await runtime.start(rcept_no, context=public_context)`는 공개 분석만 수행해 DONE으로 저장한다.
- R4 worker가 주는 AnalysisContext의 `execution_context`가 위 public_context 매핑이다. R2 factory는 `runtime.start(context.rcept_no, context=context.execution_context)`로 연결한다. 이미 고정된 facts_version을 최신 DB 버전으로 바꾸면 안 된다. 전체 입력·사실 버전 차이를 검증하는 경로는 R4 wiring 테스트로 확인한다. 실제 factory의 운영 연결은 별도 통합 사항이다.
- `await runtime.start_review(rcept_no, completed_public_state, context=review_context)`는 같은 판정·자료 버전의 경보에 대해 조직별 고객 검토를 시작한다. 공개 분석 State는 R4 내부 저장소에서 읽은 값이어야 한다.
- `get_state/get_review/resume/retry_tasks`에도 같은 `context=`를 전달한다. 공개 분석과 조직별 검토의 thread는 분리된다.
- 같은 판정의 일시 실패는 `error_retryable=True, status=FAILED`로 반환한다. 다음 `attempt`로 다시 `start`하면 소비 예산을 유지하고 재실행한다. MANUAL_CHECK는 자동 재시작하지 않는다.
- `open_runtime(deps, checkpointer=saver)`는 주입된 saver를 사용하고 닫지 않는다. 새 unified 경로는 R2가 DB를 선택·생성하지 않는다. 기존 경로의 로컬 SQLite/Postgres 팩토리만 호환용으로 남아 있다.
- `analyze_receipts(..., contexts={rcept_no: context})`도 지원한다. 이 함수가 DB 선점을 대신하지 않는다.

### 내부 보존과 출처

- 새 계산 응답은 R4의 `composite_ratio/manual_check/expected_state_version/corp_code/as_of/rules_digest`를 검증·보존한다. 옛 R2 DTO의 `combined_ratio/needs_manual_check`와 억지로 혼합하지 않는다.
- `input_manifest`는 실제 전달한 신규 신호·이번 구조화 사실·원문 출처 버전을 보존한다. **계산 도구가 DB에서 읽은 재무 스냅샷·과거 신호의 실제 입력 기록은 R4 저장 경계에서 추가 보존해야 한다.** R2만으로 전체 재무 판정 재현이 완성된 것은 아니다.
- API 근거는 `{kind:api, api, field, rcept_no, row_key, value, unit, period, basis, data_version}`를 사용하고 문서 quote/location을 만들지 않는다. 현재 R4 엔진의 API 근거 전달·저장은 별도 연결이 필요하다.
- `source_chunks/chunks/text/body_text/raw_response/messages` 등 일시 본문은 영속 State에 넣기 전에 제거한다. 원문 해시와 정규화 버전이 없는 새 검색 결과는 확인 필요로 남긴다.

### R4·R3 후속 연결 (R2 외부 담당)

1. R4 `structured_facts` 컬럼·조회 MCP·공통 스키마는 구현됐다. 조회는 중첩 structured_facts와 facts_hash를 제공하며 number를 그대로 유지한다. R2 validator/receive가 해시 및 context.facts_version을 검증한다. 실제 운영 factory 연결과 UNAVAILABLE의 원문 분석 분기는 별도 통합 확인이 필요하다.
2. R4 조립에서 새 bindings·카탈로그·모델·체크포인터를 주입한다. 현재 adapter가 자동으로 바뀐 것은 아니다.
3. R4 dispatcher가 공개 분석 후 각 이용 조직의 review를 실행하고, 선점·lease·재시도·예산 장부를 관리한다.
4. R4가 판정 최신성·업무 생성의 tenant·멱등성을 DB 트랜잭션으로 보장한다.
5. R3가 검색 응답에 content_sha256·normalization_version을 제공한다. R4가 최종 검색 후 release와 실패 정리를 연결한다.
6. R4 API·SSE가 공개 투영을 사용한다. 다른 담당 파일·규칙·의존성은 이번 R2 변경에서 수정하지 않는다.

이 후속 연결이 없는 상태에서는 실제 전체 서비스 동작 완료를 주장하지 않는다.

## 12. 구조화 사실 조회 연결 보정 — PR #50 대응

### 합의된 원천 형식을 R2가 수용

R3 저장/R4 조회 형식은 유지한다. R2 `validate_facts`는 `invoke_tool`이 반환한
**ToolResponse.data**의 `availability/rcept_no/corp_code/structured_facts/facts_hash`를 받는다.
`{ok,data}` 응답 전체를 한 번 더 풀거나 R4 응답을 최상위 facts로 바꾸지 않는다.

- AVAILABLE: `structured_facts`의 공시·기업·스키마·출처를 검증하고 facts를 사용한다.
- v1 자료형은 **string / number / integer / boolean**이다. number는 유한한 JSON 숫자만
  허용하고 문자열·bool·NaN·Infinity는 거절한다. 결측·false·0을 서로 대체하지 않는다.
- 다중 행 키는 R4와 동일하게 name/api/field/row_key/period/basis를 사용한다.
- `facts_hash`를 구조화 값의 버전 키로 유지한다. R4의 canonical JSON 방식
  (sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)의 SHA-256을
  R2에서도 확인한다. JSON 변조·접수번호·기업 불일치는 중단한다.
- R2 내부 검증 결과에는 facts 목록과 **facts_hash**를 보존한다. 이를 content_hash나
  data_version으로 개명하지 않는다. 원문 해시 및 전체 입력 input_version과 혼용하지 않는다.
- `receive`는 dispatcher가 제공한 `ExecutionContext.facts_version`과 facts_hash를 비교한다.
  facts_version 누락 시 input_version으로 조용히 대체하지 않는다. R4가 이 값을 주입해야 한다.
- UNAVAILABLE/NULL은 위험 없음이 아니다. 이번 보정에서는 기존 수동 확인 처리를 유지한다.
  원문 분석 fallback·백필 대상 선별은 별도 실행 정책이며 이 PR에서 임의로 바꾸지 않는다.
- 순수 payload 단위 검증(옛 decimal/date 포함)은 기존 직접 호출 호환용으로만 유지한다.
  새 MCP 통합 경로에서는 중첩 응답과 위 v1 네 자료형을 요구한다.

### 시험과 담당 경계

R2 가짜 도구도 실제 R4 중첩 응답을 사용한다. R3의 실제 수집 함수 → R4 store_facts/
get_disclosure_facts → R2 receive 연결은 임시 DB·가짜 DART로 확인하며 실제 외부 API
성능 평가와 구분한다. 버전·값 변조·number 거절 오류도 검사한다.

R4 담당 `mcp_business.md`에는 같은 응답·자료형·facts_hash 의미를 반영 요청한다.
R3 담당 #50 시험은 xfail을 제거하되, 모양 시험에서 facts를 지우면 해시를 다시 계산해야
한다(또는 실제 응답을 그대로 사용). 버전 기대값도 facts_hash로 변경한다. 변조를 통과시키기
위해 R2 검증을 약화하지 않는다. 별도 리뷰 요청 패치로 제공하며 담당 파일을 대신 수정하지 않는다.

## 13. R2 운영 진입점·검색 수명 보완 (2026-10-23)

### R4 호스트의 호출 경계

`agent.hosted.build_runtime(deps, saver)`는 동기 factory이며 R4
`agent.adapters.executors.open_executors`가 주입한 체크포인터를 두 Graph에 사용한다.
객체를 생성·종료하거나 DB를 선택하지 않는다. 반환 객체의 메서드는 다음과 같다.

| 메서드 | 입력 | 반환·책임 |
| --- | --- | --- |
| `analyze_public(context)` | R4 `AnalysisContext` (`rcept_no`, `execution_context`) | DONE일 때 `status/score_result/ai_explanation`. 수동 확인은 MANUAL_CHECK. 일시 오류는 worker가 재시도 가능한 `TimeoutError` |
| `draft_review(context)` | R4 `ReviewContext`의 11개 필드 | 보유 고객 전원의 검사된 `drafts`만 반환. 승인·업무 생성·DB 저장은 하지 않음 |

**활성화 선행조건:** R4 조립에서 `UnifiedBindings.classify/prepare_signals/ensure_current`
및 필요한 `record_budget`을 실제 구현으로 주입한다. 현재 `build_dependencies()`가
이를 제공하지 않으므로 이번 R2 변경만으로 운영 활성화가 완료되지는 않는다.
연결 후 설정은 `PHASE1_RUNTIME_FACTORY=agent.hosted:build_runtime`이다.
bindings가 없으면 시작 시 명시적으로 거절하며 legacy Graph로 우회하지 않는다.

### 공개 결과 저장과 조직별 승인 소유권

- hosted factory는 `Dependencies.defer_publication=True`를 설정한다. 공개 Graph는
  계산 완료를 체크포인트에 남기고 반환한다. 구형 `save_assessment` 저장기를 호출하지
  않는다. 최종 공개 판정·상태 변경·processed_at 기록은 R4 worker의 lease 검증을 거쳐
  `complete_analysis`가 수행한다. MANUAL_CHECK/FAILED도 R4가 작업 상태로 기록한다.
- 이때 `analysis_complete=True`는 **계산 완료**이며 DB 공개 저장 성공을 뜻하지 않는다.
- 새 조직별 경로는 전달받은 불변 공개 결과·설명·고객 목록으로 초안만 생성하는
  별도 LangGraph다. R4 `run_review/save_drafts/decide`가 저장·승인·연락 업무의 정본이다.
  구형 `start_review/resume` 경로와 동시에 승인·업무 생성을 실행하지 않는다.
- `ReviewContext`에는 대표 접수번호·제목이 없다. R2는 이를 추정하거나 현재 DB에서
  다시 읽지 않는다. 제공된 기업 코드·근거·설명을 사용한다. 추가 메타데이터가
  필요하면 R4가 계약을 확장한다.
- tenant/review/assessment/claim version 및 입력 전체의 해시로 private checkpoint를
  분리한다. 같은 입력·같은 version의 완료 결과는 재사용하고 이미 결정한 문안은
  보존한다. 다른 조직·새 version은 재사용하지 않는다.
- 새 private 경로는 공시 원문을 검색하지 않는다. 안내문 가이드만 검색하며 모델에는
  점수 없는 허용 필드를 전달한다. 검사 실패 후 3회가 소진되면 문안을 반환하지 않는다.
- 개별 호출의 사용량은 checkpoint로 보존한다. 서로 다른 R4 claim version에 걸친
  누적 사용량·재시도 장부는 R4와 별도로 연결해야 하며 이번 변경이 이를 대신하지 않는다.

### 검색 사용권·완전성

- `agent.retrieval.RAGScope`는 새 public Graph의 invocation마다 생성된다.
  실행 ID·시도 번호를 포함한 해시와 invocation nonce로 128자 이하의 holder를 만든다.
  같은 작업의 교체 worker가 이전 worker의 늦은 cleanup에 영향을 받지 않게 한다.
- 공통 `invoke_tool`에서 모든 공시 검색·release에 같은 holder를 전달한다.
  R4 `EvidenceTools`의 중첩 과거 검색도 이 경계를 사용한다. 가이드 검색에는 넣지 않는다.
- 처음 조회한 공시와 과거 근거 공시를 모두 추적한다. 성공·실패·취소의 finally에서
  동일 holder로 멱등 release를 수행한다(전체 최대 5초). 해제 실패는 원문이나 오류
  내용을 기록하지 않고, R3의 유한 TTL을 최종 회수 수단으로 사용한다.
- `truncated:true`, 잘못된 완전성 타입, 누락된 해시/정규화 버전, 재검색 중 버전 변경은
  `RAGContractError`로 즉시 수동 확인한다. 추가 조회 루프가 이 오류를 무시할 수 없다.
- 해시와 normalization_version은 응답과 기존 verified_sources를 비교한다.
  R2가 정규화 버전 값을 만들어 채우거나 R3 정규화 로직을 복제하지 않는다.

### 남은 외부 연결과 검증 범위

1. R3: 실제 공시 검색 응답의 `normalization_version` 제공. 현재 코드에는 없으므로
   이 필드 없이 새 텍스트 추출 경로를 정상 완료할 수 없다. row_key 채우기도 R3 영역이다.
2. R4: 실제 UnifiedBindings·예산 장부·호스트 활성화 연결. 구형 저장 adapter가 런타임
   밖에서 직접 release한다면 분석과 동일 holder를 전달해야 한다.
3. 구조화 사실 UNAVAILABLE의 원문 fallback·백필 선별 정책은 이번 변경 범위가 아니다.
   이를 위험 없음으로 바꾸지 않고 현재 수동 확인 동작을 유지한다.
4. 테스트는 가짜 모델·도구, 실제 R4 SQLite 체크포인터 경계와 R3 메모리 사용권을 확인한다.
   실제 DART·모델·PostgreSQL 전체 실행이나 위험 예측 정확도 검증을 의미하지 않는다.

백필은 R3가 수집 완료를 인계하는 동작을 유지한다. 위험 대상/정정/후속의 선별·실행량 제한은
R1 정책과 R4 dispatcher/enqueue_ready 책임이며 구조화 사실 유무만으로 제외하지 않는다.
