import hashlib

from mcp_servers.business.tools._common import guarded, invalid, not_found, now_iso
from shared.config import get_settings
from shared.db import get_session
from shared.models import Disclosure
from shared.schemas import ok


@guarded
def purge_disclosure_text(rcept_no: str, content_hash: str | None = None) -> dict:
    """처리가 끝난 공시의 원문 본문을 DB에서 비우고, 원문 해시와 처리·삭제 시각만 남긴다.

    운영 모드(RAG_INDEX_MODE=ephemeral)에서만 본문을 지운다. 기본(persistent) 모드에서는 아무것도 바꾸지 않는다.
    모델이 아니라 Agent 코드가 경보를 저장한 직후 부른다. 같은 호출을 되풀이해도 결과가 같다.

    Args:
        rcept_no: 접수번호 (14자리 숫자)
        content_hash: 원문 sha256. 없으면 DB에 남아 있는 본문으로 계산한다.

    Returns:
        purged(본문을 비웠는지), content_hash, processed_at, purged_at
    """
    if not isinstance(rcept_no, str) or not rcept_no.isdigit():
        raise invalid("rcept_no는 숫자로 된 접수번호여야 합니다.")
    if get_settings().RAG_INDEX_MODE != "ephemeral":
        return ok({"rcept_no": rcept_no, "purged": False, "reason": "persistent 모드에서는 본문을 지우지 않습니다."})
    with get_session() as s:
        d = s.get(Disclosure, rcept_no)
        if d is None:
            raise not_found(f"접수번호 {rcept_no} 공시를 찾을 수 없습니다.")
        now = now_iso()
        if not d.content_hash:
            d.content_hash = content_hash or (hashlib.sha256(d.body_text.strip().encode()).hexdigest() if d.body_text else None)
        d.processed = True
        d.processed_at = d.processed_at or now
        if d.body_text:
            d.body_text = None
            d.purged_at = now
        return ok({"rcept_no": rcept_no, "purged": d.purged_at is not None, "content_hash": d.content_hash,
                   "processed_at": d.processed_at, "purged_at": d.purged_at})
