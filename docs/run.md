# 로컬 시연 실행

현재 사용자용 실행 방식은 Docker + PostgreSQL 하나다. 온라인 배포는 이번 범위가 아니다.
시작 스크립트를 실행할 때마다 고정된 시연 DB `dartch_demo`를 삭제·재생성하고 기본 데이터를 적재한다.
실행 중 새로고침·로그아웃·개별 컨테이너 재시작은 초기화하지 않는다.

## 최초 준비

1. Docker Desktop을 설치하고 실행한다. Windows에서는 Linux 컨테이너를 사용한다.
2. 저장소 최상위의 `.env.example`을 `.env`로 복사한다(기존 파일을 덮어쓰지 않는다).
3. `.env`에 유효한 `OPENAI_API_KEY`, `DART_API_KEY`를 입력한다.

```dotenv
OPENAI_API_KEY=직접_발급받은_키
DART_API_KEY=직접_발급받은_키
ANTHROPIC_API_KEY=
LLM_PRIMARY=gpt-5.4-mini
LLM_FALLBACK=gpt-5.4-mini
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
# 기본 포트를 사용할 수 없을 때만 변경
WEB_PORT=8080
API_PORT=8000
```

모델명은 계정에서 접근 가능한 모델로 설정한다. 실제 키를 제출 소스·대화·로그에 넣지 않는다.
Python, uv, Node.js, PostgreSQL을 호스트에 각각 설치할 필요는 없다. 최초 이미지 다운로드·빌드에는 인터넷과 시간이 필요하다.

## 실행

저장소 최상위에서 Windows PowerShell:

```powershell
.\start.ps1
```

PowerShell의 로컬 스크립트 실행 정책에 막히는 경우에만 별도 프로세스로 실행:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

macOS/Linux:

```bash
sh ./start.sh
```

API 접근 검사·기본 공시 응답 검증 → 시연 DB 재생성 → 마이그레이션 → 기본 데이터 → 서버 기동 순서다.
API 검사 또는 기본 공시 응답 검증 실패 시 이전 DB는 삭제하지 않는다. 서비스는 중지된 상태일 수 있으므로 설정 수정 후 시작 명령을 다시 실행한다.
화면은 http://127.0.0.1:8080, API 문서는 http://127.0.0.1:8000/docs (포트 변경 시 해당 포트).
`.env`의 키는 Compose `env_file`로 컨테이너에 직접 전달한다. 호스트 셸의 오래된 API 키는 이를 덮어쓰지 않는다.
DB/MCP에는 호스트 공개 포트가 없고 화면/API도 loopback에만 연결된다.

## 기본 자료와 실제 동작

- `data/seed/customers.json`: 가상 PB 2명, 고객 20명, 보유 42건. 화면의 데모 계정 버튼과 일치한다.
- P01: `p01@demo.local` / `demo1234`, 관리자 권한으로 수집·분석 가능.
- P02: `p02@demo.local` / `demo1234`, 일반 PB로 담당 고객 접근을 확인한다.
- `data/demo_companies.json`, `data/disclosures/*.json`: 기존 실제 기업·공시 메타데이터. 과거 기대 점수·영향도는 판정에 넣지 않는다.
- 저장 원문·가짜 분석·사전 승인·연락 업무는 넣지 않는다. 원문은 실제 DART에서 가져와 임시 색인한다.
- 시작 시 위니아 부도 공시 `20231005000522`의 `dfOcr` 사실을 실제 API에서 재조회해 저장하고, 이 1건만 분석 인계한다.
- 나머지 기본 공시는 목록 자료이며 아직 수집 완료 인계된 자료가 아니다. 추가 분석은 화면의 보유 종목 공시 수집으로 실제 수집한 뒤 요청한다.
- 실제 모델/임베딩 호출은 비용이 발생한다. 시작마다 초기 분석 1건도 반복된다. 추가 백필 수집은 다수 공시 분석을 만들 수 있다.
- 기본 분석이 끝나기 전 경보 목록은 비어 있을 수 있다. 수동 확인·실패를 정상 분석 결과로 대체하지 않는다.

시연: P01 로그인 → 고객 현황 → 분석 작업 상태/경보 → 공시 근거 → 안내문 생성 → 담당 고객 승인 → 연락 업무.
P01/P02는 각자의 고객만 승인한다. 승인 전 연락 업무가 없어야 하고 반복 제출해도 중복 생성되면 안 된다.
역사적 공시의 현재 판정은 기준일·감쇠·후속 자료에 따라 달라진다. 과거 정답 배지를 고정해서 보여주지 않는다.

## 종료·재실행

```bash
docker compose --env-file .env -p dartch-local-demo -f docker-compose.demo.yml stop
```

종료는 현재 DB를 즉시 삭제하지 않는다. 다음에 시작 스크립트를 실행하면 시연 DB와 체크포인트를 함께 새로 만든다.
즉 접속·새로고침·컨테이너 장애 재시작으로 데이터가 사라지지는 않는다.
같은 시연을 복구하고 싶다면 시작 스크립트 대신 아래 명령으로 기존 컨테이너를 재개할 수 있다(일반 시연 시작 절차는 아님).

```bash
docker compose --env-file .env -p dartch-local-demo -f docker-compose.demo.yml start
```

기존 `data/app.db`, `data/checkpoints.db`, `dartch-release-validation` 및 이전 배포 볼륨은 변경하지 않는다.
전용 Compose 프로젝트/DB에만 사용하는 내부 접속 계정은 로컬 데모 전용이다. 이 구성을 온라인에 공개하지 않는다.
`docker-compose.yml`은 과거 영구 저장 구성이며 이번 사용자 실행 경로가 아니다. 이전 절차는 [보존 문서](legacy/run-before-local-docker-demo.md)에 있다.

## 문제 확인

```bash
docker compose --env-file .env -p dartch-local-demo -f docker-compose.demo.yml ps
docker compose --env-file .env -p dartch-local-demo -f docker-compose.demo.yml logs --tail 80 api business rag
```

키 검사 성공은 모델 메타데이터 접근 확인이며 실제 생성 성공까지 보장하지 않는다.
401은 키/전달 경로, 403·404는 모델 권한, 429는 한도·결제 등을 확인한다. 민감한 로그를 공유하기 전 내용을 확인한다.
검증 결과와 제한은 [작업 로그](log/2026-10-24-local-docker-demo.md)를 따른다.

시작 스크립트가 강제 종료되어 시작 잠금이 남았다면, 다른 시작 작업이 실제로 끝났는지 확인한 후에만 잠금을 제거한다. 이 명령은 DB를 삭제하지 않는다.

```bash
docker rm dartch-local-demo-start-lock
```

같은 Docker 엔진에서 시연 시작을 동시에 실행하지 않는다. 시작 잠금은 중복 초기화를 차단한다.
