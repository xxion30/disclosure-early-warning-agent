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
| `cursor.py` | 종목별 증분 조회 시작일 계산 (순수 함수). 커서의 저장·예약은 R4의 `shared/collection.py`(`reserve_stocks`, `finish_stock`, `heartbeat_run`, `finish_run`)를 `jobs.py`가 직접 부른다 |
| `jobs.py` | 종목 1개 수집 작업 (목록 → 항목 API → 재무제표 → 저장) |
| `backfill.py` | 새 종목의 백필 시작일(12개월 전) 계산. 정기보고서는 그 기간 목록에서 찾는다 |
| `reports.py` | (계획에 없던 작은 모듈) 정기보고서 제목 → `reprt_code`·`period`. `사업보고서 (2024.12)`·`분기보고서 (2024.03)`·`[기재정정]` 해석, 12월 결산이 아니면 건너뜀 |
| `indicators.py` | 재무 지표 계산 |
| `repo.py` | DB 저장 (idempotent upsert) |
| `scheduler.py`, `__main__.py` | 매일 09:05(KST)까지 asyncio로 기다렸다 실행(APScheduler는 아직 의존성에 없어 쓰지 않음). `python -m collector --once`(지금 1회) / `--schedule`(매일) |

## 3. 수집 흐름

```
09:05  collect_run 시작 (R4가 만든 collect_runs 행을 받아 heartbeat로 임대 유지)
 ① watchlist: holdings의 stock_code 합집합 → ETF 제외 → corp_code 변환
 ② reserve_stocks(run_id, stock_codes): 다른 실행이 잡고 있는 종목은 건너뜀
 ③ 종목마다(병렬, 호출 속도 제한 안에서):
     커서 없음(last_success_at null) → backfill (최근 12개월 list + 최신 정기보고서)
     커서 있음 → list.json(corp_code, 커서 날짜 하루 전 ~ 오늘)
     신규 접수번호마다: disclosures에 upsert (본문 없음)
        제목이 "주요사항보고서(...)" → 정정 체인의 최초 접수일로 항목 API 호출 → 체인 최신 접수번호 행 → disclosures.structured_facts 저장 (3-3)
        제목이 거래소 공시 → 제목·접수번호·링크만 저장 (원문은 나중에 rag가 읽음)
        새 정기보고서 → 재무제표 API → indicators → financial_snapshots
     모든 페이지를 저장한 뒤에만 finish_stock(success_at=...) — 실패하면 success_at 없이 호출(커서 유지)
 ④ finish_run(fetched_count=성공한 종목 수, failed_count=실패한 종목 수) → 상태 DONE / PARTIAL / FAILED
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
- **발견은 `list.json`으로 한다.** 항목 API는 정정 체인의 최신본만 주고 기간 필터가 최초 접수일 기준이라(3-3), 새 공시를 찾는 데 쓸 수 없다. 값을 가져오는 데만 쓴다.

### 3-3. 항목 API는 정정 체인의 최신본만 준다 (확인됨, 2026-10-23)
처음에는 "금양 유상증자결정이 `list.json`에 4건인데 `piicDecsn`은 2건만 줬다"는 현상만 알았고 이유를 몰랐다. 전 종목 탐색과 사례 44개를 만들면서 원인을 확인했다 (자세한 표는 `data_sources.md` "항목 API는 정정 체인의 최신본만 돌려준다").

- 항목 API(주요사항보고서 주요정보)와 재무제표 API는 같은 사건의 **정정 체인에서 최신 정정본 1건만** 돌려준다. 정정이 없는 공시는 자기 접수번호 그대로 나온다.
- 기간 필터(`bgn_de`~`end_de`)는 **정정본이 아니라 체인의 최초 접수일**에 적용된다. 정정본의 날짜로 조회하면 0행이다 (CJ CGV 유상증자: 최초 2023-06-20 → 정정 2023-09-04, 9월 날짜로 조회하면 0행).
- 그래서 최초 접수번호로 행을 찾으면 **정정이 이어진 공시는 거의 항상 없다.** 정정이 최초보다 많은 항목이 있다(유상증자결정 최초 2,966 / 정정 6,684, 전환사채 2,566 / 4,318, 합병 724 / 1,744). 이 경로는 예외가 아니라 흔한 경로다.

**수집기가 값을 가져오는 방법 (구현됨: `collector/jobs.py` `_resolve_facts`)**
1. `list.json`으로 체인을 만든다: 같은 `corp_code`, 제목에서 `[기재정정]`·`[첨부정정]` 등 접두를 뺀 것이 같은 공시들. 본문의 `정정대상 공시서류`·`최초제출일`이 일치하면 확실하다 (3-4).
2. 체인의 **최초 접수일**로 항목 API를 조회하고, 돌아온 행의 `rcept_no`를 체인의 최신 접수번호로 맞춘다.
3. 그 값을 체인 전체의 `structured_facts`로 쓴다. 이 값은 **최신 정정본의 값**이다. 최초 공시 시점의 값은 항목 API로 얻을 수 없다.
4. 체인에서 행을 찾지 못하면(태영건설 관리절차 개시 `20240112000001`은 다른 날짜의 행이 돌아왔다) 억지로 맞추지 않고 `MISSING`으로 저장하고 원문 읽기(RAG)로 넘긴다.

"API 행이 있으니 이 공시를 다 안다"고 가정하지 않는다.

**정정 본문에서 최초제출일을 읽는 방법의 실제 성공률 (2026-10-23, 항목 10종에서 무작위 74건)**
- 본문을 받은 63건 중 **62건(98%)** 에서 읽었다. 날짜 표기가 `2024년 09월 27일`·`2025.03.20`·`2022. 3. 3.` 세 가지였다.
- 1건은 쌍방울이 `정정대상 공시서류`와 `최초제출일` 두 칸의 값을 서로 바꿔 적은 오기였다. 이런 건 억지로 맞추지 않고 사실 없이 넘긴다.
- **11건(15%)은 `document.xml`이 없다**(`014 파일이 존재하지 않습니다`, 영구 오류). 이 정정은 체인을 연결할 수 없어 사실이 없다.
- 직전 최초 공시에 붙이는 단순 규칙은 틀린다: 금양 `20250117000380`은 직전 최초 공시(10/31)가 아니라 본문이 말하는 `2024-09-27`의 정정이다.

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
- **`fetched_count`는 성공한 종목 수로 쓴다** (구현 결정, R4 확인 필요). R4의 `finish_run`이 "`fetched_count` 0 + 실패 ≥ 1"을 `FAILED`로 판정하므로, 새 공시 수를 넣으면 새 공시가 없는 날 한 종목만 실패해도 `PARTIAL`이 아니라 `FAILED`로 보인다. `failed_count`는 실패한 종목 수, `error`는 `종목코드: 사유`를 `;`로 이은 문자열(키·원문 없음).
- 전체(고객사 구분 없는) 정기 수집은 `tenant_id`가 `NULL`인 실행이다 (R4 API의 `/collect/status`가 이미 `tenant_id IS NULL`을 같이 보여 준다). 이미 `PENDING`/`RUNNING`인 전체 실행이 있으면 새로 만들지 않는다.
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
| `period_kind` | 사업보고서는 `ANNUAL`, 반기·분기는 `YTD`(R4 테스트 fixture와 같음). **확인됨:** 반기·분기 재무제표의 IS·CF `thstrm_amount`는 누적이 아니라 **당분기(3개월) 값**이고 누적은 `thstrm_add_amount`다(금양 2024 반기). 그래서 반기·분기는 시점 값인 재무상태표 기반 지표만 만들고, 이 구분(`YTD`/`QUARTER`)은 결과에 영향이 없다 |
| `basis` | `CONSOLIDATED`(CFS) / `SEPARATE`(OFS). **R1 결정: 둘 다 저장**하고 `fs_div`를 기록한다. 같은 기간이면 R4가 연결을 먼저 고른다 (`select_snapshot_signals` 테스트). **자본잠식 판정은 R1이 결정했다(`risk_policy.md` 3-2): 개별(OFS)·연결(CFS)을 둘 다 계산해 하나라도 기준을 넘으면 켜고, 점수는 높은 구간 하나만, 근거에 `fs_div`를 표시한다.** 이전의 "개별 임시"는 폐기됐다. 판정 구현은 R4이고, 수집기는 둘 다 저장한다. 위 "같은 기간이면 연결 먼저"는 자본잠식 외 지표에 해당한다 |
| `rcept_no` | 지표를 만든 보고서의 접수번호 (재무제표 API 행마다 있음, 확인됨) |
| `revision` | 같은 보고서의 정정본이 새 `rcept_no`로 오면 1씩 올린다 |
| `sources` | `[{api, rcept_no, accounts: [{account_id 또는 account_nm, sj_div, 값}], fetched_at}]` — 어떤 계정을 읽어 계산했는지 추적 |
| `indicators` | 5장 |

### 4-4. 기존 표 사용
- `disclosures`: `rcept_no corp_code title filed_at url`을 채운다. **`body_text`는 항상 비워 둔다.** `processed=false`로 넣는다. R4가 `event_id`·`event_started_at`·`original_rcept_no` 칸을 추가했다. 수집기는 3-4의 후보 `original_rcept_no`만 제안한다. 항목 API 값은 `structured_facts`(4-5)에 넣는다 — 이 컬럼은 R4가 추가해야 한다.
- `companies`: `corp_code stock_code name market`을 채운다.

### 4-5. 구조화 사실 `disclosures.structured_facts` (결정됨: R2, 2026-10-23 PR #32 코멘트)
항목 API가 준 값은 **별도 표가 아니라 `disclosures.structured_facts` JSON 컬럼**에 저장한다. R3 초안 v2의 `disclosure_facts` 표 제안은 철회한다. 원문 텍스트는 담지 않고 **API가 준 필드 값**만 담는다.

| 역할 | 맡는 것 |
| --- | --- |
| **R3** | 항목 API 값의 정제(쉼표·`-` 처리, 단위), 출처 기록, `structured_facts`에 쓰는 저장 연결 |
| **R4** | 컬럼 추가·마이그레이션, 저장 인터페이스, 공통 스키마, MCP 도구 `get_disclosure_facts(rcept_no)` |
| **R2** | 응답 검증, Agent 활용 |

**구현됨 (2026-10-23).** R4가 스키마(`shared/facts.py`의 `StructuredFacts`, 컬럼 `disclosures.structured_facts`, 도구 `get_disclosure_facts`)를 넣었고, R3는 `collector/facts.py`가 만든 payload를 **수집 트랜잭션 안에서 `store_facts`(R4 검증기)로 저장**한다. 이름·값 규칙:

- **의미를 확인한 항목 10종의 필드만** 사실로 만든다(아래 표). 의미를 추측해야 하는 필드(`ssl_at` 등)와 값 확인 전 항목(분할·전환사채 외 사채·자산양수도 등)은 만들지 않는다 → `structured_facts`는 NULL(UNAVAILABLE)이고 위험이 없다는 뜻이 아니다.
- `-`는 `MISSING`(사유 `API가 값을 주지 않았다`), 읽지 못한 값은 `NEEDS_CHECK`, 값이 없을 때 0·`false`로 채우지 않는다.
- **200자를 넘는 서술 텍스트는 구조화하지 않고 `NEEDS_CHECK`**(원문 검색으로 확인). 짧은 사유·명칭은 사실로 담는다.
- 날짜는 ISO(`2024-10-31`), 금액은 원(정수), 주식 수는 주(정수), 비율은 %(실수). 원본 문자열은 `raw`에 남긴다.
- **계산한 값**은 `raw`를 비우고 `source.field`에 계산식을 적는다: `issue_ratio_common_pct`(신주 ÷ 증자 전 주식 × 100, R4 규칙의 `ratio_pct`), `third_party_allocation`(증자 방식에 `제3자배정` 포함), `refixing_floor_below_price`(전환가 조정 하한 < 전환가). 하한이 비어 있으면 '조항 없음'으로 단정하지 않고 `MISSING`이다.
- 소송은 청구 금액 필드가 없어 `claim_amount`를 `NEEDS_CHECK`로 고정한다(자기자본 대비 청구액을 만들 수 없음을 명시).
- **정정 체인에서는 최신 정정본에만** 사실이 붙고(출처 접수번호 = 그 공시), 대체된 최초·중간 정정본은 NULL이다. 정정 공시에는 `original_rcept_no` **후보**를 남기되 `event_id`는 건드리지 않는다(확정은 R4 `confirm_event_link`).

| 위험 항목 | 항목 API | 사실 이름 |
| --- | --- | --- |
| 부도발생 | `dfOcr` | `default_amount`, `default_bank`, `default_content`, `default_date`, `default_reason` |
| 회생절차 개시신청 | `ctrcvsBgrq` | `applicant`, `court`, `request_date`, `request_reason` |
| 해산사유 발생 | `dsRsOcr` | `dissolution_reason`, `dissolution_date` |
| 채권은행 등의 관리절차 개시 | `bnkMngtPcbg` | `management_start_date`, `management_body`, `management_period`, `management_reason` |
| 영업정지 | `bsnSp` | `suspended_business`, `suspended_revenue`, `total_revenue`, `suspended_revenue_share_pct`, `suspension_content`, `suspension_date` |
| 유상증자 결정 | `piicDecsn` | `new_common_shares`, `new_other_shares`, `common_shares_before`, `other_shares_before`, `par_value`, `issue_method`, `use_facility`, `use_business_acquisition`, `use_operating`, `use_debt_repayment`, `use_securities_acquisition`, `use_other`, `issue_ratio_common_pct(계산)`, `third_party_allocation(계산)` |
| 전환사채 발행결정 | `cvbdIsDecsn` | `bond_face_amount`, `bond_kind`, `issue_method`, `coupon_rate_pct`, `maturity_rate_pct`, `maturity_date`, `conversion_price`, `conversion_shares`, `conversion_shares_share_pct`, `conversion_start_date`, `conversion_end_date`, `conversion_price_floor`, `payment_date`, `refixing_floor_below_price(계산)` |
| 감자 결정 | `crDecsn` | `reduction_ratio_common_pct`, `reduction_ratio_other_pct`, `reduced_common_shares`, `capital_before`, `capital_after`, `common_shares_before`, `common_shares_after`, `reduction_base_date`, `reduction_method`, `reduction_reason` |
| 소송 등의 제기 | `lwstLg` | `case_name`, `court`, `plaintiff`, `filed_date`, `litigation_date`, `claim_amount(NEEDS_CHECK 고정)` |
| 회사합병 결정 | `cmpMgDecsn` | `merger_type`, `counterparty`, `counterparty_relation`, `exchange_ratio`, `external_valuation`, `counterparty_total_assets`, `counterparty_total_debt`, `counterparty_total_equity`, `counterparty_capital`, `counterparty_revenue`, `counterparty_net_income`, `merger_date` |

**분석 인계 (R4 `shared/analysis.py`).** 사실 저장 뒤 **새로 저장했거나 아직 인계되지 않은 모든 공시**를 `mark_ready`로 인계한다(사실이 없는 공시도 — 그래야 R4 `enqueue_ready`가 분석 작업으로 만든다). `analysis_ready_at`이 비어 있는 공시만 처리하므로 실패한 공시는 다음 실행이 다시 시도하고, 인계된 공시는 다시 건드리지 않는다. 한 공시의 사실이 R4 검증에 실패해도 그 공시만 사실 없이 넘어가고(저장 도중 오류는 되돌림) `StockOutcome.problems`에 남는다.
- **실제 DART로 확인:** 금양·위니아 최근 12개월에서 공시 81건이 모두 인계되고 R4 `enqueue_ready`가 분석 작업 81개를 만들었다. 사실은 정정 체인의 최신본 2건에만 붙었다.
- **알아 둘 점(R4·R2 확인):** 수집기는 분류하지 않고 새 공시를 **전부** 인계한다. 새 종목 백필 한 번에 종목당 약 70~280건이 분석 대기열에 들어간다(금양 74건). 분석 대상을 줄이려면 R4가 `enqueue_ready` 단계에서 거르거나 R3에게 제외 기준을 알려 줘야 한다.

**`get_disclosure_facts` 응답 모양 (R2 정리, R4가 `shared/facts.py`의 `StructuredFacts` v1로 확정).** 아래 모양으로 값을 만든다.

```json
{"schema_version": "...", "rcept_no": "...", "corp_code": "...", "collected_at": "2026-10-23T09:05:00+09:00",
 "facts": [{"name": "issue_method", "dtype": "string", "raw": "주주배정후 실권주 일반공모", "value": "주주배정후 실권주 일반공모",
            "unit": null, "status": "PRESENT", "reason": null,
            "source": {"api": "piicDecsn", "field": "ic_mthn", "rcept_no": "...", "fetched_at": "..."}}]}
```

- 각 사실에 `name`(안정된 영문 이름), `dtype`, `raw`(API 원본 값), `value`(정제값), `unit`, `status`, `reason`, `source`(API·필드·접수번호·수집 시각)를 둔다. 위 필드 이름은 R2가 말한 항목(항목명·자료형·원천 값·정제값·단위·상태·사유·출처)을 제가 영문으로 옮긴 **제안**이며 `TODO(확인 필요)`(R4 확정).
- **상태 4가지 (R2):** `PRESENT`(값 있음) / `MISSING`(값 없음, 예: DART가 `-`로 줌) / `NOT_APPLICABLE`(해당 없음) / `NEEDS_CHECK`(확인 필요). 결측을 0이나 `false`로 대체하지 않는다. (R3 v2의 `OK`·`FETCH_FAILED`·`NEEDS_REVIEW`는 폐기.)
- **조회 실패는 정상 빈 결과와 구분한다 (R2 요구).** 구현: 항목 API 호출이 실패하면(시간 초과·한도·5xx) 그 종목의 수집이 실패하고(커서 유지) 아무 공시도 인계하지 않는다 → 다음 실행이 다시 시도한다. 호출은 성공했는데 **그 접수번호의 행이 없는 경우**(정정 체인에서 대체됨, 정정 본문에서 최초제출일을 못 읽음, 정정 `document.xml` 없음 등)에는 **사실을 만들지 않고 `structured_facts`를 NULL로 두며 `StockOutcome.problems`에 이유를 남긴다.** NULL은 "빈 사실"도 "위험 없음"도 아니고 R4 도구가 `UNAVAILABLE`로 돌려준다. (이전 초안의 "행이 없으면 `MISSING` + `api_row_not_found`로 담는다"는 철회: 값이 없다(`MISSING`)가 아니라 값을 알 수 없는 것이라 가짜 사실을 붙이지 않는다.) 값을 읽었지만 정제하지 못한 필드만 `NEEDS_CHECK`다.
- 정정 공시의 수량 필드가 `-`로 오는 경우(금양 정정 `20250117000380`, 확인됨)는 `MISSING`이다.
- 정정본은 새 `rcept_no`의 새 `disclosures` 행이므로 그 행의 `structured_facts`에 따로 저장한다.
- 인용문·원문 위치는 담지 않는다. API 값의 출처 검증은 원문 인용 검증과 구분한다(R2). 원문 근거는 rag 검색 결과가 따로 준다.
- 어휘 주의: `financial_snapshots.indicators`의 상태는 R4 구현대로 `OK`/`MISSING`이고, 구조화 사실은 R2 기준 `PRESENT`/`MISSING`/`NOT_APPLICABLE`/`NEEDS_CHECK`다. 두 곳의 상태 어휘가 다르다. 통일할지는 R4·R2 `TODO(확인 필요)`.

**메타데이터·해시·재수집 규칙 (R2 반영안, 2026-10-23).** 구현 상태: 같은 접수번호에 항목 API 행이 여러 개면 첫 행을 고르지 않고 모든 사실을 `NEEDS_CHECK`로 둔다(`collector/facts.py`의 `build_ambiguous_payload`, `jobs.py`). 행을 구분할 안정된 값을 아직 확인하지 못해 **`row_key`는 채우지 않는다**(다중 행 사례를 DART에서 찾으면 그때 정한다). 재수집 실패 시 기존 값 보존은 인계된 공시를 다시 건드리지 않는 구조라 이미 지켜진다(`test_second_run_does_not_touch_handed_off_disclosures_or_call_dart_again`). 해시 정의는 R4 확인 대기.
- **고정 항목 코드:** 사실의 `name`은 안정된 영문 이름이다(위 표). 이름이 바뀌면 `schema_version`을 올린다.
- **`row_key`:** 항목 API가 한 접수번호에 여러 행을 주면 첫 행을 임의로 고르지 않는다. 원천 API·업무 구분·대상/기간 등 **안정된 구분 값**으로 `source.row_key`를 만든다. 안정된 구분이 없으면 해당 다중 행을 `NEEDS_CHECK`로 둔다. API 행 번호는 수집 내 위치일 뿐 사건 ID가 아니다. R4 스키마는 `(name, api, field, row_key, period, basis)` 중복을 거절한다(`shared/facts.py`).
- **값 표현:** `raw`는 개별 원천 값이다. HTTP 전체 응답이나 공시 원문을 넣지 않는다. 금액·정밀 수치는 단위를 명시하고, 자료형은 R4 스키마(`number` 등)를 따른다. `0`·`false`는 원천에 실제 있을 때만 `PRESENT`다.
- **기간·재무 기준:** 값이 보고 기간이나 연결/개별 기준에 묶이면 `source.period`·`source.basis`를 채운다.
- **해시:** 원문 해시(`content_sha256`)와 구조화 사실 해시(`facts_hash`)는 다른 대상이다. R2 요청은 사실 해시를 **의미 필드의 canonical JSON(키 정렬·UTF-8·비유한 수 금지)** 으로 계산하고 `collected_at`·`fetched_at` 같은 수집 시각은 빼는 것이다. 현재 R4 `facts_hash`는 저장된 payload 전체를 해시하므로 수집 시각이 바뀌면 해시도 바뀐다 → 시각 제외 여부는 **R4 확인 필요** `TODO(확인 필요)`.
- **재수집 실패:** 재수집이 실패해도 기존에 성공적으로 저장한 값을 NULL로 덮어쓰지 않는다. 최근 수집 오류(`collect_runs.error`)와 기존 자료의 기준 시각·버전을 구분해 전달한다. 분석 이력은 실제로 쓴 입력 스냅샷(해시)에 연결한다(R4).
- **연결 경계:** R3는 공개 수집 결과를 DB에 저장할 뿐 `agent`를 import·호출하지 않는다. 분석 시작(dispatcher)은 R4다. MISSING일 때의 원문 검색과 충돌 조건의 확인 필요 처리는 R2다.
- **검증 항목:** 다중 행, 실제 0/`false`, API 행 없음, 호출 실패, 재수집 실패 시 기존 값 보존, 과거 정정본 선택, JSON 버전 변경.

## 5. 재무 지표 `indicators` (R1 결정 반영)

점수 구간은 R4가 `rules.yaml`로 적용한다. 수집기는 **값과 상태**만 넣는다. 형식은 `{지표명: {"status": "OK"|"MISSING", "detail": {...}}}`이고, `MISSING`이면 `detail`을 비운다.

### 5-1. 지표별 정의

| 지표명 (`rules.yaml`) | `detail` | 계산 (결정됨 / 제안) |
| --- | --- | --- |
| `CAPITAL_IMPAIRMENT` | `impairment_pct` (100 = 전액) | **결정됨(R1):** `(자본금 − 자기자본) ÷ 자본금 × 100`. 자기자본 ≤ 0이면 전액 잠식(100). 자본금 ≤ 0이거나 계정 없음 → `MISSING`. 음수 결과는 0으로 둔다(제안). 자본총계는 `BS` 행만 쓴다(`SCE` 제외, 확인됨). **OFS·CFS 둘 다 계산해 각각 저장**한다(R1: 하나라도 기준을 넘으면 켠다) |
| `OPERATING_CASH_FLOW` | `negative_years` | 제안: 당기 영업활동현금흐름 ≥ 0이면 0, 당기 < 0·전기 ≥ 0이면 1, 당기·전기 모두 < 0이면 2. 같은 응답의 당기·전기 열을 쓴다(금양 2024 당기 −203억, 확인됨). `ANNUAL`에만 넣는다 (R4 `snapshot_signals`가 분기에선 비움) |
| `INTEREST_BURDEN` | `interest_coverage` | **결정됨(R1, 2026-10-23):** `영업이익 ÷ 금융비용`. 분모는 손익계산서 `금융비용`/`금융원가`(`ifrs-full_FinanceCosts`)이다(5-2). 계정을 못 찾으면 0점이 아니라 `MISSING`("산정 불가")이고 점수에 안 넣는다. 금융비용 ≤ 0이면 나눌 수 없어 `MISSING`으로 두고 사유를 `sources`에 남긴다(R4 `ratio()`도 분모 ≤ 0이면 수동 확인) `TODO(확인 필요)` (R4: 이 경우를 "부담 없음"으로 볼지) |
| `SHORT_TERM_DEBT` | `debt_to_cash_pct` | **결정됨(R1):** 분자 = CP·단기사채·1년 내 회사채(유동성사채 포함)·단기차입금·유동성장기부채, 분모 = `현금및현금성자산`만. `단기차입금`은 표준계정코드가 없어 이름으로 찾는다(확인됨). **구현(2026-10-23, `collector/indicators.py`):** 계정명이 회사마다 달라서(위니아는 `단기차입금및유동성장기부채` 한 줄, SK하이닉스는 `차입금` 하나) "구성 항목 하나라도 못 찾으면 `MISSING`"으로는 거의 모든 회사가 `MISSING`이 된다. 그래서 **재무상태표에서 id·이름 목록에 맞는 계정만 합산하고, 하나도 못 찾을 때만 `MISSING`**으로 한다(부채가 없는 건지 계정을 못 알아본 건지 구분할 수 없으므로). 유동/비유동을 알 수 없는 계정(표준계정코드 없는 `회사채`, `전환사채`)은 세지 않고 `notes`에 남긴다. 합친 줄(`단기차입금및유동성장기부채` 등)이 있으면 그 줄만 쓴다(이중 계산 방지). 금액을 읽지 못한 계정이 있거나 현금이 0 이하면 `MISSING`. **7개 회사(금양·위니아·오스템임플란트·CJ CGV·SK·삼성전자·SK하이닉스)의 재무상태표만 보고 만든 목록**이라 다른 회사에서 계정을 놓칠 수 있다. CP·단기사채를 주기 API 만기 구간(`yy1_below` 등)에서 가져올지는 `TODO(확인 필요)` |
| `AUDIT_OPINION` | `opinion`(거절·부적정·한정·적정), `going_concern`(bool) | `accnutAdtorNmNdAdtOpinion`의 `adt_opinion`(구조화 값, 확인됨). 계속기업 불확실성은 별도 필드가 없어 `emphs_matter`·`core_adt_matter` 텍스트에서 읽어야 한다 → 텍스트 해석은 R2 영역이므로 수집기는 `going_concern`을 채우지 않는다(`detail`에서 빼고 `opinion`만 `OK`로 넣음). 계속기업 불확실성은 R2가 원문에서 읽는다 `TODO(확인 필요)`. **구현(2026-10-23):** DART는 `적정의견`과 `적정`을 섞어 쓰므로(확인됨) `적정`·`한정`·`부적정`·`의견거절`로 통일한다(`rules.yaml`에 `적정의견`은 없음). 한 보고서에 당기·전기·전전기가 같이 오고 `bsns_year`가 `제70기\n(당기)` 같은 문자열이라 `(당기)` 행만 고른다(금양 2024는 같은 연도가 2행씩 중복). 당기 의견이 갈리면 가장 나쁜 의견을 쓰고 `notes`에 남긴다. 모르는 문구는 추측하지 않고 `MISSING` |
| `DEBT_ISSUANCE` · `FUND_USAGE` · `LARGEST_SHAREHOLDER_CHANGE` | 플래그 | 전년 대비 비교·사용 목적 판단이 필요하다. Phase 1 후순위, `MISSING`으로 시작 |
| `EQUITY_INVESTMENT` | — | `manual_only` (R4 규칙). 값만 저장 |

**반기·분기 보고서:** 손익·현금흐름의 `thstrm_amount`가 누적이 아니라 당분기(3개월) 값이다(확인됨, 4-3). 그래서 사업보고서(`ANNUAL`)가 아니면 재무상태표 기반 지표(`CAPITAL_IMPAIRMENT`, `SHORT_TERM_DEBT`)만 만들고 영업현금흐름·이자보상배율은 만들지 않는다. 누적(`thstrm_add_amount`)으로 연환산 지표를 만들지는 정하지 않았다.

**정정 보고서와 `revision`:** 재무제표 API는 정정본이 있으면 **최신 정정본의 값과 접수번호**만 돌려준다(금양 FY2022: 원본 `20230322001310`이 아니라 2년 뒤 정정 `20240313001231`의 값). 그래서 스냅샷의 `rcept_no`는 목록 제목이 아니라 **재무제표 행의 `rcept_no`**를 쓰고, 이미 같은 기간에 접수번호가 같거나 더 큰 스냅샷이 있으면 다시 받지 않는다. `revision`은 수집하는 동안 서로 다른 접수번호가 나타난 순서(접수번호 오름차순)로 1, 2, …가 매겨진다. 백필은 최신 정정본 하나만 본다.

**기간·누적 혼합 금지, 단기채무 완전성 (R2 반영안).** 기간(`ANNUAL`/`QUARTER`/`YTD`)·기준(`basis`)·수정 버전·출처를 구분하고, 누적과 분기 값을 섞어 계산하지 않는다. 일부 부채 계정만 파악된 경우 그 합계를 **전체 단기채무라고 보장하지 않는다.** 부분 합산은 `notes`에 어떤 계정을 세었고 어떤 계정은 못 세었는지 기록하고, 완전성이 불명확한 값을 낮은 위험의 근거로 확정해 쓰지 않는다. 새 지표 키가 필요하면 R4와 매핑한다.

**`notes` 칸:** 각 지표는 `{status, detail, notes}`이고 `notes`는 R3가 추가한 설명 문구 목록이다(`MISSING` 사유, 세지 않은 계정, 금융비용 기준 한계 등). R4는 `status`·`detail`만 읽으므로 영향이 없다고 보지만 확인이 필요하다 `TODO(확인 필요)` (R4).

비교 가능성: R4 `comparable()`은 `basis`·`period_kind`가 같을 때만 전년 비교를 인정하고, 분기 비교에는 `period_slot`을 쓴다. `period_slot`이 어디에 담기는지는 R4 코드에서 확정되지 않았다 `TODO(확인 필요)`.

### 5-2. 이자비용 계정 — 결정됨 (R1, 2026-10-23): `금융비용`/`금융원가`
5개 회사(금양·삼성전자·SK하이닉스·CJ CGV·SK) 2024 사업보고서 연결재무제표에서 **손익계산서에 `이자비용` 계정은 없었고**, 후보 두 가지 중 R1이 A를 골랐다.

| 후보 | 계정 | 결과 |
| --- | --- | --- |
| **A (채택)** | 손익계산서 `금융비용`/`금융원가` (`ifrs-full_FinanceCosts`) — 5곳 모두 있음 | 외환손실·파생손실 등이 섞여 분모가 커진다. 이자보상배율이 낮게 나와 **경보 쪽으로 치우친다** |
| B (채택 안 함) | 현금흐름표 `이자의 지급` (`ifrs-full_InterestPaidClassifiedAsOperatingActivities`) | 현금 기준. 이자 지급을 재무활동으로 분류하는 회사는 다른 계정일 수 있다(이번에 보지 않음) |

- 찾는 방법: 손익계산서(`sj_div`가 `IS` 또는 `CIS`) 행에서 `account_id == ifrs-full_FinanceCosts`를 먼저 찾고, 없으면 `account_nm`이 `금융비용` 또는 `금융원가`인 행. 둘 다 없으면 `MISSING`.
- **알려진 한계:** 이 값은 이자 외 손익을 포함하므로 이자보상배율이 실제 이자 부담보다 낮게 나올 수 있다(경보가 늘어날 쪽). 확인한 것은 5개 회사뿐이다. **경보 빈도 평가(`eval`)에서 이 지표가 오탐을 만드는지 따로 본다.**
- **표시 이름:** 화면·문서에서는 "금융비용 기준 보상배율"이라 부르고 순수 이자비용 기준과 같다고 설명하지 않는다. 경계값 `1 미만 +2`와 `enabled:false` 해제는 R1이 확정했다(`risk_policy.md` 3-2, `docs/tasks/r1-decisions-requests.md` R4 2번). `rules.yaml` 반영은 R4다. `detail` 키(`interest_coverage`)와 계정(`ifrs-full_FinanceCosts`)은 그대로 둔다.
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
- **실제로 샌 적이 있다 (수정함):** `httpx`는 요청 URL(쿼리 포함)을 INFO 로그로, `httpcore`는 DEBUG로 남긴다. INFO 로깅을 켜고 수집기를 실제 DART로 돌리자 `crtfc_key`가 평문으로 찍혔다(예외 메시지만 가리고 로그는 막지 못했던 것). 지금은 로그 레코드를 만드는 단계에서 `crtfc_key=` 값을 `***`로 바꾼다(`dart_client._install_log_redaction`, 회귀 테스트 있음). 이 보호는 `collector.dart_client`를 import한 프로세스에만 적용된다.
- 이 재시도는 수집기 자체 규칙이며 `harness/recovery_tools.py`(R4)를 import하지 않는다.

## 7. 설정·의존성 — R4에게 요청 (이름·의미·단위·기본값·필수 여부)

| 이름 | 의미 | 단위·형식 | 기본값 (제안) | 필수 |
| --- | --- | --- | --- | --- |
| `DART_API_KEY` | OpenDART 키 | 문자열 | (있음) | 수집·원문 조회 시 필수 |
| `COLLECT_ENABLED` | (현재 쓰지 않음) 스케줄러는 `python -m collector --schedule`로 명시해서 켠다 | — | — | — |
| `COLLECT_TIME` | 매일 실행 시각 (Asia/Seoul) | `HH:MM` | `09:05` | 아니오 |
| `COLLECT_BACKFILL_MONTHS` | 새 종목 백필 범위 | 개월 | `12` | 아니오 |
| `DART_TIMEOUT_SECONDS` | 요청 시간 제한 | 초 | `30` | 아니오 |
| `DART_RETRY_MAX` | 재시도 최대 횟수 | 정수 | `3` | 아니오 |
| `DART_MAX_RPS` | 초당 호출 상한 | 정수 | `4` — 제 확인 호출이 `0.25초` 간격(≈4/초)에서 문제없었다는 것뿐이다. 한도는 모른다 `TODO(확인 필요)` | 아니오 |
| `DART_MAX_CONCURRENCY` | 동시 종목 수 | 정수 | `3` (제안) | 아니오 |
| `COLLECTION_LEASE_SECONDS` | 실행 임대 | 초 | (R4가 이미 있음) | — |

- 의존성: `httpx`(dev → 일반)만 필요하다. 재시도·스케줄러는 직접 구현해 `tenacity`·`apscheduler`는 **아직 필요 없다**(나중에 바꾸고 싶으면 그때 요청). `COLLECT_TIME`·`DART_*` 설정은 `shared/config.py`에 있으면 읽고 없으면 기본값을 쓴다(`getattr`). R4가 `ErrorCode.UPSTREAM_ERROR`를 추가하면 rag 도구의 한도 초과·장애 응답에 쓴다 (mcp_rag.md).
- DB: `DB_URL` 그대로. 수집기는 SQLAlchemy 세션만 쓴다.

## 7-1. R4와 연결하는 방법

| 연결 | 방법 |
| --- | --- |
| 수동 수집 (`POST /collect/run`) | R4의 `collect_run`은 `PENDING` 실행을 만들고 종목을 예약한 뒤 `request.app.state.collect_dispatcher(run_id, stock_codes)`를 `await`한다. 그 자리에 **`collector.jobs.dispatch_collection`을 꽂는다**(`app.state.collect_dispatcher = dispatch_collection`). 이 함수는 실행을 백그라운드로 시작하고 바로 돌아오므로 API는 202를 돌려줄 수 있다. 연결 코드는 `api/main.py`라 R4 몫이다. |
| 정기 수집 (09:05) | `python -m collector --schedule` 프로세스가 `collect_once`를 매일 부른다. docker compose의 collector 서비스는 이 명령으로 띄우면 된다 (R4). 지금 1회 돌려 보려면 `python -m collector --once`. |
| 선행 조건 | R4의 `/collect/run`은 `companies`에 있는 종목만 대상으로 삼는다(`Company`와 join). 새로 보유한 종목은 **정기 수집이나 `--once`가 한 번 돌아 `companies`를 만든 뒤**에야 수동 수집 대상이 된다. 첫 수집 전에는 `409 수집할 보유 종목이 없습니다`가 나올 수 있다. |
| 종료 코드 | `--once`: 0 성공, 1 일부·전체 실패, 2 시작하지 않음(이미 실행 중이거나 종목 없음) |

## 8. 테스트 (`tests/collector/`)

DART 호출은 모두 가짜 응답으로 한다 (키·네트워크 불필요). 사례 응답은 실제 관측 형태를 줄여 쓰되 접수번호는 실제 공시의 것만 쓴다.

- 증분: `last_success_at` 하루 전부터 조회, 중복 접수번호 무시, 주말 건너뛰기 후 이어 받기, **일부 페이지만 저장된 채 실패하면 커서가 전진하지 않음**
- 예약: 다른 실행이 잡은 종목은 건너뜀 (R4 `reserve_stocks`), 임대 만료 후 늦은 작업자가 커서를 전진시키지 못함
- 백필: 새 종목, 중간 실패 후 재실행이 중복을 만들지 않음
- ETF 제외(`069500`)
- 정정 체인: 최초 접수번호로는 행이 없고 체인 최신본만 오는 경우를 최초 접수일로 조회해 맞추고, 체인에서 행을 못 찾으면 `MISSING` (실제 DART에서 본 4가지 체인 모양으로 시험)
- 사례 파일(`eval/cases/r3_cases.json`): 형식·기대값 일관성 검사(오프라인, `tests/collector/test_eval_cases.py`), DART와 일치 여부는 `scripts/data/verify_cases.py`
- `013`은 빈 결과, `020`·5xx는 재시도, 한 종목 실패가 다른 종목을 막지 않음
- 지표: 자본잠식(자기자본 ≤ 0 → 100), 영업현금흐름 당기·전기, 구성 항목 일부 없음 → `MISSING`(0 아님), 금융비용 계정 없음·금융비용 ≤ 0 → `MISSING`(이자보상배율), 정정 보고서는 `revision` 증가
- 연결·개별 둘 다 저장
- 정정 본문의 `정정대상 공시서류`·`최초제출일`로 원본 후보를 찾고, 못 찾으면 `original_rcept_no`를 비움
- 키·원문이 로그와 DB에 남지 않음

## 9. 합의 상황

| # | 내용 | 상태 |
| --- | --- | --- |
| 1 | `collect_cursors` 신설 | **결정됨** (R1 승인, R4 구현). `last_success_at` 의미는 3-2로 제안 → R4 확인 |
| 2 | 구조화 값 저장 | **결정됨** (R2): `disclosures.structured_facts` JSON 컬럼 + MCP `get_disclosure_facts(rcept_no)`, 상태 `PRESENT/MISSING/NOT_APPLICABLE/NEEDS_CHECK`. **구현됨**: 컬럼·도구는 R4 #41, 연결·정렬은 R4 #52·#60과 R2 #53, R3 저장 연결 #49. 남은 점은 4-5의 해시 정의(수집 시각 제외) `TODO(확인 필요)` (R4) |
| 3 | 정정 ↔ 원본 | 방향 합의(`event_id`·`original_rcept_no`). DART에서 확인할 근거는 3-4. 연결 알고리즘 확정 필요 (R4·R2) |
| 4 | 거래소 공시 경보 대상 | **결정됨** (R1 `risk_policy.md` 3-1, data_sources.md에 반영). 풍문 정지·`풍문등조회공시`는 관찰 중, 관리종목 변형 제목은 🔴, 횡령ㆍ배임사실확인은 첫 관측이면 🔴, 자회사·종속회사는 참고 표시, 무상증자는 수집하지 않는다. 제목 후보는 R3, `rules.yaml` 반영은 R4 |
| 5 | 재무 지표 정의 | **대부분 결정됨** (R1). 이자비용은 `금융비용`/`금융원가`로 **결정됨**. 유동성사채 계정명, 금융비용 ≤ 0 처리는 열림. 연결/개별은 R1이 **둘 다 계산, 하나라도 넘으면 켬**으로 결정(4-3) |
| 6 | 수집 → 분석 시작 | 큐 없이 DB 조회로 합의. `processed`만으로 부족 → 선점·재시도·상태 구분은 R2·R4 |
| 7 | 설정·의존성 | R4 반영 예정, 7장 표로 전달 |
| 8 | `release_disclosure_index` 전환 | 방향 동의, 상세는 mcp_rag.md |
| 9 | `purge_disclosure_text` | **결정됨** (코드가 맞음, 메모리 색인 전환 때 정리) |

## 10. 한계 (문서·화면에 명시)

- 하루 1회(09:05) 수집이라 장중에 나온 공시는 다음 날 아침에야 반영된다 (설계서에서 허용).
- DART 호출 한도와 제출 후 재무 반영 시차는 아직 모른다 (`data_sources.md` 참고).
- 과거 기준 시점에 공개됐는지 확인할 수 없는 **최신 API 값은 소급 평가에 쓰지 않는다.**
