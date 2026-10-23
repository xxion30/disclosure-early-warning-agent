# business-mcp 도구 계약

> 2026-10-23 전환 상태: `purge_disclosure_text`는 기존 Graph 호출자를 위한 호환 도구로만 남는다. 새 공개 분석 경로는 본문을 저장하지 않고 해시·성공 완료 시각을 별도 기록한다. 최종 명세는 purge 불필요이며, 기존 호출자 전환 전 즉시 삭제하지 않는다.

> 담당: **R4** · 상태: 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. `create_contact_task`·`update_task_status`는 오후 회의로 되살아나고, `compute_customer_impact`와 `purge_disclosure_text`는 삭제 대상이다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.


## R4 구현 계약 (2026-10-22)

- calculate_risk_score: 새 위험 규칙 출력(stage, 그룹 점수, active_stages, 근거, 정규화 signals). 회사·공시 메타데이터를 DB로 검증하며 클라이언트 points는 무시한다.
- save_assessment: 공시별 공개 평가 upsert. score_result가 새 stage 형식이면 risk_states도 트랜잭션 안에서 갱신한다. 과거 평가 재저장이 최신 상태를 덮지 않는다.
- get_risk_state(corp_code), save_risk_state(corp_code, score_result, filed_at, resolution_evidence?)로 상태를 조회·저장한다. 해소는 근거가 필요하다.
- get_holders(stock_code, tenant_id): 회사 필터를 적용하며 평가금액 내림차순, 고객 ID 순으로 반환한다. tenant_id 생략은 기존 연결용 default 테넌트로 제한한다.
- create_contact_task는 기존 승인 후 코드 호출 경로를 유지한다. (assessment_id,customer_id) 유일 제약으로 중복 생성을 방지한다. PB의 소유권은 adapter/API가 검증한다.
- tenant_runs의 회사별 실행 키는 API adapter가 제공한다. 모델은 회사·PB 권한을 지정할 수 없다.
- 수집 요청 실행은 API의 명시적 dispatcher 경계를 사용한다. R3 수집기가 이 경계로 실제 실행·커서 갱신을 연결한다.


## 오후 회의(10/22) 반영 — 이 절이 아래 본문보다 우선한다

- 되살리는 도구: `create_contact_task(assessment_id, customer_id, draft_message)` — **PB 승인 후에만**, `priority` 인자 없음, (assessment_id, customer_id)당 1건(멱등). `update_task_status(task_id, status, note)` — PB가 화면에서만, 승인·보류 → 연락완료·보류.
- 되살리지 않는 도구: `compute_customer_impact`, `purge_disclosure_text`. 이에 따라 `create_contact_task`의 "영향도 규칙으로 다시 계산한 우선순위와 같아야 만든다" 검사는 **없앤다**. 대신 승인된 PB의 `pb_id`가 `customers.pb_id`와 같은지, 평가 status가 `WAITING_APPROVAL`인지만 검사한다.
- `get_holders(stock_code)`: 보유 고객 전원, **`eval_amount` 큰 순**으로 돌려준다 (고객 이름은 마스킹 유지).
- `calculate_risk_score`는 서버 안에서 점수를 계산하지만, 그 점수를 PB 화면용 응답까지 전달하지 않는다 (api.md). Agent 내부 State에는 정렬을 위해 남는다.

## 4-2. business-mcp 도구 (포트 8001)

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
| purge_disclosure_text | rcept_no: str, content_hash: str = None | purged, content_hash, processed_at, purged_at | 변경 (Agent 코드만, 운영 모드 `RAG_INDEX_MODE=ephemeral`에서만 동작) |

## 4-4. 공통 응답·오류 형식 (모든 도구)

```json
{"ok": true, "data": {}}
{"ok": false, "error": {"code": "NOT_FOUND", "message": "..."}}
```

- 오류 코드: NOT_FOUND, INVALID_INPUT, DB_ERROR, TIMEOUT
- 입력 검사는 도구 안에서 한다 (없는 rcept_no, 허용되지 않은 status 등).
- 예외를 밖으로 던지지 말고 위 형식으로 돌려준다.

---

## 도구 사용 설명서 (docs/contracts/mcp_business.md에서 옮김)

# business-mcp (포트 8001)

```bash
uv run python -m mcp_servers.business.server     # 서버
uv run pytest tests/mcp_servers -q               # 테스트 (임시 DB, 네트워크 불필요)
```

점수·등급·우선순위는 **전부 이 서버의 규칙표(`rules/rules.yaml`)**가 정한다. AI가 넘기는 `points`는 무시한다.
평가에서 다른 규칙표를 쓰려면 `RULES_PATH=<복사본 경로>` 환경 변수를 지정한다 (예: `combo.enabled: false`).

## A(Agent)가 `calculate_risk_score`에 넘기는 값

`new_signals` 항목: `{signal_type, detail, evidence?, filed_at?, rcept_no?}`

- `evidence`: `[{source, quote, rcept_no, location}]` — RAG 조각에서 검증한 인용. 점수 내역(`breakdown[i].evidence`)에 그대로 실려 나온다.
- `detail`에는 아래 **키 이름 그대로** 원문에서 찾은 값을 넣는다. 못 찾은 값은 키를 빼거나 null (추측 금지 → "수동 확인 필요").
- `market`(KOSPI/KOSDAQ)은 넣지 않아도 된다. 회사 정보에서 채운다.

| signal_type | detail 키 | 비고 |
| --- | --- | --- |
| AUDIT_OPINION | `opinion`: 거절 / 부적정 / 한정 / 적정 | 거절·부적정 Critical, 한정 High, 적정은 영향 없음 |
| TRADING_HALT | `lifted`: 해제 공시면 true | 해제면 무시, 아니면 Critical |
| ADMIN_ISSUE | - | Critical |
| DELISTING | `lifted` | Critical |
| EMBEZZLEMENT | `ratio_to_equity_pct` (자기자본 대비 %) | 5%(코스닥 3%) 이상 Critical, 미만 High, 비율 모르면 High + 수동 확인 |
| CAPITAL_IMPAIRMENT | `impairment_pct` (100 = 전액) | 100 이상 Critical, 50 이상 High |
| FILING_DELAY | - | 4점 |
| AUDITOR_CHANGE | `periodic_designation`: 주기적 지정제면 true | true면 0점, 아니면 3점 |
| GOOD_NEWS_CANCEL | - | 3점 |
| CB_BW | `refixing`: 리픽싱 조항 있으면 true | 2점, 리픽싱 +1, 12개월 내 2회 이상 +1(B가 이력으로 계산) |
| RIGHTS_OFFERING | `ratio_pct` (신주 ÷ 증자 전 주식 × 100), `third_party` | 20% 미만이면 0점, 2점 + (50% 이상 또는 제3자배정) +1 |
| PLEDGE | `pledge_ratio_pct` (담보 ÷ 최대주주 보유 × 100) | 2점, 50% 이상 +1 |
| REPEATED_CORRECTION | - | 2점 |

disclosure_type(분류) 이름은 `rules.yaml`의 `classification`과 같다. `OTHER`는 해당 없음, `OWNERSHIP`·`PERIODIC_REPORT`·`AUDIT_REPORT`는 원문을 읽어
위 signal_type 중 무엇인지 정해야 한다 (예: AUDIT_REPORT → AUDIT_OPINION, OWNERSHIP → 담보면 PLEDGE).

## 점수에서 빼는 경우 (모두 `rules.yaml`, AI가 아니라 코드가 판단)

- **정정 공시**: 제목이 `[기재정정]`·`[첨부정정]`으로 시작하면 정정이다 (도구가 DB의 공시 제목으로 판단). 같은 유형의 신호가 12개월 안에 이미 있으면 0점,
  없으면 일반 신호로 평가한다. 같은 유형 정정이 3번 이상이면 `REPEATED_CORRECTION`(+2)을 한 번 더한다. 첨부만 올리는 정정(`skip_title_prefixes`)은 분류 단계에서 건너뛴다.
- **자회사·종속회사**: 공시 제목에 `자회사`·`종속회사`가 있으면 RIGHTS_OFFERING, CB_BW, AUDIT_OPINION을 점수·등급에 반영하지 않는다.
- **PLEDGE**: 근거 인용에 `설정`·`체결`·`신규`·`제공`이 없으면(기존 담보의 변경·갱신·연장) 0점.
- **TRADING_HALT**: 정지 사유(`reason`)나 제목에 `분할`·`합병`·`액면`·`병합`이 있으면 무시, `lifted: true`(해제)도 무시.
- 점수 내역 항목마다 `note`에 이유가 적힌다. `detail`에는 도구가 `title`·`is_correction`·`market`을 넣는다.

## 돌려주는 값

`{ok, data}` / `{ok: false, error: {code, message}}`. `calculate_risk_score`의 `data`:
`total_score`(0.5 단위 소수 가능), `level`, `combo_upgraded`, `breakdown[]`, `signals[]`(저장용으로 정리된 새 신호).
`breakdown[]` 항목: `group`(BASE/A/B), `item`, `signal_type`, `points`, `weight`, `evidence[]`, 과거 신호는 `source_chunks[]`도.

## 저장·업무

- `save_assessment`: **rcept_no당 1건 upsert.** 같은 공시를 다시 저장하거나 상태만 바꿔도(`signals`·`score_result`를 비우면 기존 값 유지) `assessment_id`가 유지된다.
  점수 계산 전에 멈춘 경우(RUNNING·FAILED·MANUAL_CHECK)는 점수 없이도 저장된다(total_score 0, level Low는 기본값일 뿐 계산 결과가 아님).
- `create_contact_task`: (assessment_id, customer_id)당 1건. 다시 부르면 기존 task_id를 `created: false`로 돌려준다. 평가 상태가 WAITING_APPROVAL/DONE이고
  규칙표로 다시 계산한 우선순위(P1·P2)와 같아야 만든다.
- `update_task_status`: 승인·보류 → 연락완료·보류만. 모델 허용 목록에는 없다.

### R2 연결 시 확인할 사항

- 구형 level은 임시 호환 필드다. 감사의견 거절은 현재 YELLOW이며 호환 level은 High다. 기존 Critical 기대값을 새 판정으로 사용하지 않는다.
- R2는 새 YAML의 red/orange/yellow signal_type을 분류·추출하고 stage를 기준으로 분기해야 한다. 구형 group_a/group_b와 옛 프롬프트는 새 위험 항목 전체를 지원하지 않는다.
- 이벤트 관계는 R3가 검증한 disclosures.event_id/original_rcept_no를 사용한다. 모델이 event_id를 지정해 독립 사건을 임의로 합칠 수 없다.

## R4 검증 후 보완 계약 (2026-10-22)

- 계산 결과에 `as_of`, `rules_digest`, `expected_state_version`을 포함한다. 저장 결과의 `risk_state_version`을 초안 판정 버전으로 보존한다. 오래된 판정과 경합 결과는 최신 공개 상태를 덮을 수 없다.
- R4 도구 adapter는 과거 근거를 RAG에서 다시 조회하고 source/rcept_no/location/quote를 실제 반환 조각과 검증한 경우에만 source_chunks를 붙인다. 저장된 인용을 원문인 것처럼 복사해 만들지 않는다. 원문 재조회 불가 시 수동 확인한다.
- 재무 스냅샷은 지표별 유효 기간·최신 revision·연결 우선으로 선택한다. 연간 전용 지표를 최신 분기 자료로 지우지 않는다. R3의 지표 계산식·출처 실제 확인은 계속 필요하다.

과거 근거 source_chunks는 실제 RAG 반환값에 대한 재검증 뒤 추출한 인용 부분과 조각 해시만 포함한다. 전체 검색 조각을 새 체크포인트·DB에 영속 저장하지 않는다.

## Pre-merge R4 도구 계약 (2026-10-23)

get_disclosure_facts(rcept_no)의 정식 응답은 `{ok,data:{availability,rcept_no,corp_code,structured_facts,facts_hash}}`다. R2는 ToolResponse.data를 그대로 validate_facts에 전달한다. AVAILABLE일 때 R2가 읽는 목록은 `structured_facts.facts`이며 v1 dtype은 `string / number / integer / boolean`이다. number를 decimal로 변환하지 않는다. raw/value/unit/status/reason/source와 R3 저장 스키마·store_facts 입력을 유지한다.

`facts_hash`는 StructuredFacts 검증·정규화된 저장 payload 전체의 SHA-256이며 store_facts 반환값과 같다. canonical JSON은 sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False다. 수집·조회 시각도 해시 대상이다. R2가 structured_facts의 해시를 검증하고 같은 이름을 내부 결과에 보존한다. 전체 응답을 해시하거나 원문 content_hash·전체 분석 input_version으로 대체하지 않는다.

최상위 schema_version/collected_at/facts/data_version은 기존 호출자의 전환을 위한 **호환용 필드이며 R2는 사용하지 않는다.** facts도 저장본과 동일한 number를 사용하며 decimal 투영은 제거한다. data_version은 facts_hash와 값이 같은 호환 별칭일 뿐 정식 버전 이름은 facts_hash다. R3 테스트 등 기존 참조를 제거한 뒤 별도 변경으로 호환 필드를 제거한다.

공시가 없으면 NOT_FOUND. NULL은 UNAVAILABLE이며 structured_facts/facts_hash 및 호환 필드는 null이다. 실패 여부를 추측하거나 facts=[]로 바꾸지 않는다. 저장된 정상 빈 목록은 AVAILABLE + structured_facts.facts=[]로 구분한다. v1 사실 항목은 name/dtype/raw/value/unit/status/reason/source이며 source는 api/field/rcept_no/fetched_at와 선택 row_key/period/basis를 보존한다. dtype와 값은 엄격 검증하고 결측을 0/false로 대체하지 않는다.

R4 worker의 AnalysisContext.facts_version은 작업 input_manifest에서 이번 접수번호의 저장 payload를 같은 규칙으로 해시한 값이다. input_hash(전체 분석 입력) 및 원문 content_hash와 구분한다. R2 factory는 `context.execution_context`를 runtime.start(rcept_no, context=...)에 전달한다. 이 매핑은 R4 adapter가 만든 공개 ExecutionContext이며 facts_version을 명시한다. 최신 DB 값으로 스냅샷 버전을 덮어쓰지 않는다. UNAVAILABLE은 facts_version=null이며 R2에서 수동 확인/원문 분석 경로를 별도로 결정해야 한다.

수집기의 전체 mark_ready 인계는 유지한다. enqueue_ready는 이번 변경에서 선별하지 않는다. 자동 분석 제외 기준은 R1과 합의 후 별도로 구현하며, structured_facts NULL만으로 제외하거나 미분석 건을 성공 처리하지 않는다.

UPSTREAM_ERROR는 외부 서비스 실패이며 DB_ERROR/NOT_FOUND와 구별한다. get_disclosure_facts는 네트워크를 호출하지 않는다.

본문 삭제와 공개 분석 완료 기록은 분리한다. 새 작업 완료는 공개 판정 저장과 processed_at 갱신을 같은 트랜잭션에서 수행한다. MANUAL_CHECK는 성공 processed_at을 쓰지 않는다. 구형 purge는 모든 legacy 호출자 전환 전까지 호환용으로만 유지하며 새 실행 경로는 사용하지 않는다.
# R4 hosted 런타임 연결 (2026-10-23)

- `PHASE1_RUNTIME_FACTORY=agent.hosted:build_runtime`일 때 R4 조립이 실제 `UnifiedBindings`를 주입한다. 구형 `Phase1Model/EvidenceTools`는 이 경로에서 사용하지 않는다. R2 Graph와 수집기는 수정하지 않는다.
- 텍스트 조건은 YAML의 flags/values/zero_if에서 지정하고 수치값은 `structured_facts`의 확정된 필드만 매핑한다. 비율 단위는 `%`를 검증하며 다중 행·상충 값·필수 수치 누락은 수동 확인한다. 수치 추측과 결측의 0/false 대체는 하지 않는다.
- 현재 승인된 수치 매핑은 유상증자 `issue_ratio_common_pct→ratio_pct`, `third_party_allocation→third_party`, 감자 `reduction_ratio_common_pct→reduction_ratio_pct`다. 소송·양수도·풋백옵션의 자기자본 대비 비율은 R3 확정 사실이 없으면 수동 확인한다. 재무 지표는 기존 DB 스냅샷으로만 계산한다.
- API 근거는 API/field/receipt/row/value/unit/period/basis/data_version을 보존하고 계산 도구가 저장된 PRESENT 사실과 대조한다. 과거 API 근거도 해당 접수번호의 저장본으로 검증한다. 문서 인용문을 만들어 대신하지 않는다.
- 공개 분석·조직별 초안은 각각 작업/검토 ID에 누적 예산을 저장한다. 호출 전에 소비량을 기록하고 재선점·재작성 버전 변경에도 초기화하지 않는다. 임대가 바뀐 실행자의 기록은 거절한다. PB 승인 대기 시간은 실행 시간에서 제외한다.
- 구조화 사실 UNAVAILABLE의 원문 fallback은 R2 후속 연결 사항이다. 현재 수동 확인을 유지하며 운영 설정만으로 해당 경로가 완성됐다고 보지 않는다.
