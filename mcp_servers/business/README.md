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
