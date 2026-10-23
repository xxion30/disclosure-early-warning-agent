"""DART 수집기 (담당: R3). 구현 중이다 — 계약은 docs/contracts/collector.md, 구성은 docs/dartch_file_plan.md.

모듈:
    dart_client.py   DART API 호출 (httpx, 재시도, 호출 속도 제한, 원문 해시, 로그에서 API 키 가림)
    watchlist.py     보유 종목 합집합(ETF 제외), 종목코드 → 회사 코드
    cursor.py        증분 조회 시작일 (last_success_at의 KST 날짜 − 1일)
    backfill.py      새 종목 백필 시작일 (12개월 전)
    reports.py       정기보고서 제목 → reprt_code·period
    indicators.py    🟡 재무 지표 계산
    repo.py          DB 저장 (회사·공시·재무 스냅샷, idempotent)
    jobs.py          종목별 수집 작업, 수집 실행(run), R4 API용 dispatch_collection
    scheduler.py     매일 09:05(KST) 실행
    __main__.py      python -m collector --once | --schedule
아직 없는 것: 주요사항 항목 API 값(structured_facts) 저장 — R4의 컬럼이 생긴 뒤.
의존 방향: shared만 import한다. agent·api·risk_engine을 import하지 않는다 (CLAUDE.md "의존 방향").
"""
