"""DART 수집기 (담당: R3). 아직 구현 전이다 — 파일 구성 계획(docs/dartch_file_plan.md)의 뼈대만 있다.

예정된 모듈:
    dart_client.py   DART API 호출 (httpx, 재시도, 호출 속도 제한)
    watchlist.py     보유 종목 합집합(ETF 제외), 종목코드 → 회사 코드
    cursor.py        증분 수집 커서(마지막 성공 시각)
    jobs.py          종목별 수집 작업
    backfill.py      새 종목의 최근 12개월 채우기
    indicators.py    🟡 재무 지표 계산
    repo.py          DB 저장 (표는 shared/models/collect.py)
    scheduler.py     매일 09:05(KST) 실행
의존 방향: shared만 import한다. agent·api·risk_engine을 import하지 않는다 (CLAUDE.md "의존 방향").
"""
