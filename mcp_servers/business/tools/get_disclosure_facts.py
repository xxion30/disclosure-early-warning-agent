from shared.db import get_session
from shared.models import Disclosure
from shared.facts import StructuredFacts, facts_hash
from shared.schemas import ok
from mcp_servers.business.tools._common import guarded, invalid, not_found


@guarded
def get_disclosure_facts(rcept_no: str) -> dict:
    """공시별 구조화 사실 조회. NULL은 빈 사실이나 위험 없음으로 해석하지 않는다."""
    if not isinstance(rcept_no, str) or len(rcept_no) != 14 or not rcept_no.isdigit():
        raise invalid('접수번호는 14자리 숫자여야 합니다.')
    with get_session() as session:
        row = session.get(Disclosure, rcept_no)
        if row is None:
            raise not_found('공시를 찾을 수 없습니다.')
        data = None
        if row.structured_facts is not None:
            parsed = StructuredFacts.model_validate(row.structured_facts)
            if parsed.rcept_no != row.rcept_no or parsed.corp_code != row.corp_code:
                raise invalid('구조화 사실의 공시 식별자가 일치하지 않습니다.')
            data = parsed.model_dump(mode='json')
        return ok({'availability': 'AVAILABLE' if data is not None else 'UNAVAILABLE',
                   'rcept_no': row.rcept_no, 'corp_code': row.corp_code,
                   'structured_facts': data, 'facts_hash': facts_hash(data) if data is not None else None})
