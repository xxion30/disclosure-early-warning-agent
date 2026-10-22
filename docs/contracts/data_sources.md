# 데이터 출처 계약

> 담당: **R3** · 상태: 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. 원문 저장(`rag_docs/companies`)은 폐지되고 DART 수집기(`collector/`)가 대신한다. DARTCH의 출처표는 이 문서에 추가한다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.

## 7. 데이터

- `data/demo_companies.json`: 데모 회사 5개, 사건, **기대 결과**(평가 정답).
- `data/seed/customers.json`: PB 2명, 고객 20명, 보유 종목. 실제 사람과 무관한 가짜 데이터다.
- `data/disclosures/<종목코드>.json`: `scripts/data/fetch_dart.py`가 OpenDART에서 받아 만든다. **공시 내용과 접수번호를 직접 지어내지 않는다.**
- `rag_docs/companies/<종목코드>_<회사명>/<YYYYMMDD>_<공시종류>.md`: 공시 원문. 파일 맨 위에 아래 머리말을 둔다.

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
