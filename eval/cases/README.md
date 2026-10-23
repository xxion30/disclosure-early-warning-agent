# 테스트 사례 세트 (공동 폴더: R3·R5, 합격 기준은 R1)

`(종목, 기준일, 기대 단계, 기대 근거 공시/항목)` 형식으로 단계(🔴🟠🟡)별 **실제 DART 과거 사례**와 위험이 없는 정상 사례를 모은다.
공시 내용과 접수번호를 지어내지 않는다. 파일 이름에 담당(`r3_`, `r5_`)을 붙여 서로의 파일을 고치지 않는다.

## R3 사례: `r3_cases.json` (44개)

접수번호·제목·접수일은 모두 OpenDART `list.json`에서 확인한 실제 값이다. 다시 확인하려면:

```bash
uv run python scripts/data/verify_cases.py              # 전부 (DART_API_KEY 필요, 읽기 전용, 약 10초)
uv run python scripts/data/verify_cases.py r3-red-001   # 하나만
uv run pytest tests/collector/test_eval_cases.py -q     # 파일 형식·기대값 일관성 (오프라인)
```

### 그룹
| `group` | 개수 | 뜻 | `expected_stage` |
| --- | --- | --- | --- |
| `RED_EVENT` | 16 | 🔴 사건 공시 한 건. 설계서: 한 가지만 있어도 경보 | 모두 `RED` (제목이 모호한 4건은 `ambiguity`에 이유) |
| `NOT_RED` | 14 | 🔴이 아니어야 하는 공시(해제·기간 변경·후속·정정·풍문·자진 상장폐지 등) | `NONE` / `WATCH` / 미정(`null`) |
| `RESOLUTION` | 2 | 해소 근거 공시(상장유지결정, 정정이 철회) | 지정하지 않음. `expected_resolved` 참고 |
| `ORANGE` | 6 | 🟠 공시. 점수는 R4의 `rules.yaml`이 계산 | `null` (`facts`에 항목 API의 실제 값) |
| `YELLOW` | 2 | 감사의견 의견거절 등 🟡 | `YELLOW` (`facts`에 재무 지표의 실제 값) |
| `NORMAL` | 2 | 경보가 없어야 하는 회사(삼성전자·SK하이닉스 2024사업연도) | `expected_alert: false` |
| `TIMELINE` | 2 | 오스템 횡령(2022), 위니아 부도·회생(2023~24) 시간순. 단계마다 `expected_new_alert` | 단계별 `expected_stage_after` |

### 필드
- `scope`: `single_filing`(공시 한 건만 보았을 때) / `company_year`(그 회사의 12개월) / `timeline`.
- `expected_stage`: `RED|ORANGE|YELLOW|COMPOSITE|WATCH|NONE` 또는 `null`. **`null`이면 아직 정해지지 않은 것**이며 이유가 반드시 있다(`ambiguity` 또는 `expectation_source`의 "규칙 계산 필요").
- `expectation_source`: 기대값의 근거 — `R1 결정(PR #31 코멘트)` / `설계서` / `R3 해석 — R1 확인 필요` / `규칙 계산 필요`.
- `ambiguity`: **R1·R4가 정해 줘야 하는 판단**과 그 이유. 사례 중 13개에 있다.
- `evidence[]`: `rcept_no`, `filed_at`, `title`, `item`(`rules.yaml` 위험 항목 이름), `role`(`trigger|context|correction|follow_up|resolution`), `url`.
- `facts` / `facts_meta`: 항목 API(또는 재무 지표)의 실제 값. 값이 없으면 `null`이고 `facts_meta.reason`에 이유(0으로 채우지 않음). **항목 API는 정정 체인의 최신본만 주므로 `facts_meta.rcept_no`가 `evidence`의 접수번호와 다를 수 있다.**
- `legacy`: 옛 프로젝트(Low~Critical 등급)의 기대값. 새 단계와 다르다는 것을 보이려고 출처와 함께 남겼다.
- `as_of`: 판단 시점 = 접수일(`rcept_dt`). 접수번호 앞 8자리가 접수일과 다를 수 있다(주말 등).

### R1·R4가 정해 줘야 하는 것 (`ambiguity`가 있는 사례)
| 사례 | 질문 |
| --- | --- |
| `r3-red-012`, `r3-red-013` | `주권매매거래정지기간변경(상장폐지사유발생)`은 "기간 변경은 🔴 제외"인가 "사유 발생은 🔴"인가? 전 종목 504건이 이 형태 |
| `r3-red-014`, `r3-red-015` | "관리종목 지정"의 실제 제목은 `…지정사유발생`(46건)·`내부결산시점관리종목지정ㆍ…사유발생`(194건)이다. 이것을 🔴로 볼지 |
| `r3-nonred-005` | `매매거래정지및정지해제(풍문등조회공시)`(정지와 해제가 한 건)는 관찰 중인가 제외인가? |
| `r3-nonred-011` | 오스템의 자진 상장폐지(`상장폐지결정`)는 위험인가? |
| `r3-nonred-012` | `투자유의안내(상장폐지 기준 해당)`는 상장폐지 사유 발생 고지인가? |
| `r3-nonred-013` | `횡령ㆍ배임사실확인`(92건)은 신규인가 후속인가? |
| `r3-nonred-014` | 자회사 횡령(`…(자회사의주요경영사항)`)을 본사 🔴로 볼지. 설계서는 `(종속회사의…)`라는 다른 단어를 쓴다 |
| `r3-resolved-001`, `r3-timeline-001` | 상장유지결정으로 거래정지가 풀린 뒤 횡령ㆍ배임 위험은 남는가? |
| `r3-resolved-002` | 정정 내용이 철회인 공시를 해소로 처리해 단계를 내릴지 |
| `r3-timeline-002` | 같은 회사의 추가 `부도발생` 공시(별도 접수번호·금액)는 새 🔴 사건인가 반복인가? |
| `r3-nonred-004` | R1 요청: 위니아 풍문 정지가 옛 시스템에서는 Critical이었다. 새 규칙에서 `WATCH`가 되는지 확인 |

### 찾지 못한 것
- **감사의견 한정·부적정**의 실제 사례를 찾지 못했다(의견거절만 확인). 한정·부적정 사례를 찾으려면 회사별로 감사의견 API를 훑어야 해서 이번에는 하지 않았다. `TODO`
- **🔴 항목 중 영업정지·해산사유·관리절차개시**는 1~2건씩만 골랐다(전 종목에는 각각 229·93·47건이 있다).
- 🟠 6개는 점수 경계 근처 사례가 아니다. 경계 사례는 R4의 점수표 값이 확정된 뒤에 고른다.

## R5 사례
`r5_` 파일은 R5가 추가한다.
