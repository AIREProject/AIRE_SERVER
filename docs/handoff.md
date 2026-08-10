# AIRE Server 인수인계·운영 가이드

이 문서는 저장소를 처음 받은 사람이 새 Windows 또는 Linux PC에서 서버와 DB를 구성하고,
업데이트·백업·복구할 수 있도록 현재 코드 기준으로 작성했습니다.

## 1. 준비물

- Git
- Python 3.13 (`>=3.13,<3.14`)
- [uv](https://docs.astral.sh/uv/)
- 서버를 열 포트. 기본 예시는 `8000`

Dockerfile과 Compose 파일은 현재 저장소에 없습니다. 이 문서의 `uv + Alembic + Uvicorn`
절차가 검증 가능한 기준입니다. Docker로 감쌀 때도 실행 순서는 동일합니다.

## 2. 새 PC에서 처음 실행

### 2.1 저장소와 의존성

Windows PowerShell:

```powershell
git clone https://github.com/AIREProject/AIRE_SERVER.git
Set-Location AIRE_SERVER
uv sync --dev
```

Linux:

```bash
git clone https://github.com/AIREProject/AIRE_SERVER.git
cd AIRE_SERVER
uv sync --dev
```

`.venv/`는 Git에 포함되지 않습니다. `uv sync --dev`가 `uv.lock`을 기준으로 해당 PC에 맞는
환경을 생성합니다.

### 2.2 기본 설정

외부 LLM 없이 먼저 확인할 때는 `.env`가 필요하지 않습니다. 코드 기본값은 다음과 같습니다.

```text
LLM_PROVIDER=mock
EMBEDDING_PROVIDER=mock
DATABASE_URL=sqlite+aiosqlite:///./data/companion.db
```

설정을 바꿀 때만 파일을 만듭니다.

```powershell
Copy-Item .env.example .env
```

```bash
cp .env.example .env
```

`.env`는 Git에 올리지 않습니다.

### 2.3 DB 생성과 migration

Chat 요청은 DB 테이블을 자동 생성하지 않습니다. 서버보다 먼저 migration을 실행합니다.

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force data
uv run alembic upgrade head
uv run alembic current
```

Linux:

```bash
mkdir -p data
uv run alembic upgrade head
uv run alembic current
```

정상 기준 Alembic revision은 `0007`입니다. Migration은 Profile, Device, Save Slot,
Offline Task, 장기기억과 게임 데이터 테이블을 만들고 기본 Item/Recipe/Enemy 데이터를
적재합니다.

`data/companion.db`가 4KB 정도로 생겼어도 `alembic current`가 비어 있으면 준비되지 않은
DB입니다. 다시 `uv run alembic upgrade head`를 실행합니다.

### 2.4 서버 실행

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

운영 기준은 단일 process/worker입니다. SQLite와 process-local 대화 상태를 사용하므로
`--workers 2` 이상으로 늘리지 않습니다.

### 2.5 실행 확인

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

예상 예시:

```json
{
  "service": "mako-companion",
  "status": "ok",
  "llm_provider": "mock"
}
```

`/health`는 프로세스와 설정값만 확인합니다. DB readiness나 실제 LLM 연결 성공을 확인하지
않습니다. DB는 `alembic current`, LLM은 실제 Chat 응답의 `ai_metadata`와 응답 내용을 함께
확인합니다.

Swagger UI:

```text
http://127.0.0.1:8000/docs
```

## 3. 제품 인증과 첫 요청

현재 제품 경로는 별도 가입이나 pairing을 사용하지 않습니다.

| Client | Bearer | Role |
|---|---|---|
| UE GameClient | `AIRE_GAME` | `GameClient` |
| Mobile WebClient | `AIRE_WEB` | `WebClient` |

두 역할은 `AIRE_OPEN / demo-slot-1 / mako`를 공유합니다. Migration 뒤 첫 유효 요청에서
Profile과 해당 고정 Device 행이 자동 생성됩니다.

Chat 예시는 [API 사용법](api-endpoints.md)을 따릅니다.

## 4. LLM 연결

먼저 `mock`으로 서버·DB·Chat이 정상인지 확인한 뒤 OpenAI 또는 Local LLM을 연결합니다.

1. `.env.example`을 `.env`로 복사합니다.
2. `LLM_PROVIDER`와 해당 provider 설정을 채웁니다.
3. 서버를 완전히 재시작합니다. 설정은 hot reload하지 않습니다.
4. Chat을 한 번 호출합니다.
5. 응답 `ai_metadata.provider`와 `model_version`을 확인합니다.

자세한 값과 Local LLM 호환 요구사항은 [LLM 설정](llm-setup.md)을 따릅니다.

## 5. 코드 구조

| 경로 | 역할 |
|---|---|
| `app/main.py` | FastAPI 조립, router와 app-lifetime service 생성 |
| `app/settings.py` | `.env`와 기본 설정의 권위 |
| `app/models.py` | Chat/Situation HTTP 계약 |
| `app/routes/` | HTTP/WS endpoint |
| `app/service.py` | 외부 계약과 MAKO brain 사이 단일 변환 경계 |
| `app/brain/` | 의도 분류, 대사, 기억과 LLM provider |
| `app/db/` | SQLAlchemy model과 repository |
| `app/gamedata/` | 기본 게임 데이터셋 |
| `migrations/versions/` | DB Schema와 seed 변경 이력 |
| `tests/` | API, service, migration과 실패 경로 검증 |

## 6. 데이터와 백업

### 6.1 `data/`에 저장되는 것

| 경로 | 내용 | 복구 필요성 |
|---|---|---|
| `data/companion.db` | Profile, Device, Save Slot, Offline Task, 장기기억, 게임 데이터 | 상태 유지 시 필수 |
| `data/companion.db-wal`, `*.db-shm` | SQLite WAL 보조 파일 | 서버 실행 중 DB와 한 세트 |
| `data/transcripts/` | 대화 원문 JSONL | 새 장기기억 증류를 이어갈 때 필요 |
| `data/requests.log*` | 요청 경로·상태·시간 메타데이터 | 운영 분석용, 복구에는 선택 |
| `data/memories/` | Migration 0005가 읽는 옛 JSON 기억 | 기존 JSON 기억을 이전할 때만 필요 |

### 6.2 안전한 백업

가장 단순한 방법은 서버를 정상 종료한 뒤 `data/` 전체를 복사하는 것입니다.

Windows PowerShell 예시:

```powershell
Compress-Archive -Path data -DestinationPath aire-data-backup.zip
```

실행 중인 SQLite 파일만 따로 복사하지 않습니다. WAL에 아직 반영되지 않은 변경이 있을 수
있습니다.

### 6.3 다른 PC로 상태 이전

1. 기존 서버를 종료합니다.
2. 기존 `data/` 전체를 별도 파일 또는 안전한 저장소로 옮깁니다.
3. 새 PC에서는 Git으로 코드를 clone합니다.
4. `uv sync --dev`를 실행합니다.
5. 새 저장소의 빈 `data/` 대신 백업한 `data/`를 복원합니다.
6. `uv run alembic upgrade head`로 현재 코드 revision까지 올립니다.
7. 서버를 시작하고 Chat과 Task 목록을 확인합니다.

기존 상태가 필요 없으면 `data/`를 복원하지 않고 빈 디렉터리에서 migration을 실행합니다.

## 7. 업데이트 절차

```text
서버 정상 종료
→ data 백업
→ git pull
→ uv sync --dev
→ uv run alembic upgrade head
→ pytest/Ruff/MyPy
→ 서버 시작
→ health와 Chat 확인
```

Migration downgrade는 데이터 손실 가능성을 검토하기 전에는 실행하지 않습니다.

## 8. Docker로 옮길 때

현재 저장소에는 Dockerfile과 Compose가 없습니다. 인프라에서 Docker를 만들 때 다음 조건을
반드시 반영합니다.

- 작업 디렉터리는 저장소 루트
- Python 3.13
- dependency는 `uv.lock` 기준 설치
- container 시작 전 `uv run alembic upgrade head`
- `data/`는 container writable volume으로 연결
- `.env` 또는 환경변수는 image에 포함하지 않고 runtime에 주입
- Uvicorn worker는 1개
- 외부에는 FastAPI port만 노출하고 Local LLM/DB port는 별도 정책으로 관리

권장 시작 명령의 의미는 다음과 같습니다.

```text
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

실제 Dockerfile/Compose가 추가되기 전에는 문서만 보고 `docker compose up`이 된다고 가정하지
않습니다.

## 9. 자주 발생하는 문제

### `no such table` 또는 DB 관련 500/503

저장소 루트에서 실행했는지 확인하고 migration을 다시 적용합니다.

```powershell
uv run alembic upgrade head
uv run alembic current
```

### Health는 되는데 Chat이 실패함

Health는 DB와 LLM readiness를 검사하지 않습니다. 다음을 각각 확인합니다.

- DB: `alembic current`
- 인증: `Bearer AIRE_GAME` 또는 `Bearer AIRE_WEB`
- body: `demo-slot-1`, `mako`, 올바른 `surface`
- LLM: [LLM 설정](llm-setup.md)의 provider 검증

### `ai_metadata.provider=mock`

선택한 provider의 필수 key가 비어 있으면 서버는 시작에 실패하지 않고 Mock provider를
선택합니다. `.env`의 provider, key, model을 확인하고 서버를 재시작합니다.

### Local LLM인데 항상 정해진 문장만 나옴

Local endpoint가 JSON Schema response format 또는 Chat Completions 계약을 지원하지 않으면
각 호출이 Mock fallback으로 복구될 수 있습니다. Base URL의 `/v1`, model name, API key와
Local LLM 로그를 확인합니다.

### 장기기억이 생기지 않음

- Mock LLM은 기억을 추출하지 않습니다.
- `TRANSCRIPT_ENABLED=false`이면 새 기억의 원본이 없습니다.
- 기억 증류는 background interval과 quiet/session-end 시간을 기다립니다.
- `LONG_TERM_MEMORY_ENABLED=false`이면 저장·회수가 비활성화됩니다.

## 10. 검증 명령

```powershell
uv run pytest
uv run ruff check .
uv run mypy
```

`mypy`에는 별도 경로를 붙이지 않습니다. `pyproject.toml`이 `app` 전체를 지정합니다.
