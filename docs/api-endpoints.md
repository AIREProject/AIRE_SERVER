# AIRE Server API 사용법

현행 로컬 HTTP 계약의 코드 권위는 `app/models.py`, `app/offline_task_models.py`,
`app/pairing_models.py`와 `app/routes/`입니다. 실행 중인 서버에서는 `/openapi.json`과 `/docs`를
최우선으로 확인합니다. 2026-08-13 현재 배포
`https://traip.mtvs2026.work/openapi.json`은 `ChatRequest.game_context`를 아직 generic
object로 노출하므로, 아래 World Context v1은 `AIRE_SERVER/`의 AX-I05 목표 계약이다. 배포
OpenAPI 반영이나 배포 smoke 성공을 이 문서로 주장하지 않는다.

AX-I05 로컬 구현은 2026-08-13 전체 Backend pytest 574건, Ruff와 mypy를
통과했다. 이후
서버에 접근할 수 없어 배포 적용과 runtime smoke는 미확인이다. 기존 Game client의 `{}` 요청은
새 계약에서 거부되므로 full Context v1을 생성하는 AX-I04 client와 서버 적용 시점을 조정한다.

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
| Body 제한 | 기본 256KB (`413 RequestTooLarge`) |
| Game Context 제한 | compact UTF-8 JSON 8KiB 이하 (`400 InvalidRequest`) |
| Request timeout | 기본 30초 |

Request model은 알 수 없는 body field를 `400 InvalidRequest`로 거부합니다. Response를 사용하는
클라이언트는 필수 field를 검증하고, 모르는 선택 field는 실행 경로에 노출하지 않고 무시합니다.

Chat은 `(profile, save_slot, companion, request_id)`와 canonical JSON digest로 멱등합니다.
HTTP/WS에서 같은 payload를 재전송하면 최초 응답을 재생하며, 다른 payload는
`409 DuplicateRequest`입니다. 원문 보존 기간이 끝난 동일 payload는
`410 IdempotencyRecordExpired`이고, Situation은 이 멱등 계약의 대상이 아닙니다.

## 2. Endpoint 요약

| Method | Path | 제품 사용 |
|---|---|---|
| GET | `/health` | 프로세스·설정 확인 |
| GET | `/ready` | DB revision 필수 readiness, LLM degraded 상태 확인 |
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
| POST | `/api/v1/events` | GameClient가 allowlist GameEvent 저장 |
| POST | `/api/v1/command-results` | GameClient가 Command 실행 결과 상태 전이 저장 |
| GET/POST/PATCH/DELETE | `/api/v1/memories/*` | WebClient 기억 조회·검색·정정·고정·삭제·초기화 |
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

`GET /ready`는 DB 연결과 Alembic `0015` head를 검사합니다. 연결 실패나 revision 불일치는
HTTP 503 `not_ready`입니다. DB가 준비된 상태에서 Memory worker의 최근 LLM 분류가 실패하면
Mock fallback이 가능한 HTTP 200 `degraded`로 반환합니다.

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
  "game_context": {
    "schema_version": 1,
    "location_id": "forest_camp",
    "threat": {
      "present": true,
      "count": 2,
      "nearest_kind": "Enemy.TrenchCrawler"
    },
    "nearby_resources": [
      {"kind": "wood", "count": 3}
    ],
    "available_workstations": ["Workbench.Basic"],
    "current_work": {"type": "Harvesting", "state": "Working"},
    "inventories": [
      {
        "container_id": "AIRE.Inventory.MAKO",
        "free_slots": 12,
        "item_totals": [{"item_id": "PlantStem", "count": 4}],
        "truncated": false
      }
    ]
  },
  "allowed_commands": []
}
```

AX-I02 대사 표시 단계에서는 `allowed_commands`를 빈 배열로 보냅니다. 이후 UE Command
Gateway가 준비된 명령만 allowlist에 추가합니다.

`Command.GatherResource`의 Game 첫 수직 슬라이스는 명시적인 wood 요청만 후보로 만든다.
`나무를 모아 줘`처럼 자원만 지정한 요청의 후보 parameters는 정확히 다음과 같으며,
`quantity` key를 포함하지 않는다.

```json
{
  "type": "Command.GatherResource",
  "target_id": null,
  "parameters": {"resource": "wood"}
}
```

`돌`, 나무·돌을 함께 말한 모호한 요청, 채집 방법·가능 여부 질문, 그리고 정수·소수·음수·
한글 수사·`많이`처럼 어떤 형태로든 수량을 말한 요청은 Game 후보를 만들지 않는다. 주변
`game_context.nearby_resources` facts만으로 후보를 추가하지 않으며, 후보를 받은 UE Gateway가
fresh bounded query로 실제 wood 대상을 다시 검증한다. Mobile surface의 `GatherResource`는
이 strict Game 범위와 별개로 기존 `OfflineTask/Gathering` 계약(wood·stone, 수량 1~50,
미지정 시 50)을 유지한다.

LLM의 Command label과 resource/quantity는 제안일 뿐이다. Backend는 이동·대기·중지·복귀·
채집·공격마다 사용자 원문에 같은 행동 계열의 명시적 요청이 있는지 다시 확인한다. Gather
resource와 quantity는 원문 parser 결과로 덮어쓰며 질문, 복수·손상 수량, Provider label 불일치와
일반 대화 오분류는 Command 후보로 승격하지 않는다.

`Command.CraftItem`은 AX-I06의 첫 제작 수직 슬라이스다. UE가 이 명령을 allowlist에 넣은
경우에만 명시적인 `철검`/`Sword_Iron`/`IronSword` 제작 요청이 후보가 된다. 후보 parameters는
항상 다음과 같고, 다른 Recipe ID나 수량은 후보를 만들지 않는다.

```json
{
  "type": "Command.CraftItem",
  "target_id": null,
  "parameters": {
    "recipe_id": "recipe-11",
    "quantity": 1
  }
}
```

`철검 만드는 법`, 재료·레시피 질문은 검증된 제작법 facts-only 대사로 남으며 `CraftItem`
후보를 만들지 않는다. `game_context`의 위치·위협·작업·인벤토리 사실만으로도 후보를 만들지
않으며, 후보를 받은 UE Command Gateway가 Recipe·재료·상태·작업대를 최종 검증한다.

Recipe 질문은 먼저 stable ID와 검증 alias를 결정론적으로 찾는다. 대상을 찾지 못한 상세 질문만
LLM이 서버가 제공한 후보 중 최대 세 개의 Recipe ID와 confidence를 구조화 출력으로 선택한다.
단일 후보는 confidence 80 이상일 때만 상세 조회로 승격하고, 복수 후보는 60 이상일 때 표시
이름으로 확인 질문을 돌려준다. 목록·비교·직전 참조 질의는 이 fallback으로 상세 하나로 바꾸지
않으며, 등록되지 않은 ID와 낮은 confidence는 거부한다.

최종 Recipe 응답은 어느 경로에서도 검증된 Recipe fact를 그대로 반환하며 LLM이 재료·수량·
작업대·시간을 생성하거나 재작성하지 않는다. 명시적 제작 요청의 `display_text`도 `알겠어. 철검
하나를 만들게.`로 고정하고, 같은 응답에 위 `CraftItem` 후보를 반드시 포함한다. 두 대사 경로에는
Inventory·주변 자원 같은 World Context fact를 섞지 않아 다른 Item을 Recipe 재료처럼 말하거나
Command 후보가 대사와 분리되는 일을 막는다.

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
  "user_message": "나무30개 캐줘",
  "surface": "mobile",
  "time_context": {
    "source": "RealWorld",
    "day": 10,
    "hour": 14,
    "period": "Afternoon"
  },
  "recent_event_ids": [],
  "allowed_commands": ["Command.GatherResource", "Command.CraftItem"]
}
```

Mobile은 `game_context`를 생략하거나 `null`로 보낸다. `{}` 및 임의 자유 형식 object는
허용하지 않는다. `surface=game`에서는 `game_context`가 반드시 위 Context v1이어야 한다.
Mobile Web은 Offline Gathering/Crafting 변환을 위해 `Command.GatherResource`와
`Command.CraftItem`을 광고한다. 검증된 요청은 UE Command 후보를 반환하지 않고
`offline_task_id`가 있는 `InProgress` Task 한 건으로 저장한다. `나무30개 캐놔줘`,
`캐 놓아줘`, `캐둬`, `모아놔줘`는 같은 채집 요청으로 정규화한다. 제작은 현재 검증된
`recipe-1` 엉성한 붕대와 수량 1~50만 허용하고 제작법 질문은 Task로 바꾸지 않는다.

현재 `TimeContext`는 `GameWorld`와 `RealWorld` 모두 `day/hour/period` 구조를 사용합니다.
`observed_at`, `timezone`, `interaction_mode`는 계약 field가 아닙니다.

### 4.3 Game Context v1

`game_context`는 관측한 사실만 담는 strict 구조다. 배열 입력 순서는 의미가 없고 중복은
거부한다. Backend는 prompt facts를 stable ID 기준으로 정렬해 만든다.

```json
{
  "schema_version": 1,
  "location_id": "forest_camp",
  "threat": {
    "present": true,
    "count": 2,
    "nearest_kind": "Enemy.TrenchCrawler"
  },
  "nearby_resources": [{"kind": "wood", "count": 3}],
  "available_workstations": ["Workbench.Basic"],
  "current_work": {"type": "Harvesting", "state": "Working"},
  "inventories": [
    {
      "container_id": "AIRE.Inventory.MAKO",
      "free_slots": 12,
      "item_totals": [{"item_id": "PlantStem", "count": 4}],
      "truncated": false
    }
  ]
}
```

- 최상위 7개 field는 모두 필수다. `location_id`, `threat.nearest_kind`,
  `current_work`만 `null`을 허용한다. GameWorld 시간은 최상위 `time_context`가 단일
  권위이며 Context에 중복하지 않는다.
- 모든 stable ID는 1~128자, `[A-Za-z0-9][A-Za-z0-9._:-]*`다. UObject/class path,
  credential key와 임의 key는 `400 InvalidRequest`로 거부한다. ID의 catalogue 존재 여부는
  이 계약에서 확인하지 않는다.
- `threat.count`는 0~32이며 `present == (count > 0)`이다. count가 0이면
  `nearest_kind`는 `null`이어야 한다.
- `nearby_resources`는 중복 없는 최대 8종의 `{kind, count}` 집계이며 count는 1~32다.
  `available_workstations`는 중복 없는 stable tag 최대 8개다.
- `current_work`는 종료 시 `null`이다. type은 `Crafting | Harvesting |
  StorageTransfer`, state는 `Requested | Moving | Working | PausedByCombat`만 허용한다.
- `inventories`는 `AIRE.Inventory.MAKO`와 `AIRE.Inventory.SharedStorage` 중복 없는 최대
  2개다. MAKO `free_slots`는 0~20, Shared Storage는 0~50이다. 컨테이너별 item kind는
  최대 16종, 합계는 각각 1,980개/4,950개 이하이며 생략 시 `truncated=true`를 표시한다.
- compact Context UTF-8 직렬화 결과가 8KiB를 넘으면 `400 InvalidRequest`다. 전체 HTTP
  body 제한 256KiB를 넘는 경우에는 `413 RequestTooLarge`다.

Context는 대사 생성용 facts-only 입력이다. 이를 근거로 Backend가 Command 후보를 추가·제거하거나
`CraftItem`/gameplay를 실행하지 않는다. Command 후보는 기존 `allowed_commands` allowlist와
위 AX-I06 `CraftItem` 계약이 정한다.

현재 일반 플레이맵의 location ID는 `forest_camp`다. AX-I04에 권위 센서가 없는 동안
`threat.nearest_kind=null`, `nearby_resources=[]`, `available_workstations=[]`인 Context도
정상이며 Backend가 임의 ID나 보스맵 ID를 보충하지 않는다.

### 4.4 성공 응답

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
    "prompt_version": "companion-v4"
  }
}
```

Response에는 최상위 `schema_version`, `interaction_mode`, `memory_candidates`가 없습니다.
`message_id`를 생략하면 서버가 canonical 사용자 Message ID를 생성하며, `response_id`는
canonical 동료 Message ID입니다. 사용자 Message를 먼저 commit하므로 LLM 실패 뒤 같은
요청을 재시도할 수 있고, 생성 draft가 저장된 뒤의 재시도는 LLM을 다시 호출하지 않습니다.

### 4.5 저장 경계

신규 Chat 원문은 SQLite `Conversation`/`Message`가 canonical source입니다. 개발 JSONL은
기본 비활성화이며 기존 `episodic_memories`는 조회만 유지합니다. Message는 기본·최대 7일,
원문 없는 audit/idempotency ledger는 기본·최대 30일 보존합니다. Memory가 source를 참조하면
`MemorySource`로 승격되고 마지막 참조가 제거되면 즉시 purge 대상 `Transient`로 돌아갑니다.

### 4.6 사용자 Memory 제어

인증된 device는 자기 `profile_id` 안의 `save_slot_id`/`companion_id` scope만 조회·수정할 수
있습니다. `GET /api/v1/memories?save_slot_id={id}&companion_id={id}`와
`GET /api/v1/memories/{memory_id}`는 Active memory만 반환합니다.
`POST /api/v1/memories/search`는 `{save_slot_id, companion_id, query, limit?}`를 받고,
사용자 정정값을 반영한 관련 Active memory만 최대 50개까지 반환합니다. 빈 검색어와 범위 밖
memory는 허용하지 않으며, Archived memory는 검색·목록·상세에 나타나지 않습니다.
`PATCH /api/v1/memories/{memory_id}`는 `importance`, `pinned` 또는
`corrected_text`와 필수 `correction_reason`을 받습니다. 정정은 append-only audit으로 저장되며
canonical Message/Event 원문은 변경하지 않습니다.

각 `MemoryView.sources[]`는 내부 ID나 원문 없이 `source_type`, `source_mode`, `occurred_at`만
제공합니다. 직접 발화는 Chat surface에 따라 `Message + RealWorld` 또는
`Message + GameWorld`로 구분하며, `LegacyUnknown` Message는 공개 응답에서 `Legacy` source로
표시됩니다. 최신 정정문은 목록·검색뿐 아니라 실제 Prompt 회상에도 동일하게 사용됩니다.

`DELETE /api/v1/memories/{memory_id}?reason={reason}`와
`POST /api/v1/memories/reset`은 memory를 `Archived`로 전이합니다. 이는 legal erasure가 아니라
durable 사용자 삭제 tombstone 정책입니다. retrieval/prompt에서 즉시 제외되고 연결 source outbox는
Tombstone이 되어 restart 뒤 재증류되지 않습니다. shared source 원문은 마지막 Active reference가
사라질 때까지 보존하며, 마지막 reference가 해제된 source는 retention purge 대상으로 전환됩니다.

## 5. GameEvent와 Command Result

두 endpoint는 `GameClient` 전용이고, body ID와 같은 `X-Request-ID` 및 정확한 raw request
body의 lowercase SHA-256인 `X-Content-SHA256`을 요구합니다. 같은 ID와 raw body는 최초
응답을 재생하고 다른 body는 `409 DuplicateRequest`입니다.

`POST /api/v1/events`는 `schema_version=1`, timezone이 있는 `occurred_at`, `GameWorld`
`time_context`, stable `actor_id`, 중복 없는 최대 8개 `target_ids`, 정확히 빈 `payload={}`를
요구합니다. 허용 type은 `Event.Combat.Started`, `Event.Combat.Ended`,
`Event.Danger.Detected`, `Event.Rescue.Completed`, `Event.Discovery.Found`,
`Event.Companion.Returned`뿐입니다. importance는 서버가 Combat/Returned=`Normal`,
Danger/Rescue/Discovery=`High`로 결정합니다.

`POST /api/v1/command-results`는 canonical Chat이 저장한 동일 scope/session의 Command
candidate를 참조합니다. 최초 상태는 `Accepted | Rejected | Expired`, 이후
`Accepted → Running → Succeeded | Failed | Cancelled | Expired`만 허용합니다. 없는 후보,
다른 scope 또는 candidate의 request/type 불일치는 존재 여부를 숨기기 위해 404이며,
잘못된 전이와 terminal 이후 보고는 `409 CommandResultTransitionNotAllowed`입니다.

### 4.5 주요 field

- `session_id`: 한 surface에서 이어지는 최근 대화와 되묻기 상태 범위
- `save_slot_id`: 장기기억과 Offline Task 범위. 현재 `demo-slot-1`
- `companion_id`: 현재 `mako`만 유효
- `surface`: `game` 또는 `mobile`; 말투에 사용
- `game_context`: `surface=game`에서 필수인 Context v1; `surface=mobile`에서는 생략 또는
  `null`만 허용
- `allowed_commands`: 서버가 반환할 수 있는 command allowlist
- `recent_event_ids`: 최대 32개를 검증하지만 현재 저장·사용하지 않음

## 6. Situation

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

## 7. Offline Task

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
  "item_id": "PlantStem",
  "quantity": 5
}
```

Task type은 `Gathering`, `Crafting`, `Scouting`, 상태는 `Pending`, `InProgress`,
`Completed`, `Claimed`입니다.

`quantity`가 있는 시간 기반 Task는 생성 즉시 `InProgress`로 시작합니다. 수량 없는 legacy
Task만 `Pending`으로 시작해 GameClient의 `/start`를 기다립니다.

나무 재료의 canonical Item ID는 `PlantStem` 하나입니다. `Branch`는 migration 0015에서
기존 Task·Recipe·Game State 참조와 함께 `PlantStem`으로 병합되며 새 요청에는 허용하지 않습니다.

`Crafting/ShoddyBandage`는 최신 Game State의 MAKO → Shared Storage 순서로 결과 1개당
`PlantStem` 2개를 Task 생성과 같은 transaction에서 예약 차감합니다. Snapshot이 없으면
`409 InventorySnapshotRequired`, 수량이 부족하면 `409 InsufficientCraftingMaterials`이며
Task와 차감 모두 생기지 않습니다. 같은 `request_id` 재전송은 기존 Task를 반환하고 다시
차감하지 않습니다. `Pending/InProgress` 제작 Task 삭제는 예약했던 각 컨테이너 수량을 같은
transaction에서 환불합니다.

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

## 8. Game State Snapshot (AX-I09 local Review 계약)

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
4. 서버가 제작 예약·환불로 Snapshot을 변경한 뒤의 GameClient PUT은 GET으로 확인한 현재
   version을 `X-Base-State-Version`에 보내야 합니다. 헤더가 없거나 현재 version과 다르면
   `409 GameStateVersionConflict`로 서버 차감을 되살리는 stale overwrite를 막습니다.

strict field, schema/content version, Inventory bounds, Item/Weapon 의미 검증 실패는
`400 InvalidRequest`이며 저장 상태를 바꾸지 않습니다.

## 9. 기존 Device/Pairing 경로

`/api/v1/devices/register-game`, `/pairing-codes`, `/pair`와 Device 조회·해지는 호환성을 위해
남아 있습니다. 현재 UE/Web 제품은 이 경로를 사용하지 않고 `AIRE_GAME`, `AIRE_WEB`을 바로
사용합니다.

기존 `register-game` 경로를 별도로 사용할 때만 bootstrap token이 필요합니다. Pepper가 비어
있으면 현재 단일 플레이어 demo용 고정 key를 사용합니다.

```dotenv
DEV_GAME_DEVICE_TOKEN=replace-with-bootstrap-token
```

## 10. Admin API

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

## 11. WebSocket 호환 경로

`WS /api/v1/chat`은 남아 있지만 AX client 기준은 HTTP입니다. WS는 frame마다 token을 넣습니다.
`payload`는 HTTP `ChatRequest`와 같은 strict 모델을 사용한다. 따라서 game frame은 Context v1을
포함해야 하고 mobile frame은 `game_context`를 생략하거나 `null`로만 보낼 수 있다.

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
    "game_context": {
      "schema_version": 1,
      "location_id": null,
      "threat": {"present": false, "count": 0, "nearest_kind": null},
      "nearby_resources": [],
      "available_workstations": [],
      "current_work": null,
      "inventories": []
    },
    "allowed_commands": []
  }
}
```

새 UE/Web 구현은 HTTP를 사용하고 WS와 HTTP를 동시에 추측 지원하지 않습니다.

## 12. 오류

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
| 400 | `InvalidRequest` | field/type/request ID 오류, Context v1 위반 또는 Context 8KiB 초과 |
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
