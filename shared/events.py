"""Explicit R4 confirmation of a candidate relationship; never an LLM-selected merge."""
from sqlalchemy import select
from shared.models import Disclosure


def confirm_event_link(db, rcept_no, original_rcept_no, verified_evidence):
    """Trusted caller has verified the candidate evidence. Not exposed as a model tool.

    A source/quote/location record must refer to this correction's document.
    Ambiguous/unverified candidates must not call this function.
    """
    if rcept_no==original_rcept_no:raise ValueError('원공시는 다른 접수번호여야 합니다.')
    rows=db.scalars(select(Disclosure).where(Disclosure.rcept_no.in_([rcept_no,original_rcept_no]))
                     .order_by(Disclosure.rcept_no).with_for_update()).all()
    by_id={row.rcept_no:row for row in rows}
    if len(by_id)!=2:raise ValueError('원공시와 후속 공시가 모두 필요합니다.')
    current,original=by_id[rcept_no],by_id[original_rcept_no]
    if current.corp_code!=original.corp_code or original.filed_at>current.filed_at:
        raise ValueError('같은 회사의 선행 원공시가 필요합니다.')
    if not verified_evidence or any(not isinstance(e,dict) or e.get('rcept_no')!=rcept_no or
        any(not isinstance(e.get(k),str) or not e[k].strip() for k in ('source','quote','location')) for e in verified_evidence):
        raise ValueError('검증된 원공시 연결 근거가 필요합니다.')
    current.original_rcept_no=original_rcept_no
    current.event_id=original.event_id or original.rcept_no
    current.event_started_at=original.event_started_at or original.filed_at
    current.event_link_evidence=verified_evidence
    current.analysis_ready_at=None
    db.flush()
    return current.event_id
