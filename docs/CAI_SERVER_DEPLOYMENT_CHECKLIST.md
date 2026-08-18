# CAI-P1~P5 서버 작업 체크리스트

이 문서는 Git push가 끝난 뒤 **운영 서버에서만** 수행할 작업입니다. 관리 PC 작업, 코드 수정,
Web 배포는 포함하지 않습니다.

## 서버 고정 정보

- 사용자: `mtvs-1`
- 저장소: `/home/mtvs-1/workspace/AIRE_SERVER`
- Backend service: user systemd `aire-server.service`
- 배포 명령: `/home/mtvs-1/.local/bin/deploy-aire-server`
- Backend 내부 주소: `127.0.0.1:8000`
- Local LLM 주소: 서버의 기존 `.env`에 설정된 OpenAI-compatible `/v1` 주소
- 공개 주소: `https://traip.mtvs2026.work`

## 1. 접속 후 현재 상태 확인

```bash
cd /home/mtvs-1/workspace/AIRE_SERVER
systemctl --user is-active aire-server.service
git branch --show-current
git status --short
git rev-parse HEAD
curl -fsS https://traip.mtvs2026.work/health
```

다음 조건을 모두 만족해야 계속합니다.

- service가 `active`
- branch가 `main`
- Git 작업 트리가 깨끗함
- 기존 `/health`가 HTTP 200

하나라도 다르면 배포하지 말고 원인부터 확인합니다.

## 2. DB·data·환경설정 백업

```bash
cd /home/mtvs-1/workspace/AIRE_SERVER
backup_stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="/home/mtvs-1/workspace/AIRE_SERVER/backups/predeploy_${backup_stamp}"
mkdir -p "$backup_dir"
cp -a .env "$backup_dir/.env"
cp -a data "$backup_dir/data"
printf '%s\n' "$backup_dir"
```

출력된 `backup_dir`을 배포 기록에 남깁니다. DB가 실행 중이므로 이 복사본은 비상 보존본이고,
배포 스크립트가 service를 정지한 뒤 만드는 data 백업도 반드시 성공해야 합니다.

## 3. 서버 `.env` 수정

`.env`는 Git에서 내려오지 않으므로 서버에서 직접 설정합니다. `.env.example`로 운영 `.env`를
통째로 덮어쓰지 말고, 아래 항목만 추가·수정합니다. 기존 DB 주소, API key와 token은 유지하며
화면이나 채팅에 복사하지 않습니다.

```bash
cd /home/mtvs-1/workspace/AIRE_SERVER
cp -a .env ".env.before-cai-${backup_stamp}"
nano .env
```

아래 값이 정확히 있어야 합니다.

```dotenv
LLM_PROVIDER=local

COMPANION_PROMPT_VERSION=companion-v4
TRANSCRIPT_ENABLED=false
TRANSCRIPT_RETENTION_DAYS=1
USER_MESSAGE_RETENTION_DAYS=7
COMPANION_MESSAGE_RETENTION_DAYS=7
GAME_EVENT_RETENTION_DAYS=7
AUDIT_RETENTION_DAYS=30

MEMORY_WORKER_ENABLED=true
MEMORY_WORKER_INTERVAL_SECONDS=5
MEMORY_WORKER_LEASE_SECONDS=60
MEMORY_WORKER_MAX_ATTEMPTS=3
MEMORY_WORKER_BATCH_SIZE=32

LEGACY_TRANSCRIPT_QUARANTINE_DIR=data/transcript_quarantine
LEGACY_TRANSCRIPT_QUARANTINE_DAYS=30
```

아래 세 값은 서버마다 다를 수 있으므로 새 예시 값으로 덮어쓰지 말고, 현재 정상 동작하는 운영
값을 그대로 유지합니다.

```dotenv
LOCAL_LLM_BASE_URL=현재 서버의 OpenAI-compatible /v1 주소
LOCAL_LLM_MODEL=현재 서버에 설치된 실제 모델 ID
LOCAL_LLM_API_KEY=기존 서버 secret
```

위 코드 블록의 설명 문구를 `.env`에 그대로 입력하면 안 됩니다. 실제 기존 값을 유지하고,
`LOCAL_LLM_BASE_URL`이 반드시 `/v1`까지 포함하는지만 확인합니다.

## 4. Local LLM Runtime 확인

LLM Runtime을 먼저 실행한 뒤 같은 서버에서 확인합니다. API key를 명령문에 직접 적지 않습니다.

```bash
cd /home/mtvs-1/workspace/AIRE_SERVER
set -a
. ./.env
set +a
curl -fsS \
  -H "Authorization: Bearer ${LOCAL_LLM_API_KEY}" \
  "${LOCAL_LLM_BASE_URL}/models"
```

HTTP 오류나 연결 실패가 나면 Backend 배포 전에 LLM Runtime부터 복구합니다.

## 5. 코드 배포·migration·재시작

```bash
/home/mtvs-1/.local/bin/deploy-aire-server
```

배포 명령은 service 정지, data 백업, `git pull --ff-only`, `uv sync --frozen`, Alembic upgrade,
service 시작을 순서대로 수행합니다. 완료 후 revision을 다시 확인합니다.

```bash
cd /home/mtvs-1/workspace/AIRE_SERVER
uv run alembic current
systemctl --user status aire-server.service --no-pager
journalctl --user -u aire-server.service -n 150 --no-pager
```

정상 revision은 `0016 (head)`입니다. migration 실패 시 downgrade하지 말고 service를 중지한 뒤
백업과 첫 오류를 보존합니다.

## 6. Legacy Transcript 이관

과거 Transcript가 백업에 있을 때만 수행합니다. 원본 백업은 보존하고 작업 복사본을 사용합니다.
`<backup_dir>`는 2단계에서 기록한 실제 절대 경로로 바꿉니다.

```bash
legacy_work="/tmp/aire-legacy-${backup_stamp}"
cp -a "<backup_dir>/data/transcripts" "$legacy_work"

cd /home/mtvs-1/workspace/AIRE_SERVER
uv run python -m scripts.import_legacy_transcripts \
  --dry-run \
  --source-dir "$legacy_work"
```

hash, 건수, invalid row를 확인한 뒤 apply합니다.

```bash
uv run python -m scripts.import_legacy_transcripts \
  --apply \
  --source-dir "$legacy_work" \
  --quarantine-dir data/transcript_quarantine
```

importer는 player 원문만 `LegacyUnknown` source로 enqueue합니다. companion 발화는 가져오지 않으며,
Memory worker가 Recipe·Command·현재 게임 상태와 중요하지 않은 발화를 다시 거부합니다. 성공 원본은
30일 quarantine 후 hash를 재검증해 삭제됩니다.

apply report와 quarantine 파일을 확인한 뒤 `/tmp` 작업 복사본만 지웁니다.

```bash
test "$legacy_work" = "/tmp/aire-legacy-${backup_stamp}"
rm -rf -- "$legacy_work"
```

## 7. 실제 LLM Memory 행렬

```bash
cd /home/mtvs-1/workspace/AIRE_SERVER
uv run python -m scripts.evaluate_live_memory_classifier
```

정상 결과는 `status=passed`, `runs_per_case=3`입니다. `blocked` 또는 `failed`이면 배포 완료로
처리하지 않습니다.

## 8. Readiness와 공개 계약 확인

`0016`부터 Web 제작 예약이 서버 Game State Inventory를 차감합니다. 배포 전에 반드시
`alembic upgrade head`를 실행하고 `/openapi.json`에서 `Command.CraftItem`과
`X-Base-State-Version`을 확인합니다. 기존 Game State Snapshot이 없는 사용자는 게임에서
Inventory를 한 번 동기화하기 전까지 Web 제작이 `InventorySnapshotRequired`로 거절되는 것이
정상입니다. 재료 부족은 `InsufficientCraftingMaterials`이며 Task나 부분 차감이 생기면 안 됩니다.

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
curl -fsS https://traip.mtvs2026.work/ready
curl -fsS https://traip.mtvs2026.work/openapi.json -o /tmp/aire-openapi.json
```

`/ready`의 정상 기준:

- HTTP 200
- `database=ready`
- `database_revision=0016`
- `status=ready`, 또는 Mock fallback을 의도적으로 허용한 경우에만 `degraded`

OpenAPI 경로를 검사합니다.

```bash
cd /home/mtvs-1/workspace/AIRE_SERVER
uv run python - <<'PY'
import json

required = {
    "/ready",
    "/api/v1/events",
    "/api/v1/command-results",
    "/api/v1/memories",
    "/api/v1/memories/search",
    "/api/v1/memories/reset",
    "/api/v1/memories/{memory_id}",
}
with open("/tmp/aire-openapi.json", encoding="utf-8") as handle:
    paths = set(json.load(handle)["paths"])
missing = sorted(required - paths)
print({"missing": missing})
raise SystemExit(1 if missing else 0)
PY
```

## 9. API smoke

Memory 목록과 Recipe 계층 응답을 확인합니다.

```bash
curl -fsS \
  -H 'Authorization: Bearer AIRE_WEB' \
  'https://traip.mtvs2026.work/api/v1/memories?save_slot_id=demo-slot-1&companion_id=mako'

request_id="deploy-recipe-$(date +%Y%m%d%H%M%S)"
curl -fsS \
  -H 'Authorization: Bearer AIRE_WEB' \
  -H "X-Request-ID: ${request_id}" \
  -H 'Content-Type: application/json' \
  --data-binary "{\"schema_version\":1,\"request_id\":\"${request_id}\",\"session_id\":\"deploy-recipe-session\",\"save_slot_id\":\"demo-slot-1\",\"companion_id\":\"mako\",\"message_id\":\"${request_id}\",\"user_message\":\"돌도끼레시피 알려줘\",\"surface\":\"mobile\",\"time_context\":{\"source\":\"RealWorld\",\"day\":1,\"hour\":12,\"period\":\"Afternoon\"},\"recent_event_ids\":[],\"allowed_commands\":[]}" \
  https://traip.mtvs2026.work/api/v1/chat
```

Recipe 응답은 검증된 돌도끼 재료·수량·작업대·시간을 직접 반환해야 합니다. 실패를 자동 성공으로
처리하거나 같은 mutation 요청을 임의로 반복하지 않습니다.

## 10. 서버 작업 완료 조건

- `aire-server.service`가 `active`
- Alembic `0016 (head)`
- 내부·공개 `/ready` HTTP 200
- 실제 LLM 행렬 `passed`
- 공개 OpenAPI의 필수 경로 누락 없음
- Memory 목록과 Recipe smoke 성공
- journal에 migration, worker, provider 반복 오류 없음

여기까지 끝난 뒤에만 별도 Web 배포에서 `VITE_MEMORY_ENABLED=true`로 전환합니다.
