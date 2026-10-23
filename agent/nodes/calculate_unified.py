"""Single R4 calculator call. All retrieved text stays local to this node."""
from datetime import date
from copy import deepcopy

from agent.evidence import retained_data, validate_evidence, validate_known_evidence, chunks_from
from agent.execution import call_tool
from agent.integration import UnifiedResult
from agent.ports import AgentError
from agent.structured_facts import api_evidence, validate_facts


async def calculate_unified(state, deps):
    disclosure = state['disclosure']
    context = state['execution_context']
    # The injected R4 adapter owns mapping and conflict policy, not the model.
    prepared = deps.unified.prepare_signals({
        'disclosure': deepcopy(disclosure), 'structured_facts': deepcopy(state.get('structured_facts', {})),
        'flags': deepcopy(state.get('flags', [])), 'evidence': deepcopy(state.get('evidence', [])),
        'numeric_facts': deepcopy(state.get('numeric_facts', [])),
        'routes': deepcopy(state.get('risk_routes', [])), 'as_of': context['as_of'],
    })
    if not isinstance(prepared, list) or any(not isinstance(s, dict) for s in prepared):
        raise AgentError('R4 계산 입력 변환 결과는 신호 목록이어야 합니다')
    input_evidence = list(state.get('evidence', []))
    for signal in prepared:
        if signal.get('rcept_no') != disclosure['rcept_no']:
            raise AgentError('현재 공시 이외의 신규 신호')
        if any(k in signal for k in ('points', 'stage', 'event_id', 'event_started_at')):
            raise AgentError('입력 변환에 점수·단계·사건 관계를 지정할 수 없습니다')
        if signal.get('evidence'):
            for evidence in signal['evidence']:
                if evidence.get('kind') == 'api':
                    verified = api_evidence(evidence, state.get('structured_facts', {}))
                    input_evidence.append(verified)
                else:
                    validate_known_evidence([evidence], input_evidence)
    raw = await call_tool(deps, 'calculate_risk_score', {
        'corp_code': disclosure['corp_code'], 'new_signals': prepared, 'as_of': context['as_of'],
    })
    result = UnifiedResult.model_validate(raw)
    if result.corp_code != disclosure['corp_code'] or result.as_of != context['as_of']:
        raise AgentError('계산 응답 기업·평가 기준 시점 불일치')
    if result.rules_digest != context['rules_version']:
        raise AgentError('실행 중 규칙 버전 변경: 새 판정 필요')
    if result.manual_check:
        raise AgentError('규칙 엔진의 수동 확인 요청')
    known, sources, fetched = input_evidence, list(state.get('verified_sources', [])), {}
    financial = [ev for row in result.breakdown for ev in row.get('evidence', [])
                 if ev.get('kind') == 'financial']
    if financial:
        checked = await call_tool(deps, 'verify_financial_evidence', {
            'corp_code': disclosure['corp_code'], 'as_of': context['as_of'], 'evidence': financial})
        if checked.get('evidence') != financial:
            raise AgentError('재무 근거 검증 응답 불일치')
        known.extend(financial)
    historical_facts = {}
    for row in result.breakdown:
        evidence = row.get('evidence') or []
        if not evidence:
            raise AgentError('판정 항목 출처 누락')
        for ev in evidence:
            if ev.get('kind') == 'financial':
                validate_known_evidence([ev], known)
                continue
            if ev.get('kind') == 'api':
                receipt = ev.get('rcept_no')
                facts = state.get('structured_facts', {})
                if receipt != disclosure['rcept_no']:
                    if receipt not in historical_facts:
                        meta = await call_tool(deps, 'get_disclosure', {'rcept_no': receipt})
                        if (meta.get('corp_code') != disclosure['corp_code']
                                or date.fromisoformat(meta['filed_at'][:10]) > date.fromisoformat(context['as_of'])):
                            raise AgentError('다른 기업·기준일 이후 API 근거')
                        envelope = await call_tool(deps, 'get_disclosure_facts', {'rcept_no': receipt})
                        historical_facts[receipt] = validate_facts(envelope, meta)
                    facts = historical_facts[receipt]
                api_evidence(ev, facts)
                known.append(ev)
                continue
            try:
                validate_known_evidence([ev], known)
                continue
            except (AgentError, ValueError):
                pass
            receipt = ev.get('rcept_no')
            if receipt not in fetched:
                meta = await call_tool(deps, 'get_disclosure', {'rcept_no': receipt})
                if meta.get('corp_code') != disclosure['corp_code'] or date.fromisoformat(meta['filed_at'][:10]) > date.fromisoformat(context['as_of']):
                    raise AgentError('다른 기업·기준일 이후 과거 근거')
                fetched[receipt] = {}
            identity = (receipt, ev.get('quote'))
            if identity not in fetched[receipt]:
                data = await call_tool(deps, 'search_disclosure_text', {
                    'rcept_no': receipt, 'query': ev.get('quote', ''), 'k': 5})
                chunks = chunks_from(data)
                digest = data.get('content_sha256') if isinstance(data, dict) else None
                if not digest:
                    raise AgentError('과거 근거 원문 해시 누락')
                fetched[receipt][identity] = chunks
                sources.append({'rcept_no': receipt, 'content_sha256': digest,
                                'normalization_version': data.get('normalization_version')})
            validate_evidence([ev], fetched[receipt][identity])
            known.append(ev)
    if result.stage != 'NONE' and not result.breakdown:
        raise AgentError('단계 판정의 근거 내역 없음')
    clean = retained_data(result.model_dump())
    return {
        'score_result': clean, 'signals': retained_data(result.signals), 'stage': result.stage,
        'risk_level': result.level, 'total_score': result.total_score,
        'orange_score': result.orange_score, 'yellow_score': result.yellow_score,
        'combined_ratio': result.composite_ratio, 'combo_upgraded': result.combo_upgraded,
        'evidence': retained_data(known), 'verified_sources': sources,
        'input_manifest': {'context': deepcopy(context), 'new_signals': retained_data(prepared),
                           'structured_facts': deepcopy(state.get('structured_facts', {})),
                           'verified_sources': sources},
    }
