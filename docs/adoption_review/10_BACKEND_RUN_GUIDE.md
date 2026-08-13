# 10. Backend 실행 가이드

대상: `C:\workspace\Github\AI_RE\ai_server`  
환경: Windows PowerShell, Python 3.13, `uv`

## 1. 결론

최소 실행에는 `.env` 파일이 필요하지 않다. 코드 기본값은 다음과 같다.

- `LLM_PROVIDER=mock`
- `DATABASE_URL=sqlite+aiosqlite:///./data/companion.db`
- GameClient Bearer `AIRE_GAME`
- WebClient Bearer `AIRE_WEB`
- 공통 profile `AIRE_OPEN`
- 공통 Save Slot `demo-slot-1`
- 공통 Companion `mako`

고정 Bearer 두 개는 `DEVICE_CREDENTIAL_PEPPER`와 `DEV_GAME_DEVICE_TOKEN`을 사용하지 않는다.
이 값들은 호환성을 위해 남긴 기존 랜덤 device 등록·pairing API에만 필요하다.

현재 제품은 단일 플레이어만 전제한다. `AIRE_GAME`과 `AIRE_WEB`은 서로 다른 계정이 아니라
같은 플레이어의 UE/Web surface이며, `AIRE_OPEN / demo-slot-1 / mako`의 기억과 상태를 의도적으로
공유한다. 사용자·Save Slot·Companion 선택 기능은 구현하지 않는다.

## 2. 최초 실행

```powershell
Set-Location C:\workspace\Github\AI_RE\ai_server

uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`uv sync --dev`는 최초 실행 또는 dependency lock 변경 뒤에만 다시 실행하면 된다.
`alembic upgrade head`는 최초 DB 생성과 migration 갱신 때 실행한다.

## 3. `.env`가 필요한 경우

OpenAI, Local LLM, 로그 경로, timeout 또는 DB 경로를 바꿀 때만 만든다.

```powershell
Copy-Item .env.example .env
```

현재 `.env.example`의 `LLM_PROVIDER`는 `mock`이므로 복사 직후에도 외부 LLM 없이 실행된다.
고정 Bearer만 사용할 때 아래 값을 채울 필요가 없다.

```dotenv
DEVICE_CREDENTIAL_PEPPER=
DEV_GAME_DEVICE_TOKEN=
```

## 4. 실행 확인

API 문서:

```text
http://127.0.0.1:8000/docs
```

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## 5. GameClient Chat 확인

```powershell
$headers = @{
    Authorization = "Bearer AIRE_GAME"
    "Content-Type" = "application/json"
    "X-Request-ID" = "test-request-1"
}

$body = @{
    schema_version = 1
    request_id = "test-request-1"
    session_id = "test-session-1"
    save_slot_id = "demo-slot-1"
    companion_id = "mako"
    user_message = "안녕 마코"
    surface = "game"
    recent_event_ids = @()
    game_context = @{}
    allowed_commands = @()
} | ConvertTo-Json -Depth 8

Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/api/v1/chat `
    -Headers $headers `
    -Body $body
```

WebClient API는 다음 Bearer를 사용한다.

```powershell
$headers.Authorization = "Bearer AIRE_WEB"
```

## 6. 종료와 재실행

foreground 실행은 `Ctrl+C`로 종료한다. 재실행은 migration이나 dependency가 바뀌지 않았다면
다음 명령 하나면 충분하다.

```powershell
Set-Location C:\workspace\Github\AI_RE\ai_server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

터미널을 닫으면 서버도 종료된다. 현재 저장소에는 Windows Service, systemd 또는 process
supervisor 설정이 없다.

## 7. 선택 검증

```powershell
uv run pytest
uv run ruff check .
uv run mypy
```

## 8. `api.mtvs2026.work` Docker 배포 가정

현재 `ai_server` 폴더에는 `Dockerfile`, `.dockerignore`, `compose.yml` 또는
`docker-compose.yml`이 없다. 따라서 아래 절차는 `api.mtvs2026.work` 서버의 별도 배포
디렉터리에 Compose 파일이 있다는 가정이다. 실제 service 이름, image registry, source 전달
방식과 volume 경로는 서버에서 확인해야 한다.

로컬 Windows에서 Uvicorn을 실행하는 것만으로 원격 주소는 갱신되지 않는다. 수정 소스 또는
새 image가 원격 서버에 전달되고, 그 서버의 Backend container가 새 image로 재생성되어야 한다.

### 8.1 현재 배포 형태 확인

SSH로 서버에 접속한 뒤 Compose 파일이 있는 배포 디렉터리에서 실행한다.

```bash
docker compose config --services
docker compose ps
docker compose images
```

Backend service 이름을 확인해 이후 명령의 `<backend-service>`를 실제 값으로 바꾼다.

현재 container command와 mount를 확인한다.

```bash
docker compose config
docker inspect <backend-container> --format '{{json .Config.Cmd}}'
docker inspect <backend-container> --format '{{json .Mounts}}'
```

SQLite `data/companion.db`가 named volume 또는 host bind mount에 포함되는지 반드시 확인한다.
volume이 없으면 container 재생성 시 DB가 사라질 수 있다.

### 8.2 서버에서 source를 build하는 Compose

수정 소스를 서버 배포 디렉터리에 반영한 뒤 다음 순서로 진행한다.

```bash
docker compose build <backend-service>
docker compose run --rm <backend-service> uv run alembic upgrade head
docker compose up -d --no-deps <backend-service>
```

### 8.3 registry image를 pull하는 Compose

CI나 다른 머신이 image를 build·push하는 구조라면 새 tag를 Compose 환경에 반영한 뒤 실행한다.

```bash
docker compose pull <backend-service>
docker compose run --rm <backend-service> uv run alembic upgrade head
docker compose up -d --no-deps <backend-service>
```

`docker compose down`, `docker volume rm`, `docker system prune`은 이 갱신 절차에 사용하지 않는다.
특히 SQLite volume을 삭제하면 기존 profile, task와 memory 데이터가 복구되지 않는다.

### 8.4 재시작 확인

```bash
docker compose ps
docker compose logs --tail=200 <backend-service>
curl -fsS https://api.mtvs2026.work/health
curl -fsS https://api.mtvs2026.work/openapi.json > /tmp/aire-openapi.json
```

`/docs`는 실행 중인 FastAPI의 `/openapi.json`을 읽으므로 container가 새 코드로 재생성되면
별도 문서 업로드 없이 갱신된다. 브라우저에 이전 Schema가 남으면 문서를 새로고침한다.

고정 Bearer 값은 OpenAPI Schema 필드가 아니라 런타임 인증 값이므로 배포만 해서는 Swagger
설명에 `AIRE_GAME`과 `AIRE_WEB` 문구가 자동 표시되지 않는다. Swagger 화면에 값을 보이려면
FastAPI description 또는 security scheme 설명을 별도로 추가해야 한다.

### 8.5 고정 GameClient 확인

```bash
curl -fsS \
  -X POST https://api.mtvs2026.work/api/v1/chat \
  -H 'Authorization: Bearer AIRE_GAME' \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: deploy-check-1' \
  -d '{
    "schema_version": 1,
    "request_id": "deploy-check-1",
    "session_id": "deploy-session-1",
    "save_slot_id": "demo-slot-1",
    "companion_id": "mako",
    "user_message": "배포 확인",
    "surface": "game",
    "recent_event_ids": [],
    "game_context": {},
    "allowed_commands": []
  }'
```

### 8.6 Rollback 원칙

새 container가 실패하면 DB volume을 보존한 채 이전 image tag로 되돌리고 Backend service만
재생성한다. migration downgrade는 migration별 데이터 손실 가능성을 검토하기 전에는 실행하지
않는다.

```bash
# Compose의 image tag를 이전 값으로 되돌린 뒤 실행한다.
docker compose up -d --no-deps <backend-service>
```

### 8.7 아직 확인이 필요한 실제 서버 정보

- SSH host와 배포 디렉터리
- Compose 파일명과 Backend service/container 이름
- image를 서버에서 build하는지 registry에서 pull하는지
- reverse proxy가 연결하는 container port/network
- SQLite DB와 transcript/memory의 volume mount
- 현재 image tag와 rollback tag
