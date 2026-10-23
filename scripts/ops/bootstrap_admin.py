"""Create one initial administrator without overwriting an existing account.

uv run python -m scripts.ops.bootstrap_admin --tenant-id default --tenant-name MyCompany \
    --pb-id admin --name Administrator --email admin@example.com
Password is requested without echo, never accepted as a command-line argument.
"""
import argparse
from getpass import getpass
from sqlalchemy import select
from api.auth import hash_password
from shared.db import get_session, init_db
from shared.models import PB, Tenant


def create_admin(db, *, tenant_id, tenant_name, pb_id, name, email, password, role='ADMIN'):
    email = email.strip().lower()
    if not tenant_id.strip() or not pb_id.strip() or '@' not in email:
        raise ValueError('조직·계정 ID와 이메일을 확인하세요.')
    if len(password) < 12 or len(password.encode('utf-8')) > 72:
        raise ValueError('비밀번호는 12자 이상, UTF-8 72바이트 이하로 정하세요.')
    if role not in ('ADMIN', 'PB'):
        raise ValueError('역할은 ADMIN 또는 PB여야 합니다.')
    if db.get(PB, pb_id) or db.scalar(select(PB).where(PB.email == email)):
        raise ValueError('이미 존재하는 계정입니다. 기존 계정은 변경하지 않습니다.')
    if db.get(Tenant, tenant_id) is None:
        db.add(Tenant(tenant_id=tenant_id, name=tenant_name))
        db.flush()
    db.add(PB(pb_id=pb_id, tenant_id=tenant_id, name=name, email=email,
              role=role, password_hash=hash_password(password)))
    db.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('tenant-id', 'tenant-name', 'pb-id', 'name', 'email'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--role', choices=['ADMIN','PB'], default='ADMIN')
    args = vars(parser.parse_args())
    password = getpass('새 관리자 비밀번호: ')
    if password != getpass('비밀번호 확인: '):
        raise SystemExit('비밀번호가 일치하지 않습니다.')
    init_db()
    with get_session() as db:
        create_admin(db, **args, password=password)
    print('계정을 생성했습니다. 고객·보유 내역은 별도로 등록하세요.')


if __name__ == '__main__':
    main()
