# collector 계약 (DART 수집기)

> 담당: **R3** · 상태: **초안 v2 — R1·R2·R4 검토(PR #31 코멘트, 2026-10-23) 반영.** DB 표 모양은 R4가 `R4-V02` 브랜치에 구현한 것(`shared/models/operations.py`, `shared/collection.py`, `risk_engine/financials.py`)을 기준으로 맞췄다. 아직 `main`에 합쳐지지 않았으므로 합쳐진 뒤 다시 확인한다.
> 근거 문서: [설계서 STEP 03](../dartch_phase1_mvp.md), [데이터 출처표](data_sources.md), [파일 구성 계획](../dartch_file_plan.md), [mcp_rag](mcp_rag.md).
> `확인됨`은 2026-10-23 OpenDART 실호출로 본 것, `결정됨`은 팀 검토에서 정해진 것, `제안`은 제가 정한 안, `TODO(확인 필요)`는 아직 모르는 것이다.

## 1. 하는 일과 경계

| 하는 일 | 하지 않는 일 |
| --- | --- |
| 보유 종목 합집합을 읽어 DART 공시 목록·구조화 값·재무제표를 받아 DB 표에 넣는다 | 위험 점수·단계 계산 (R4 `risk_engine`) |
| 재무 지표 계산 값을 `financial_snapshots.indicators`에 넣는다 | 위험 항목 분류와 AI 플래그 추출 (R2 `agent`) |
| 종목별 증분 커서·수집 이력 기록, 실패 재시도 | 공시 원문 저장 (**DB·디스크 어디에도 남기지 않는다**) |
| 매일 09:05 (Asia/Seoul) 자동 실행, 수동 실행 진입점 | 화면·API (R4·R5). 수집 결과는 DB 표로만 전달한다 |

- 의존: `collector` → `shared`만 import한다. `agent`·`risk_engine`·`api`를 import하지 않는다. `mcp_servers/rag`가 `collector.dart_client`를 부르는 것은 허용된다.
- 수집기와 규칙 엔진은 **DB 표로만** 만난다. 지표 값은 수집기가 계산해 넣고, 점수는 R4가 그 값을 읽어 계산한다.
- **값이 없으면 `MISSING`이지 0이나 `false`가 아니다** (R2·R4 공통 요구). 값 없음·해당 없음·조회 실패·확인 필요는 구분해서 기록한다 (5-3).

## 2. 모듈 (파일 구성 계획과 같다)

| 파일 | 책임 |
| --- | --- |
| `dart_client.py` | DART 호출 (`httpx` async, 재시도, 호출 속도 제한). 지금 `scripts/data/fetch_dart.py`와 `mcp_servers/rag/dart_source.py`에 따로 있는 호출 코드를 합친다. 원문 받기·정규화(`fetch_document`, `normalize_document`)도 여기 둔다 — **원문 해시는 이 함수 하나만 계산한다** (mcp_rag.md 참고) |
| `watchlist.py` | 보유 종목 합집합 읽기, ETF 제외, 종목코드 → `corp_code` 변환 |
| `cursor.py` | R4의 `shared/collection.py`(`reserve_stocks`, `finish_stock`, `heartbeat_run`, `finish_run`)를 감싸 종목별 증분 조회 범위를 계산한다 |
| `jobs.py` | 종목 1개 수집 작업 (목록 → 항목 API → 재무제표 → 저장) |
| `backfill.py` | 새 종목의 최근 12개월 + 최신 정기보고서 채우기 |
| `indicators.py` | 재무 지표 계산 |
| `repo.py` | DB 저장 (idempotent upsert) |
| `scheduler.py`, `__main__.py` | APScheduler 09:05, `python -m collector` 진입점 (수동 실행·1회 실행) |

## 3. 수집 흐름

```
09:05  collect_run 시작 (R4가 만든 collect_runs 행을 받아 heartbeat로 임대 유지)
 ① watchlist: holdings의 stock_code 합집합 → ETF 제외 → corp_code 변환
 ② reserve_stocks(run_id, stock_codes): 다른 실행이 잡고 있는 종목은 건너뜀
 ③ 종목마다(병렬, 호출 속도 제한 안에서):
     커서 없음(last_success_at null) → backfill (최근 12개월 list + 최신 정기보고서)
     커서 있음 → list.json(corp_code, 커서 날짜 하루 전 ~ 오늘)
     신규 접수번호마다: disclosures에 upsert (본문 없음)
        제목이 "주요사항보고서(...)" → 해당 항목 API 호출 → rcept_no로 행 고름 → disclosure_facts 저장
        제목이 거래소 공시 → 제목·접수번호·링크만 저장 (원문은 나중에 rag가 읽음)
        새 정기보고서 → 재무제표 API → indicators → financial_snapshots
     모든 페이지를 저장한 뒤에만 finish_stock(success_at=...) — 실패하면 success_at 없이 호출(커서 유지)
 ④ finish_run(fetched_count, failed_count) → 상태 DONE / PARTIAL / FAILED
```

### 3-1. ETF·비상장 제외 (확인됨)
- 종목코드가 `corpCode.xml`의 `stock_code` 목록에 없으면 수집 대상이 아니다. 시드 보유 종목 8개 중 `069500`(KODEX 200)만 `corpCode.xml`에 없었고 나머지 7개는 `corp_code`가 나온다.
- 제외한 종목은 조용히 버리지 않고 로그와 `collect_runs.error`에 남긴다. `skipped` 건수를 따로 담을 칸이 R4 구현에는 없다 → `TODO(확인 필요)` (R4: `skipped_count` 추가 여부).
- `corpCode.xml`은 약 12만 건(종목코드 있는 회사 4,001개)이고 하루 1회 내려받아 캐시한다.

### 3-2. 증분 — `last_success_at`의 의미 (결정됨 방향, 세부 R4 확인)
R4가 요청한 대로 의미를 고정한다.

- **`collect_cursors.last_success_at` = 그 종목의 조회 범위(시작일~실행일)를 모든 페이지까지 저장하고 끝낸 시각.** 시간대를 포함한다(R4 `finish_stock`이 시간대 없는 값을 거절한다).
- 작업 종료 시각이나 마지막으로 발견한 공시의 날짜가 **아니다.** 새 공시가 하나도 없어도 조회가 성공하면 전진한다.
- **다음 조회 시작일 = `last_success_at`을 KST로 바꾼 날짜 − 1일.** 이유: `list.json`의 `rcept_dt`는 날짜까지만 있다(확인됨). 같은 날 09:05 이후에 접수된 공시는 그 실행에서 못 받으므로, 하루 겹쳐 받고 `rcept_no` 기본키로 중복을 버린다.
- 실패한 종목은 `success_at` 없이 `finish_stock`을 불러 예약만 풀고 커서를 유지한다(R4 구현과 같다). 주말·휴일·실패로 건너뛴 날은 커서가 그대로라 다음 실행이 이어 받는다.
- 커서는 고객사별이 아니라 **종목별**이다(공시는 고객사가 공유).
- **발견은 `list.json`으로 한다.** 주요사항 API는 기간 필터를 믿을 수 없고(`end_de` 밖의 건이 옴, 확인됨), 행도 `list.json`보다 적을 수 있다(3-3).

### 3-3. 항목 API는 `list.json`의 일부만 줄 수 있다 (확인됨)
금양 유상증자결정은 `list.json`에 4건(2024-09-27, 10-18, 10-31, 2025-01-17)이 있는데 `piicDecsn`은 2건(10-31, 2025-01-17)만 돌려줬다. 이유는 모른다. 따라서:
- 항목 API에서 해당 `rcept_no` 행을 못 찾으면 **오류가 아니라 `MISSING`**으로 저장하고 원문 읽기(RAG)로 넘긴다.
- "API 행이 있으니 이 공시를 다 안다"고 가정하지 않는다.

### 3-4. 정정 공시 (확인됨 + 연결 규칙은 후보)
- 정정은 **새 접수번호의 별도 행**이다. 수집기는 정정도 한 건으로 저장한다 (R4 결정과 같음: 접수번호별 보존).
- API는 원본을 알려 주지 않지만, **정정 공시 본문에 `정정대상 공시서류`(서류명)와 `최초제출일`이 있다**(금양 유상증자 정정 `20250117000380`: 정정대상 "유상증자 결정", 최초제출일 2024-09-27 → `list.json`에 같은 날 `20240927000916` 주요사항보고서(유상증자결정) 존재).
- 연결 규칙 **후보**: `(corp_code, 정정대상 서류명, 최초제출일)` = 원본의 `(corp_code, 서류명, rcept_dt)`.
- **한계:** 이 패턴은 1건에서만 확인했다. 오스템 횡령·배임 정정(`20220110900349`) 본문에는 "정정" 문구가 없었다. 보편 규칙이 아니므로, 후보로 못 찾으면 **연결하지 않고** `original_rcept_no`를 비워 수동 확인으로 둔다 (R4·R2 원칙과 같음).
- 같은 서류명이어도 후속 재결정(10-31 `20241031000530`은 제목에 정정 표시 없음)이 같은 사건인지는 이 규칙으로 알 수 없다 → 자동 연결하지 않는다.
- **정정이 곧 철회일 수 있다.** 위 금양 정정의 "정정 후"는 "유상증자 철회"다. 해소 판정의 근거가 될 수 있지만 판단은 R4·R2 몫이다. 수집기는 `정정사유` 텍스트를 따로 해석하지 않는다.
- 수집기가 `event_id`·`original_rcept_no`를 채울지(R3), R4가 채울지: **수집기는 후보 값(`original_rcept_no`)만 제안하고, `event_id` 확정은 R4**로 한다 `TODO(확인 필요)` (R4 확인).

### 3-5. 백필
- 새 종목은 `list.json`을 최근 12개월 한 구간으로 받는다. `corp_code`를 지정하면 16개월을 한 번에 조회할 수 있었다(확인됨).
- 최신 정기보고서는 `list.json`에서 `사업보고서 (YYYY.MM)` 제목으로 접수번호를 찾는다.
- 백필 중 실패하면 커서가 전진하지 않으므로 다음 실행이 처음부터 다시 한다(upsert라 중복되지 않는다).

### 3-6. 보유 종료
수집을 멈추되 이력과 저장된 공시는 지우지 않는다. 워치리스트 합집합에 없는 종목은 예약하지 않으므로 별도 `active` 칸은 두지 않는다(R4 구현에 없음).

## 4. DB 표 — R4 구현 기준 (`shared/models/operations.py`)

### 4-1. `collect_runs` (R4 구현, 감사용)
`run_id, tenant_id, requested_by, status, stock_codes, started_at, finished_at, fetched_count, failed_count, error, lease_expires_at`
- `status`: `PENDING → RUNNING → DONE / PARTIAL / FAILED`. 임대가 만료되면 R4의 `recover_expired_runs`가 `FAILED`로 닫는다.
- 증분 기준의 정본은 이 표가 아니라 `collect_cursors`다 (R1 결정).
- "마지막 수집 시각"(화면)은 종목별 `last_success_at`에서 구한다.

### 4-2. `collect_cursors` (R4 구현, 증분 정본)
`stock_code(PK), last_success_at, active_run_id` — 위 3-2의 의미를 따른다. R3 초안 v1에서 제안했던 `last_rcept_dt`, `backfilled_at`, `active`, `last_error`는 **두지 않는다**: `last_success_at`이 null이면 백필 전, 오류는 `collect_runs.error`에 남긴다.

### 4-3. `financial_snapshots` (R4 구현, 값은 R3가 채움)
`id, corp_code, rcept_no, period, period_kind, basis, indicators(JSON), sources(JSON), revision, created_at` — 유일 키 `(corp_code, rcept_no, period, basis, revision)`.

| 칸 | 값 (제안, R4 `select_snapshot_signals` 기준) |
| --- | --- |
| `period` | 사업보고서 `"2024"`, 분기·반기 `"2024-Q1"`~`"2024-Q4"` (`ANNUAL`이면 `-Q` 없음, 아니면 있음 — R4가 어기면 오류로 거절) |
| `period_kind` | `ANNUAL` / `QUARTER` / `YTD`. 반기·분기 재무제표를 누적(YTD)으로 볼지 분기 값(QUARTER)으로 볼지는 R4 정의와 DART 응답 확인이 필요하다 `TODO(확인 필요)` |
| `basis` | `CONSOLIDATED`(CFS) / `SEPARATE`(OFS). **R1 결정: 둘 다 저장**하고 `fs_div`를 기록한다. 같은 기간이면 R4가 연결을 먼저 고른다 (`select_snapshot_signals` 테스트). R1은 "판정 기준 확정 전엔 개별 임시"라고 했으므로 **자본잠식 판정에서 개별을 쓸지 연결을 쓸지는 R1·R4 확인 필요** `TODO(확인 필요)` |
| `rcept_no` | 지표를 만든 보고서의 접수번호 (재무제표 API 행마다 있음, 확인됨) |
| `revision` | 같은 보고서의 정정본이 새 `rcept_no`로 오면 1씩 올린다 |
| `sources` | `[{api, rcept_no, accounts: [{account_id 또는 account_nm, sj_div, 값}], fetched_at}]` — 어떤 계정을 읽어 계산했는지 추적 |
| `indicators` | 5장 |

### 4-4. 기존 표 사용
- `disclosures`: `rcept_no corp_code title filed_at url`을 채운다. **`body_text`는 항상 비워 둔다.** `processed=false`로 넣는다. R4가 `event_id`·`event_started_at`·`original_rcept_no` 칸을 추가했다. 수집기는 3-4의 후보 `original_rcept_no`만 제안한다.
- `companies`: `corp_code stock_code name market`을 채운다.

### 4-5. `disclosure_facts` — 신규 (R4·R2 모두 방향 동의, 필드 확정 필요)
R2·R4가 요구한 정보를 담는다. 원문 텍스트는 담지 않고 **API가 준 필드 값**만 담는다. 모델은 `shared/models/collect.py`(R3 주담당, R4 검토)에 둔다.

| 칸 | 설명 |
| --- | --- |
| `id` | PK |
| `rcept_no`, `corp_code` | 공시 접수번호·기업 코드 |
| `fact_type` | 사실 유형. 항목 API 이름(`piicDecsn`)과 분리해 위험 항목 코드(예: `RIGHTS_OFFERING`)와 같은 어휘를 쓰는 것을 제안 `TODO(확인 필요)` (R4 `phase1_classification`과 맞춤) |
| `item` | 사실 이름 (`ic_mthn` → `issue_method` 같은 안정된 영문 이름). **원본 필드명은 `source_field`에 따로 둔다** |
| `value` | 값. 숫자는 쉼표를 지워 숫자로, 못 읽으면 `null`+상태 |
| `unit` | `원`, `주`, `%`, 없음 |
| `as_of` | 기준일 또는 대상 기간 (있을 때) |
| `origin` | `API` (이 표는 항상 `API`; 코드 계산 값은 `financial_snapshots`, AI 추출은 별도) |
| `source_api`, `source_field` | 출처. 예: `piicDecsn` / `ic_mthn` |
| `fetched_at` | 수집 시각 |
| `status` | `OK` / `MISSING`(값 없음, DART가 `-`로 줌) / `NOT_APPLICABLE`(해당 없음) / `FETCH_FAILED`(조회 실패) / `NEEDS_REVIEW`(확인 필요) — **R2 요구: 이 넷을 0·`false`로 합치지 않는다** |

- 유일 키 `(rcept_no, fact_type, item)`. 정정본은 새 `rcept_no`의 새 행이다.
- 정정 공시의 수량 필드가 `-`로 오는 경우(금양 정정, 확인됨)는 `MISSING`이다.
- **조회 경로(`disclosure_facts`를 R2가 읽는 방법)는 R4의 MCP 도구와 어댑터를 거친다** (R2 제안). 도구 이름·입출력은 R4·R2가 정한다 `TODO(확인 필요)`.
- 인용문·원문 위치는 이 표에 없다 (API 값은 인용 검증 대상이 아님, R2도 "구조화 값의 출처 검증은 원문 인용 검증과 구분"에 동의). 원문 근거는 rag 검색 결과가 따로 준다.

## 5. 재무 지표 `indicators` (R1 결정 반영)

점수 구간은 R4가 `rules.yaml`로 적용한다. 수집기는 **값과 상태**만 넣는다. 형식은 `{지표명: {"status": "OK"|"MISSING", "detail": {...}}}`이고, `MISSING`이면 `detail`을 비운다.

### 5-1. 지표별 정의

| 지표명 (`rules.yaml`) | `detail` | 계산 (결정됨 / 제안) |
| --- | --- | --- |
| `CAPITAL_IMPAIRMENT` | `impairment_pct` (100 = 전액) | **결정됨(R1):** `(자본금 − 자기자본) ÷ 자본금 × 100`. 자기자본 ≤ 0이면 전액 잠식(100). 자본금 ≤ 0이거나 계정 없음 → `MISSING`. 음수 결과는 0으로 둔다(제안). 자본총계는 `BS` 행만 쓴다(`SCE` 제외, 확인됨) |
| `OPERATING_CASH_FLOW` | `negative_years` | 제안: 당기 영업활동현금흐름 ≥ 0이면 0, 당기 < 0·전기 ≥ 0이면 1, 당기·전기 모두 < 0이면 2. 같은 응답의 당기·전기 열을 쓴다(금양 2024 당기 −203억, 확인됨). `ANNUAL`에만 넣는다 (R4 `snapshot_signals`가 분기에선 비움) |
| `INTEREST_BURDEN` | `interest_coverage` | **결정됨(R1, 2026-10-23):** `영업이익 ÷ 금융비용`. 분모는 손익계산서 `금융비용`/`금융원가`(`ifrs-full_FinanceCosts`)이다(5-2). 계정을 못 찾으면 0점이 아니라 `MISSING`("산정 불가")이고 점수에 안 넣는다. 금융비용 ≤ 0이면 나눌 수 없어 `MISSING`으로 두고 사유를 `sources`에 남긴다(R4 `ratio()`도 분모 ≤ 0이면 수동 확인) `TODO(확인 필요)` (R4: 이 경우를 "부담 없음"으로 볼지) |
| `SHORT_TERM_DEBT` | `debt_to_cash_pct` | **결정됨(R1):** 분자 = CP·단기사채·1년 내 회사채(유동성사채 포함)·단기차입금·유동성장기부채, 분모 = `현금및현금성자산`만. `단기차입금`은 표준계정코드가 없어 이름으로 찾는다(확인됨). 구성 항목 **하나라도 못 찾으면 `MISSING`**(일부만 더하면 과소 계산). 유동성사채·유동성장기부채의 실제 계정명과, CP·단기사채·회사채를 주기 API 만기 구간(`yy1_below` 등)에서 가져올지 재무제표 계정으로 가져올지는 `TODO(확인 필요)` |
| `AUDIT_OPINION` | `opinion`(거절·부적정·한정·적정), `going_concern`(bool) | `accnutAdtorNmNdAdtOpinion`의 `adt_opinion`(구조화 값, 확인됨). 계속기업 불확실성은 별도 필드가 없어 `emphs_matter`·`core_adt_matter` 텍스트에서 읽어야 한다 → 텍스트 해석은 R2 영역이므로 수집기는 `going_concern`을 채우지 않고 원문 문구만 `NEEDS_REVIEW`로 넘길 수 있다 `TODO(확인 필요)` |
| `DEBT_ISSUANCE` · `FUND_USAGE` · `LARGEST_SHAREHOLDER_CHANGE` | 플래그 | 전년 대비 비교·사용 목적 판단이 필요하다. Phase 1 후순위, `MISSING`으로 시작 |
| `EQUITY_INVESTMENT` | — | `manual_only` (R4 규칙). 값만 저장 |

비교 가능성: R4 `comparable()`은 `basis`·`period_kind`가 같을 때만 전년 비교를 인정하고, 분기 비교에는 `period_slot`을 쓴다. `period_slot`이 어디에 담기는지는 R4 코드에서 확정되지 않았다 `TODO(확인 필요)`.

### 5-2. 이자비용 계정 — 결정됨 (R1, 2026-10-23): `금융비용`/`금융원가`
5개 회사(금양·삼성전자·SK하이닉스·CJ CGV·SK) 2024 사업보고서 연결재무제표에서 **손익계산서에 `이자비용` 계정은 없었고**, 후보 두 가지 중 R1이 A를 골랐다.

| 후보 | 계정 | 결과 |
| --- | --- | --- |
| **A (채택)** | 손익계산서 `금융비용`/`금융원가` (`ifrs-full_FinanceCosts`) — 5곳 모두 있음 | 외환손실·파생손실 등이 섞여 분모가 커진다. 이자보상배율이 낮게 나와 **경보 쪽으로 치우친다** |
| B (채택 안 함) | 현금흐름표 `이자의 지급` (`ifrs-full_InterestPaidClassifiedAsOperatingActivities`) | 현금 기준. 이자 지급을 재무활동으로 분류하는 회사는 다른 계정일 수 있다(이번에 보지 않음) |

- 찾는 방법: 손익계산서(`sj_div`가 `IS` 또는 `CIS`) 행에서 `account_id == ifrs-full_FinanceCosts`를 먼저 찾고, 없으면 `account_nm`이 `금융비용` 또는 `금융원가`인 행. 둘 다 없으면 `MISSING`.
- **알려진 한계:** 이 값은 이자 외 손익을 포함하므로 이자보상배율이 실제 이자 부담보다 낮게 나올 수 있다(경보가 늘어날 쪽). 확인한 것은 5개 회사뿐이다. **경보 빈도 평가(`eval`)에서 이 지표가 오탐을 만드는지 따로 본다.**
- 이 결정은 R1과 한예빈이 직접 이야기해 정한 것이다. PR 코멘트로 R1이 남기면 출처를 그쪽으로 바꾼다.

### 5-3. 계정 찾기와 값 읽기
1. `account_id`가 있으면 그것으로. 없으면 `account_nm` 일치.
2. 같은 계정이 여러 행이면 `sj_div`로 거른다 (`BS`, `IS`/`CIS`, `CF`).
3. 숫자는 쉼표 문자열이고 `-`가 올 수 있다. 읽지 못하면 `null`이 아니라 `MISSING`.
4. 연결(`CFS`)과 개별(`OFS`)을 **둘 다** 받는다. 한쪽이 없으면 그쪽만 저장한다.

## 6. 오류·재시도 (R4 요청: 재시도 가능 여부 명시)

| 응답 | 재시도 | 처리 |
| --- | --- | --- |
| `000` 정상 | — | |
| `013` 조회된 데이터 없음 | 아니오 | **실패가 아닌 정상 빈 결과** |
| `100` 부적절한 값 | 아니오 | 버그로 기록 (종목 실패) |
| `020`(호출 한도 초과로 알려짐, 이번에 못 일으킴) | **제한적** | 백오프 후 최대 횟수까지 재시도. 계속되면 남은 종목을 두고 `PARTIAL`로 끝낸다. 일시 초과와 일일 소진을 응답 코드만으로 구분할 수 있는지는 모른다 `TODO(확인 필요)` |
| 네트워크 오류·5xx·시간 초과 | 예 | 지수 백오프 |
| 그 밖의 상태 코드 | 아니오 | 종목 실패로 기록하고 계속 |
| 한 종목 실패 | — | 그 종목만 실패 처리(커서 유지, 예약 해제), 나머지는 계속 |

- 가짜 키(`x`×40)로도 `000`이 왔다(확인됨, 원인 모름). 키 오류를 응답으로 감지할 수 있다고 가정하지 않고, **키가 비어 있으면 시작 때 실패**시킨다.
- 로그에 `crtfc_key`와 원문을 남기지 않는다. 오류 메시지에서 URL 쿼리 문자열을 지운다.
- 이 재시도는 수집기 자체 규칙이며 `harness/recovery_tools.py`(R4)를 import하지 않는다.

## 7. 설정·의존성 — R4에게 요청 (이름·의미·단위·기본값·필수 여부)

| 이름 | 의미 | 단위·형식 | 기본값 (제안) | 필수 |
| --- | --- | --- | --- | --- |
| `DART_API_KEY` | OpenDART 키 | 문자열 | (있음) | 수집·원문 조회 시 필수 |
| `COLLECT_ENABLED` | 스케줄러 켜기 | bool | `false` | 아니오 (로컬·테스트에서 꺼 둠) |
| `COLLECT_TIME` | 매일 실행 시각 (Asia/Seoul) | `HH:MM` | `09:05` | 아니오 |
| `COLLECT_BACKFILL_MONTHS` | 새 종목 백필 범위 | 개월 | `12` | 아니오 |
| `DART_TIMEOUT_SECONDS` | 요청 시간 제한 | 초 | `30` | 아니오 |
| `DART_RETRY_MAX` | 재시도 최대 횟수 | 정수 | `3` | 아니오 |
| `DART_MAX_RPS` | 초당 호출 상한 | 정수 | `4` — 제 확인 호출이 `0.25초` 간격(≈4/초)에서 문제없었다는 것뿐이다. 한도는 모른다 `TODO(확인 필요)` | 아니오 |
| `DART_MAX_CONCURRENCY` | 동시 종목 수 | 정수 | `3` (제안) | 아니오 |
| `COLLECTION_LEASE_SECONDS` | 실행 임대 | 초 | (R4가 이미 있음) | — |

- 의존성: `httpx`(dev → 일반), `tenacity`, `apscheduler`. R4가 `ErrorCode.UPSTREAM_ERROR`를 추가하면 rag 도구의 한도 초과·장애 응답에 쓴다 (mcp_rag.md).
- DB: `DB_URL` 그대로. 수집기는 SQLAlchemy 세션만 쓴다.

## 8. 테스트 (`tests/collector/`)

DART 호출은 모두 가짜 응답으로 한다 (키·네트워크 불필요). 사례 응답은 실제 관측 형태를 줄여 쓰되 접수번호는 실제 공시의 것만 쓴다.

- 증분: `last_success_at` 하루 전부터 조회, 중복 접수번호 무시, 주말 건너뛰기 후 이어 받기, **일부 페이지만 저장된 채 실패하면 커서가 전진하지 않음**
- 예약: 다른 실행이 잡은 종목은 건너뜀 (R4 `reserve_stocks`), 임대 만료 후 늦은 작업자가 커서를 전진시키지 못함
- 백필: 새 종목, 중간 실패 후 재실행이 중복을 만들지 않음
- ETF 제외(`069500`)
- 주요사항 API가 기간 밖 행을 섞어 주거나 행이 빠져도 `rcept_no`로 맞는 행만 고르고, 빠진 행은 `MISSING`
- `013`은 빈 결과, `020`·5xx는 재시도, 한 종목 실패가 다른 종목을 막지 않음
- 지표: 자본잠식(자기자본 ≤ 0 → 100), 영업현금흐름 당기·전기, 구성 항목 일부 없음 → `MISSING`(0 아님), 금융비용 계정 없음·금융비용 ≤ 0 → `MISSING`(이자보상배율), 정정 보고서는 `revision` 증가
- 연결·개별 둘 다 저장
- 정정 본문의 `정정대상 공시서류`·`최초제출일`로 원본 후보를 찾고, 못 찾으면 `original_rcept_no`를 비움
- 키·원문이 로그와 DB에 남지 않음

## 9. 합의 상황

| # | 내용 | 상태 |
| --- | --- | --- |
| 1 | `collect_cursors` 신설 | **결정됨** (R1 승인, R4 구현). `last_success_at` 의미는 3-2로 제안 → R4 확인 |
| 2 | 구조화 값 저장 | 방향 합의(`disclosure_facts`). 칸·MCP 조회 경로 확정 필요 (R2·R3·R4) |
| 3 | 정정 ↔ 원본 | 방향 합의(`event_id`·`original_rcept_no`). DART에서 확인할 근거는 3-4. 연결 알고리즘 확정 필요 (R4·R2) |
| 4 | 거래소 공시 경보 대상 | **결정됨** (R1, data_sources.md에 반영). 위니아 풍문 정지 사례 확인은 `eval/cases` 작업 때 |
| 5 | 재무 지표 정의 | **대부분 결정됨** (R1). 이자비용은 `금융비용`/`금융원가`로 **결정됨**. 유동성사채 계정명, 연결/개별 판정 기준, 금융비용 ≤ 0 처리는 열림 |
| 6 | 수집 → 분석 시작 | 큐 없이 DB 조회로 합의. `processed`만으로 부족 → 선점·재시도·상태 구분은 R2·R4 |
| 7 | 설정·의존성 | R4 반영 예정, 7장 표로 전달 |
| 8 | `release_disclosure_index` 전환 | 방향 동의, 상세는 mcp_rag.md |
| 9 | `purge_disclosure_text` | **결정됨** (코드가 맞음, 메모리 색인 전환 때 정리) |

## 10. 한계 (문서·화면에 명시)

- 하루 1회(09:05) 수집이라 장중에 나온 공시는 다음 날 아침에야 반영된다 (설계서에서 허용).
- DART 호출 한도와 제출 후 재무 반영 시차는 아직 모른다 (`data_sources.md` 참고).
