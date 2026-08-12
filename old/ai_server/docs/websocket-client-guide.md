# WebSocket 연동 가이드 (클라이언트용)

현행 계약이다. `POST /api/v1/chat`(HTTP)과 **동일한 페이로드 스키마**를 지속 연결 위에서
주고받는다. HTTP 엔드포인트는 그대로 유지되므로 언제든 병행하거나 폴백할 수 있다.

권위 있는 정의는 `app/models.py`(Pydantic)에 있다.

> [!WARNING]
> **디바이스 토큰이 필요하다.** `POST /api/v1/devices/*` 로 페어링해 얻은 `device_token` 을
> 매 메시지의 `token` 필드에 실어 보내야 한다 — 브라우저 `WebSocket` 은 핸드셰이크에
> 커스텀 헤더를 못 실으므로 HTTP 의 `Authorization` 대신 이 방식을 쓴다. 없거나 무효/해지된
> 토큰은 `UnauthorizedDevice` 로 거절된다(연결은 끊기지 않는다). 페어링 절차는 README 의
> "디바이스 페어링" 절 참고.

---

## 1. 엔드포인트

| 엔드포인트 | 대상 |
|---|---|
| `WS /api/v1/chat` | 모든 클라이언트 |

인증이 헤더가 아니라 봉투의 `token` 필드로 옮겨 가서 게임용/브라우저용을 나눌 이유가
없다(그 구분은 원래 토큰을 헤더로 받느냐 첫 메시지로 받느냐의 차이였을 뿐이다). 연결하면
바로 chat 메시지를 보내면 된다.

---

## 2. 메시지 형식

모든 메시지는 `type` + `token` + `payload` 봉투다. `payload` 는 HTTP 본문/응답과 100% 동일한
스키마라 기존 DTO 를 그대로 재사용할 수 있다. `type` 은 두 가지를 받는다 — `chat`(`ChatRequest`)
과 `situation`(`SituationRequest`, `docs/api-endpoints.md` §5.4). 그 외 값은 400
`InvalidRequest` 로 거절되지만 연결은 끊기지 않는다.

**보내기**

```json
{ "type": "chat", "token": "<device_token>", "payload": { /* ChatRequest */ } }
{ "type": "situation", "token": "<device_token>", "payload": { /* SituationRequest */ } }
```

**받기**

```json
{ "type": "chat_response",      "payload": { /* ChatResponse */ } }
{ "type": "situation_response", "payload": { /* SituationResponse */ } }
{ "type": "error",              "payload": { /* ErrorEnvelope */ } }
```

---

## 3. ChatRequest

```json
{
  "request_id": "request-001",
  "schema_version": 1,
  "session_id": "session-001",
  "save_slot_id": "slot-001",
  "companion_id": "mako",
  "message_id": "message-001",
  "user_message": "여기서 기다려",
  "surface": "game",
  "time_context": {
    "source": "GameWorld",
    "day": 7,
    "hour": 23,
    "period": "Night"
  },
  "recent_event_ids": ["event-001"],
  "game_context": { "location_id": "region_abandoned_mining_village" },
  "allowed_commands": ["Command.HoldPosition", "Command.Follow"]
}
```

`game_context` 와 `allowed_commands`, 신원 대조용 `profile_id`/`device_id`, 그리고 표에 필수가
아닌 선택 필드는 생략 가능하다. 신원 자체는 이 본문이 아니라 봉투의 `token` 이 정한다.

반드시 지켜야 할 것들:

- **정의되지 않은 필드를 넣으면 거부된다**(`extra="forbid"`). 무시되지 않고 `InvalidRequest` 다.
  현재 지원하는 선택 필드는 `schema_version`(값 `1`), `message_id`, `time_context`,
  `recent_event_ids`다. `interaction_mode`와 `player_name`은 여전히 정의되지 않았다.
- `companion_id`: 지금은 `"mako"` 만 유효하다. 다른 값은 `UnknownCompanion`(400).
- `profile_id` / `device_id`: 보내면 `token` 이 가리키는 인증된 신원과 대조한다. 다르면
  `IdentityScopeMismatch`(403) — 신원 위조 시도로 취급한다.
- `request_id` / `session_id` / `save_slot_id` / `companion_id`:
  `^[A-Za-z0-9][A-Za-z0-9._:-]*$`, 최대 128자.
- `user_message`: 1~2000자.
- `game_context`: 최대 32개 키. `token`/`password`/`secret` 같은 **민감 키는 금지**(거부된다).
  (봉투 최상위의 인증 `token` 필드와는 무관하다.)

### 대화가 갈리는 기준

**인증된 프로필 + `save_slot_id` + `companion_id` + `session_id`** 다. 넷 중 하나라도
다르면 마코 입장에서 다른 대화이고, 직전 턴의 기억("뭘 캐?" 에 대한 답 같은 것)이
이어지지 않는다.

한 플레이어의 대화를 이어가려면 같은 디바이스 토큰으로, **세 값(`save_slot_id`,
`companion_id`, `session_id`)을 모두 고정**해서 보낸다.

---

## 4. ChatResponse

```json
{
  "request_id": "request-001",
  "message_id": "message-001",
  "session_id": "session-001",
  "save_slot_id": "slot-001",
  "companion_id": "mako",
  "response_id": "response-<uuid>",
  "display_text": "알겠어. 여기서 기다릴게.",
  "command_candidates": [
    {
      "command_id": "command-<uuid>",
      "request_id": "request-001",
      "type": "Command.HoldPosition",
      "target_id": null,
      "priority": "Normal",
      "issued_at": "...",
      "expires_at": "...",
      "parameters": {}
    }
  ],
  "ai_metadata": { "provider": "...", "model_version": "...", "prompt_version": "..." }
}
```

명령 처리 시 주의:

- `command_candidates` 는 **0개 또는 1개**다(스키마 상한은 4). 없으면 대사만 출력한다.
- **`allowed_commands` 에 넣지 않은 명령은 절대 나오지 않는다.** 지금 상황에서 실행 가능한
  것만 보낸다.
- **`expires_at` 을 반드시 확인한다**(기본 TTL 30초). 만료된 명령은 버린다.
- 현재 마코가 방출하는 명령은 **`Command.Follow` / `Command.HoldPosition` /
  `Command.CancelCurrent` / `Command.GatherResource`** 넷이다. 채집은
  `parameters` 에 `{"resource": "wood"}` 를 담고, 플레이어가 수량을 말했을 때만
  `"quantity"` 가 함께 온다. **수량 키가 없으면 게임이 알아서 정한다** — 서버는 인벤토리를
  모르므로 지어내지 않는다.

---

## 5. 에러 처리

```json
{
  "type": "error",
  "payload": {
    "request_id": "request-001",
    "error": {
      "code": "AIServiceUnavailable",
      "message": "...",
      "retryable": true,
      "details": {}
    }
  }
}
```

**핵심: 에러가 나도 연결은 끊기지 않는다.** 다음 메시지를 계속 보낼 수 있으므로 소켓을
다시 열지 않는다.

| code | retryable | 의미 |
|---|---|---|
| `InvalidRequest` | false | 봉투/스키마 위반 — 고쳐서 보내야 함 |
| `RequestTooLarge` | false | 메시지 크기 초과 (기본 256KB) |
| `AIServiceUnavailable` | true | AI 일시 장애 |
| `AIServiceTimeout` | true | AI 타임아웃 |
| `AIServiceInvalidOutput` | true | AI 가 허용 밖 출력을 냄(서버가 거절) |
| `RequestTimeout` | true | 메시지 처리 타임아웃 (기본 30초) |
| `InternalError` | true | 서버 내부 오류 |
| `UnauthorizedDevice` | false | `token` 이 없거나 무효/해지됨 — 재페어링 필요 |
| `AuthenticationUnavailable` | true | 서버에 `DEVICE_CREDENTIAL_PEPPER` 가 설정되지 않음 |
| `IdentityScopeMismatch` | false | 본문의 `profile_id`/`device_id` 가 인증된 신원과 다름 |
| `UnknownCompanion` | false | `companion_id` 가 등록되지 않음(지금은 `"mako"` 만 유효) |

> [!CAUTION]
> **멱등성이 없다.** 같은 `request_id` 로 재시도하면 LLM 이 **다시 호출되고 다른 응답이
> 온다.** 이전처럼 "재시도해도 저장된 응답이 온다" 고 가정하면 안 된다.
> 재시도한 요청이 명령을 내면 **중복 명령**이 될 수 있으므로, `command_id` 기준으로
> 클라이언트가 중복을 거르거나 애초에 재시도하지 않는다.

---

## 6. 연결 운영

- **메시지는 순차 처리된다.** 응답을 받기 전에 여러 chat 을 밀어넣어도 병렬 처리되지 않고
  순서대로 하나씩 처리된다. 파이프라이닝 이득이 없으므로 응답을 받고 다음을 보내는 방식을
  권장한다.
- 응답은 보낸 순서대로 온다.
- 재연결은 클라이언트 책임이다(서버는 자동 재연결을 하지 않는다).
- **서버 재시작은 대화 기억을 지운다.** 마코의 저장소는 인메모리다. 진행 중이던 되묻기
  ("뭘 캐?")도 함께 사라지므로, 재연결 후에는 완전한 문장으로 다시 요청한다.
- 대화 기억은 **30분 유휴**(`COMPANION_CONVERSATION_IDLE_TTL_SECONDS`) 후에도 사라진다.

---

## 7. 아직 없는 것

- **스트리밍 없음** — `display_text` 는 완성된 상태로 한 번에 온다.
- **서버 푸시 없음** — 현재는 요청에 대한 응답만 온다.

> 나중에 `chat_delta` / `chat_commit` 같은 타입이 추가될 수 있으므로, **모르는 `type` 은
> 조용히 무시하도록** 파서를 작성해 두면 클라이언트 수정 없이 호환된다.
