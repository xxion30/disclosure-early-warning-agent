"""Recover expired dispatch reservations. Does not perform collection itself."""
from shared.db import get_session
from shared.collection import recover_expired_runs


def recover():
    with get_session() as session:
        return recover_expired_runs(session)


if __name__ == '__main__':
    print(f'recovered={len(recover())}')
