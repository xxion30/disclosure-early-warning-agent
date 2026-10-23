"""Validate model citations against retrieved text, never against model claims."""
from copy import deepcopy

from agent.ports import AgentError
from agent.state import Evidence


def _verified_record(raw):
    item = Evidence.model_validate(raw)
    if any(not getattr(item, k).strip() for k in ("source", "quote", "rcept_no", "location")):
        raise AgentError("빈 출처·인용 식별자")
    return item


def chunks_from(data):
    chunks = data.get("chunks") if isinstance(data, dict) else data
    if not isinstance(chunks, list) or not chunks:
        raise AgentError("RAG 원문 조각 없음")
    for chunk in chunks:
        if not isinstance(chunk, dict) or any(
            not isinstance(chunk.get(key), str) or not chunk[key].strip()
            for key in ("source", "text", "rcept_no", "location")
        ):
            raise AgentError("RAG 조각에 source/text/rcept_no/location 필요")
    return chunks[:5]


def validate_evidence(evidence, chunks):
    for raw in evidence:
        item = _verified_record(raw)
        if not any(item.source == c["source"] and item.rcept_no == c["rcept_no"]
                   and item.location == c["location"] and item.quote in c["text"]
                   for c in chunks):
            raise AgentError("원문에서 확인되지 않은 인용")


def validate_known_evidence(evidence, known):
    # Explanation citations can only refer to previously verified facts/score sources.
    from agent.state import APIEvidence
    from shared.financial_evidence import FinancialEvidence
    chunks = [{**item, "text": item["quote"]} for item in known if 'quote' in item]
    for item in evidence:
        if hasattr(item, 'model_dump'):
            item = item.model_dump()
        if item.get('kind') in ('api', 'financial'):
            model = FinancialEvidence if item['kind'] == 'financial' else APIEvidence
            reference = model.model_validate(item).model_dump()
            if reference not in known:
                raise AgentError('확인되지 않은 API 근거')
        else:
            validate_evidence([item], chunks)


def render_evidence(item):
    if item.get('kind') == 'financial':
        basis = '연결' if item['basis'] == 'CONSOLIDATED' else '개별'
        values = ', '.join(f'{key}={value}' for key, value in item['values'].items())
        return f"{item['indicator']}: {values} (접수번호 {item['rcept_no']}, {item['period']} {basis}, 정정 버전 {item['revision']})"
    if item.get('kind') == 'api':
        return (f"{item['api']}.{item['field']}: {item['value']} {item.get('unit') or ''} "
                f"(접수번호 {item['rcept_no']}, 행 {item.get('row_key') or '단일 행'}, "
                f"기간 {item.get('period') or '해당 없음'}, {item.get('basis') or '해당 없음'})")
    return f'“{item["quote"]}” ({item["source"]}, {item["rcept_no"]}, {item["location"]})'


def select_chunks(chunks, limit=5):
    """One model-input limit across searches; never pad with unrelated chunks."""
    selected, seen = [], set()
    for chunk in chunks:
        chunks_from([chunk])
        key = (chunk['rcept_no'], chunk.get('content_sha256'), chunk['source'], chunk['location'])
        if key not in seen:
            selected.append(chunk)
            seen.add(key)
        if len(selected) == limit:
            break
    return selected


def retained_data(value):
    """Remove transient raw content recursively before a Graph checkpoint.

    Keep actual structured values, citations and internal scores. This is not
    the public projection: numerical scores still belong in internal storage.
    """
    forbidden = {'source_chunks', 'chunks', 'text', 'body_text', 'body',
                 'raw_response', 'tool_response', 'messages', 'retrieved'}
    if isinstance(value, dict):
        return {k: retained_data(v) for k, v in value.items() if k not in forbidden}
    if isinstance(value, list):
        return [retained_data(v) for v in value]
    return deepcopy(value)
