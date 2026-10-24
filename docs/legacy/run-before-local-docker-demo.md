# DARTCH 실행·배포 (2026-10-24)

공시 수집 → 공개 위험 판정 → 조직별 안내문 검토 → 담당 PB 승인 → 연락 업무 순서다.
직접 메시지를 발송하지 않으며 점수·가중치는 화면이나 설명 모델에 보내지 않는다.

## 1. 설정

```bash
uv sync --frozen
# .env가 없는 새 환경에서만 복사한다. 기존 파일을 덮어쓰지 않는다.
cp -n .env.example .env
uv run python -m scripts.ops.prepare_release
```

`.env`에 유효한 `DART_API_KEY`, `OPENAI_API_KEY`를 설정한다. 키는 대화·Git·로그에 넣지 않는다.
`LLM_PRIMARY`/`LLM_FALLBACK`은 해당 계정에서 사용 가능한 모델로 설정한다.
임베딩은 기본 `text-embedding-3-small`이다. 키 존재만으로 호출 성공을 보장하지 않는다.
`uv run python -m scripts.ops.check_providers`는 키 값을 출력하지 않고 모델 메타데이터 접근과 작은 DART 목록 조회를 검사한다(생성/임베딩은 호출하지 않으며 DART 조회량은 사용한다). 실제 분석 성공은 별도 확인한다.
`data/release.env`에는 무작위 JWT·PostgreSQL 비밀번호가 생성되며 재실행해도 교체하지 않는다.
이 파일은 Git·Docker 이미지에서 제외된다. 분실·교체 전에는 운영 DB의 비밀번호 변경 절차가 필요하다.

## 2. Docker 배포

```bash
docker compose --env-file .env --env-file data/release.env up -d --build
```

새 Compose 프로젝트에는 별도의 PostgreSQL 볼륨이 생긴다. 기존 SQLite 데이터는 자동 이관하지 않는다.
마이그레이션(현재 head `20261024_07`) → business/rag MCP health → API/DB worker → web 순서로 시작한다.
화면은 `http://127.0.0.1:8080`, API는 `http://127.0.0.1:8000/docs`다.
화면의 `/api`는 Nginx가 API로 전달한다. MCP와 DB는 외부 공개하지 않는다.
`WEB_PORT`, `API_PORT`, `POSTGRES_PORT`로 호스트 포트를 바꿀 수 있다.
운영 환경은 약한 JWT 비밀값과 fake/비 hosted 실행을 거부한다.

```bash
docker compose --env-file .env --env-file data/release.env ps
curl --fail http://127.0.0.1:8000/ready
```

`/health`는 프로세스 확인, `/ready`는 DB 연결 및 실행기 생존 확인이다.
공급자 키·한도·실제 공시 분석 성공까지 뜻하지 않는다.
외부 서버에서는 HTTPS reverse proxy/도메인과 백업 위치를 먼저 준비한 뒤 loopback 화면 포트를 연결한다.
외부 서버·도메인은 아직 지정 대기이며 로컬 기동을 외부 배포 완료로 간주하지 않는다.

## 3. 계정·보유 내역 준비

기본 데모 계정을 자동 생성하지 않는다. 최초 관리자는 아래 명령으로 만든다.
비밀번호는 터미널에서 숨김 입력하며 기존 계정은 덮어쓰지 않는다.

```bash
docker compose --env-file .env --env-file data/release.env exec api python -m scripts.ops.bootstrap_admin \
  --tenant-id default --tenant-name MyCompany --pb-id admin --name Administrator --email admin@example.com
```

추가 PB는 같은 명령에 `--role PB`를 붙여 같은 조직 소속으로 만든다. 고객·보유 JSON은 `data/imports/portfolio.json`에 둔다.
첫 실행은 검증 후 rollback이고, `--apply`를 붙여야 저장된다. 존재하는 PB와 같은 조직의 고객만 받는다.
제공된 고객·보유 종목을 upsert하며 입력에서 빠진 고객·종목을 삭제하지 않는다.
매도·담당자 변경 등 운영 데이터 변경은 원장과 대조해 별도 관리해야 한다.

```json
{"customers":[{"customer_id":"internal-customer-id","pb_id":"admin","name":"고객명","risk_profile":"안정형","total_assets":1000000,"holdings":[{"stock_code":"001570","eval_amount":100000,"on_credit":false}]}]}
```

위 값은 입력 형식 예시이며 실제 고객·공시 사실이 아니다. 실제 데이터를 Git에 넣지 않는다.

```bash
docker compose --env-file .env --env-file data/release.env cp data/imports/portfolio.json api:/tmp/portfolio.json
docker compose --env-file .env --env-file data/release.env exec api python -m scripts.ops.import_portfolio /tmp/portfolio.json --tenant-id default
# 검증 결과를 확인한 다음 같은 명령에 --apply를 추가한다.
```

## 4. 실제 기능 확인

1. 준비한 계정으로 로그인한다. 관리자는 수집 실행 버튼으로 보유 종목 공시 수집을 요청한다. ETF 등 corp_code 없는 종목은 제외 기록을 확인한다.
2. `/collect/status`에서 DONE/PARTIAL/FAILED와 종목별 문제를 확인한다. 사실이 NULL이어도 분석 대상으로 인계되며 위험 없음으로 바꾸지 않는다.
3. `/analysis/jobs`에서 PENDING/RUNNING/RETRY_WAIT/DONE/MANUAL_CHECK/FAILED를 확인한다. 기업별 순서대로 처리한다. 일시적 장애만 최초 포함 최대 3회 재시도한다.
   재무 일부가 결측인 경우 검증된 위험은 표시할 수 있지만 작업은 MANUAL_CHECK로 남는다. 상세의 추가 확인 항목을 확인한다. 결측만 있는 판단 보류를 정상 완료로 취급하지 않는다.
4. 경보 상세와 종목 상세에서 한국어 단계·근거·이번 변화를 확인한다. 원문 인용, API 사실, 재무 스냅샷 근거를 구분한다.
5. 최신 조직별 검토에서 초안을 생성한다. 담당 고객별 승인·수정 승인·재작성·반려를 확인한다. 승인 전 연락 업무가 없어야 하며 반복 제출해도 중복 생성되면 안 된다.
6. 다른 PB/조직의 고객·초안·승인은 접근할 수 없어야 한다. 확인함은 화면에서 받은 알림 버전으로 요청한다.

관리자의 직접 분석은 먼저 수집된 접수번호를 `/analysis/jobs`로 요청한다.
명시적 재분석은 새 UUID `reanalysis_request_id`를 사용한다. 기존 실패를 자동 무한 반복하지 않는다.
현재 서비스에서 구형 `/runs`·구형 승인 경로는 사용하지 않는다.
자동 수집이 필요하면 `docker compose --env-file .env --env-file data/release.env --profile integration up -d collector`를 실행한다(기본 09:05 KST).

## 5. 로컬 개발 실행

```bash
uv run python scripts/ops/run_stack.py
```

실제 hosted 실행·DB 작업 실행기·DART 임시 색인을 연결한다. 기존 DB 초기화나 Chroma ingest는 하지 않는다.
화면은 5173, API/MCP는 8000/8001/8002다. `--api-port`, `--business-port`, `--rag-port`, `--web-port`, `--no-web`을 지원한다.
SQLite는 로컬용이고 PostgreSQL은 `DB_URL`, `CHECKPOINT_DB`를 설정한 뒤 migration을 적용한다.
`--fake`는 명시적인 구형 화면 개발용이며 서비스 검증 결과에 포함하지 않는다.

## 6. 검증·운영

```bash
uv run pytest -q
cd web && npm ci && npm run build
# 별도의 PostgreSQL 테스트 DB에서만 실행(무작위 스키마 생성·삭제)
R4_POSTGRES_TEST_URL=postgresql+psycopg://user:password@localhost/test uv run pytest tests/business/test_postgres_review.py tests/business/test_premerge_postgres.py -q
```

수집은 커서·분석은 입력/규칙 버전·승인은 조직/검토 버전으로 재개한다. 새 공시가 없는 날도 기준일·규칙이 바뀌면 최신 공시를 재평가한다. 동일한 의미의 판정은 기존 조직별 검토·결정을 유지한다.
DB 볼륨을 지우는 `down -v`, seed reset, 체크포인트 삭제는 일반 재시작 절차에 포함하지 않는다.
정기 PostgreSQL dump와 복구 시험은 실제 배포 환경에서 설정한다.
이전 실행·시연 기대값은 [과거 기록](legacy/run-before-service-integration.md)에 보존했다.
최신 구현·실제 검증 범위와 남은 제약은 [통합 로그](log/2026-10-24-service-integration-release.md)를 확인한다.
