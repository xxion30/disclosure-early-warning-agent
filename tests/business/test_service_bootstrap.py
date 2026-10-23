"""Fresh-deployment account/portfolio boundaries using protocol-only records."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from shared.models import Base, PB, Customer, Holding
from scripts.ops.bootstrap_admin import create_admin
from scripts.ops.import_portfolio import import_portfolio


def account(db, tenant='org', pb='admin'):
    create_admin(db, tenant_id=tenant, tenant_name=tenant, pb_id=pb,
                 name='protocol', email=pb+'@example.invalid', password='test-only-long-password')


def portfolio(pb='admin', cid='customer'):
    return {'customers':[{'customer_id':cid, 'pb_id':pb, 'name':'protocol', 'risk_profile':'protocol',
        'total_assets':1, 'holdings':[{'stock_code':'001570','eval_amount':1,'on_credit':False}]}]}


def test_bootstrap_never_overwrites_and_import_does_not_need_a_company_record():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account(db); db.commit()
        old = db.get(PB, 'admin').password_hash
        with pytest.raises(ValueError, match='이미 존재'):
            account(db)
        assert db.get(PB, 'admin').password_hash == old
        assert import_portfolio(db, 'org', portfolio()) == {'customers':1,'holdings':1}
        db.commit()
        # Omitted holdings remain; empty is not an instruction to delete them.
        payload = portfolio(); payload['customers'][0]['holdings'] = []
        import_portfolio(db, 'org', payload)
        assert db.get(Holding, ('customer','001570')).eval_amount == 1
    engine.dispose()


def test_bad_relationship_is_rejected_before_any_customer_is_written():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account(db); account(db, 'other', 'other'); db.commit()
        payload = portfolio()
        payload['customers'] += portfolio('other', 'other-customer')['customers']
        with pytest.raises(ValueError, match='조직'):
            import_portfolio(db, 'org', payload)
        assert db.scalar(select(Customer)) is None
    engine.dispose()
