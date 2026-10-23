# collector 계약 (DART 수집기)

> 담당: **R3** · 상태: **초안 — R4 합의 대기.** DB 표(4장)와 환경 변수(7장)는 R4 파일을 바꾸게 하므로, 합의가 끝나기 전에는 코드를 만들지 않는다.
> 근거 문서: [설계서 STEP 03](../dartch_phase1_mvp.md), [데이터 출처표](data_sources.md), [파일 구성 계획](../dartch_file_plan.md).
> `확인됨`은 2026-10-23 OpenDART 실호출로 본 것이고, `제안`은 제가 정한 안이며, `TODO(확인 필요)`는 모르는 것이다.

## 1. 하는 일과 경계

| 하는 일 | 하지 않는 일 |
| --- | --- |
| 보유 종목 합집합을 읽어 DART 공시 목록·구조화 값·재무제표를 받아 DB 표에 넣는다 | 위험 점수·단계 계산 (R4 `risk_engine`) |
| 재무 지표 4종 계산 (자본잠식, 영업현금흐름, 이자보상배율, 단기채무 비율) | 위험 항목 분류와 AI 플래그 추출 (R2 `agent`) |
| 수집 이력·증분 커서 기록, 실패 재시도 | 공시 원문 저장 (**DB·디스크 어디에도 남기지 않는다**) |
| 매일 09:05 (Asia/Seoul) 자동 실행, 수동 실행 진입점 | 화면·API (R4·R5). 수집 결과는 DB 표로만 전달한다 |

- 의존: `collector` → `shared`만 import한다. `agent`·`risk_engine`·`api`를 import하지 않는다. `mcp_servers/rag`가 `collector.dart_client`를 부르는 것은 허용된다.
- 수집기와 규칙 엔진은 **DB 표로만** 만난다. 재무 지표 값은 수집기가 계산해 넣고, 점수는 R4가 그 값을 읽어 계산한다.

## 2. 모듈 (파일 구성 계획과 같다)

| 파일 | 책임 |
| --- | --- |
| `dart_client.py` | DART 호출 (`httpx` async, 재시도, 호출 속도 제한). 지금 `scripts/data/fetch_dart.py`와 `mcp_servers/rag/dart_source.py`에 따로 있는 호출 코드를 합친다. 원문 받기(`fetch_document`)도 여기 둔다 |
| `watchlist.py` | 보유 종목 합집합 읽기, ETF 제외, 종목코드 → `corp_code` 변환 |
| `cursor.py` | 종목별 증분 커서 읽기·쓰기 |
| `jobs.py` | 종목 1개 수집 작업 (목록 → 항목 API → 재무제표 → 저장) |
| `backfill.py` | 새 종목의 최근 12개월 + 최신 정기보고서 채우기 |
| `indicators.py` | 재무 지표 계산 |
| `repo.py` | DB 저장 (idempotent upsert) |
| `scheduler.py`, `__main__.py` | APScheduler 09:05, `python -m collector` 진입점 (수동 실행·1회 실행) |

## 3. 수집 흐름

```
09:05  collect_run 시작 (collect_runs 한 행)
 ① watchlist: holdings의 stock_code 합집합 → ETF 제외 → corp_code 변환
 ② 종목마다(병렬, 호출 속도 제한 안에서):
     cursor 없음 → backfill (최근 12개월 list + 최신 정기보고서)
     cursor 있음 → list.json(corp_code, 커서 이후)
     신규 접수번호마다: disclosures에 upsert (본문 없음)
        제목이 "주요사항보고서(...)" → 해당 항목 API 호출 → rcept_no로 행 고름 → 구조화 값 저장
        제목이 거래소 공시 → 제목·접수번호·링크만 저장 (원문은 나중에 rag가 읽음)
        새 정기보고서 → 재무제표 API → indicators → financial_snapshots
     성공하면 cursor 갱신
 ③ collect_run 종료 (건수·실패·상태 기록)
```

### 3-1. ETF·비상장 제외 (확인됨)
- 종목코드가 `corpCode.xml`의 `stock_code` 목록에 없으면 수집 대상이 아니다. 시드 보유 종목 8개 중 `069500`(KODEX 200)만 `corpCode.xml`에 없었다. 나머지 7개는 모두 `corp_code`가 나온다.
- 제외한 종목은 `skipped`로 세어 `collect_runs`에 남긴다 (조용히 버리지 않는다).
- `corpCode.xml`은 약 12만 건(종목코드 있는 회사 4,001개)이고 하루 1회 내려받아 캐시한다. 갱신 주기는 `TODO(확인 필요)`.

### 3-2. 증분 (제안)
- `list.json`의 `rcept_dt`는 **날짜까지만** 있다(확인됨). 그래서 커서를 시각이 아니라 `last_rcept_dt`(날짜)로 두고, **조회 시작일을 커서 하루 전으로 겹쳐서** 받은 뒤 `rcept_no` 기본키로 중복을 버린다. 시각 단위로 자르면 같은 날 늦게 접수된 건을 놓칠 수 있다.
- 주말·휴일·실패로 건너뛴 날은 커서가 그대로라 다음 실행이 자동으로 이어 받는다.
- **발견은 `list.json`으로 한다.** 주요사항 API는 기간 필터를 믿을 수 없다(`end_de` 밖의 건이 옴, 확인됨). 항목 API 행은 `rcept_no`로 맞춰서 고른다.
- 정정 공시는 새 접수번호의 별도 행이다(확인됨). 수집기는 정정도 그냥 한 건으로 저장하고, 원본과 짝짓는 규칙은 `TODO(확인 필요)`(R2·R4와 합의, 제목의 `[기재정정]` 접두 + 같은 `(corp_code, 항목)`이 후보).

### 3-3. 백필 (제안)
- 새 종목은 `list.json`을 최근 12개월 한 구간으로 받는다. `corp_code`를 지정하면 16개월을 한 번에 조회할 수 있었다(금양 2024-01~2025-04, 페이지 넘김 사용, 확인됨). 옛 코드의 89일 쪼개기는 `corp_code`가 없을 때의 제한에서 온 것으로 보이며 `TODO(확인 필요)`.
- 최신 정기보고서는 `list.json`에서 `사업보고서 (YYYY.MM)` 제목으로 접수번호를 찾는다. 옛 `compute_base_score.py`의 사업연도 추정(3/31 기준)은 쓰지 않는다.
- 백필이 끝나야 `backfilled_at`을 기록한다. 중간에 실패하면 다음 실행이 처음부터 다시 한다(upsert라 중복되지 않는다).

### 3-4. 보유 종료
수집을 멈추되 이력과 저장된 공시는 지우지 않는다(설계서). 보유 종목에서 빠진 종목의 커서는 `active=false`로 둔다.

## 4. DB 표 — 제안 (R4와 합의 후 `shared/models/collect.py` 작성, 마이그레이션은 R4)

설계서의 신규 표는 `financial_snapshots`, `collect_runs` 두 개다. 종목마다 커서가 있어야 새 종목 백필과 일부 종목 실패 후 재개가 되는데, `collect_runs`(실행 단위)로는 담을 수 없어서 **`collect_cursors`를 하나 더 제안한다.** 설계서에 없는 표이므로 R1·R4 승인이 필요하다.

### 4-1. `collect_runs` (실행 이력)
| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| run_id | PK | |
| trigger | str | `scheduled` / `manual` / `backfill` |
| started_at, finished_at | ISO 문자열 | 날짜 규칙은 db.md와 같다 |
| status | str | `RUNNING` / `SUCCESS` / `PARTIAL` / `FAILED` |
| target_count | int | 대상 종목 수 |
| skipped_count | int | ETF 등 제외 종목 수 |
| ok_count, fail_count | int | 종목 단위 |
| new_disclosures | int | 새로 저장한 공시 수 |
| api_calls | int | 한도 점검용 |
| errors | JSON | `[{stock_code, status, message}]`. 키·원문은 넣지 않는다 |

`last_success_at`은 화면의 "마지막 수집 시각"용이다. `status in (SUCCESS, PARTIAL)`인 가장 최근 `finished_at`으로 구하면 별도 컬럼이 필요 없다 (설계서의 "마지막 성공 시각" 컬럼과 다르므로 R4 확인).

### 4-2. `collect_cursors` (종목별 증분 커서, 신규 제안)
| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| stock_code | PK | |
| corp_code | str | |
| last_rcept_dt | str \| null | 마지막으로 처리한 접수일 (날짜). null이면 아직 백필 전 |
| backfilled_at | str \| null | 백필 완료 시각 |
| last_success_at | str \| null | 이 종목의 마지막 성공 수집 |
| last_error | str \| null | 마지막 실패 요약 |
| active | bool | 보유 중 여부 |

### 4-3. `financial_snapshots` (정기보고서별 재무 지표)
| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| snapshot_id | PK | |
| corp_code, bsns_year, reprt_code, fs_div | | 보고서 식별 (`11011` 사업 / `11012` 반기 / `11013` 1분기 / `11014` 3분기, `CFS`/`OFS`) |
| rcept_no | str | **출처 접수번호.** 재무제표 API 행마다 있어서 그대로 남긴다(확인됨) |
| fetched_at | ISO | 우리가 가져온 시각 |
| first_seen_at | ISO | 이 접수번호를 처음 본 시각 (제출~반영 시차 측정용) |
| total_equity, paid_in_capital | int \| null | 자본총계, 자본금 (`BS` 행만 사용, `SCE` 행 제외) |
| capital_impairment_ratio | float \| null | 자본잠식률 (5-1) |
| operating_cf, operating_cf_prev | int \| null | 영업활동현금흐름 당기·전기 (같은 응답의 `thstrm`/`frmtrm` 열) |
| operating_income | int \| null | 영업이익 |
| interest_expense | int \| null | 이자비용. **계정명을 못 찾으면 null** |
| interest_coverage | float \| null | 영업이익 ÷ 이자비용 |
| cash_and_equivalents | int \| null | 현금및현금성자산 |
| short_term_debt | int \| null | 단기채무 합계 (5-1) |
| short_debt_to_cash | float \| null | 단기채무 ÷ 현금성자산 |
| inputs | JSON | 지표마다 쓴 계정 `{지표: {account_id 또는 account_nm, 값}}` — 계산을 추적할 수 있게 |
| missing | JSON | 계산하지 못한 지표와 이유 `{interest_coverage: "이자비용 계정 없음"}` |

- 유일 키: `(corp_code, bsns_year, reprt_code, rcept_no)`. 정정 보고서는 새 `rcept_no`로 쌓이고, 규칙 엔진은 같은 `(bsns_year, reprt_code)`에서 **접수번호가 가장 큰 행**을 최신으로 읽는다 (접수번호는 날짜순 — 같은 날 접수 순서는 `TODO(확인 필요)`).
- 값이 없으면 **null이지 0이 아니다.** 규칙 엔진이 "계산 못 함"과 "0"을 구분해야 한다. 이 규칙은 R4와 함께 `docs/contracts/risk_rules.md`에 반영한다.

### 4-4. 기존 표 사용
- `disclosures`(R4 모델): `rcept_no corp_code title filed_at url`을 채운다. `body_text`는 **항상 비워 둔다.** `processed=false`로 넣고, 분석이 끝나면 에이전트 쪽이 `true`로 바꾼다. 분석 시작 신호는 별도 큐 없이 `processed=false` 조회(`list_new_disclosures`)로 한다 — 신호 방식은 R2·R4가 정한다.
- `companies`: `corp_code stock_code name market`을 채운다 (`base_score`는 새 설계에서 쓰지 않음, R4 확인).
- **구조화 값(항목 API 응답)을 어디에 둘지는 미정이다.** 에이전트가 "부도금액", "증자방식" 같은 값을 쓰려면 DB 어딘가에 있어야 한다. 후보: `disclosure_facts(rcept_no, item_api, fields JSON, fetched_at)` 표 신설. 원문 텍스트가 아니라 API가 준 필드 값만 담는다. `TODO(확인 필요)` — R2·R4와 합의. 이게 정해져야 `jobs.py`의 저장 단계를 만들 수 있다.

## 5. 재무 지표 정의 (제안 — 정의는 R1 확인)

점수 구간(설계서 🟡 표)은 R4가 `rules.yaml`로 적용한다. 수집기는 **값만** 계산한다.

### 5-1. 정의
| 지표 | 계산 | 메모 |
| --- | --- | --- |
| 자본잠식률 | `(자본금 - 자본총계) ÷ 자본금 × 100`, 음수면 0 | 50% 이상, 자본총계 ≤ 0(전액)을 R4가 구분. 금양 2024: 자본총계 4,945억 > 자본금 320억이라 잠식 아님(확인됨) |
| 영업현금흐름 | 당기·전기 값 그대로 | "2년 연속 적자"는 같은 응답의 당기·전기 열로 판단 (금양 2024 당기 -203억, 확인됨. 전기 열 값은 이번에 보지 않음) |
| 이자보상배율 | `영업이익 ÷ 이자비용` | **이자비용 계정을 금양 `CIS`에서 `이자비용`·`금융비용` 이름으로 찾지 못했다.** 계정 확인 전에는 null. `TODO(확인 필요)` |
| 단기채무 비율 | `단기채무 ÷ 현금및현금성자산` | "단기채무"의 범위(단기차입금 + 유동성장기부채 + CP·단기사채·회사채 1년 이내 구간)는 정하지 않았다. `단기차입금`은 표준계정코드가 없어 이름으로 찾아야 한다(확인됨). `TODO(확인 필요)` (R1) |

### 5-2. 계정 찾기
1. `account_id`가 있으면 그것으로(`ifrs-full_Equity`, `ifrs-full_IssuedCapital`, `ifrs-full_CashAndCashEquivalents`, `ifrs-full_CashFlowsFromUsedInOperatingActivities`, `dart_OperatingIncomeLoss`).
2. 없으면 `account_nm` 일치. 연결(`CFS`)을 먼저 받고 없으면 별도(`OFS`).
3. 같은 계정이 여러 행이면(`SCE`에 자본총계 여러 행) `sj_div`로 거른다 (`BS`).
4. 숫자는 쉼표 문자열이다. 파싱 못 하면 null.

## 6. 오류·재시도

| 상황 | 처리 |
| --- | --- |
| `013` 조회된 데이터 없음 | 정상. 빈 결과 |
| `100` 부적절한 값 | 재시도하지 않는다. 버그로 기록 |
| 호출 한도 초과(`020`으로 알려짐, 이번에 못 일으킴) | 대기 후 재시도, 계속되면 남은 종목을 `PARTIAL`로 끝내고 `collect_runs.errors`에 기록. 커서는 성공한 종목만 갱신 |
| 네트워크 오류·5xx·시간 초과 | `tenacity` 지수 백오프, 최대 횟수 `TODO(확인 필요)` |
| 한 종목 실패 | 그 종목만 실패 처리하고 나머지는 계속 (실행 전체를 멈추지 않는다) |
| 잘못된 키 | 가짜 키로도 `000`이 왔다(원인 모름, 확인됨). 키 오류를 응답으로 감지할 수 있다고 가정하지 않는다. 키가 비어 있으면 시작 때 실패시킨다 |

- 응답 로그에 `crtfc_key`와 원문을 남기지 않는다. 오류 메시지에서 URL의 쿼리 문자열을 지운다.
- 재시도 규칙(LLM 쪽 Harness)과는 별개다. 수집기 자체 재시도이며 `harness/recovery_tools.py`(R4)를 import하지 않는다.

## 7. 설정 — R4에게 요청 (`shared/config.py`, `.env.example`, `pyproject.toml`)

| 항목 | 제안 값 | 비고 |
| --- | --- | --- |
| `DART_API_KEY` | (있음) | |
| `COLLECT_TIME` | `09:05` | Asia/Seoul 고정 |
| `COLLECT_ENABLED` | `false` 기본 | 로컬·테스트에서 스케줄러가 켜지지 않게 |
| `DART_MAX_RPS` / `DART_MAX_CONCURRENCY` | `TODO(확인 필요)` | 한도를 모르는 채로 값을 정하지 않는다 |
| 의존성 | `httpx`(지금 dev 그룹 → 일반), `tenacity`, `apscheduler` | |
| DB | `DB_URL` 그대로 | 수집기는 SQLAlchemy 세션만 쓴다. PostgreSQL 전환은 R4 |

## 8. 테스트 (`tests/collector/`)

DART 호출은 모두 가짜 응답으로 한다 (키·네트워크 불필요). 사례 응답은 실제 관측 형태를 줄여 쓰되 접수번호는 실제 공시의 것만 쓴다.

- 증분: 커서 하루 전 겹침 + 중복 접수번호 무시, 주말 건너뛰기 후 이어 받기
- 백필: 새 종목, 중간 실패 후 재실행이 중복을 만들지 않음
- ETF 제외(`069500`) 및 `skipped` 집계
- 주요사항 API가 기간 밖 행을 섞어 줘도 `rcept_no`로 맞는 행만 고름
- `013`은 빈 결과, 한도 초과·5xx는 재시도, 한 종목 실패가 다른 종목을 막지 않음
- 지표: 자본잠식, 영업현금흐름 당기·전기, 계정 없음 → null, 정정 보고서는 새 행
- 키·원문이 로그와 DB에 남지 않음

## 9. 합의가 필요한 것

| # | 내용 | 누구와 | 막히는 일 |
| --- | --- | --- | --- |
| 1 | `collect_cursors` 신설 여부, `collect_runs.last_success_at` 대신 계산 | R1·R4 | `shared/models/collect.py`, 증분 |
| 2 | 구조화 값 저장 위치(`disclosure_facts` 등) | R2·R4 | `jobs.py` 저장 단계, 에이전트 입력 |
| 3 | 정정 공시를 원본과 짝짓는 규칙 | R2·R4 | 해소·반복 판정 |
| 4 | 거래소 공시 경보 대상 판정(거래정지·상장폐지 관련 구분) | R1 | 🔴 감지 규칙 ([출처표](data_sources.md) 참고) |
| 5 | 단기채무 범위, 이자비용 계정, 자본잠식률 정의 | R1 | `indicators.py` |
| 6 | 분석 시작 신호 방식(`processed=false` 조회 vs 큐) | R2·R4 | 수집 → 판정 연결 |
| 7 | 설정·의존성 추가 | R4 | 수집기 실행 |

## 10. 한계 (문서·화면에 명시)

- 하루 1회(09:05) 수집이라 장중에 나온 공시는 다음 날 아침에야 반영된다 (설계서에서 허용).
- DART 호출 한도와 제출 후 재무 반영 시차는 아직 모른다 (`data_sources.md` 참고).
