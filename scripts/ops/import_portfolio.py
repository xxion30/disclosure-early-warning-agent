"""Validate/upsert customer holdings for existing PBs. Default is rollback/dry-run.

python -m scripts.ops.import_portfolio portfolio.json --tenant-id default [--apply]
Input: {"customers":[{"customer_id":...,"pb_id":...,"name":...,"risk_profile":...,
"total_assets":...,"holdings":[{"stock_code":"001570","eval_amount":123,"on_credit":false}]}]}
Holdings are upserted; omitted holdings/customers are preserved. No delete/reset.
"""
import argparse
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from shared.db import get_session
from shared.models import PB, Customer, Holding, Tenant


class HoldingInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    stock_code: str = Field(pattern=r'^\d{6}$')
    eval_amount: int = Field(ge=0)
    on_credit: StrictBool = False


class CustomerInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    customer_id: str = Field(min_length=1)
    pb_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    risk_profile: str = Field(min_length=1)
    total_assets: int = Field(ge=0)
    holdings: list[HoldingInput]


class PortfolioInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    customers: list[CustomerInput] = Field(min_length=1)


def import_portfolio(db, tenant_id, payload):
    parsed = PortfolioInput.model_validate(payload)
    if db.get(Tenant, tenant_id) is None:
        raise ValueError('먼저 조직과 관리자/PB 계정을 준비하세요.')
    seen = set()
    for row in parsed.customers:
        if row.customer_id in seen or len({h.stock_code for h in row.holdings}) != len(row.holdings):
            raise ValueError('중복 고객 또는 보유 종목이 있습니다.')
        seen.add(row.customer_id)
        pb = db.get(PB, row.pb_id)
        customer = db.get(Customer, row.customer_id)
        if pb is None or pb.tenant_id != tenant_id or (customer and customer.tenant_id != tenant_id):
            raise ValueError('담당 PB와 고객의 조직을 확인하세요.')
    # Validate every relationship before changing a row. Caller owns the transaction.
    for row in parsed.customers:
        customer = db.get(Customer, row.customer_id)
        if customer is None:
            customer = Customer(customer_id=row.customer_id, tenant_id=tenant_id)
            db.add(customer)
        for key in ('pb_id', 'name', 'risk_profile', 'total_assets'):
            setattr(customer, key, getattr(row, key))
        db.flush()
        for entry in row.holdings:
            holding = db.get(Holding, (row.customer_id, entry.stock_code))
            if holding is not None and holding.tenant_id != tenant_id:
                raise ValueError('보유 내역의 조직이 일치하지 않습니다.')
            if holding is None:
                holding = Holding(customer_id=row.customer_id, stock_code=entry.stock_code, tenant_id=tenant_id)
                db.add(holding)
            holding.eval_amount, holding.on_credit = entry.eval_amount, entry.on_credit
    db.flush()
    return {'customers': len(parsed.customers), 'holdings': sum(len(c.holdings) for c in parsed.customers)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--tenant-id', required=True)
    parser.add_argument('--apply', action='store_true', help='Commit validated upserts; omitted rows remain unchanged')
    args = parser.parse_args()
    with get_session() as db:
        counts = import_portfolio(db, args.tenant_id, json.loads(args.path.read_text()))
        if not args.apply:
            db.rollback()
    print(('저장 완료' if args.apply else '검증 완료 (저장하지 않음)'), counts)


if __name__ == '__main__':
    main()
