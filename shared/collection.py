"""R4 per-stock reservation/cursor API used by R3 dispatchers."""
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from shared.models import CollectCursor, CollectRun


def reserve_stocks(db, run_id, stock_codes):
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


def finish_stock(db, run_id, stock_code, *, success_at=None):
    values = {'active_run_id': None}
    if success_at is not None:
        from datetime import datetime
        datetime.fromisoformat(success_at)
        row = db.get(CollectCursor, stock_code)
        if row and row.last_success_at and success_at < row.last_success_at:
            raise ValueError('증분 커서를 과거로 되돌릴 수 없습니다.')
        values['last_success_at'] = success_at
    # Failed stocks release reservation without advancing the cursor.
    db.execute(update(CollectCursor).where(CollectCursor.stock_code == stock_code, CollectCursor.active_run_id == run_id).values(**values))


def release_run(db, run_id):
    db.execute(update(CollectCursor).where(CollectCursor.active_run_id == run_id).values(active_run_id=None))
