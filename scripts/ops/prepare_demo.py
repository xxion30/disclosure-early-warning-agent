"""Reset ONLY the fixed local Compose demo database, then load source fixtures."""
import asyncio
from pathlib import Path
import secrets
import subprocess
import sys
from datetime import datetime, timezone

import psycopg
from sqlalchemy import select

from shared.config import get_settings


def require_demo_target(settings):
    # Never accept an arbitrary database/host supplied by a developer's .env.
    expected = 'postgresql+psycopg://demo_owner:local-demo-only@postgres:5432/dartch_demo'
    if settings.DB_URL != expected or settings.CHECKPOINT_DB != expected.replace('+psycopg', ''):
        raise RuntimeError('시연 전용 Compose DB 이외의 초기화는 거부합니다.')


def seed_demo():
    from scripts.data.seed_db import seed_companies, seed_disclosures, seed_people
    from shared.db import get_session
    from shared.models import Company, Disclosure, PB, Tenant
    with get_session() as db:
        db.merge(Tenant(tenant_id='default', name='DARTCH 시연 조직'))
        db.flush()
        seed_people(db)
        db.flush()
        # P01 operates collection/analysis; P02 exercises ordinary PB isolation.
        db.get(PB, 'P01').role = 'ADMIN'
        companies = seed_companies(db)
        db.flush()
        seed_disclosures(db, companies)
        db.flush()
        # Legacy fixture scores/expected answers are NOT Phase 1 judgments.
        for company in db.scalars(select(Company)):
            company.base_score, company.base_reasons = 0, []
        # Ephemeral RAG fetches original documents from DART, not stored bodies.
        for disclosure in db.scalars(select(Disclosure)):
            disclosure.body_text = None


async def fetch_example():
    """One real historical filing, not hundreds of automatic paid analyses."""
    from collector.dart_client import DartClient
    from collector.facts import build_payload
    from shared.facts import StructuredFacts
    receipt, corp = '20231005000522', '00300557'
    async with DartClient.from_settings() as client:
        rows = await client.major_report('dfOcr', corp, '2023-10-05', '2023-10-05')
    matched = [row for row in rows if row.get('rcept_no') == receipt]
    if len(matched) != 1:
        raise RuntimeError('기본 시연 공시의 실제 API 행을 유일하게 확인할 수 없습니다.')
    return StructuredFacts.model_validate(build_payload(
        'dfOcr', matched[0], rcept_no=receipt, corp_code=corp,
        fetched_at=datetime.now(timezone.utc).isoformat())).model_dump(mode='json')


def save_example(payload):
    from shared.analysis import mark_ready
    from shared.db import get_session
    from shared.facts import store_facts
    receipt = payload['rcept_no']
    with get_session() as db:
        store_facts(db, receipt, payload)
        mark_ready(db, receipt)
    print(f'실제 API 사실 준비: 위니아 {receipt}. 서비스 시작 후 실제 분석 1건을 실행합니다.')


def main():
    settings = get_settings()
    require_demo_target(settings)
    # Probe before destroying the prior session. Only status/model names are printed.
    from scripts.ops.check_providers import check
    results = asyncio.run(check())
    if not all(row['ok'] for row in results):
        for row in results:
            print({k: v for k, v in row.items() if k in ('provider', 'model', 'ok', 'error_code')})
        raise SystemExit('API 연결 확인 실패. .env를 확인하세요. 기존 시연 DB는 보존했습니다.')
    # Fetch and validate the full starter payload BEFORE discarding the old DB.
    payload = asyncio.run(fetch_example())
    with psycopg.connect('postgresql://demo_owner:local-demo-only@postgres:5432/postgres', autocommit=True) as conn:
        conn.execute('DROP DATABASE IF EXISTS dartch_demo WITH (FORCE)')
        conn.execute('CREATE DATABASE dartch_demo')
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    seed_demo()
    save_example(payload)
    secret = Path('/run/dartch-demo/jwt_secret')
    secret.write_text(secrets.token_hex(32))
    secret.chmod(0o600)
    print('기본 데이터 준비 완료. P01/P02: demo1234. 실제 API·MCP로 실행합니다.')


if __name__ == '__main__':
    main()
