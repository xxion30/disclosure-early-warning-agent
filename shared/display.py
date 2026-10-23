"""Public labels; API codes and scoring policy remain separate."""
STAGE_LABELS = {'RED':'즉시 경보','ORANGE':'사건 경보','YELLOW':'재무 경보',
                'COMPOSITE':'복합 위험','WATCH':'관찰 중','NONE':'경보 없음'}
MISSING_LABELS = {
    'going_concern':'계속기업 불확실성','opinion':'감사의견','impairment_pct':'자본잠식률',
    'negative_years':'영업현금흐름 연속 적자 연수','interest_coverage':'금융비용 기준 보상배율',
    'debt_to_cash_pct':'현금성자산 대비 단기채무 비율','ratio_to_equity_pct':'자기자본 대비 비율',
    'core_business':'핵심 사업 관련 여부','loss_compensation':'결손 보전 목적 여부',
    'reduction_ratio_pct':'감자 비율','ratio_pct':'증자 비율','third_party':'제3자 배정 여부',
    'payment_delayed_or_failed':'납입 지연·실패 여부','refixing':'리픽싱 조항',
    'early_redemption':'조기상환 조건','core_asset_sale':'핵심 자산 매각 여부',
    'obligation_to_equity_pct':'자기자본 대비 의무 부담 비율','unfavorable_ratio':'불리한 교환 비율',
    'distressed_transfer':'부실 이전 여부','urgent_funding':'긴급 자금 확보 목적',
    'negotiation_failed':'협상 결렬 여부','distress_or_violation':'재무 악화·규정 위반 사유',
    'rate_increased':'발행 금리 상승 여부','maturity_shortened':'만기 단축 여부',
    'purpose_mismatch':'자금 사용 목적 불일치 여부','changed_within_window':'최근 최대주주 변경 여부',
}


def stage_fields(assessment):
    available = (assessment.stage in STAGE_LABELS and assessment.stage != 'NONE') or (
        assessment.risk_state_version is not None or assessment.score_fingerprint is not None
        or any(r.get('group') in ('RED','ORANGE','YELLOW') for r in (assessment.reasons or []) if isinstance(r,dict)))
    return {'stage_available':available, 'stage_label':STAGE_LABELS.get(assessment.stage) if available else None,
            'legacy':not available and assessment.level in ('Low','Medium','High','Critical')}


def missing_labels(codes):
    return [{'code':code,'label':MISSING_LABELS.get(code,code)} for code in codes or []]
