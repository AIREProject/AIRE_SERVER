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
| DELETE | `/api/v1/tasks/{id}` | WebClient 미정산 예약 취소 |
| PUT | `/api/v1/game-state` | GameClient가 마지막 승인 Game State Snapshot 저장 |
| GET | `/api/v1/game-state` | GameClient/WebClient가 마지막 승인 Snapshot 조회 |
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
| 삭제 | `AIRE_WEB` |

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

`quantity`가 있는 시간 기반 Task는 생성 즉시 `InProgress`로 시작합니다. 수량 없는 legacy
Task만 `Pending`으로 시작해 GameClient의 `/start`를 기다립니다.

### 6.3 목록

```http
GET /api/v1/tasks?save_slot_id=demo-slot-1
Authorization: Bearer AIRE_WEB
```

선택 query `status=InProgress`처럼 상태를 필터링할 수 있습니다.

`InProgress` 수량 Task의 `progress_quantity`는 조회 시점 서버 시간으로 계산한 정수 완성
수량입니다. GET은 상태나 DB 결과를 변경하지 않으므로 Web은 polling 없이 명시적 새로고침에서
현재 수량만 확인할 수 있습니다.

수량 Task의 `/complete` 또는 `/collect` 시 첫 단위 시간도 충족되지 않았다면 상태는
`InProgress`, `progress_quantity=0`, `result_quantity=null`을 유지합니다. 이후 UE 실행이나
명시적 동기화에서 다시 계산하며 Inventory 적용과 Claim은 수행하지 않습니다. 한 개 이상
완성된 경우에만 정수 완성 수량을 확정하고 `Completed`로 전환합니다.

### 6.4 상태 전환

```text
POST /api/v1/tasks/{task_id}/start
POST /api/v1/tasks/{task_id}/complete
POST /api/v1/tasks/{task_id}/claim
POST /api/v1/tasks/{task_id}/collect
```

`AIRE_WEB`은 자기 프로필의 `Pending` 또는 `InProgress` 작업만 다음 경로로 삭제할 수
있습니다. `Completed`와 `Claimed`는 UE Inventory 정산과 충돌할 수 있으므로 `409`로
거부하고, 다른 프로필 또는 존재하지 않는 Task는 `404`로 응답합니다.

```text
DELETE /api/v1/tasks/{task_id}
```

현재 Claim은 서버 상태 전환입니다. UE Inventory에 보상을 정확히 한 번 적용하는 settlement
receipt API는 아직 없습니다.

### 6.5 관리자 작업 시간 정책

Swagger의 Admin 경로에서 `ADMIN_API_TOKEN`으로 인증한 뒤 지원 Offline Task의 개당 현실
시간을 조회·수정할 수 있습니다.

```text
GET   /api/v1/admin/offline-task-policies
GET   /api/v1/admin/offline-task-policies/{policy_id}
PATCH /api/v1/admin/offline-task-policies/{policy_id}
```

기본 `policy_id`는 `gathering-plant-stem`과 `crafting-shoddy-bandage`이며 각각 5초/개,
10초/개입니다. PATCH body는 다음과 같습니다.

```json
{
  "seconds_per_item": 10
}
```

허용 범위는 `0초 초과, 86400초 이하`입니다. Task 생성 시점의 정책값을
`offline_tasks.seconds_per_item`에 snapshot하므로 정책 변경은 이후 생성 Task에만 적용되고
이미 존재하는 Task의 계산은 바뀌지 않습니다.

## 7. Game State Snapshot (AX-I09 local Review 계약)

AX-I09의 다음 계약은 로컬 구현과 사전 배포 Review 기준입니다. 아직 공개 배포 서버의
`/openapi.json`에는 이 경로가 없으며, 이 문서만으로 배포 런타임 지원을 주장하지 않습니다.
서버 Snapshot은 UE가 검증하고 로컬 저장한 상태의 조회용 복사본이며 gameplay 실행 권위가
아닙니다.

### 7.1 역할과 Header

| 동작 | Bearer | 요구 사항 |
|---|---|---|
| `PUT /api/v1/game-state` | `AIRE_GAME` | Snapshot 저장 |
| `GET /api/v1/game-state?save_slot_id={id}&companion_id={id}` | `AIRE_GAME`, `AIRE_WEB` | 마지막 승인 Snapshot 읽기 |

PUT은 `X-Request-ID`와 `X-Content-SHA256`을 모두 요구합니다. `X-Request-ID`는 body의
`operation_id`와 정확히 같아야 합니다. `X-Content-SHA256`은 HTTP로 전송하는 **원문 JSON
body의 정확한 UTF-8 bytes**를 SHA-256으로 계산한 64자리 소문자 hex입니다. JSON을 다시
직렬화하거나 key 정렬·공백 정규화한 결과의 hash가 아닙니다. 누락, 형식 오류, body hash
불일치와 request/operation ID 불일치는 `400 InvalidRequest`입니다.

GET은 query의 `save_slot_id`와 `companion_id`로 scope를 지정합니다. 응답 상관관계용
`X-Request-ID`를 보내지 않으면 서버가 생성합니다. 두 동작 모두 Bearer에서 해석한
`profile_id`가 권위이며, 다른 role 또는 profile scope 접근은 기존 인증·scope 오류로
거부합니다.

### 7.2 PUT 요청

```http
PUT /api/v1/game-state
Authorization: Bearer AIRE_GAME
Content-Type: application/json
X-Request-ID: state-sync-1
X-Content-SHA256: <raw-body-utf8-sha256-lowercase-hex>
```

```json
{
  "schema_version": 1,
  "content_version": 1,
  "operation_id": "state-sync-1",
  "state_version": 1,
  "world_session_id": "world-session-1",
  "captured_at": "2026-08-12T12:00:00Z",
  "save_slot_id": "demo-slot-1",
  "companion_id": "mako",
  "inventory": {
    "player": {
      "capacity": 30,
      "revision": 3,
      "stacks": [
        {"slot_index": 0, "item_id": "PlantStem", "count": 5},
        {"slot_index": 100, "item_id": "ShoddyBandage", "count": 1}
      ],
      "equipment": {"equipped_item_id": null}
    },
    "containers": [
      {
        "container_id": "AIRE.Inventory.MAKO",
        "capacity": 20,
        "revision": 4,
        "stacks": [],
        "equipment": {"equipped_item_id": null}
      },
      {
        "container_id": "AIRE.Inventory.SharedStorage",
        "capacity": 50,
        "revision": 2,
        "stacks": [],
        "equipment": {"equipped_item_id": null}
      }
    ]
  }
}
```

`schema_version`과 `content_version`은 현재 모두 `1`만 지원합니다. `state_version`은 1 이상의
scope 내 단조 증가 정수이고, `captured_at`은 UTC offset이 포함된 datetime입니다. ID는 공통
stable ID 규칙을 따르며 `snapshot_id`, 임의 World summary와 실행 가능한 Command는 body에
포함하지 않습니다.

Inventory의 정확한 상한과 검증은 다음과 같습니다.

- Player는 `capacity=30`, `revision>=0`, Stack 최대 40개입니다. 일반 Slot은 0~29, Quick
  Slot은 100~109만 허용합니다.
- `containers`에는 중복 없이 정확히 `AIRE.Inventory.MAKO`와
  `AIRE.Inventory.SharedStorage` 두 항목이 있어야 합니다. MAKO는 `capacity=20`, Shared
  Storage는 `capacity=50`이며 각 Stack 수는 capacity 이하입니다. Slot은 각 container의
  0부터 `capacity-1`까지입니다.
- 모든 Stack은 `{slot_index, item_id, count}`이며 `count`는 1~99입니다. Slot index는 한
  Inventory 안에서 중복될 수 없습니다.
- `item_id`는 서버 Item master data에 있어야 합니다. Weapon Stack은 항상 count 1입니다.
- Player와 MAKO의 `equipment`는 `{ "equipped_item_id": <Weapon ID 또는 null> }`입니다.
  장착 ID는 서버 Item master의 Weapon이어야 합니다. Shared Storage도 같은 Equipment object를
  보내되 `equipped_item_id`는 반드시 `null`입니다.

### 7.3 응답, 멱등성과 충돌

정상 PUT과 GET은 HTTP 200으로 요청 Snapshot 전체에 `request_id`와 서버
`last_synced_at`을 더해 반환합니다. PUT 응답의 `request_id`는 `operation_id`이고, GET 응답의
`request_id`는 GET의 `X-Request-ID`입니다. 응답의 `operation_id`는 저장된 PUT operation을
가리킵니다. Snapshot이 없는 scope의 GET은 `404 GameStateNotFound`입니다.

PUT은 `(profile, save_slot, companion, operation_id)`와 원문 body hash를 기준으로
멱등합니다.

1. 같은 `operation_id`와 같은 원문 body bytes를 다시 보내면 최초 HTTP 200 응답을 그대로
   반환하며 version이나 `last_synced_at`을 바꾸지 않습니다.
2. 같은 `operation_id`에 다른 원문 body bytes를 보내면 `409 DuplicateRequest`이며 현재
   Snapshot은 바뀌지 않습니다. JSON 의미가 같아도 bytes가 다르면 다른 body입니다.
3. 새 operation은 현재 값보다 큰 `state_version`만 허용합니다. 같거나 낮으면
   `409 GameStateVersionConflict`이며 부분 저장이나 last-write-wins를 하지 않습니다.

strict field, schema/content version, Inventory bounds, Item/Weapon 의미 검증 실패는
`400 InvalidRequest`이며 저장 상태를 바꾸지 않습니다.

## 8. 기존 Device/Pairing 경로

`/api/v1/devices/register-game`, `/pairing-codes`, `/pair`와 Device 조회·해지는 호환성을 위해
남아 있습니다. 현재 UE/Web 제품은 이 경로를 사용하지 않고 `AIRE_GAME`, `AIRE_WEB`을 바로
사용합니다.

기존 `register-game` 경로를 별도로 사용할 때만 bootstrap token이 필요합니다. Pepper가 비어
있으면 현재 단일 플레이어 demo용 고정 key를 사용합니다.

```dotenv
DEV_GAME_DEVICE_TOKEN=replace-with-bootstrap-token
```

## 9. Admin API

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

## 10. WebSocket 호환 경로

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

## 11. 오류

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
| 404 | `GameStateNotFound` | 요청 scope에 승인된 Game State Snapshot이 없음 |
| 409 | `DuplicateRequest` | 같은 operation ID가 다른 원문 body bytes로 재사용됨 |
| 409 | `GameStateVersionConflict` | 새 operation의 state version이 최신 값보다 크지 않음 |
| 413 | `RequestTooLarge` | Body 제한 초과 |
| 500 | `InternalError` | 처리되지 않은 서버 오류 |
| 503 | `AIServiceUnavailable` | AI 서비스 사용 불가 |
| 504 | `AIServiceTimeout`/`RequestTimeout` | AI 또는 전체 요청 timeout |

`retryable=true`여도 Chat을 같은 request ID로 자동 재전송하지 않습니다. 사용자가 새 전송을
선택하면 새 request/message ID를 생성합니다.
