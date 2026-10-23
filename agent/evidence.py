"""Validate model citations against retrieved text, never against model claims."""
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
    chunks = [{**item, "text": item["quote"]} for item in known]
    validate_evidence(evidence, chunks)
