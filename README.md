# AIRE Server

MAKO의 HTTP/WebSocket Chat, 상황 대사, 장기기억, 게임 데이터와 Offline Task를 제공하는
FastAPI 서버입니다.

현재 제품은 단일 플레이어를 전제로 합니다.

- GameClient Bearer: `AIRE_GAME`
- WebClient Bearer: `AIRE_WEB`
- Profile: `AIRE_OPEN`
- Save Slot: `demo-slot-1`
- Companion: `mako`

`AIRE_GAME`과 `AIRE_WEB`은 서로 다른 계정이 아니라 같은 플레이어가 사용하는 UE와 Mobile
Web surface입니다. 두 클라이언트가 같은 기억과 Offline Task를 보는 것이 정상 동작입니다.

## 처음 실행하기

필수 도구는 Python 3.13과 [uv](https://docs.astral.sh/uv/)입니다. 명령은 저장소 루트에서
실행합니다. 저장소가 Private이므로 처음 clone하는 PC는 GitHub read 권한과 인증이 필요합니다.

먼저 인증을 확인합니다.

```powershell
gh auth status
```

실패하면 `gh auth login --hostname github.com --git-protocol https --web` 실행 후
`gh auth setup-git`을 실행합니다. 인증 성공 뒤에만 다음 명령을 실행합니다.

```powershell
$sourceRoot = Join-Path $env:USERPROFILE "source"
New-Item -ItemType Directory -Force $sourceRoot | Out-Null
Set-Location $sourceRoot
git clone https://github.com/AIREProject/AIRE_SERVER.git
Set-Location .\AIRE_SERVER
uv sync --dev
New-Item -ItemType Directory -Force data
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8010
```

브라우저에서 다음 주소를 확인합니다.

- Health: `http://127.0.0.1:8010/health`
- Readiness: `http://127.0.0.1:8010/ready`
- Swagger UI: `http://127.0.0.1:8010/docs`
- OpenAPI JSON: `http://127.0.0.1:8010/openapi.json`

`.env` 없이 실행하면 SQLite와 Mock LLM을 사용합니다. `companion.db` 파일만 생겼다고 DB가
준비된 것은 아닙니다. Chat은 테이블을 자동 생성하지 않으므로 서버 시작 전에 반드시
`uv run alembic upgrade head`를 실행해야 합니다.

## 첫 Chat 확인

서버를 실행한 PowerShell과 별도 창에서 호출합니다.

```powershell
$requestId = "quickstart-1"
$headers = @{
    Authorization = "Bearer AIRE_GAME"
    "X-Request-ID" = $requestId
}
$body = @{
    schema_version = 1
    request_id = $requestId
    session_id = "quickstart-session-1"
    save_slot_id = "demo-slot-1"
    companion_id = "mako"
    message_id = "quickstart-message-1"
    user_message = "안녕"
    surface = "game"
    time_context = @{
        source = "GameWorld"
        day = 1
        hour = 12
        period = "Afternoon"
    }
    recent_event_ids = @()
    game_context = @{
        schema_version = 1
        location_id = "forest_camp"
        threat = @{
            present = $false
            count = 0
            nearest_kind = $null
        }
        nearby_resources = @()
        available_workstations = @()
        current_work = $null
        inventories = @()
    }
    allowed_commands = @()
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8010/api/v1/chat" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
```

첫 유효 요청에서 `AIRE_OPEN` Profile과 고정 Device 행이 DB에 자동 생성됩니다.

## LLM 선택

기본값은 외부 연결이 없는 `mock`입니다. OpenAI 또는 OpenAI-compatible Local LLM을 쓰려면
`.env.example`을 `.env`로 복사하고 필요한 값만 변경합니다.

```powershell
Copy-Item .env.example .env
```

정확한 설정과 검증 방법은 [LLM 설정](docs/llm-setup.md)을 따릅니다.

## 데이터 보존

다음 항목은 Git에 올리지 않습니다.

- `.env`: API key와 운영 설정
- `.venv/`: `uv sync`로 재생성되는 설치 결과
- `data/`: SQLite DB, 요청 로그, 대화 전사와 기억 원본

다른 서버로 상태를 옮길 때는 Git으로 코드를 받은 뒤 `data/`를 별도 백업에서 복원합니다.
새 상태로 시작할 때는 빈 `data/`를 만들고 migration을 실행합니다.

## 문서

- [원격 운영 단일 가이드](REMOTE_SERVER_OPERATIONS.md) — 같은 LAN의 SSH 접속, user systemd, 한 줄 배포와 장애 대응
- [이전 원격 배포 문서 안내](SERVER_REMOTE_DEPLOY_SETUP.md) — 과거 generic 예시 대신 단일 가이드로 연결
- [인수인계·운영 가이드](docs/handoff.md) — 새 PC 설치, DB, 실행, 백업, 복구와 장애 대응
- [공개 서버 배포 작업서](docs/하는방법.md) — 폴더를 통째로 전달해 기존 설정·DB를 보존하며 교체하고 검증
- [CAI-P1~P5 서버 작업 체크리스트](docs/CAI_SERVER_DEPLOYMENT_CHECKLIST.md) — Git 반영 후 운영 서버에서 수행할 설정·migration·LLM·smoke 절차
- [LLM 설정](docs/llm-setup.md) — Mock, OpenAI, Local LLM과 Embedding
- [API 사용법](docs/api-endpoints.md) — 고정 인증, Chat, Situation, Offline Task와 오류
- [게임 데이터](docs/game-data.md) — seed 데이터, migration과 수정 절차
- [현재 제한과 임시 경계](docs/temporary-scaffolds.md) — 의도적으로 남긴 미구현·제약

## 검증

```powershell
uv run pytest
uv run ruff check .
uv run mypy
```

Unreal Engine이나 WebClient를 실행하지 않아도 위 세 명령으로 서버 코드 기준선을 검증할 수
있습니다.
