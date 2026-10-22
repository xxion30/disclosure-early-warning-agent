"""테이블 정의. 예전처럼 `from shared.models import Company, ...`로 그대로 쓸 수 있다.

파일은 담당과 성격으로 나눴다: base.py(공통 베이스), core.py(공개 데이터), tenant.py(회사별 사용자·고객),
risk.py(평가·업무·감사), collect.py(수집기). 테이블을 추가하면 이 파일의 import에도 넣는다.
"""
from shared.models.base import Base
from shared.models.collect import *  # noqa: F401,F403
from shared.models.core import Company, Disclosure, Signal
from shared.models.risk import (
    ASSESSMENT_STATUSES, LEVELS, STAGES, TASK_STATUSES, AuditLog, ContactTask, RiskAssessment,
)
from shared.models.tenant import PB, Customer, Holding

__all__ = [
    "Base", "Company", "Disclosure", "Signal", "PB", "Customer", "Holding",
    "RiskAssessment", "ContactTask", "AuditLog",
    "ASSESSMENT_STATUSES", "TASK_STATUSES", "LEVELS", "STAGES",
    "Tenant", "RiskState", "StateRead", "FinancialSnapshot", "CollectRun", "CollectCursor", "TenantRun",
]

from shared.models.operations import Tenant, RiskState, StateRead, FinancialSnapshot, CollectRun, CollectCursor, TenantRun
