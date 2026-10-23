"""DART 수집기 (담당: R3). 구현 중이다 — 계약은 docs/contracts/collector.md, 구성은 docs/dartch_file_plan.md.

모듈 (구현됨 표시):
    dart_client.py   DART API 호출 (httpx, 재시도, 호출 속도 제한)  [구현됨]
    watchlist.py     보유 종목 합집합(ETF 제외), 종목코드 → 회사 코드
    cursor.py        증분 수집 커서(마지막 성공 시각)
    jobs.py          종목별 수집 작업
    backfill.py      새 종목의 최근 12개월 채우기
    indicators.py    🟡 재무 지표 계산
    repo.py          DB 저장 (표는 shared/models/collect.py)
    scheduler.py     매일 09:05(KST) 실행
의존 방향: shared만 import한다. agent·api·risk_engine을 import하지 않는다 (CLAUDE.md "의존 방향").
"""
