# business-mcp 도구 계약

> 담당: **R4** · 상태: 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. 연락 관련 도구(`create_contact_task`, `update_task_status`, `compute_customer_impact`)와 `purge_disclosure_text`는 삭제 대상이다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.

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
