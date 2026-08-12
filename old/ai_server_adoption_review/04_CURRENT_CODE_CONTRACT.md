# 04. 현재 코드 계약

## 1. 문서 목적

이 문서는 `ai_server` 코드 스냅샷이 실제로 구현한 후보 계약을 요약한다. AI_RE의 공식 외부
계약이 아니며, UE/Web 구현 근거로 바로 사용하면 안 된다.

현행 후보 계약의 권위는 [`app/models.py`](../ai_server/app/models.py),
[`app/pairing_models.py`](../ai_server/app/pairing_models.py),
[`app/offline_task_models.py`](../ai_server/app/offline_task_models.py)와 각 route다.

## 2. 공통 처리

- HTTP body 기본 상한: 설정 기본 262,144 bytes
- HTTP 전체 request timeout: 설정 기본 30초
- AI request timeout: 설정 기본 10초
- `X-Request-ID`: 안정 ID 형식, body request ID가 있으면 같아야 함
- Validation error: HTTP 400의 공통 ErrorEnvelope
- `schema_version`: Chat/Situation에서 선택적, 제공하면 `1`만 허용
- 외부 모델: `extra="forbid"`

오류 envelope:

```json
{
  "request_id": "request-id",
  "error": {
    "code": "StableErrorCode",
    "message": "Safe public message",
    "retryable": false,
    "details": {}
  }
}
```

## 3. 인증과 범위

### Device token

- token 원문은 client에 반환한다.
- DB에는 lookup ID와 HMAC hash만 저장한다.
- 인증 시 hash, revoked state를 확인하고 `last_used_at`을 갱신한다.
- 인증 결과가 `profile_id`, `device_id`, `role`의 권위다.
- body의 profile/device ID는 선택적 주장값이며 불일치 시 403이다.

### 역할

- `GameClient`: pairing code 발급, profile device 관리, task 진행/완료/claim
- `WebClient`: 자기 기기 조회/폐기, task 생성/collect, mobile Chat

### 대화 범위

- 작업기억: profile + save slot + companion + session
- 장기기억: profile + save slot

두 범위가 다르므로 두 번째 companion을 추가하기 전 장기기억 key를 변경해야 한다.

## 4. Chat

### Request 핵심 필드

| 필드 | 필수 | 제약/의미 |
|---|---|---|
| `request_id` | 예 | Stable ID, 멱등 키 아님 |
| `schema_version` | 아니오 | 제공 시 `1` |
| `session_id` | 예 | 대화 작업기억 범위 |
| `save_slot_id` | 예 | profile 안의 세이브 범위 |
| `companion_id` | 예 | 현재 `mako`만 허용 |
| `profile_id`, `device_id` | 아니오 | 인증 신원과 대조 |
| `message_id` | 아니오 | 응답에 echo만 함 |
| `user_message` | 예 | 1~2000자 |
| `surface` | 아니오 | `game` 기본, 또는 `mobile`; 말투만 변경 |
| `time_context` | 아니오 | `GameWorld` 또는 `RealWorld` |
| `recent_event_ids` | 아니오 | 최대 32, 중복 금지, 현재 미사용 |
| `game_context` | 아니오 | 최대 32 top-level key, 민감해 보이는 key 재귀 차단 |
| `allowed_commands` | 아니오 | 최대 16, 중복 금지 |

### Response 핵심 필드

| 필드 | 의미 |
|---|---|
| `request_id`, `message_id` | 요청 상관값 |
| `session_id`, `save_slot_id`, `companion_id` | 범위 echo |
| `response_id` | 서버 생성 UUID 기반 ID |
| `display_text` | 1~4000자 대사 |
| `command_candidates` | 최대 4개, 현재 service는 0 또는 1개 |
| `offline_task_id` | WebClient 채집을 Offline Task로 바꾼 경우 |
| `ai_metadata` | provider/model/prompt version |

### Command candidate

모델 enum은 다음 열 가지를 허용한다.

- `Command.Follow`
- `Command.HoldPosition`
- `Command.ReturnToPlayer`
- `Command.EngageTarget`
- `Command.DistractTarget`
- `Command.MoveToLocation`
- `Command.CancelCurrent`
- `Command.GatherResource`
- `Command.Attack`
- `Command.Switch`

현재 brain이 실제 생성하도록 설계된 것은 Follow, HoldPosition, CancelCurrent,
GatherResource, Attack, Switch 여섯 가지다. request의 `allowed_commands`에 없는 candidate는 service가
거부한다. candidate에는 command/request ID, issued/expires time, priority, parameters가 있다.

## 5. Situation

`POST /api/v1/situations`는 player 발화 없이 client가 관찰한 상황 1~4줄을 보내 선제 대사를
받는다.

- Chat와 같은 인증·profile/save/companion/session 범위를 사용한다.
- command candidate를 반환하지 않는다.
- 자유 상황 문장을 분류 없이 LLM prompt에 넣는다.
- Chat와 같은 conversation memory와 transcript에 합쳐진다.

이 경로는 검증된 Event 계약을 대체하지 않는다.

## 6. WebSocket

`WS /api/v1/chat`은 Chat과 Situation frame을 처리한다.

- 브라우저 WebSocket의 header 제한 때문에 frame마다 token을 payload 옆에 보낸다.
- frame마다 token을 다시 인증하고 DB session을 연다.
- 한 연결의 frame은 순차 처리하므로 느린 요청이 뒤 frame을 막는다.
- server push가 아니라 request-response transport다.

token이 frame body에 있으므로 client debug logging과 proxy/message capture에서 반드시 마스킹해야 한다.

## 7. Device와 pairing

흐름:

```text
bootstrap bearer
  -> register-game
  -> GameClient token/profile 발급
  -> GameClient가 8자리 5분 pairing code 발급
  -> WebClient가 익명 pair 요청으로 code 교환
  -> WebClient token 발급
```

pairing code는 HMAC-derived 8자리 값이고 DB에는 hash만 저장한다. 만료와 1회 사용을 적용하며
동시 사용은 조건부 update로 막는다. rate limit은 없다.

## 8. Offline Task

상태:

```text
Pending -> InProgress -> Completed -> Claimed
```

- WebClient: create, collect
- GameClient: start, complete, claim
- Chat에서 생성한 Web gather task는 바로 `InProgress`가 된다.
- create만 profile/save/request ID unique key로 멱등이다.
- start/complete/claim/collect는 mutation request ID를 받지 않는다.
- `quantity`에 API 경계 제한이 없다.
- `Scouting` enum은 있지만 duration/실행 모델은 없다.

## 9. 기억과 원문

- 최근 conversation memory는 인메모리, 기본 idle TTL 30분이다.
- transcript는 conversation별 JSONL이고 player/companion/situation 원문을 담는다.
- 기본 transcript retention은 30일이다.
- long-term memory는 SQLite `episodic_memories`에 저장한다.
- Mock LLM은 기억 추출을 하지 않으므로 기본 코드 설정에서는 새 장기기억이 생기지 않는다.
- 일반 사용자 memory API와 삭제 API는 없다.

## 10. 공식 AI_RE 목표와 다른 점

| AI_RE 목표 | 현재 후보 구현 |
|---|---|
| 배포 OpenAPI가 런타임 권위 | 실시간 배포 대조 미완료 |
| required schema version | Chat/Situation에서 생략 가능 |
| Chat/Event/Command Result 멱등 | Chat 비멱등, Event/Command Result 없음 |
| 검증된 Event source | `recent_event_ids` 수신만 하고 미사용 |
| profile/save/companion memory scope | profile/save만 사용 |
| 사용자 memory delete | Admin delete만 존재 |
| InGame/Offline 시간 분리 | surface와 TimeSource 조합 미검증 |
| LLM runtime을 client contract에서 격리 | `ai_metadata`와 health provider 노출 |
| Backend 장애 시 UE local behavior 유지 | client fallback은 이 repository 범위 밖 |

