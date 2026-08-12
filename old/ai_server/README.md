# AI Companion Server

생존 크래프팅 게임의 동료 **마코(Mako)** 를 서빙하는 서버입니다.

`app/brain/` 이 마코의 지능(2단계 LLM 라우팅, 사실 기반 한국어 대사 생성, 검증된
제작법/적 공략/세계관 저장소, 전사·대화 기억·장기기억의 세 층)을 담고, 나머지 `app/` 은 그것을
HTTP/WebSocket 으로 내보내는 얇은 가장자리입니다. 서버는 상태를 보관하지 않습니다 —
기억은 전부 마코가 들고 있습니다.

> [!WARNING]
> **인증은 돌아왔지만, 요청 멱등성과 감사 기록은 아직 없습니다.** 페어링으로 발급받은
> Bearer 토큰이 신원입니다 — `player_name` 자기신고는 사라졌습니다. 그래도 같은
> `request_id` 를 두 번 보내면 지금도 LLM 을 두 번 호출하고 서로 다른 응답을 받을 수
> 있습니다. 마코가 세션을 넘어 기억하는 내용은 인증된 신원의 해시로 색인된 파일
> (`LONG_TERM_MEMORY_DIR`, 기본 `data/memories`)에 영속되고, 그 기억을 뽑아 내는 원본인
> **대화 원문**도 대화별 파일(`TRANSCRIPT_DIR`, 기본 `data/transcripts`)에 남습니다.
> 무엇을 되살렸고 무엇이 아직 범위 밖인지는
> [docs/temporary-scaffolds.md](docs/temporary-scaffolds.md) §2 에 있습니다.
> 원치 않으면 `LONG_TERM_MEMORY_ENABLED=false`, `TRANSCRIPT_ENABLED=false` 로 끌 수
> 있습니다(전사를 끄면 새 장기기억도 생기지 않습니다). 전사는
> `TRANSCRIPT_RETENTION_DAYS`(기본 30) 가 지나면 자동으로 지워집니다.

> [!TIP]
> **이 저장소를 처음 본다면 [docs/handoff.md](docs/handoff.md) 부터 읽으세요.** 서버를 띄우는
> 순서, 코드 지도, 무엇이 완료됐고 무엇이 남았는지, 되돌리기 전에 알아야 할 설계 결정을
> 한 문서에 모았습니다.

## 실행

```powershell
Set-Location C:\workspace\Github\AI_RE\ai_server
uv sync --dev
uv run alembic upgrade head                                # 디바이스 레지스트리 DB 생성/갱신
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

최소 실행에는 `.env`가 필요하지 않습니다. 코드 기본값이 SQLite DB와 `LLM_PROVIDER=mock`을
사용합니다. GameClient는 `Authorization: Bearer AIRE_GAME`, WebClient는
`Authorization: Bearer AIRE_WEB` 고정 공개값을 사용하며 둘 다 `AIRE_OPEN` profile에
연결됩니다. 이 두 값은 `DEVICE_CREDENTIAL_PEPPER`와 `DEV_GAME_DEVICE_TOKEN` 없이 동작합니다.

현재 제품 경로는 단일 플레이어만 사용합니다. UE와 Web은 각각 다른 사용자가 아니라 같은
플레이어의 두 surface이며, canonical identity `AIRE_OPEN / demo-slot-1 / mako`를 공유합니다.
따라서 장기기억, Offline Task와 마지막 승인 상태도 의도적으로 공유합니다. 사용자, Save Slot,
Companion 선택 기능은 현재 범위에 없습니다.

설정을 바꿀 때만 `Copy-Item .env.example .env`를 실행합니다. 현재 `.env.example`도
`LLM_PROVIDER=mock`이므로 복사 직후 외부 LLM 없이 실행됩니다. 기존 랜덤 device 등록·pairing
경로는 남아 있으며, 그 경로만 `DEVICE_CREDENTIAL_PEPPER`와 bootstrap 설정이 필요합니다.

### LLM 공급자

`LLM_PROVIDER` 로 고릅니다: `mock`(기본, 외부 호출 없음) / `openai` / `local`.
키가 없으면 조용히 `mock` 으로 떨어집니다.

```dotenv
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://mtvs2026.work/v1
LOCAL_LLM_API_KEY=replace-with-your-local-key
LOCAL_LLM_MODEL=balanced-q4-k-m-mtp
```

## 검증

```powershell
uv run pytest
uv run ruff check .
uv run mypy
```

## 디바이스 페어링

현재 UE/Web 제품 경로는 페어링 대신 각각 `AIRE_GAME`, `AIRE_WEB`을 사용합니다. 아래는
호환성을 위해 남긴 기존 랜덤 디바이스 발급 경로입니다. `POST /api/v1/devices/*` 가 전부
`request_id` 를 받고(재전송 시 같은 응답을 그대로 돌려줍니다), 아래 순서로 씁니다.

1. **`POST /api/v1/devices/register-game`** — `Authorization: Bearer $DEV_GAME_DEVICE_TOKEN`
   (부트스트랩 전용 고정 토큰). 서버 인스턴스당 `GameClient` 디바이스는 하나뿐이라, 두 번째
   요청은 `409 DeviceAlreadyRegistered` 입니다. 응답의 `device_token` 이 이 게임 클라이언트의
   신원입니다.
2. **`POST /api/v1/devices/pairing-codes`** — `Authorization: Bearer <game_token>`. 8자리
   숫자 페어링 코드를 발급합니다(`PAIRING_CODE_TTL_SECONDS`, 기본 300초 후 만료).
3. **`POST /api/v1/devices/pair`** — 인증 없이, 본문에 `pairing_code` 를 실어 보냅니다.
   `WebClient` 역할의 새 `device_token` 을 돌려줍니다. 한 번 쓴 코드는 다시 못 씁니다.
4. **`GET /api/v1/devices`**(GameClient 전용) / **`GET,DELETE /api/v1/devices/me`**(WebClient
   전용) / **`DELETE /api/v1/devices/{device_id}`**(GameClient 가 WebClient 를 해지) 로
   조회·해지합니다.

한 프로필이 가질 수 있는 디바이스 수는 `MAX_DEVICES_PER_PROFILE`(기본 20)로 제한되고,
초과하면 페어링이 `403 DeviceLimitExceeded` 로 거부됩니다(자동 해지 없음).

## API

엔드포인트 전체 명세와 마코 기능별 요청/응답 예시는
[docs/api-endpoints.md](docs/api-endpoints.md) 에 있습니다. 아래는 요약입니다.

### `POST /api/v1/chat`

```http
POST /api/v1/chat
Authorization: Bearer <device_token>

{
  "request_id": "request-001",
  "session_id": "session-001",
  "save_slot_id": "slot-001",
  "companion_id": "mako",
  "user_message": "여기서 기다려",
  "game_context": { "location_id": "region_abandoned_mining_village" },
  "allowed_commands": ["Command.HoldPosition"]
}
```

```json
{
  "request_id": "request-001",
  "session_id": "session-001",
  "save_slot_id": "slot-001",
  "companion_id": "mako",
  "response_id": "response-…",
  "display_text": "알겠어. 여기서 기다릴게.",
  "command_candidates": [
    {
      "command_id": "command-…",
      "request_id": "request-001",
      "type": "Command.HoldPosition",
      "target_id": null,
      "priority": "Normal",
      "issued_at": "…",
      "expires_at": "…",
      "parameters": {}
    }
  ],
  "ai_metadata": { "provider": "mock", "model_version": "…", "prompt_version": "companion-v1" }
}
```

필드는 **전부 필수이거나 기본값이 있고, 모르는 필드는 400 으로 거절**됩니다(`extra="forbid"`).
`game_context` 와 `allowed_commands` 는 생략할 수 있습니다. `Authorization` 헤더가 없거나
토큰이 무효/해지됐으면 `401 UnauthorizedDevice` 입니다.

- `save_slot_id` — 인증된 프로필과 함께 세이브 진행을 가릅니다. 장기기억은 이 축(프로필 +
  세이브슬롯)으로 스코프됩니다.
- `companion_id` — 지금은 `"mako"` 만 유효합니다. 다른 값은 `400 UnknownCompanion`.
- `session_id` — `save_slot_id`/`companion_id` 와 함께 대화를 가릅니다. 셋 중 하나라도
  다르면 되묻기 상태와 최근 대화 기억이 이어지지 않습니다.
- `profile_id` / `device_id` — 생략 가능. 보내면 인증된 신원과 대조해 다르면
  `403 IdentityScopeMismatch` 입니다(신원 위조 시도로 취급).
- `allowed_commands` — 게임이 지금 받을 수 있는 명령. **여기 없는 명령은 절대 방출되지
  않습니다**(마코가 내더라도 서버가 거절합니다).

### `POST /api/v1/situations`

플레이어 발화 없이, 게임 클라이언트가 코드로 트리거하는 상황에 마코가 먼저 한마디
건넵니다. 무슨 상황인지는 클라이언트가 이미 판단했으므로 서버는 다시 분류하지 않고
**대사만** 돌려줍니다(명령 후보 없음).

```http
POST /api/v1/situations
Authorization: Bearer <device_token>

{
  "request_id": "sit-001",
  "session_id": "session-001",
  "save_slot_id": "slot-001",
  "companion_id": "mako",
  "situation": ["플레이어 체력이 20% 남았다", "주변에 적 2마리가 있다"]
}
```

`session_id`/`save_slot_id`/`companion_id` 를 `POST /chat` 과 같은 값으로 보내면 같은 대화
기억(`[최근 대화]`)에 얹힙니다. 되묻기(ask-back) 슬롯은 건드리지 않습니다.

### `WS /api/v1/chat`

같은 페이로드를 `{"type": "chat"|"situation", "token": "<device_token>", "payload": {…}}`
봉투로 감싸 보냅니다. 브라우저 `WebSocket` 은 핸드셰이크에 커스텀 헤더를 못 실으므로, HTTP 의
`Authorization` 대신 매 메시지마다 `token` 을 함께 보냅니다. 응답은
`{"type": "chat_response"|"situation_response", "payload": {…}}`, 실패는
`{"type": "error", "payload": {…}}` 이고 **어떤 실패도 연결을 끊지 않습니다.** 자세한 내용은
[docs/websocket-client-guide.md](docs/websocket-client-guide.md).

### `GET /health`

```json
{ "service": "mako-companion", "status": "ok", "llm_provider": "mock" }
```

## 오류

모든 실패는 같은 봉투로 나갑니다.

```json
{
  "request_id": "request-001",
  "error": { "code": "AIServiceTimeout", "message": "…", "retryable": true, "details": {} }
}
```

`InvalidRequest`(400) · `RequestTooLarge`(413) · `RequestTimeout`(504) ·
`AIServiceUnavailable`(503) · `AIServiceTimeout`(504) · `AIServiceInvalidOutput`(503) ·
`InternalError`(500) · `UnauthorizedDevice`(401) · `AuthenticationUnavailable`(503) ·
`DeviceLimitExceeded`(403) · `IdentityScopeMismatch`(403) · `UnknownCompanion`(400) ·
`DeviceAlreadyRegistered`(409) · `DeviceNotFound`(404) · `DeviceRoleNotAllowed`(403) ·
`InvalidPairingCode`(400) · `ExpiredPairingCode`(410) · `UsedPairingCode`(409) ·
`DuplicateRequest`(409).

## 아키텍처 메모

- **번역은 한 번뿐입니다.** [app/service.py](app/service.py) 의 `CompanionService` 가
  `ChatRequest`→`CompanionTurn`, `CompanionReply`→`ChatResponse` 를 맡고, 명령 후보의
  식별자·만료 시각도 여기서 붙입니다. 두뇌는 `ChatRequest` 를 보지 않습니다 — HTTP 계약의
  모양이 라우팅 코드로 새면 안 되기 때문입니다.
- **`app/brain/` 은 전송 계층을 모릅니다.** FastAPI·Starlette·라우트·요청 컨텍스트를
  건드리지 않습니다(`app.models` 와 `app.settings` 는 leaf 라 씁니다). 예전에는 별도 패키지와
  import 금지 테스트로 강제했지만, 합친 뒤로는 리뷰에서 지킵니다.
- 서버가 마코의 컨텍스트를 조립하지 않습니다. 불투명한 키 두 개만 넘기고 —
  대화를 가리키는 `conversation_key`(인증된 `profile_id`+`save_slot_id`+`companion_id`+
  `session_id` 의 HMAC)와 사람을 가리키는 `player_key`(`profile_id`+`save_slot_id` 의
  HMAC) — 마코가 자기 저장소에서 기억을 꺼내 씁니다. 새 종류의 컨텍스트가 계약 변경이
  되지 않게 하기 위해서입니다.
- **기억은 세 층입니다.** 오간 말 그대로를 남기는 **전사**(대화별 JSONL, 보존 기간이 지나면
  자동 삭제), 한 세션 안에서만 살다 프로세스와 함께 사라지는 **대화 기억**, 그리고 세션과
  재시작을 넘는 **장기기억**(플레이어 프로필 사실·중요 에피소드·세션 요약, `player_key` 로
  스코프된 SQLite `episodic_memories` 행). 장기기억은 전사에서 **증류**됩니다 — 백그라운드
  루프 하나가 아직 증류하지 않은 구간을 읽어 옮기므로 응답 지연이 늘지 않고, 대화가 몇 턴에서
  끝나든 기억이 유실되지 않습니다. 검색은 임베딩이 가능할 때 의미 유사도를 사용하고,
  공급자가 없거나 실패하면 키워드+시간 감쇠로 자동 폴백합니다. 회수된 기억은 대사
  프롬프트의 `[기억]` 블록으로만 들어갑니다 — **확정 사실이 되지 않습니다.**
- 명령 매핑: `follow→Command.Follow`, `wait→Command.HoldPosition`,
  `stop→Command.CancelCurrent`, `gather→Command.GatherResource`(`{"resource","quantity"}`).
  recipe·lore·conversation 등은 `display_text` 로만 응답합니다.
