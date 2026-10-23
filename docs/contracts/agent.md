# Agent 계약 — R2 구현·통합 기준

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

별도 disclosure_facts 테이블 없이 disclosures.structured_facts JSON을 사용한다. 조회는 R4의 get_disclosure_facts(rcept_no)를 통한다. 이 도구·컬럼의 실제 구현 여부는 별도 확인한다.

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
| `facts_version` | 이번 공시 structured_facts의 content_hash/data_version. 생략하면 input_version을 같은 값으로 사용하는 호환 경로 |
| `run_id`, `attempt` | R4 선점된 작업과 최초 포함 1~3 시도 |
| `kind`, `tenant_id` | public 또는 review. review는 이용 조직 필수 |
| `model_requests_used`, `active_seconds_used` | R4가 영속 예산 장부에서 복원한 누적 사용량 |

- `await runtime.start(rcept_no, context=public_context)`는 공개 분석만 수행해 DONE으로 저장한다.
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

1. R4 `structured_facts` 컬럼·조회 MCP·공통 스키마를 실제 구현하고 위 R2 validator와 키를 맞춘다.
2. R4 조립에서 새 bindings·카탈로그·모델·체크포인터를 주입한다. 현재 adapter가 자동으로 바뀐 것은 아니다.
3. R4 dispatcher가 공개 분석 후 각 이용 조직의 review를 실행하고, 선점·lease·재시도·예산 장부를 관리한다.
4. R4가 판정 최신성·업무 생성의 tenant·멱등성을 DB 트랜잭션으로 보장한다.
5. R3가 검색 응답에 content_sha256·normalization_version을 제공한다. R4가 최종 검색 후 release와 실패 정리를 연결한다.
6. R4 API·SSE가 공개 투영을 사용한다. 다른 담당 파일·규칙·의존성은 이번 R2 변경에서 수정하지 않는다.

이 후속 연결이 없는 상태에서는 실제 전체 서비스 동작 완료를 주장하지 않는다.
