# 데이터 출처 계약

> 담당: **R3** · 상태: 맨 위 "DARTCH 데이터 출처표"는 2026-10-23 새로 쓴 것이고, `## 7. 데이터` 이하는 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. 원문 저장(`rag_docs/companies`)은 폐지되고 DART 수집기(`collector/`)가 대신한다. DARTCH의 출처표는 이 문서에 추가한다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.

## DARTCH 데이터 출처표 (R3, 2026-10-23 OpenDART 실호출 확인)

> 2026-10-23 R1·R2·R4 검토 반영: 거래소 공시 경보 대상(R1 결정), 정정 ↔ 원본 연결 근거, 이자비용 계정 후보, 항목 API 행 누락을 추가했다.
> 확인 방법: 공식 키로 읽기 전용 호출. 대상은 금양(00106119)·위니아(00300557)·오스템임플란트(00341916)·CJ CGV(00303873)의 과거 기간과 삼성전자(00126380) 2023·2024 정기보고서다.
> **확인한 것**은 아래에 필드명을 적었고, 데이터가 없어 응답만 확인한 것은 `필드 미확인`으로 표시했다. 지어낸 값은 없다. 확인 못 한 칸은 `TODO(확인 필요)`.

### 공통 사실 (확인됨)
| 항목 | 확인 결과 |
| --- | --- |
| 응답 상태 | `status` `000` 정상, `013` 조회된 데이터 없음(정상 흐름), `100` 필드 값 부적절(`corp_code="123"`). 수집기는 `013`을 오류가 아니라 빈 결과로 다룬다 |
| 회사 단위 조회 | 주요사항 26종·정기보고서 10종 모두 `corp_code` 단위로 조회된다 (JSON이 아닌 응답은 한 건도 없었다) |
| `list.json` | 필드: `corp_cls corp_code corp_name flr_nm rcept_dt rcept_no report_nm rm stock_code`. `corp_code` 없이도 조회된다(하루·3개월 구간 확인, 더 긴 구간 제한은 `TODO(확인 필요)`). `page_count=100`, `total_page`로 페이지를 넘긴다. `pblntf_ty=I`로 거래소 공시만 가려진다(금양 2024-01~2025-04 59건) |
| `rm`(비고) | 관측한 값은 `유`(유가증권시장), `연`(연결부속), 빈 문자열뿐이다. 정정 여부를 `rm`으로 판단하지 않는다. 그 밖의 값 뜻은 `TODO(확인 필요)` |
| 정정 공시 | **덮어쓰기가 아니라 새 접수번호의 별도 행**이다. 제목 앞에 `[기재정정]`이 붙는다. 예: 금양 유상증자결정은 `list.json`에 `20240927000916`(최초), `20241018000341`(`[기재정정]`), `20241031000530`(제목에 정정 표시 없음), `20250117000380`(`[기재정정]`) 4건이 따로 있다. API(`list.json`·항목 API)는 **어느 원본을 고친 것인지 알려 주지 않는다.** 다만 정정 공시 **본문**에 `정정대상 공시서류`와 `최초제출일`이 있다(아래 "정정 ↔ 원본 연결" 참고) |
| 항목 API 행 누락 | 금양 유상증자결정은 `list.json`에 4건인데 `piicDecsn`은 2건(`20241031000530`, `20250117000380`)만 돌려줬다. 이유는 모른다. **항목 API에 행이 없다고 그 공시의 값이 없다는 뜻이 아니다** → 행이 없으면 `MISSING`으로 저장하고 원문 읽기로 넘긴다 |
| **기간 필터 불신** | 주요사항 API는 `end_de` 밖의 건도 돌려준다. CJ CGV 유상증자 `20230904000025`가 `end_de=20230731` 조회에 나왔다. 그래서 **발견은 `list.json`으로 하고, 주요사항 API 행은 `rcept_no`로 맞춰 고른다** |
| 한 회사 여러 건 | 위니아 부도발생은 2023-10~11에 별도 접수번호 6건(금액이 모두 다름). 개별 공시는 건마다 근거로 쌓고, 단계는 종목당 한 상태로 묶는다(설계서 "반복·해소") |
| 호출 한도 | 한도 초과 응답(`020`으로 알려져 있음)은 **이번에 일으키지 않았다**. 일일 한도 수치와 응답은 `TODO(확인 필요)`. 수집기는 `020`을 만나면 재시도 후 `collect_runs`에 기록한다 |
| 키 검증 | 가짜 키(`x`×40)로 호출했는데 `000`과 데이터가 왔다. 원인은 모른다(키 검증 방식인지 환경 문제인지). **이 동작에 기대지 않는다.** `TODO(확인 필요)` |
| 재무 반영 시차 | 과거 보고서라 제출 직후 몇 시간 뒤 값이 올라왔는지 이번 호출로는 알 수 없다. 수집기가 보고서를 처음 본 시각을 `financial_snapshots`에 남겨 실제 시차를 측정한다. `TODO(확인 필요)` |

### 🔴 즉시 경보
| 위험 항목 | API (`/api/…json`) | 확인된 필드 | 텍스트 해석 필요? | 비고 |
| --- | --- | --- | --- | --- |
| 부도발생 | `dfOcr` | `dfd df_amt df_bnk df_cn df_rs rcept_no corp_code corp_name corp_cls` | 아니오(발생 사실이 곧 경보). `dfd`가 `-`로 오는 건이 있다 | 위니아 6건 확인 |
| 회생절차 개시신청 | `ctrcvsBgrq` | `apcnt cpct ft_ctp_sc rq_rs rqd rcept_no …` | 아니오 | 위니아 1건 확인 |
| 영업정지 | `bsnSp` | 필드 미확인(자료 없음) | 정지 기간·매출 비중은 필드 확인 뒤 `TODO(확인 필요)` | 엔드포인트 응답만 확인 |
| 해산사유 발생 | `dsRsOcr` | 필드 미확인 | `TODO(확인 필요)` | 엔드포인트 응답만 확인 |
| 채권은행 등의 관리절차 개시 | `bnkMngtPcbg` | 필드 미확인 | `TODO(확인 필요)` | 엔드포인트 응답만 확인 |
| 거래정지 · 관리종목 지정 · 국내 상장폐지 사유 · 횡령·배임 | **정리된 값 API 없음** → `list.json` 제목 감지 + 원문 읽기 | `report_nm rcept_no rcept_dt flr_nm` | 예(제외 규칙 판단) | 아래 "거래소 공시 제목 패턴" 참고 |

#### 거래소 공시 제목 패턴 (실제 목록에서 관측) — R1 결정(2026-10-23) 반영
단순 키워드 매칭은 오탐이 많다. 아래는 관측한 제목이다. **R1 결정:** 새로 위험이 생긴 사건만 🔴이고, 후속 공시는 같은 위험 상태의 타임라인에만 붙는다 (금액 기준은 Phase 2). 제목 규칙의 정확한 문구는 R3가 후보로 만들고 R4가 `rules.yaml`에 반영한다.

| 구분 | 관측한 제목 (제출자) | R1 결정 | 메모 |
| --- | --- | --- | --- |
| 횡령·배임 **신규** | `횡령ㆍ배임혐의발생`, `[기재정정]횡령ㆍ배임혐의발생` (회사 본인) | **🔴** (최초 공시만) | 제목이 `횡령ㆍ배임혐의발생`으로 **시작**할 때만 신규 후보. 가운뎃점은 `ㆍ`(U+318D). `[기재정정]`은 새 사건이 아니라 같은 사건의 정정(3-4 참고) |
| 횡령·배임 **후속** | `기타경영사항(자율공시)(횡령ㆍ배임 혐의 발생 이후 횡령금액 회수 진행사항)`, `기타시장안내(횡령 혐의 진행사항)` | 타임라인만 | 옛 `fetch_dart.py`의 `횡령[ㆍ·]?배임`과 R4의 `phase1_classification`(`EMBEZZLEMENT: 횡령ㆍ배임, …`)은 제목에 `횡령ㆍ배임`이 들어 있으면 매칭하는 방식이라 **이 후속 건까지 신규로 잡을 수 있다**(코드를 돌려 확인한 것은 아니다) → R4 규칙 반영 때 `횡령ㆍ배임혐의발생`으로 시작하는 제목만 신규로 하도록 확인 |
| 거래정지 **새 정지** | `주권매매거래정지(상장적격성 실질심사 대상 (사유발생))` | **🔴** (새 정지만) | |
| 거래정지 **풍문·보도 조회** | `주권매매거래정지(풍문 또는 보도 관련)` | 🔴 제외, **"관찰 중"** | 위니아가 이 제목(2023-10-04)으로 이전 프로젝트에서 Critical이 나왔다. `eval/cases` 정답 사례를 만들 때 새 규칙에서 달라지는지 확인한다 (R1 요청) |
| 거래정지 **제외** | `주권매매거래정지해제(회생절차개시결정)`, `주권매매거래정지기간변경(…)`, `매매거래정지및정지해제(불성실공시)` | 🔴 제외 | 해제·기간 변경·불성실공시 당일 정지. `거래정지`가 들어 있어도 새 정지가 아니다 |
| 거래정지 **판정 모호** | `매매거래정지및정지해제(풍문등조회공시)` | `TODO(확인 필요)` (R1) | 풍문 조회이지만 정지와 해제가 한 건에 같이 있다. "풍문 조회 정지는 관찰 중" 규칙에 넣을지 제외할지 R1 확인 |
| 관리종목 | `내부결산시점관리종목지정ㆍ형식적상장폐지ㆍ상장적격성실질심사사유발생` (회사 본인) | `TODO(확인 필요)` | `관리종목지정`이라는 단독 제목은 관측하지 못했다. R1 결정문에는 관리종목이 없다 |
| 상장폐지 **사유 발생** | `주권매매거래정지기간변경 (상장폐지 사유 발생)` | **🔴** ("사유 발생"만) | 같은 사유가 `…기간변경`·`기타시장안내` 등 여러 제목으로 반복될 수 있어 신규/후속 구분은 제목만으로 어렵다 `TODO(확인 필요)` |
| 상장폐지 **후속** | `기타시장안내 (상장폐지 이의신청서 제출)`, `기타시장안내 (상장폐지 관련 이의신청서 접수 및 개선기간 부여)`, `투자유의안내 (상장폐지 기준 해당)`, `기타시장안내 (상장폐지 관련)` | 후속 → 타임라인만 | 이의신청·개선기간 |
| 경보 아님(참고) | `불성실공시법인지정(예고)`, `조회공시요구(외부감사의견)` | — | 현재 🔴 목록에 없음 |

### 🟠 내용 분석 후 경보 — 주요사항보고서 (`corp_code` + 기간, `rcept_no`로 맞춤)
| 위험 항목 | API | 확인된 필드 | 텍스트 해석 필요? |
| --- | --- | --- | --- |
| 유상증자 결정 | `piicDecsn` | `ic_mthn`(증자방식) `nstk_ostk_cnt nstk_estk_cnt`(신주 수) `bfic_tisstk_ostk bfic_tisstk_estk`(증자 전 주식 수) `ssl_at ssl_bgd ssl_edd fdpp_*`(자금 사용 목적) `fv_ps` | 아니오(비율 계산 가능). 정정 건은 수량 필드가 `-`로 올 수 있다 |
| 회사분할 결정 | `cmpDvDecsn` | `dv_rt dv_impef dvdt dvfcmp_* atdvfdtl_* ffdtl_*`(분할 전후 재무) `popt_ctr_atn`(풋옵션 계약) 등 | 일부(교환비율 불리 판단은 원문) |
| 자기주식 처분 결정 | `tsstkDpDecsn` | `dp_pp`(처분 목적) `dppln_stk_*` `dppln_prc_*` `dpprpd_bgd dpprpd_edd dp_m_*` 등 | 예(`dp_pp` 긴급 자금 여부) |
| 유무상증자 결정 | `pifricDecsn` | 필드 미확인 | `TODO(확인 필요)` |
| 무상증자 결정 | `fricDecsn` | 필드 미확인 | 점수표에 없음. 수집 여부 `TODO(확인 필요)` (R1) |
| 감자 결정 | `crDecsn` | 필드 미확인 | 결손 보전 목적은 필드/원문 `TODO(확인 필요)` |
| 소송 등의 제기 | `lwstLg` | 필드 미확인 | 청구액은 필드, 핵심 영업 관련은 원문 `TODO(확인 필요)` |
| 전환사채 발행결정 | `cvbdIsDecsn` | 필드 미확인 | 리픽싱·조기상환 조항은 `TODO(확인 필요)` |
| 신주인수권부사채 / 교환사채 | `bdwtIsDecsn` / `exbdIsDecsn` | 필드 미확인 | 〃 |
| 영업양수 / 영업양도 | `bsnInhDecsn` / `bsnTrfDecsn` | 필드 미확인 | `TODO(확인 필요)` |
| 타법인주식 양수 / 양도 | `otcprStkInvscrInhDecsn` / `otcprStkInvscrTrfDecsn` | 필드 미확인 | `TODO(확인 필요)` |
| 자산양수도(기타)·풋백옵션 | `astInhtrfEtcPtbkOpt` | 필드 미확인 | `TODO(확인 필요)` |
| 회사합병 / 분할합병 / 주식교환·이전 | `cmpMgDecsn` / `cmpDvmgDecsn` / `stkExtrDecsn` | 필드 미확인 | 교환비율 불리 판단은 원문 |
| 자기주식 취득 신탁계약 해지 | `tsstkAqTrctrCcDecsn` | 필드 미확인 | `TODO(확인 필요)` |
| 채권은행 관리절차 중단 | `bnkMngtPcsp` | 필드 미확인 | 정상화/협의 실패 구분은 원문 |
| 해외 상장폐지 결정 | `ovDlstDecsn` | 필드 미확인 | 국내 상장폐지와 구분 |

필드 미확인 항목은 해당 사례가 있는 회사·기간을 찾아 다시 호출해 채운다(`eval/cases/r3_*` 사례 수집과 함께 한다).

### 🟡 재무 추세 — 정기보고서 주요정보 (`corp_code` + `bsns_year` + `reprt_code`)
`reprt_code`: `11011` 사업보고서, `11012` 반기, `11013` 1분기, `11014` 3분기 (재무제표 API로 모두 응답 확인).

| 위험 항목 | API | 확인된 필드 |
| --- | --- | --- |
| 감사의견 | `accnutAdtorNmNdAdtOpinion` | `adtor adt_opinion adt_reprt_spcmnt_matter emphs_matter core_adt_matter bsns_year stlm_dt rcept_no` |
| 단기사채 미상환 잔액 | `srtpdPsndbtNrdmpBlce` | 만기 구간별 `de10_below … de180_excess_yy1_below`, `isu_lmt remndr_lmt sm` |
| 기업어음증권 미상환 잔액 | `cprndNrdmpBlce` | 만기 구간별 `yy1_below … yy10_excess`, `sm` |
| 회사채 미상환 잔액 | `entrprsBilScritsNrdmpBlce` | 만기 구간별 `de10_below … yy1_excess_yy2_below …`, `sm` |
| 채무증권 발행실적 | `detScritsIsuAcmslt` | `isu_cmpny scrits_knd_nm isu_mth_nm isu_de facvalu_totamt intrt evl_grad_instt repy_at mtd` |
| 공모자금 사용내역 | `pssrpCptalUseDtls` | `se_nm tm pay_de pay_amount on_dclrt_cptal_use_plan real_cptal_use_dtls_cn real_cptal_use_dtls_amount dffrnc_occrrnc_resn` |
| 사모자금 사용내역 | `prvsrpCptalUseDtls` | `cptal_use_plan mtrpt_cptal_use_plan_useprps real_cptal_use_dtls_cn dffrnc_occrrnc_resn` 등 |
| 최대주주 현황 | `hyslrSttus` | `nm relate stock_knd bsis_posesn_stock_co trmend_posesn_stock_co *_qota_rt` |
| 최대주주 변동현황 | `hyslrChgSttus` | `mxmm_shrholdr_nm change_on change_cause posesn_stock_co qota_rt` |
| 타법인 출자현황 | `otrCprInvstmntSttus` | `inv_prm invstmnt_purps frst_acqs_de frst_acqs_amount trmend_blce_* recent_bsns_year_fnnr_sttus_tot_assets` |

- **감사의견 판단**: `adt_opinion`은 구조화된 값이다. 계속기업 불확실성은 별도 필드가 없어 `emphs_matter` 등 텍스트에서 읽어야 한다(`TODO(확인 필요)`: 실제 문구 형태).
- 위 필드의 숫자는 문자열(`"5,917,159"`, `"-"` 포함)로 온다. 쉼표를 지우고 `-`는 값 없음으로 처리해야 한다.

### 🟡 재무제표 — `fnlttSinglAcntAll` (자본잠식·현금흐름·이자보상·단기채무)
- 파라미터: `corp_code bsns_year reprt_code fs_div`(`CFS` 연결 우선, 없으면 `OFS`). 금양 2024 사업보고서 `CFS`로 218행.
- 필드: `rcept_no reprt_code bsns_year corp_code sj_div sj_nm account_id account_nm account_detail thstrm_amount frmtrm_amount bfefrmtrm_amount thstrm_nm frmtrm_nm bfefrmtrm_nm ord currency`. `sj_div`: `BS CF CIS SCE`. **행마다 `rcept_no`가 있어** 지표의 출처 접수번호를 그대로 남길 수 있다.
- 계정 식별: 표준 계정은 `account_id`가 있다(`ifrs-full_Equity`, `ifrs-full_IssuedCapital`, `ifrs-full_CashAndCashEquivalents`, `ifrs-full_CashFlowsFromUsedInOperatingActivities`, `dart_OperatingIncomeLoss`). `단기차입금`은 `-표준계정코드 미사용-`이라 `account_nm`으로 찾아야 한다. **`account_id` 우선, 없으면 `account_nm` 일치**로 한다.
- 자본총계는 `SCE`(자본변동표)에도 여러 행으로 나온다. 지표는 `sj_div="BS"` 행만 쓴다.
- **이자비용(이자보상배율)**: 5개 회사(금양·삼성전자·SK하이닉스·CJ CGV·SK) 2024 사업보고서 연결재무제표 손익계산서에 `이자비용` 계정은 **없었다.** 후보는 (A) `금융비용`/`금융원가`(`ifrs-full_FinanceCosts`, 5곳 모두 있음, 이자 외 손익이 섞여 분모가 커짐)와 (B) 현금흐름표 `이자의 지급`(`ifrs-full_InterestPaidClassifiedAsOperatingActivities`, 5곳 모두 있음, 재무활동 분류 회사는 미확인)이다. **R1 결정(2026-10-23): (A) `금융비용`/`금융원가`를 쓴다.** 계정을 못 찾으면 0점이 아니라 "산정 불가"로 점수에 안 넣는다. 이자 외 손익이 섞여 이자보상배율이 낮게 나오는 한계가 있다. 자세한 내용은 `collector.md` 5-2.
- **R1 결정 반영**: 자본잠식률 = (자본금−자기자본)÷자본금×100, 자기자본 ≤ 0이면 전액(100). 개별(OFS)·연결(CFS) 둘 다 저장. 단기채무 범위는 `collector.md` 5-1.
- 기준일 직전 사업보고서를 찾는 방법: 옛 `compute_base_score.py`의 `reference_year()`는 3/31 기준 추정이다. 새 수집기는 `list.json`의 `사업보고서 (YYYY.MM)` 접수번호로 찾는다.

### 정정 ↔ 원본 연결 (R4 요청: 실제 DART에서 확인할 수 있는 정보)
- `list.json`·항목 API에는 원본 접수번호 필드가 **없다**(`list.json` 필드는 위 "공통 사실"의 9개뿐).
- 정정 공시 **본문**(`document.xml`)에서 읽을 수 있었던 것: 금양 유상증자 정정 `20250117000380` 본문에 `정정대상 공시서류 : 유상증자 결정`, `정정대상 공시서류의 최초제출일 : 2024년 09월 27일`이 있었다. `list.json`에서 2024-09-27에 `20240927000916 주요사항보고서(유상증자결정)`이 실제로 확인된다. 본문에 **접수번호 14자리는 없었다.**
- 연결 규칙 **후보**: `(corp_code, 정정대상 서류명, 최초제출일)` = 원본의 `(corp_code, 서류명, rcept_dt)`.
- **한계 (1건만 확인):** 오스템 횡령·배임 정정 `20220110900349`의 본문(1,161자)에는 "정정" 문구가 없었다. 모든 정정 공시가 이 양식을 따른다고 말할 수 없다. 후보로 찾지 못하면 **연결하지 않는다.**
- 제목에 정정 표시가 없는 후속 재결정(예: 금양 `20241031000530`)은 이 규칙으로 같은 사건인지 알 수 없다.
- **정정이 철회일 수 있다:** 금양 정정(`20250117000380`)의 "정정 후"는 `유상증자 철회`다. 해소 판정의 근거가 될 수 있다(판단은 R4·R2).

### 수집기 설계에 반영할 점
1. 하루 수집은 종목별 `list.json`(`corp_code`+지난 수집 이후)으로 신규 접수번호를 찾고, 항목별 API는 신규 `주요사항보고서(…)` 제목이 있을 때만 호출한다. 보유 종목 수가 작아 종목별 호출이 전체 시장 조회보다 가볍다고 보지만, 실제 건수 비교는 `TODO(확인 필요)`.
2. 주요사항 API 행은 `rcept_no`로 맞춘다(기간 필터를 믿지 않는다).
3. 정정은 새 행이다. 원본과 짝짓는 규칙 후보는 위 "정정 ↔ 원본 연결"이고, 확정(`event_id`)은 R4·R2와 한다.
4. 거래소 공시는 제목 규칙 + 후속/제외 구분이 필요하다. 정책은 R1이 정했고(위 표), 규칙 반영은 R3(후보)·R4(`rules.yaml`)가 한다.
5. 항목 API에 행이 없으면 `MISSING`으로 저장하고 원문 읽기로 넘긴다.

---

## 7. 데이터

- `data/demo_companies.json`: 데모 회사 5개, 사건, **옛 기대 결과**(Low~Critical 등급 기준 평가 정답). 새 단계(🔴🟠🟡) 기준의 테스트 사례는 `eval/cases/r3_*`가 대신하고, 이 파일은 `seed_db`와 R5의 재생 시험이 아직 쓰므로 합의 후 정리한다.
- `data/seed/customers.json`: PB 2명, 고객 20명, 보유 종목. 실제 사람과 무관한 가짜 데이터다.
- `data/disclosures/<종목코드>.json`: 예전에 `fetch_dart.py`(삭제됨)가 OpenDART에서 받아 만든 **공시 목록**(제목·접수번호·접수일)이다. 새 공시는 collector가 수집한다. 원문(`body_text`)은 테스트가 쓰는 6건에만 남기고 나머지는 비웠다. **공시 내용과 접수번호를 직접 지어내지 않는다.**
- `rag_docs/companies/<종목코드>_<회사명>/<YYYYMMDD>_<공시종류>.md`: **테스트용 실제 공시 원문 6건만** 남겼다(예전 139건에서 줄임, `rag_docs/companies/README.md` 참고). 운영에서는 원문을 저장하지 않는다. 파일 맨 위에 아래 머리말을 둔다.

```
---
rcept_no: "20250401000123"
corp_name: 금양
stock_code: "001570"
filed_at: 2025-04-01
title: 감사보고서제출
url: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...
---
```

- `rag_docs/reference/`: `listing_rules.md`(상장규정), `disclosure_guide.md`(공시해설), `accounting_terms.md`(회계용어), `message_guide.md`(안내문가이드).

---
