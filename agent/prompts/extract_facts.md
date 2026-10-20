version: v7
해당 공시의 검색 조각에서 위험 규칙 판단에 필요한 사실을 추출한다.
facts 각 항목은 signal_type, values, evidence로 구성한다. values에는 비율·금액의 단위를 명시하고, 확인되지 않는 값은 null로 둔다.
points·총점·등급을 만들지 않는다. 근거가 없는 신호를 추가하지 않는다.
evidence에는 검색 결과의 source, rcept_no, location을 그대로 복사하고 quote는 해당 조각에서 **한 곳을 그대로 복사한** 100자 이내의 연속된 원문 문장으로 쓴다. 떨어진 문장을 이어 붙이거나 줄이거나 바꿔 쓰지 않는다.
해당 유형에서 찾을 사실이 없으면 빈 facts를 반환한다. 대량보유(OWNERSHIP) 공시는 신호가 없는 것이 정상이라 그대로 종료되고, 그 밖의 유형은 근거를 못 찾은 것으로 보고 수동 확인으로 이어진다. 추측해서 채우지 않는다.

signal_type은 아래 목록의 이름만 쓴다. disclosure_type 이름(OWNERSHIP, AUDIT_REPORT 등)은 signal_type이 아니다.

## signal_type과 values (아래 이름을 대문자 그대로 정확히 쓴다. 목록에 없는 signal_type은 만들지 않는다)
입력의 disclosure_type에 해당하는 항목만 사용한다.

- AUDIT_REPORT → AUDIT_OPINION: values.opinion = "거절" | "부적정" | "한정" | "적정" (감사의견 문구에서). 원문에 자본잠식률이 있으면 CAPITAL_IMPAIRMENT도 추가: values.impairment_pct = 숫자(전액 잠식이면 100)
- EMBEZZLEMENT → EMBEZZLEMENT: values.ratio_to_equity_pct = 자기자본 대비 횡령·배임 금액 비율(%). 원문에 없으면 null
- TRADING_HALT → TRADING_HALT: values.reason = 공시에 적힌 정지 사유 문구 그대로(예: 분할, 상장적격성 실질심사 대상, 풍문 또는 보도 관련). values.lifted = 거래정지가 해제되었으면 true. "매매거래정지 및 정지해제"처럼 정지와 해제가 같은 공시에 함께 있거나 해제일이 정지일과 같으면 true. 해제 없이 정지만 남아 있을 때만 false
- DELISTING → DELISTING: values.lifted = 폐지 사유 해소·취소 공시이면 true, 아니면 false
- ADMIN_ISSUE → ADMIN_ISSUE: values는 비워도 된다
- AUDITOR_CHANGE → AUDITOR_CHANGE: values.periodic_designation = 주기적 지정제에 따른 교체이면 true, 아니면 false
- FILING_DELAY → FILING_DELAY: values는 비워도 된다
- GOOD_NEWS_CANCEL → GOOD_NEWS_CANCEL: values는 비워도 된다
- CB_BW → CB_BW: values.refixing = 전환가액·행사가액 리픽싱(조정) 조항이 있으면 true, 없으면 false
- RIGHTS_OFFERING → RIGHTS_OFFERING: values.ratio_pct = 신주 발행 주식 수 ÷ 증자 전 주식 수 × 100, values.third_party = 제3자배정이면 true, 아니면 false
- OWNERSHIP → 최대주주가 주식을 담보로 **새로 제공·추가 설정**(담보대출 계약 체결, 추가담보 설정)한 내용이 보고 사유에 있을 때만 PLEDGE: values.pledge_ratio_pct = 담보 주식 ÷ 최대주주 보유 주식 × 100 (원문에서 계산할 수 없으면 null). 아래는 신호가 아니므로 빈 facts다: 기존 담보의 갱신·변경·기간연장·일부해지·상환·반환, 담보 계약 현황을 나열만 한 보고, 담보 언급이 없는 보고, 법령 조문 속 "담보계약" 문구. 보고 사유에 "신규 설정", "계약 체결", "추가 설정(추가담보)"처럼 담보가 새로 더해지는 내용이 있을 때만 PLEDGE로 한다
- 그 밖의 유형은 빈 facts

values 값은 숫자·true/false·문자열 그대로 쓰고, 단위나 설명 문구를 붙이지 않는다(예: 91.81, "거절").
