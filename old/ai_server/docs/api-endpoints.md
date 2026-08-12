# 엔드포인트 명세 · 마코 기능 사용법

이 서버가 노출하는 **모든 엔드포인트**와, 지금 마코에 구현된 **각 기능을 어떤 데이터로
요청하고 무엇을 돌려받는지**를 한 문서에 모았다.

> [!NOTE]
> 권위는 코드다 — 채팅 계약은 [`app/models.py`](../app/models.py), 디바이스/페어링 계약은
> [`app/pairing_models.py`](../app/pairing_models.py), 오류 매핑은
> [`app/errors_http.py`](../app/errors_http.py) 에 있다. 이 문서와 코드가 어긋나면 코드가 맞다.
> WebSocket 연동 실무는 [`websocket-client-guide.md`](websocket-client-guide.md) 가 더 자세하다.

---

## 1. 공통 규칙

| 항목 | 값 |
|---|---|
| 베이스 경로 | `/api/v1` (헬스체크만 `/health`) |
| 인증 | HTTP `Authorization: Bearer <device_token>` / WS 는 봉투의 `token` 필드 |
| 본문 형식 | JSON. **모르는 필드는 무시되지 않고 거절된다**(`extra="forbid"` → 400 `InvalidRequest`) |
| 요청 크기 | 기본 256KB(`MAX_REQUEST_BODY_BYTES`). 초과 시 413 `RequestTooLarge` |
| 요청 타임아웃 | 기본 30초(`REQUEST_TIMEOUT_SECONDS`) → 504 `RequestTimeout`. 그 안에 AI 호출은 10초(`AI_REQUEST_TIMEOUT_SECONDS`) |
| 상관 ID | 응답 헤더 `X-Request-ID`. 요청에 보내면 **본문 `request_id` 와 같아야 한다**(다르면 400) |
| ID 형식 | `^[A-Za-z0-9][A-Za-z0-9._:-]*$`, 1~128자 (`request_id`/`session_id`/`save_slot_id`/`companion_id`/`profile_id`/`device_id`) |
| 시각 | 모두 ISO-8601 UTC |

**모든 실패는 같은 봉투로 나간다.**

```json
{
  "request_id": "request-001",
  "error": { "code": "AIServiceTimeout", "message": "…", "retryable": true, "details": {} }
}
```

`retryable` 이 `false` 면 그대로 다시 보내도 같은 결과다 — 요청을 고치거나 재페어링해야 한다.

> [!CAUTION]
> **멱등성이 없다.** 채팅은 같은 `request_id` 로 재시도하면 LLM 을 **다시 호출**하고 다른
> 응답이 온다. 디바이스 엔드포인트는 반대로 `request_id` 재사용 시 같은 응답을 돌려준다
> (아래 각 절 참고). 자세한 배경은 [`temporary-scaffolds.md`](temporary-scaffolds.md) §2.

---

## 2. 엔드포인트 일람

| 메서드 · 경로 | 인증 | 하는 일 |
|---|---|---|
| `GET /health` | 없음 | 서버 상태와 현재 LLM 공급자 |
| `POST /api/v1/devices/register-game` | 부트스트랩 고정 토큰 | 게임 클라이언트 최초 등록 → 디바이스 토큰 발급 |
| `POST /api/v1/devices/pairing-codes` | GameClient | 8자리 페어링 코드 발급 |
| `POST /api/v1/devices/pair` | 없음 | 페어링 코드 사용 → WebClient 디바이스 토큰 발급 |
| `GET /api/v1/devices` | GameClient | 이 프로필의 디바이스 목록 |
| `GET /api/v1/devices/me` | WebClient | 지금 토큰이 가리키는 디바이스 |
| `DELETE /api/v1/devices/me` | WebClient | 자기 자신 해지 |
| `DELETE /api/v1/devices/{device_id}` | GameClient | 지정한 WebClient 해지 |
| `POST /api/v1/chat` | 디바이스 토큰 | 마코와의 한 턴 |
| `WS /api/v1/chat` | 봉투의 `token` | 같은 페이로드를 지속 연결로 (`chat`/`situation` 둘 다) |
| `POST /api/v1/situations` | 디바이스 토큰 | 플레이어 발화 없이, 클라이언트가 알려 온 상황에 마코가 먼저 한마디 |

---

## 3. `GET /health`

인증 없이 호출한다. `DEVICE_CREDENTIAL_PEPPER` 가 비어 있어도 통과한다 — 헬스체크가
초록불이라고 인증이 설정된 것은 아니다.

```json
{ "service": "mako-companion", "status": "ok", "llm_provider": "mock" }
```

`llm_provider` 는 **설정값 그대로**다. 키가 없어 실제로는 mock 으로 폴백된 경우까지 반영된
값은 채팅 응답의 `ai_metadata.provider` 에 있다.

---

## 4. 디바이스 · 페어링

### 4.0 흐름

```
게임 클라이언트                       모바일/웹 클라이언트
      │
      │ ① POST /devices/register-game   (Bearer: DEV_GAME_DEVICE_TOKEN)
      │    → device_token (GameClient)
      │
      │ ② POST /devices/pairing-codes   (Bearer: game_token)
      │    → "48213905" (기본 300초)
      │ ─────── 코드를 사람이 옮긴다 ──────▶
      │                                  │ ③ POST /devices/pair  (인증 없음, 코드만)
      │                                  │    → device_token (WebClient)
      │
      │ ④ GET/DELETE /devices…           │ ④ GET/DELETE /devices/me
```

프로필당 디바이스 수는 `MAX_DEVICES_PER_PROFILE`(기본 20)로 막혀 있고, 넘으면 403
`DeviceLimitExceeded` 로 **거부**한다(오래된 것을 자동 해지하지 않는다).

`device_token` 형식은 `<lookup_id>.<base64>` 다. 서버는 원문을 저장하지 않고 HMAC 해시만
들고 있으므로, **분실하면 재발급(재페어링)뿐이다.**

### 4.1 `POST /api/v1/devices/register-game`

게임 클라이언트가 아직 아무 신원도 없을 때 쓰는 부트스트랩. `Authorization` 에는 서버
설정 `DEV_GAME_DEVICE_TOKEN` 과 **정확히 같은 값**을 실어야 한다.

```http
POST /api/v1/devices/register-game
Authorization: Bearer <DEV_GAME_DEVICE_TOKEN>
Content-Type: application/json

{ "request_id": "reg-001" }
```

```json
{
  "request_id": "reg-001",
  "profile_id": "profile-…",
  "device": {
    "device_id": "device-…",
    "role": "GameClient",
    "created_at": "2026-07-31T00:00:00Z",
    "last_used_at": null,
    "revoked_at": null
  },
  "device_token": "token-….<base64>"
}
```

- **호출마다 새 프로필 + 새 GameClient 를 만든다.** 서로 다른 `request_id` 는 각각 자기
  프로필을 얻는다(다중 플레이어). GameClient 는 **프로필당 하나**다 —
  `docs/temporary-scaffolds.md` §2 "2026-08-05: 다중 플레이어 등록" 참조.
- 같은 `request_id` 로 재전송하면 같은 응답(같은 토큰)이 그대로 온다 — **두 번째 프로필을
  만들지 않는다.** 그러니 `request_id` 는 그 설치본의 **고정 안정 ID**로 쓰고, 토큰을
  잃으면 같은 값으로 다시 불러 복구한다.
- `DEV_GAME_DEVICE_TOKEN` 이 설정돼 있지 않으면 503 `AuthenticationUnavailable`.

### 4.2 `POST /api/v1/devices/pairing-codes` (GameClient 전용)

```http
POST /api/v1/devices/pairing-codes
Authorization: Bearer <game_token>

{ "request_id": "code-001" }
```

```json
{ "request_id": "code-001", "pairing_code": "48213905", "expires_at": "2026-07-31T00:05:00Z" }
```

- 코드는 **8자리 숫자**이며 `PAIRING_CODE_TTL_SECONDS`(기본 300초) 후 만료된다.
- 같은 `request_id` 면 같은 코드를 다시 돌려준다.
- WebClient 토큰으로 호출하면 403 `DeviceRoleNotAllowed`.

### 4.3 `POST /api/v1/devices/pair` (인증 없음)

```http
POST /api/v1/devices/pair

{ "request_id": "pair-001", "pairing_code": "48213905" }
```

응답은 4.1 과 같은 `DeviceTokenResponse` 이고 `device.role` 이 `"WebClient"` 다.

| 상황 | 응답 |
|---|---|
| 코드가 틀림 / 형식 위반 | 400 `InvalidPairingCode` (형식은 422·400 `InvalidRequest`) |
| 만료됨 | 410 `ExpiredPairingCode` |
| 이미 사용됨 | 409 `UsedPairingCode` |
| 프로필 디바이스 상한 초과 | 403 `DeviceLimitExceeded` |

같은 `request_id` 로 재전송하면 처음 만들어 준 디바이스 토큰이 다시 온다(코드 재사용이
아니다). 코드 자체는 **한 번만** 쓸 수 있다.

### 4.4 조회 · 해지

```http
GET    /api/v1/devices              Authorization: Bearer <game_token>     # 목록
GET    /api/v1/devices/me           Authorization: Bearer <web_token>      # 나
DELETE /api/v1/devices/me           Authorization: Bearer <web_token>      # 나를 해지
DELETE /api/v1/devices/{device_id}  Authorization: Bearer <game_token>     # WebClient 해지
```

```json
{ "request_id": "…", "devices": [ { "device_id": "device-…", "role": "WebClient",
  "created_at": "…", "last_used_at": "…", "revoked_at": null } ] }

{ "request_id": "…", "profile_id": "profile-…", "device_id": "device-…",
  "role": "WebClient", "status": "Active" }

{ "request_id": "…", "device_id": "device-…", "status": "Revoked" }
```

- 역할이 맞지 않으면 403 `DeviceRoleNotAllowed`(예: WebClient 가 목록 조회, GameClient 가
  다른 GameClient 를 해지).
- 다른 프로필의 디바이스를 지정하면 403 `IdentityScopeMismatch`, 없으면 404 `DeviceNotFound`.
- 해지된 토큰으로 이후 어떤 요청을 보내도 401 `UnauthorizedDevice` 다.
- 이 네 경로는 본문이 없으므로 `request_id` 는 `X-Request-ID` 헤더로 준다(없으면 서버가 UUID 를 만든다).

---

## 5. `POST /api/v1/chat`

### 5.1 요청

```http
POST /api/v1/chat
Authorization: Bearer <device_token>
X-Request-ID: request-001            # 선택. 보내면 본문 request_id 와 같아야 한다
Content-Type: application/json

{
  "request_id": "request-001",
  "schema_version": 1,
  "session_id": "session-001",
  "save_slot_id": "slot-001",
  "companion_id": "mako",
  "message_id": "message-001",
  "user_message": "나무 20개만 캐 줘",
  "surface": "game",
  "time_context": {
    "source": "GameWorld",
    "day": 7,
    "hour": 23,
    "period": "Night"
  },
  "recent_event_ids": ["event-001"],
  "game_context": { "location_id": "region_abandoned_mining_village" },
  "allowed_commands": ["Command.Follow", "Command.HoldPosition",
                       "Command.CancelCurrent", "Command.GatherResource"]
}
```

| 필드 | 필수 | 규칙 · 의미 |
|---|:--:|---|
| `request_id` | ✔ | 상관용. 응답에 그대로 되돌아온다 |
| `schema_version` | | 보내면 `1`만 허용. 생략하면 현재 버전으로 처리한다 |
| `session_id` | ✔ | 대화를 가르는 축. 바뀌면 직전 턴 기억이 이어지지 않는다 |
| `save_slot_id` | ✔ | 세이브 슬롯. 인증된 프로필과 함께 **장기기억 스코프**가 된다 |
| `companion_id` | ✔ | 지금은 `"mako"` 만 유효. 다른 값은 400 `UnknownCompanion` |
| `message_id` | | 클라이언트 메시지 식별자. 응답에 그대로 에코된다 |
| `user_message` | ✔ | 1~2000자 |
| `surface` | | `"game"`(기본) / `"mobile"`. **말투만** 바꾼다 |
| `time_context` | | `source`(`GameWorld`/`RealWorld`), `day`(0 이상), `hour`(0~23), `period`(영문 식별자). 대사 상황으로 전달된다 |
| `recent_event_ids` | | 최대 32개, 중복 금지. 현재는 수신·검증만 하며 이벤트 해석은 하지 않는다 |
| `game_context` | | 최대 32키. `location_id` 만 서버가 읽는다 |
| `allowed_commands` | | 최대 16개, 중복 금지. 생략하면 빈 목록 = 명령 없음 |
| `profile_id` / `device_id` | | 신원 주장. 보내면 토큰의 신원과 대조 — 다르면 403 `IdentityScopeMismatch` |

`allowed_commands` 에 넣을 수 있는 값(게임 프로토콜 전체):

```
Command.Follow          Command.HoldPosition    Command.ReturnToPlayer   Command.EngageTarget
Command.DistractTarget  Command.MoveToLocation  Command.CancelCurrent    Command.GatherResource
```

> [!IMPORTANT]
> **마코가 실제로 내는 것은 `Follow` · `HoldPosition` · `CancelCurrent` · `GatherResource`
> 넷뿐이다.** 나머지를 허용해도 방출되지 않는다. 반대로 **허용 목록에 없는 명령은 절대
> 나오지 않는다** — 두뇌가 걸러 내고, 서버가 한 번 더 단언한다(회귀 시 503
> `AIServiceInvalidOutput`).

`game_context` 는 위치 조회에만 쓰이며 `token` · `password` · `secret` · `authorization` ·
`databaseUrl` 처럼 **비밀로 보이는 키는 중첩 포함 거절**된다(400). `time_context` 는 별도
`[상황]` 블록으로 대사 프롬프트에 전달된다.

### 5.2 응답

```json
{
  "request_id": "request-001",
  "message_id": "message-001",
  "session_id": "session-001",
  "save_slot_id": "slot-001",
  "companion_id": "mako",
  "response_id": "response-<uuid>",
  "display_text": "알겠어. 나무 20개 캐 올게.",
  "command_candidates": [
    {
      "command_id": "command-<uuid>",
      "request_id": "request-001",
      "type": "Command.GatherResource",
      "target_id": null,
      "priority": "Normal",
      "issued_at": "2026-07-31T00:00:00Z",
      "expires_at": "2026-07-31T00:00:30Z",
      "parameters": { "resource": "wood", "quantity": 20 }
    }
  ],
  "ai_metadata": { "provider": "local", "model_version": "…", "prompt_version": "companion-v1" }
}
```

클라이언트가 지켜야 할 것:

- `command_candidates` 는 **0개 또는 1개**다(스키마 상한 4). 비었으면 대사만 출력한다.
- **`expires_at` 을 확인한다.** 기본 TTL 30초(`COMPANION_COMMAND_TTL_SECONDS`). 지난 명령은 버린다.
- `display_text` 는 완성된 한 줄이다(스트리밍 없음, 최대 200자에서 잘리는 게 아니라 그 길이를
  넘으면 폴백 대사로 대체된다).
- `ai_metadata.provider` 는 **폴백까지 반영된 실제 공급자**다. `LLM_PROVIDER=openai` 인데
  키가 없으면 여기에 `mock` 이 온다.

### 5.3 `WS /api/v1/chat`

같은 페이로드를 봉투로 감싼다. 브라우저 `WebSocket` 은 핸드셰이크에 커스텀 헤더를 못
실으므로 **메시지마다** `token` 을 함께 보낸다.

```json
// 보내기
{ "type": "chat", "token": "<device_token>", "payload": { /* 5.1 과 동일 */ } }
{ "type": "situation", "token": "<device_token>", "payload": { /* 5.4 와 동일 */ } }

// 받기
{ "type": "chat_response",      "payload": { /* 5.2 와 동일 */ } }
{ "type": "situation_response", "payload": { /* 5.4 와 동일 */ } }
{ "type": "error",              "payload": { /* 오류 봉투 */ } }
```

- **어떤 실패도 연결을 끊지 않는다.** 오류를 받아도 소켓을 다시 열지 말고 다음 메시지를 보낸다.
- 메시지는 **순차 처리**된다. 파이프라이닝 이득이 없으므로 응답을 받고 다음을 보낸다.
- 모르는 `type` 은 400 `InvalidRequest` 로 거절된다(연결은 유지). 파서를 forward-compatible
  하게 짜 두면 나중에 타입이 늘어도 대응하기 쉽다.

---

## 5.4 `POST /api/v1/situations`

게임 클라이언트가 **코드로 트리거하는** 상황 이벤트 — 플레이어가 아무 말도 하지 않았는데
마코가 먼저 한마디 건네는 계기다. 무슨 상황인지는 클라이언트가 이미 판단했으므로 서버는
다시 분류하지 않는다. `POST /api/v1/chat` 과 별개의 계약이고, **명령을 내지 않는다** —
대사만 돌아온다. 명령이 필요하면 여전히 `POST /api/v1/chat` 하나로만 낼 수 있다.

### 요청

```http
POST /api/v1/situations
Authorization: Bearer <device_token>
X-Request-ID: sit-001                # 선택. 보내면 본문 request_id 와 같아야 한다
Content-Type: application/json

{
  "request_id": "sit-001",
  "session_id": "session-001",
  "save_slot_id": "slot-001",
  "companion_id": "mako",
  "surface": "game",
  "situation": ["플레이어 체력이 20% 남았다", "주변에 적 2마리가 있다"]
}
```

| 필드 | 필수 | 규칙 · 의미 |
|---|:--:|---|
| `request_id` | ✔ | 상관용. `POST /chat` 과 같은 멱등성 제약(없음)이 적용된다 |
| `session_id` / `save_slot_id` / `companion_id` | ✔ | `POST /chat` 과 같은 값을 주면 **같은 대화 기억**에 얹힌다 — 신원 스코프도 동일하다 |
| `situation` | ✔ | 클라이언트가 관찰한 상황을 자유 문장 1~4줄로. 검증된 사실이 아니라 그대로 프롬프트에 실린다 |
| `surface` | | `POST /chat` 과 동일 — 말투만 바꾼다 |
| `time_context` | | `POST /chat` 과 동일 |
| `profile_id` / `device_id` | | `POST /chat` 과 동일한 신원 주장 |

`game_context`/`allowed_commands`/`user_message` 는 이 계약에 **없다** — 있으면 400
`InvalidRequest` 다.

### 응답

```json
{
  "request_id": "sit-001",
  "session_id": "session-001",
  "save_slot_id": "slot-001",
  "companion_id": "mako",
  "response_id": "response-<uuid>",
  "display_text": "저기 뭔가 있어, 조심해.",
  "ai_metadata": { "provider": "local", "model_version": "…", "prompt_version": "companion-v2" }
}
```

- `command_candidates` 가 없다. 명령 판단은 클라이언트가 이미 끝냈다는 전제다.
- `display_text` 는 `POST /chat` 과 같은 규칙(완성된 한 줄, 최대 4000자)을 따른다.
- 기억은 **읽고 쓴다**: 같은 대화의 `[최근 대화]`를 참고해 응답하고, 상황과 마코의 대사를
  다시 그 대화 기억에 남긴다(화자 라벨은 `situation`). 장기기억 회수·추출도 `POST /chat`
  과 같은 경로를 탄다.
- 되묻기(`gather_resource` ask-back) 슬롯은 **건드리지 않는다** — 상황 이벤트는 플레이어의
  답이 아니므로, 되묻는 도중에 상황이 끼어들어도 슬롯이 사라지지 않는다.

---

## 6. 지금 구현된 마코 기능과 요청 방법

한 번의 `POST /api/v1/chat`(또는 chat 봉투)이 아래 전부를 처리한다. **엔드포인트를 나눠
부르지 않는다** — 무엇을 할지는 `user_message` 와 `allowed_commands` 가 정한다.

| 기능 | 보내야 하는 것 | 돌아오는 것 |
|---|---|---|
| 따라오기 | `"따라와"` + `Command.Follow` 허용 | 대사 + `Command.Follow` |
| 대기 | `"여기서 기다려"` + `Command.HoldPosition` 허용 | 대사 + `Command.HoldPosition` |
| 작업 중지 | `"그만"`, `"됐어"`, `"취소"` + `Command.CancelCurrent` 허용 | 대사 + `Command.CancelCurrent` |
| 채집 | `"나무 20개 캐 줘"` + `Command.GatherResource` 허용 | 대사 + `Command.GatherResource(parameters)` |
| 채집 되묻기 | `"저것 좀 캐 줘"` (자원 불명) | `"무엇을 캐면 될까?"` — 명령 없음, **다음 턴에 이어진다** |
| 제작법 | `"철검 어떻게 만들어?"` | 검증된 제작법 대사, 명령 없음 |
| 지역 이야기 | `"여기 무슨 곳이야?"` + `game_context.location_id` | 검증된 세계관 대사, 명령 없음 |
| 잡담 | `"안녕"`, `"고마워"` | 가벼운 반응, 명령 없음 |
| 창구별 말투 | `surface: "mobile"` | 같은 판단, 다른 말투 |
| 기억 | `session_id`/`save_slot_id` 고정 | 최근 대화·지난 세션 기억이 대사에 반영 |

### 6.1 이동 계열 명령 — 따라오기 / 대기 / 중지

```json
{ "request_id": "request-010", "session_id": "session-001", "save_slot_id": "slot-001",
  "companion_id": "mako", "user_message": "여기서 기다려",
  "allowed_commands": ["Command.HoldPosition"] }
```

```json
{ "display_text": "알겠어. 여기서 기다릴게.",
  "command_candidates": [ { "type": "Command.HoldPosition", "parameters": {}, "…": "…" } ] }
```

매핑은 고정이다.

| 플레이어 발화 | 명령 |
|---|---|
| `"따라와"`, `"나를 따라와 줘"` | `Command.Follow` |
| `"기다려"`, `"여기서 대기해"` | `Command.HoldPosition` |
| `"그만"`, `"멈춰"`, `"됐어"`, `"취소"`, `"나중에 하자"` | `Command.CancelCurrent` |

> **허용하지 않은 명령을 알아들었을 때는 수락 대사가 나오지 않는다.** `"따라와"` 를
> `allowed_commands: []` 로 보내면 `"지금은 그렇게 해 줄 수 없어."` 만 온다 — 말만 하고
> 움직이지 않는 상태를 만들지 않기 위해서다.

### 6.2 채집 — 자원과 수량

지원 자원은 **나무(`wood`)와 돌(`stone`) 둘뿐**이다. 별칭까지 알아듣는다.

| 정식 값 | 알아듣는 표현 |
|---|---|
| `wood` | 나무, 목재, 장작, 통나무, 나뭇가지, 땔감 |
| `stone` | 돌, 바위, 석재, 자갈 |

```json
{ "user_message": "나무 20개만 캐 줘", "allowed_commands": ["Command.GatherResource"], "…": "…" }
```

```json
{ "display_text": "알겠어. 나무 20개 캐 올게.",
  "command_candidates": [ { "type": "Command.GatherResource",
    "parameters": { "resource": "wood", "quantity": 20 }, "…": "…" } ] }
```

`parameters` 규칙:

- **수량은 플레이어가 말했을 때만 실린다.** `"나무 캐 줘"` 면 `{"resource": "wood"}` 로만
  오고, **몇 개를 캘지는 게임이 정한다** — 서버는 인벤토리를 모르므로 숫자를 지어내지 않는다.
- 상한은 **한 번에 50개**다. `"나무 200개"` 는 `"한 번에 50개까지만 캘 수 있어."` 대사만
  오고 **명령이 없다**(임의로 50으로 깎지 않는다).
- 지원하지 않는 자원(`"철광석 캐 줘"`)도 대사만 온다.
- `"돌이랑 나무 캐 줘"` 처럼 둘을 같이 말하면 하나를 임의로 고르지 않고 되묻는다.

### 6.3 채집 되묻기 — 두 턴에 걸친 대화

자원을 알 수 없으면 마코가 되묻고, **다음 턴의 답을 이어받는다.** 클라이언트가 상태를
들고 있을 필요는 없다 — 같은 `session_id`·`save_slot_id`·`companion_id` 로 보내기만 하면 된다.

```
① {"user_message": "저것 20개만 캐 줘",  "allowed_commands": ["Command.GatherResource"]}
   → {"display_text": "무엇을 캐면 될까?", "command_candidates": []}

② {"user_message": "나무",               "allowed_commands": ["Command.GatherResource"]}
   → {"display_text": "알겠어. 나무 20개 캐 올게.",
      "command_candidates": [{"type": "Command.GatherResource",
                              "parameters": {"resource": "wood", "quantity": 20}}]}
```

- **①에서 말한 수량 20이 ②로 살아 넘어간다.** 되묻는다고 사라지지 않는다.
- 되묻기 슬롯의 수명은 `COMPANION_PENDING_TTL_SECONDS`(기본 120초)다. 그 안에 답하지 않으면
  잊는다.
- ②에서 화제를 바꾸면(`"철검 어떻게 만들어?"`) 슬롯을 버리고 평소대로 처리한다.
- 모호하게 두 번까지만 되묻는다(`MAX_ASK_COUNT = 2`). 그 뒤에는 되묻기를 멈추고
  `"지금은 나무와 돌 채집만 도와줄 수 있어."` 로 끝낸다.
- **서버를 재시작하면 진행 중이던 되묻기가 사라진다**(대화 기억은 인메모리다). 재연결
  후에는 완전한 문장으로 다시 요청한다.

### 6.4 제작법

```json
{ "user_message": "돌도끼 어떻게 만들어?", "allowed_commands": [], "…": "…" }
```

```json
{ "display_text": "돌도끼는 나뭇가지 2개와 돌 1개로 맨손에서 만들 수 있어.",
  "command_candidates": [] }
```

- 현재 서버 데이터셋에는 일반 제작법 13종과 **ERD에 없는 서버 확장 제련법 4종**이 있다.
  `돌도끼`, `돌곡괭이`, `모닥불`, `붕대`, `철괴`, `강철괴`, `철검`, `고급 포션`처럼
  결과물의 한국어 별칭을 넣어 물을 수 있다. 전체 이름·별칭과 출처 차이는
  [`docs/game-data.md`](game-data.md)에 있다.
- `IronIngot`와 `SteelIngot`은 일반 제작과 서버 확장 제련에 서로 다른 경로가 있어 두
  경로를 모두 확정 사실로 전달한다.
- 대사의 숫자는 검증된 사실에 있는 숫자만 통과한다. LLM이 다른 숫자를 쓰면 그 대사는
  버려지고 고정 문장으로 대체된다.
- 서로 다른 결과물을 한 번에 물으면 임의로 하나를 고르지 않는다.
- `"그거 재료가 뭐라고?"` 같은 지시대명사는 **아직 이어지지 않는다** — 사실 조회는 현재
  발화만 보기 때문이다.

### 6.5 적 약점·공략

적 이름이나 별칭을 발화에 넣으면 검증된 약점과 공략 조언을 답한다.

```json
{ "user_message": "골리앗 약점이 뭐야?", "allowed_commands": [], "…": "…" }
```

```json
{
  "display_text": "외상성 골리앗은 가슴의 깨진 코어가 약점이고, 폭발물에 약해. ...",
  "command_candidates": []
}
```

- 현재 데이터는 녹슨 참호병·절규하는 사이렌 드론·외상성 골리앗 3종이다.
- `참호병`, `드론`, `골리앗` 같은 별칭도 사용할 수 있다. 전체 목록과 약점 속성의
  한국어 표시는 [`docs/game-data.md`](game-data.md)의 검수표에 있다.
- 서로 다른 적을 함께 묻는 발화는 임의로 하나를 고르지 않고 확인된 적 정보 없음으로
  폴백한다.
- `"저거 어떻게 잡아?"`처럼 적 이름을 말하지 않은 지시대명사는 아직 지원하지 않는다.
  현재 사실 조회는 발화에 적 이름이 직접 들어 있는 경우만 처리한다.
- 적 공략은 정보 응답이므로 `command_candidates`를 만들지 않는다.

### 6.6 지역 이야기 (세계관)

`game_context.location_id` 가 있어야 답할 수 있다.

```json
{ "user_message": "여기 무슨 곳이야?",
  "game_context": { "location_id": "region_abandoned_mining_village" }, "…": "…" }
```

```json
{ "display_text": "버려진 광산 마을은 오래전 광산이 폐쇄된 뒤 사람들이 떠난 곳이야. …",
  "command_candidates": [] }
```

- **등록된 위치는 `region_abandoned_mining_village` 하나뿐이다.** 모르는 위치이거나
  `location_id` 가 없으면 `"지금 위치에 대해 확인된 이야기는 아직 없어."` 가 온다.
- 서버 설정 `COMPANION_DEFAULT_LOCATION_ID` 는 클라이언트가 위치를 보내기 전까지 쓰는
  **임시 발판**이다([`temporary-scaffolds.md`](temporary-scaffolds.md) §1). 클라이언트가
  보낸 값이 언제나 이긴다.

### 6.7 잡담

```json
{ "user_message": "안녕", "allowed_commands": [], "…": "…" }
```

인사·감사 같은 말에 가볍게 반응한다. 명령은 나오지 않는다. 이 장면만 대사의 숫자 검사를
받지 않는다(전할 확정 사실이 없으므로).

### 6.7 창구(`surface`) — 게임 옆 / 휴대폰 채팅

`surface` 는 **말투와 "못 해 준다"는 말의 문구만** 바꾼다. 판단(무엇을 명령으로 볼지,
어떤 사실을 쓸지)과 응답 스키마는 완전히 같다.

| 상황 | `"game"` | `"mobile"` |
|---|---|---|
| 인사 | `"안녕! 오늘은 어디부터 둘러볼까?"` | `"안녕! 무슨 일이야?"` |
| 못 하는 요청 | `"…따라오기, 대기, 중지를 말해 줘."` | `"그건 여기서는 못 도와줘. 제작법이나 지역 이야기라면 물어봐."` |
| 명령 불가 | `"지금은 그렇게 해 줄 수 없어."` | `"채팅으로는 아직 그걸 시킬 수 없어."` |
| 위치 미상 | `"지금 위치에 대해 확인된 이야기는 아직 없어."` | `"어느 지역 얘기인지 모르겠어. 게임에서 물어봐 줄래?"` |

> **모바일에서 명령이 안 나오는 이유는 `surface` 때문이 아니라 모바일 클라이언트가
> `allowed_commands` 를 비워 보내기 때문이다.** 나중에 채팅으로도 작업을 시키게 되면
> 목록만 채우면 된다.

### 6.8 기억 — 무엇을 같게 보내야 이어지는가

세 층이 서로 다른 수명으로 돌아간다.

| 층 | 스코프 | 수명 | 대사에 들어가는 방식 |
|---|---|---|---|
| 전사(원문 로그) | 프로필+슬롯+컴패니언+세션 | 파일, `TRANSCRIPT_RETENTION_DAYS`(30일) 후 삭제 | 직접 안 들어간다(장기기억의 원본) |
| 대화 기억 | 프로필+슬롯+컴패니언+세션 | 30분 유휴 후 소멸, **재시작하면 사라짐** | `[최근 대화]` 최대 6턴 |
| 장기기억 | **프로필+슬롯** | SQLite `episodic_memories`에 영속, 상한(32개)에서만 밀려남 | `[기억]` 최대 3줄 |

- **대화를 이어가려면**: 같은 디바이스 토큰으로 `save_slot_id` · `companion_id` ·
  `session_id` **셋 다** 고정한다. 하나라도 바뀌면 마코에게는 다른 대화다.
- **세션을 넘겨 기억하게 하려면**: `save_slot_id` 만 같으면 된다. `session_id` 가 바뀌어도
  장기기억은 따라온다.
- 클라이언트가 기억을 실어 보낼 필요는 없다. 서버는 신원 해시 두 개만 두뇌에 넘기고,
  기억은 마코가 자기 저장소에서 꺼낸다.
- **회수된 기억은 확정 사실이 아니다.** 참고용 문장으로만 프롬프트에 들어가고, 숫자를
  담지 않는다.
- 기본 공급자(`mock`)는 **아무것도 추출하지 않고 임베딩도 만들지 않는다**. 장기기억은
  `openai` 나 `local` 공급자로 추출할 수 있고, 임베딩이 실패하거나 없는 기억은 키워드+
  시간 감쇠 검색으로 자동 폴백한다. 끄려면 `LONG_TERM_MEMORY_ENABLED=false` /
  `TRANSCRIPT_ENABLED=false`(전사를 끄면 새 장기기억도 생기지 않는다).
- 장기기억 중요도는 ERD와 맞춘 1~10이며, 임베딩은 `episodic_memories.embedding` JSON과
  모델명에 함께 저장한다. 질의 임베딩은 요청 경로에서 제한 시간 안에 시도하고 실패하면
  해당 턴도 정상 응답한다.

### 6.9 Offline_Task — 모바일 작업 지시

채팅과 별도로 모바일 클라이언트가 게임 클라이언트에 수행할 작업을 저장해 둘 수 있다.
`Offline_Task`는 프로필과 세이브 슬롯으로 격리되고, 생성은 `WebClient`, 상태 전이는
`GameClient`만 할 수 있다.

```json
{
  "request_id": "task-001",
  "save_slot_id": "slot-001",
  "task_type": "Gathering",
  "item_id": "Branch"
}
```

```powershell
# 모바일(WebClient)이 작업을 만든다
$task = Invoke-RestMethod "$base/api/v1/tasks" -Method Post `
  -Headers @{ Authorization = "Bearer $($web.device_token)" } `
  -ContentType "application/json" -Body (@{
    request_id = "task-001"; save_slot_id = "slot-001"
    task_type = "Gathering"; item_id = "Branch"
  } | ConvertTo-Json)

# 게임(GameClient)이 Pending → InProgress → Completed → Claimed 로 진행한다
Invoke-RestMethod "$base/api/v1/tasks/$($task.task.task_id)/start" -Method Post `
  -Headers @{ Authorization = "Bearer $($game.device_token)" }
Invoke-RestMethod "$base/api/v1/tasks/$($task.task.task_id)/complete" -Method Post `
  -Headers @{ Authorization = "Bearer $($game.device_token)" }
Invoke-RestMethod "$base/api/v1/tasks/$($task.task.task_id)/claim" -Method Post `
  -Headers @{ Authorization = "Bearer $($game.device_token)" }
```

- `task_type`은 `Gathering`, `Crafting`, `Scouting`이다. `Scouting`은 대상 아이템 없이
  만들 수 있고, 나머지는 존재하는 `item_id`가 필요하다.
- 목록은 `GET /api/v1/tasks?save_slot_id=slot-001`이며 `status`로 필터할 수 있다.
- 잘못된 상태 전이는 `OfflineTaskTransitionNotAllowed`(409)로 거절한다.
- `Claimed`는 현재 상태 표시만 한다. 보상 지급 원장과 이벤트 보고는 아직 연결하지 않았다.
- `request_id`는 같은 프로필·세이브 슬롯에서 작업 생성 재시도 시 같은 작업을 돌려준다.

### 6.10 아직 없는 것

- 스트리밍(`display_text` 는 한 번에 완성돼 온다), 서버 푸시.
- **채팅·상황 요청** 멱등성 — 같은 `request_id` 재시도는 **LLM 을 다시 호출**한다. 재시도가
  명령을 내면 중복 명령이 될 수 있으므로 `command_id` 로 걸러 낸다. Offline_Task 생성은
  같은 프로필·세이브 슬롯 범위에서 같은 요청 ID를 재사용하면 기존 작업을 돌려준다.
- 감사 기록(요청/메시지 영속화).
- 지시대명사 해소, 마코 외의 컴패니언.
- `recent_event_ids` (`POST /chat`) 은 여전히 검증만 하고 해석하지 않는다
  (`docs/temporary-scaffolds.md` §3) — `POST /situations` 는 이것과 다른 계약이다: 클라이언트가
  이벤트 ID 대신 상황을 산문으로 직접 보내고, 서버는 그 문장을 검증하지 않은 채 그대로
  프롬프트에 싣는다. 이벤트 카탈로그가 생겨도 이 엔드포인트가 대체되는 것은 아니다.
- **레이트 리밋 없음** — `POST /situations` 를 연달아 부르면 `[최근 대화]`(최대 6줄)가
  상황 대사로 가득 차 진짜 대화를 밀어낸다. 트리거 빈도는 클라이언트가 조절한다.

---

## 7. 오류 코드

| code | HTTP | retryable | 언제 | 대처 |
|---|:--:|:--:|---|---|
| `InvalidRequest` | 400 | false | 스키마 위반, 모르는 필드, `X-Request-ID` 불일치 | 요청을 고친다 |
| `RequestTooLarge` | 413 | false | 본문 256KB 초과 | 줄여서 보낸다 |
| `RequestTimeout` | 504 | true | 처리 30초 초과 | 재시도(중복 명령 주의) |
| `AIServiceUnavailable` | 503 | true | 두뇌 호출 실패 | 재시도 |
| `AIServiceTimeout` | 504 | true | AI 10초 초과 | 재시도 |
| `AIServiceInvalidOutput` | 503 | true | 허용 밖 명령이 방출됨(서버가 거절) | 재시도 · 서버 로그 확인 |
| `InternalError` | 500 | true | 처리되지 않은 예외 | 재시도 · 서버 로그 확인 |
| `UnauthorizedDevice` | 401 | false | 토큰 없음/무효/해지됨 | 재페어링 |
| `AuthenticationUnavailable` | 503 | true | `DEVICE_CREDENTIAL_PEPPER` 미설정 | 서버 설정 |
| `IdentityScopeMismatch` | 403 | false | 본문 `profile_id`/`device_id` 가 토큰과 다름 | 필드를 빼거나 맞춘다 |
| `UnknownCompanion` | 400 | false | `companion_id` 미등록 | `"mako"` 를 쓴다 |
| `DeviceLimitExceeded` | 403 | false | 프로필 디바이스 상한 초과 | 기존 디바이스 해지 |
| `DeviceNotFound` | 404 | false | 없는 `device_id` | 목록으로 확인 |
| `DeviceRoleNotAllowed` | 403 | false | 역할에 맞지 않는 호출 | 맞는 토큰으로 호출 |
| `InvalidPairingCode` | 400 | false | 코드 불일치 | 새 코드 발급 |
| `ExpiredPairingCode` | 410 | false | 코드 만료(기본 300초) | 새 코드 발급 |
| `UsedPairingCode` | 409 | false | 이미 쓴 코드 | 새 코드 발급 |
| `DuplicateRequest` | 409 | false | 같은 `request_id` 를 다른 내용으로 재사용 | 새 `request_id` |
| `OfflineTaskNotFound` | 404 | false | 현재 프로필의 작업이 없음 | 작업 목록 확인 |
| `OfflineTaskTransitionNotAllowed` | 409 | false | 현재 상태에서 해당 전이가 불가능 | 올바른 순서로 갱신 |
| `OfflineTaskInvalidRequest` | 400 | false | 작업 종류·아이템 조합이 잘못됨 | 요청을 고친다 |

---

## 8. 처음부터 끝까지 (PowerShell)

```powershell
$base = "http://localhost:8000"

# ① 게임 클라이언트 등록
$game = Invoke-RestMethod "$base/api/v1/devices/register-game" -Method Post `
  -Headers @{ Authorization = "Bearer $env:DEV_GAME_DEVICE_TOKEN" } `
  -ContentType "application/json" -Body '{"request_id":"reg-001"}'

# ② 페어링 코드 발급 → ③ 모바일이 사용
$code = Invoke-RestMethod "$base/api/v1/devices/pairing-codes" -Method Post `
  -Headers @{ Authorization = "Bearer $($game.device_token)" } `
  -ContentType "application/json" -Body '{"request_id":"code-001"}'

$web = Invoke-RestMethod "$base/api/v1/devices/pair" -Method Post `
  -ContentType "application/json" `
  -Body (@{ request_id = "pair-001"; pairing_code = $code.pairing_code } | ConvertTo-Json)

# ④ 채팅 — 게임 창구에서 채집 요청
$body = @{
  request_id       = "request-001"
  session_id       = "session-001"
  save_slot_id     = "slot-001"
  companion_id     = "mako"
  user_message     = "나무 20개만 캐 줘"
  surface          = "game"
  game_context     = @{ location_id = "region_abandoned_mining_village" }
  allowed_commands = @("Command.Follow", "Command.HoldPosition",
                       "Command.CancelCurrent", "Command.GatherResource")
} | ConvertTo-Json -Depth 5

Invoke-RestMethod "$base/api/v1/chat" -Method Post `
  -Headers @{ Authorization = "Bearer $($game.device_token)" } `
  -ContentType "application/json; charset=utf-8" -Body $body
```

모바일 창구는 `Authorization` 을 `$web.device_token` 으로, `surface` 를 `"mobile"` 로,
`allowed_commands` 를 `@()` 로 바꾸면 된다.
