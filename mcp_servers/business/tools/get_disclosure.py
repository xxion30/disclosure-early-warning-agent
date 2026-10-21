from mcp_servers.business.tools._common import guarded, invalid, not_found
from shared.db import get_session
from shared.models import Company, Disclosure
from shared.schemas import ok


@guarded
def get_disclosure(rcept_no: str) -> dict:
    """공시 1건의 제목·종류·접수일·원문을 돌려준다.

    Args:
        rcept_no: 접수번호 (14자리 숫자)
    """
    if not isinstance(rcept_no, str) or not rcept_no.isdigit():
        raise invalid("rcept_no는 숫자로 된 접수번호여야 합니다.")
    with get_session() as s:
        d = s.get(Disclosure, rcept_no)
        if not d:
            raise not_found(f"접수번호 {rcept_no} 공시를 찾을 수 없습니다.")
        c = s.get(Company, d.corp_code)
        return ok(
            {
                "rcept_no": d.rcept_no,
                "corp_code": d.corp_code,
                "stock_code": c.stock_code if c else None,
                "corp_name": c.name if c else None,
                "market": c.market if c else None,
                "title": d.title,
                "disclosure_type": d.disclosure_type,
                "filed_at": d.filed_at,
                "url": d.url,
                "body_text": d.body_text,
                "text_purged": d.purged_at is not None,  # 운영: 처리 후 본문을 비웠다 (content_hash와 DART 주소로 다시 받을 수 있다)
                "content_hash": d.content_hash,
                "processed": d.processed,
            }
        )
