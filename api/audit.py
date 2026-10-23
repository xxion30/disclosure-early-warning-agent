"""사람의 결정(승인·반려·수정·상태 변경)을 audit_logs에 남긴다 (CLAUDE.md 6장 Observability)."""
from sqlalchemy.orm import Session

from api.graph_adapter import now_iso
from shared.models import AuditLog, PB


def write_audit(db: Session, actor: str, action: str, target: str, payload: dict, trace_id: str | None = None) -> None:
    pb = db.get(PB, actor)
    db.add(AuditLog(tenant_id=pb.tenant_id if pb else None, actor=actor, action=action, target=target, payload=payload, trace_id=trace_id, created_at=now_iso()))
