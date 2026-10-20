version: v2
해당 공시의 검색 조각에서 위험 규칙 판단에 필요한 사실을 추출한다.
facts 각 항목은 signal_type, values, evidence로 구성한다. values에는 비율·금액의 단위를 명시하고, 확인되지 않는 값은 null로 둔다.
points·총점·등급을 만들지 않는다. 근거가 없는 신호를 추가하지 않는다.
evidence에는 검색 결과의 source, rcept_no, location을 그대로 복사하고 quote는 해당 조각의 연속된 원문 문장으로 인용한다.
충분한 사실이 없으면 빈 facts를 반환하여 검증 실패와 수동 확인으로 이어지게 한다.

## signal_type과 values (아래 이름을 대문자 그대로 정확히 쓴다. 목록에 없는 signal_type은 만들지 않는다)
입력의 disclosure_type에 해당하는 항목만 사용한다.

- AUDIT_REPORT → AUDIT_OPINION: values.opinion = "거절" | "부적정" | "한정" | "적정" (감사의견 문구에서). 원문에 자본잠식률이 있으면 CAPITAL_IMPAIRMENT도 추가: values.impairment_pct = 숫자(전액 잠식이면 100)
- EMBEZZLEMENT → EMBEZZLEMENT: values.ratio_to_equity_pct = 자기자본 대비 횡령·배임 금액 비율(%). 원문에 없으면 null
- TRADING_HALT → TRADING_HALT: values.lifted = 거래정지 "해제" 공시이면 true, 아니면 false
- DELISTING → DELISTING: values.lifted = 폐지 사유 해소·취소 공시이면 true, 아니면 false
- ADMIN_ISSUE → ADMIN_ISSUE: values는 비워도 된다
- AUDITOR_CHANGE → AUDITOR_CHANGE: values.periodic_designation = 주기적 지정제에 따른 교체이면 true, 아니면 false
- FILING_DELAY → FILING_DELAY: values는 비워도 된다
- GOOD_NEWS_CANCEL → GOOD_NEWS_CANCEL: values는 비워도 된다
- CB_BW → CB_BW: values.refixing = 전환가액·행사가액 리픽싱(조정) 조항이 있으면 true, 없으면 false
- RIGHTS_OFFERING → RIGHTS_OFFERING: values.ratio_pct = 신주 발행 주식 수 ÷ 증자 전 주식 수 × 100, values.third_party = 제3자배정이면 true, 아니면 false
- OWNERSHIP → 원문에 최대주주의 주식 담보 제공이 있을 때만 PLEDGE: values.pledge_ratio_pct = 담보 주식 ÷ 최대주주 보유 주식 × 100. 담보 내용이 없으면 빈 facts
- 그 밖의 유형은 빈 facts

values 값은 숫자·true/false·문자열 그대로 쓰고, 단위나 설명 문구를 붙이지 않는다(예: 91.81, "거절").
