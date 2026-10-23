from sqlalchemy import delete, select

from risk_engine.loader import load_rules
from mcp_servers.business.tools._common import guarded, invalid, not_found, now_iso, require_company
from shared.db import get_session
from shared.models import ASSESSMENT_STATUSES, LEVELS, Disclosure, RiskAssessment, Signal
from shared.schemas import ok

# 이 상태가 되면 공시를 "처리됨"으로 본다 (list_new_disclosures에서 빠진다).
PROCESSED_STATUSES = {"WAITING_APPROVAL", "DONE", "MANUAL_CHECK"}
# 점수 계산 전에 멈춘 평가는 점수 없이도 이 상태로 저장할 수 있다 (한도 초과·오류 → MANUAL_CHECK/FAILED).
NO_SCORE_STATUSES = {"RUNNING", "FAILED", "MANUAL_CHECK"}


@guarded
def save_assessment(
    corp_code: str,
    rcept_no: str,
    signals: list[dict] | None = None,
    score_result: dict | None = None,
    ai_explanation: str | None = None,
    status: str = "RUNNING",
) -> dict:
    """위험도 평가 결과를 저장한다. 모델이 아니라 Agent 코드(노드)만 호출한다.

    rcept_no당 평가는 1건이다. 이미 있으면 갱신한다(upsert). 그래서 같은 공시를 다시 저장하거나
    상태만 바꿔도(WAITING_APPROVAL → DONE) 행이 늘지 않는다. 이때 signals·score_result·ai_explanation을
    비우면 기존 값을 그대로 둔다. 처음 저장할 때는 signals와 score_result가 필요하다
    (RUNNING·FAILED·MANUAL_CHECK는 점수 계산 전에 멈춘 경우라 없이도 저장된다).

    Args:
        corp_code: DART 고유번호
        rcept_no: 접수번호
        signals: calculate_risk_score가 돌려준 signals
        score_result: calculate_risk_score 결과 (total_score, level, combo_upgraded, breakdown)
        ai_explanation: AI가 쓴 설명 (점수 내역마다 원문 인용 포함)
        status: RUNNING | WAITING_APPROVAL | DONE | FAILED | MANUAL_CHECK

    Returns:
        assessment_id, created(새로 만들었으면 true)
    """
    if status not in ASSESSMENT_STATUSES:
        raise invalid(f"status는 {', '.join(ASSESSMENT_STATUSES)} 중 하나여야 합니다: {status!r}")
    group_a = load_rules()["group_a"]
    from risk_engine.states import score_fingerprint
    with get_session() as s:
        company = require_company(s, corp_code)
        from shared.models import PublicAssessment
        if s.scalar(select(PublicAssessment.assessment_id).where(PublicAssessment.rcept_no==rcept_no)):
            raise invalid('새 공개 판정은 legacy save_assessment로 수정할 수 없습니다. 조직별 검토 경로를 사용하세요.')
        # Public analysis for a company is serialized, including first upsert.
        from shared.models import Company
        s.scalar(select(Company).where(Company.corp_code == corp_code).with_for_update())
        disc = s.get(Disclosure, rcept_no)
        if not disc:
            raise not_found(f"접수번호 {rcept_no} 공시를 찾을 수 없습니다.")
        if disc.corp_code != corp_code:
            raise invalid(f"접수번호 {rcept_no}는 corp_code {corp_code}의 공시가 아닙니다.")

        row = s.scalars(
            select(RiskAssessment).where(RiskAssessment.rcept_no == rcept_no).order_by(RiskAssessment.assessment_id.desc())
        ).first()
        created = row is None
        if created:
            if (score_result is None or signals is None) and status not in NO_SCORE_STATUSES:
                raise invalid(
                    f"처음 저장할 때는 signals와 score_result가 필요합니다 (점수 없이 저장할 수 있는 상태: {', '.join(sorted(NO_SCORE_STATUSES))})."
                )
            # 점수 없이 저장하면 total_score=0, level=Low(기본값), reasons=[]. 점수 계산이 끝난 평가가 아니다.
            row = RiskAssessment(corp_code=corp_code, rcept_no=rcept_no, created_at=now_iso(), reasons=[])
            s.add(row)

        fingerprint = score_fingerprint(score_result) if score_result and score_result.get('stage') else None
        publish = fingerprint is not None and fingerprint != row.score_fingerprint
        if publish:
            from risk_engine.states import update_state
            current = update_state(s, corp_code, score_result, disc.filed_at)
            row.risk_state_version = current.state_version
            row.score_fingerprint = fingerprint
        if score_result is not None:
            level = score_result.get("level")
            if level not in LEVELS:
                raise invalid(f"score_result.level은 {', '.join(LEVELS)} 중 하나여야 합니다: {level!r}")
            try:
                row.total_score = float(score_result["total_score"])
            except (KeyError, TypeError, ValueError):
                raise invalid("score_result.total_score가 필요합니다.") from None
            row.stage = score_result.get("stage") or "NONE"
            row.level = level
            row.combo_upgraded = bool(score_result.get("combo_upgraded", False))
            row.reasons = list(score_result.get("breakdown") or [])
        if ai_explanation is not None:
            row.ai_explanation = ai_explanation
        row.status = status

        if signals is not None:
            s.execute(delete(Signal).where(Signal.rcept_no == rcept_no))
            for sig in signals:
                st = sig.get("signal_type")
                points = float(sig.get("points") or 0)
                if not st:
                    raise invalid("signals 항목에는 signal_type이 필요합니다.")
                is_correction = bool((sig.get("detail") or {}).get("is_correction"))
                if "stage" not in (score_result or {}) and points <= 0 and st not in group_a and not is_correction:
                    continue  # 0점 B그룹 신호는 이력에 남기지 않는다. 단 정정 공시는 반복 횟수를 세려고 남긴다
                s.add(
                    Signal(
                        corp_code=corp_code,
                        rcept_no=rcept_no,
                        signal_type=st,
                        event_id=sig.get("event_id") or (sig.get("detail") or {}).get("event_id"),
                        event_started_at=sig.get("event_started_at") or (sig.get("detail") or {}).get("event_started_at"),
                        points=points,
                        detail=dict(sig.get("detail") or {}),
                        filed_at=str(sig.get("filed_at") or disc.filed_at),
                    )
                )
        if status in PROCESSED_STATUSES:
            disc.processed = True
        s.flush()
        return ok({"assessment_id": row.assessment_id, "created": created, "status": row.status, "level": row.level, "stage": row.stage, "risk_state_version": row.risk_state_version})
