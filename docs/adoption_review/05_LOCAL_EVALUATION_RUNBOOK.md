# 05. 로컬 평가 Runbook

## 1. 적용 범위

이 절차는 `ai_server`를 공식 Backend로 운영하는 절차가 아니다. 사설 개발 PC에서 단일
프로세스, Mock LLM, 가짜 데이터로 후보 기능을 평가할 때만 사용한다.

다음 조건에서는 사용하지 않는다.

- 인터넷에 직접 노출
- 실제 대화·개인정보·운영 token 사용
- 둘 이상의 worker/process
- UE/Web 공식 통합 계약 검증
- backup/restore가 검증되지 않은 중요 데이터 저장

## 2. 사전 조건

- Python 3.13
- `uv`
- SQLite file을 쓸 수 있는 로컬 작업 폴더
- 랜덤한 `DEVICE_CREDENTIAL_PEPPER`
- 랜덤한 `DEV_GAME_DEVICE_TOKEN`
- 외부 LLM 없이 평가하려면 `LLM_PROVIDER=mock`

`.env.example`은 현재 `LLM_PROVIDER=local`과 평문 remote URL을 예제로 둔다. 복사 후 반드시
Mock으로 바꾸고 remote key/base URL을 비운다.

```dotenv
LLM_PROVIDER=mock
EMBEDDING_PROVIDER=mock
OPENAI_API_KEY=
LOCAL_LLM_API_KEY=
LOCAL_LLM_BASE_URL=
ADMIN_API_TOKEN=
TRANSCRIPT_ENABLED=false
LONG_TERM_MEMORY_ENABLED=false
```

기억 기능을 평가할 때만 transcript와 long-term memory를 켠다. 이 경우 가짜 발화만 사용하고
평가 뒤 DB와 transcript의 삭제 범위를 확인한다.

## 3. repository가 선언한 설치·검증 명령

아래 명령은 코드 스냅샷의 README와 CI가 선언한 절차다. 이번 감사에서는 실행하지 않았다.

```powershell
uv sync --locked --dev
uv run alembic upgrade head
uv run ruff check .
uv run mypy
uv run pytest
```

각 단계가 성공하기 전에는 다음 단계의 결과를 신뢰하지 않는다. 특히 test fixture의
`create_all()` 성공을 실제 migration 성공으로 대신하지 않는다.

## 4. 단일 프로세스 시작

품질 Gate와 migration을 통과한 경우에만 loopback에 시작한다.

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

`--reload`와 다중 worker는 transcript, in-memory conversation, memory distillation 평가에 사용하지
않는다. import-time DB bootstrap과 프로세스 로컬 상태 때문에 결과가 달라질 수 있다.

## 5. 시작 후 수동 Gate

`GET /health`의 `status=ok`만으로 준비 완료 처리하지 않는다. 다음을 별도로 확인한다.

1. `alembic current`가 head revision인지 확인
2. DB file 경로가 의도한 평가 폴더인지 확인
3. `DEVICE_CREDENTIAL_PEPPER`와 bootstrap token이 비어 있지 않은지 확인
4. Mock provider가 실제 선택됐는지 확인
5. register -> pairing code -> pair -> authenticated chat 순서 확인
6. token 원문과 pairing code가 terminal/log에 남지 않는지 확인
7. 잘못된 token, 폐기 token, 만료/재사용 code가 거부되는지 확인
8. body 초과와 request timeout이 공통 ErrorEnvelope로 반환되는지 확인
9. 동일 Chat request를 재시도하면 중복 처리된다는 현재 제한을 기록
10. 종료 후 열린 HTTP client와 DB engine이 정리되는지 확인

기존 `scripts/onboard_smoke.sh`는 token prefix와 pairing code를 출력하므로 공유 terminal이나 CI에서
사용하지 않는다.

## 6. 기능 평가 순서

### A. 무상태 Mock Chat

- transcript와 long-term memory를 끈다.
- `allowed_commands=[]`로 일반 대사만 확인한다.
- 잘못된 schema, 추가 field, 중복 allowed command를 거부하는지 확인한다.

### B. Command 후보

- 단 하나의 command만 allowlist에 넣는다.
- 반환 candidate의 type, request ID, issued/expires time을 검사한다.
- allowlist 밖 command가 절대 반환되지 않는지 확인한다.
- UE에서는 별도로 target/state/expiry/duplicate를 Command Gateway에서 검증한다.

### C. Pairing과 role

- GameClient만 pairing code를 발급할 수 있는지 확인한다.
- WebClient가 Game task transition을 호출하면 403인지 확인한다.
- code 만료, 1회 사용, 동시 사용을 확인한다.
- rate limit이 없으므로 외부 네트워크에는 노출하지 않는다.

### D. Offline Task

- 현재 quantity 검증 결함을 고치기 전에는 직접 API quantity를 사용하지 않는다.
- Scouting을 평가 대상에서 제외한다.
- create retry와 상태 전이 경쟁을 별도로 구분한다.

### E. 기억

- 가짜 profile/save/companion만 사용한다.
- 두 companion 사이 memory isolation은 현재 보장되지 않는다고 표시한다.
- transcript 파일과 SQLite episodic memory를 모두 검사한다.
- graceful shutdown 이전의 미증류 tail과 종료 중 새 turn 경쟁을 확인한다.
- 사용자 삭제 API가 없으므로 평가 종료 후 수동 데이터 제거 계획을 먼저 세운다.

## 7. 데이터와 복구

현재 repository에는 완성된 backup/restore 절차가 없다. 평가 데이터라도 다음을 하나의 세트로
취급한다.

- SQLite DB와 WAL/SHM
- transcript directory
- access log
- migration revision
- secret을 제외한 설정 목록

복구 검증 없이 file 일부만 복사해 운영 데이터로 승격하지 않는다. pepper를 바꾸면 기존 device
token과 HMAC 기반 memory/conversation key의 접근 의미가 바뀌므로 secret rotation은 단순 환경변수
교체로 처리하지 않는다.

## 8. 평가 종료 기준

- 실행한 exact command와 결과를 기록
- 사용한 revision/provenance를 기록
- 생성된 DB/transcript/log 위치를 기록
- 실제 데이터가 없음을 확인
- 발견한 계약 차이를 공식 Backend 담당자에게 전달
- 평가 결과를 이유로 UE/Web 공식 DTO를 변경하지 않음

