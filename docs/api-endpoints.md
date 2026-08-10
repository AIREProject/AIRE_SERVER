# AIRE Server API 사용법

현행 HTTP 계약의 코드 권위는 `app/models.py`, `app/offline_task_models.py`,
`app/pairing_models.py`와 `app/routes/`입니다. 실행 중인 서버에서는 `/openapi.json`과 `/docs`를
최우선으로 확인합니다.

## 1. 공통 규칙

| 항목 | 값 |
|---|---|
| Base URL | 로컬 예시 `http://127.0.0.1:8010` |
| 제품 인증 | UE `Bearer AIRE_GAME`, Web `Bearer AIRE_WEB` |
| Profile | `AIRE_OPEN` |
| Save Slot | `demo-slot-1` |
| Companion | `mako` |
| Content Type | `application/json` |
| Request ID | body와 `X-Request-ID`가 모두 있으면 같은 값이어야 함 |
| ID 형식 | 영문/숫자로 시작하는 1~128자, 이후 `._:-` 허용 |
| Body 제한 | 기본 256KB |
| Request timeout | 기본 30초 |

Request model은 알 수 없는 body field를 `400 InvalidRequest`로 거부합니다. Response를 사용하는
클라이언트는 필수 field를 검증하고, 모르는 선택 field는 실행 경로에 노출하지 않고 무시합니다.

Chat과 Situation의 같은 `request_id` 재전송은 멱등하지 않습니다. Timeout 뒤 같은 요청을
자동 재전송하면 LLM과 기억 side effect가 중복될 수 있습니다.

## 2. Endpoint 요약

| Method | Path | 제품 사용 |
|---|---|---|
| GET | `/health` | 프로세스·설정 확인 |
| POST | `/api/v1/chat` | UE/Web Chat |
| POST | `/api/v1/situations` | UE가 관찰한 상황에 대한 선제 대사 |
| WS | `/api/v1/chat` | 호환용 WebSocket Chat/Situation |
| POST/GET | `/api/v1/tasks` | Web 작업 생성, UE/Web 목록 조회 |
| POST | `/api/v1/tasks/{id}/start` | GameClient 작업 시작 |
| POST | `/api/v1/tasks/{id}/complete` | GameClient 작업 완료 |
| POST | `/api/v1/tasks/{id}/claim` | GameClient 작업 수령 상태 전환 |
| POST | `/api/v1/tasks/{id}/collect` | WebClient 시간 경과 작업 완료 |
| `/api/v1/devices/*` | 여러 Method | 기존 random token/pairing 호환 경로 |
| `/api/v1/admin/*` | 여러 Method | 운영자 CRUD, `ADMIN_API_TOKEN` 필요 |

## 3. Health

```http
GET /health
```

```json
{
  "service": "mako-companion",
  "status": "ok",
  "llm_provider": "mock"
}
```

Health는 DB migration, DB query와 실제 LLM 호출을 검사하지 않습니다.

## 4. Chat

### 4.1 Game 요청

```http
POST /api/v1/chat
Authorization: Bearer AIRE_GAME
Content-Type: application/json
X-Request-ID: chat-game-1
```

```json
{
  "schema_version": 1,
  "request_id": "chat-game-1",
  "session_id": "game-session-1",
  "save_slot_id": "demo-slot-1",
  "companion_id": "mako",
  "message_id": "game-message-1",
  "user_message": "안녕",
  "surface": "game",
  "time_context": {
    "source": "GameWorld",
    "day": 1,
    "hour": 12,
    "period": "Afternoon"
  },
  "recent_event_ids": [],
  "game_context": {},
  "allowed_commands": []
}
```

AX-I02 대사 표시 단계에서는 `allowed_commands`를 빈 배열로 보냅니다. 이후 UE Command
Gateway가 준비된 명령만 allowlist에 추가합니다.

### 4.2 Mobile 요청

Header는 `Authorization: Bearer AIRE_WEB`을 사용하고 다음 field를 바꿉니다.

```json
{
  "schema_version": 1,
  "request_id": "chat-mobile-1",
  "session_id": "mobile-session-1",
  "save_slot_id": "demo-slot-1",
  "companion_id": "mako",
  "message_id": "mobile-message-1",
  "user_message": "오늘 뭐 할까?",
  "surface": "mobile",
  "time_context": {
    "source": "RealWorld",
    "day": 10,
    "hour": 14,
    "period": "Afternoon"
  },
  "recent_event_ids": [],
  "game_context": {},
  "allowed_commands": []
}
```

현재 `TimeContext`는 `GameWorld`와 `RealWorld` 모두 `day/hour/period` 구조를 사용합니다.
`observed_at`, `timezone`, `interaction_mode`는 계약 field가 아닙니다.

### 4.3 성공 응답

```json
{
  "request_id": "chat-game-1",
  "message_id": "game-message-1",
  "session_id": "game-session-1",
  "save_slot_id": "demo-slot-1",
  "companion_id": "mako",
  "response_id": "response-...",
  "display_text": "안녕. 오늘도 같이 가자.",
  "command_candidates": [],
  "offline_task_id": null,
  "ai_metadata": {
    "provider": "mock",
    "model_version": "mock-v1",
    "prompt_version": "companion-v2"
  }
}
```

Response에는 최상위 `schema_version`, `interaction_mode`, `memory_candidates`가 없습니다.

### 4.4 주요 field

- `session_id`: 한 surface에서 이어지는 최근 대화와 되묻기 상태 범위
- `save_slot_id`: 장기기억과 Offline Task 범위. 현재 `demo-slot-1`
- `companion_id`: 현재 `mako`만 유효
- `surface`: `game` 또는 `mobile`; 말투에 사용
- `game_context`: 최대 32개 property. 현재 서비스는 `location_id`만 직접 사용
- `allowed_commands`: 서버가 반환할 수 있는 command allowlist
- `recent_event_ids`: 최대 32개를 검증하지만 현재 저장·사용하지 않음

## 5. Situation

플레이어 발화 없이 UE가 관찰한 상황을 전달하고 MAKO 대사만 받습니다.

```http
POST /api/v1/situations
Authorization: Bearer AIRE_GAME
Content-Type: application/json
X-Request-ID: situation-1
```

```json
{
  "schema_version": 1,
  "request_id": "situation-1",
  "session_id": "game-session-1",
  "save_slot_id": "demo-slot-1",
  "companion_id": "mako",
  "surface": "game",
  "time_context": {
    "source": "GameWorld",
    "day": 1,
    "hour": 18,
    "period": "Evening"
  },
  "situation": [
    "플레이어 체력이 낮다",
    "주변에 적이 있다"
  ]
}
```

`situation`은 1~4개, 각 1~200자입니다. 응답에는 command candidate가 없습니다.

## 6. Offline Task

### 6.1 역할

| 동작 | Bearer |
|---|---|
| 생성 | `AIRE_WEB` |
| 목록 조회 | `AIRE_GAME`, `AIRE_WEB` |
| 시작 | `AIRE_GAME` |
| 완료 | `AIRE_GAME` |
| Claim | `AIRE_GAME` |
| Collect | `AIRE_WEB` |

### 6.2 생성

```http
POST /api/v1/tasks
Authorization: Bearer AIRE_WEB
Content-Type: application/json
X-Request-ID: task-create-1
```

```json
{
  "request_id": "task-create-1",
  "save_slot_id": "demo-slot-1",
  "task_type": "Gathering",
  "item_id": "Branch",
  "quantity": 5
}
```

Task type은 `Gathering`, `Crafting`, `Scouting`, 상태는 `Pending`, `InProgress`,
`Completed`, `Claimed`입니다.

### 6.3 목록

```http
GET /api/v1/tasks?save_slot_id=demo-slot-1
Authorization: Bearer AIRE_WEB
```

선택 query `status=InProgress`처럼 상태를 필터링할 수 있습니다.

### 6.4 상태 전환

```text
POST /api/v1/tasks/{task_id}/start
POST /api/v1/tasks/{task_id}/complete
POST /api/v1/tasks/{task_id}/claim
POST /api/v1/tasks/{task_id}/collect
```

현재 Claim은 서버 상태 전환입니다. UE Inventory에 보상을 정확히 한 번 적용하는 settlement
receipt API는 아직 없습니다.

## 7. 기존 Device/Pairing 경로

`/api/v1/devices/register-game`, `/pairing-codes`, `/pair`와 Device 조회·해지는 호환성을 위해
남아 있습니다. 현재 UE/Web 제품은 이 경로를 사용하지 않고 `AIRE_GAME`, `AIRE_WEB`을 바로
사용합니다.

기존 `register-game` 경로를 별도로 사용할 때만 bootstrap token이 필요합니다. Pepper가 비어
있으면 현재 단일 플레이어 demo용 고정 key를 사용합니다.

```dotenv
DEV_GAME_DEVICE_TOKEN=replace-with-bootstrap-token
```

## 8. Admin API

`/api/v1/admin/*`는 Profile, Device, Pairing Code, Save Slot, Item, Recipe, Smelting Recipe,
Enemy, Location, Episodic Memory와 Offline Task CRUD를 제공합니다.

`.env`에 Admin token을 설정합니다.

```dotenv
ADMIN_API_TOKEN=replace-with-admin-token
```

호출 예시:

```http
GET /api/v1/admin/items
Authorization: Bearer replace-with-admin-token
```

Token이 비어 있으면 Admin API는 `503 AdminAuthenticationUnavailable`입니다. 자세한 개별
Admin request/response는 실행 중인 `/docs`를 사용합니다.

## 9. WebSocket 호환 경로

`WS /api/v1/chat`은 남아 있지만 AX client 기준은 HTTP입니다. WS는 frame마다 token을 넣습니다.

```json
{
  "type": "chat",
  "token": "AIRE_GAME",
  "payload": {
    "schema_version": 1,
    "request_id": "ws-chat-1",
    "session_id": "ws-session-1",
    "save_slot_id": "demo-slot-1",
    "companion_id": "mako",
    "user_message": "안녕",
    "surface": "game",
    "allowed_commands": []
  }
}
```

새 UE/Web 구현은 HTTP를 사용하고 WS와 HTTP를 동시에 추측 지원하지 않습니다.

## 10. 오류

모든 HTTP 실패는 같은 ErrorEnvelope를 사용합니다.

```json
{
  "request_id": "chat-game-1",
  "error": {
    "code": "InvalidRequest",
    "message": "Request validation failed.",
    "retryable": false,
    "details": {}
  }
}
```

주요 오류:

| HTTP | Code | 의미 |
|---:|---|---|
| 400 | `InvalidRequest` | field/type/request ID 오류 |
| 400 | `UnknownCompanion` | `mako` 외 companion |
| 401 | `UnauthorizedDevice` | Bearer 누락/불일치 |
| 403 | `DeviceRoleNotAllowed` | Client role에 허용되지 않은 작업 |
| 403 | `IdentityScopeMismatch` | body의 profile/device 주장이 인증과 다름 |
| 413 | `RequestTooLarge` | Body 제한 초과 |
| 500 | `InternalError` | 처리되지 않은 서버 오류 |
| 503 | `AIServiceUnavailable` | AI 서비스 사용 불가 |
| 504 | `AIServiceTimeout`/`RequestTimeout` | AI 또는 전체 요청 timeout |

`retryable=true`여도 Chat을 같은 request ID로 자동 재전송하지 않습니다. 사용자가 새 전송을
선택하면 새 request/message ID를 생성합니다.
