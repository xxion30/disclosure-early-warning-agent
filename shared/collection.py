"""R4 per-stock reservation/cursor API used by R3 dispatchers."""
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from shared.models import CollectCursor, CollectRun


def reserve_stocks(db, run_id, stock_codes):
    run = db.scalar(select(CollectRun).where(CollectRun.run_id == run_id).with_for_update())
    if run is not None and run.status not in ('PENDING', 'RUNNING'):
        return []
    reserved = []
    for stock in sorted(set(stock_codes)):
        if db.get(CollectCursor, stock) is None:
            try:
                with db.begin_nested():
                    db.add(CollectCursor(stock_code=stock))
                    db.flush()
            except IntegrityError:
                pass  # another process initialized this stock
        result = db.execute(update(CollectCursor).where(CollectCursor.stock_code == stock, CollectCursor.active_run_id.is_(None))
                            .values(active_run_id=run_id))
        if result.rowcount:
            reserved.append(stock)
    return reserved


def finish_stock(db, run_id, stock_code, *, success_at=None, now=None):
    run = db.scalar(select(CollectRun).where(CollectRun.run_id == run_id).with_for_update().execution_options(populate_existing=True))
    if run is not None and (run.status not in ('PENDING', 'RUNNING') or _expired(run, now)):
        return False
    values = {'active_run_id': None}
    if success_at is not None:
        from datetime import datetime
        candidate = datetime.fromisoformat(success_at)
        if candidate.tzinfo is None:
            raise ValueError('수집 성공 시각에는 시간대가 필요합니다.')
        row = db.get(CollectCursor, stock_code)
        if row and row.last_success_at and candidate < datetime.fromisoformat(row.last_success_at):
            raise ValueError('증분 커서를 과거로 되돌릴 수 없습니다.')
        values['last_success_at'] = success_at
    # Failed stocks release reservation without advancing the cursor.
    db.execute(update(CollectCursor).where(CollectCursor.stock_code == stock_code, CollectCursor.active_run_id == run_id).values(**values))


def release_run(db, run_id):
    db.scalar(select(CollectRun).where(CollectRun.run_id == run_id).with_for_update())
    db.execute(update(CollectCursor).where(CollectCursor.active_run_id == run_id).values(active_run_id=None))


def lease_deadline(now=None):
    from datetime import datetime, timezone, timedelta
    from shared.config import get_settings
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(seconds=get_settings().COLLECTION_LEASE_SECONDS)).isoformat()


def _expired(run, now=None):
    from datetime import datetime, timezone, timedelta
    from shared.config import get_settings
    now = now or datetime.now(timezone.utc)
    deadline = datetime.fromisoformat(run.lease_expires_at) if run.lease_expires_at else (
        datetime.fromisoformat(run.started_at) + timedelta(seconds=get_settings().COLLECTION_LEASE_SECONDS))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline <= now


def recover_expired_runs(db, *, now=None):
    """Expired jobs become terminal; new requests use a new ID so late workers cannot advance cursors."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    recovered = []
    runs = db.scalars(select(CollectRun).where(CollectRun.status.in_(['PENDING','RUNNING']))
                      .with_for_update(skip_locked=True).execution_options(populate_existing=True)).all()
    for run in runs:
        if not _expired(run, now):
            continue
        release_run(db, run.run_id)
        run.status, run.finished_at, run.error = 'FAILED', now.isoformat(), '수집 실행 임대 만료: 실행 인계 또는 작업자 복구 필요'
        recovered.append(run.run_id)
    db.flush()
    return recovered


def heartbeat_run(db, run_id, *, now=None):
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    row = db.scalar(select(CollectRun).where(CollectRun.run_id == run_id).with_for_update().execution_options(populate_existing=True))
    if row is None or row.status not in ('PENDING','RUNNING'):
        return False
    if _expired(row, now):
        return False
    row.status, row.lease_expires_at = 'RUNNING', lease_deadline(now)
    db.flush()
    return True


def finish_run(db, run_id, *, fetched_count=0, failed_count=0, error=None):
    from datetime import datetime, timezone
    row = db.scalar(select(CollectRun).where(CollectRun.run_id == run_id).with_for_update().execution_options(populate_existing=True))
    if row is None or row.status not in ('PENDING','RUNNING'):
        return False
    now = datetime.now(timezone.utc)
    if _expired(row, now):
        return False
    if db.scalar(select(CollectCursor.stock_code).where(CollectCursor.active_run_id == run_id).limit(1)):
        raise ValueError('종목별 성공·실패 처리를 먼저 완료해야 합니다.')
    if fetched_count < 0 or failed_count < 0:
        raise ValueError('수집 건수는 음수가 될 수 없습니다.')
    row.status = 'FAILED' if failed_count and not fetched_count else 'PARTIAL' if failed_count else 'DONE'
    row.fetched_count, row.failed_count, row.error = fetched_count, failed_count, error
    row.finished_at, row.lease_expires_at = now.isoformat(), None
    db.flush()
    return True
