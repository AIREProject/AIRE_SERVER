# 새 플레이어 기기 연결 가이드

새로 시작한 플레이어가 기기를 서버에 연결하는 전체 흐름을 정리한다. 클라이언트 개발자에게
그대로 넘겨도 되는 실무 문서다.

> [!NOTE]
> 권위는 코드다 — 계약은 [`app/pairing_models.py`](../app/pairing_models.py), 오류 매핑은
> [`app/errors_http.py`](../app/errors_http.py). 엔드포인트 전체 명세는
> [`api-endpoints.md`](api-endpoints.md), WS 연동은
> [`websocket-client-guide.md`](websocket-client-guide.md).

## 큰 그림

**게임(PC)이 먼저 자기 프로필을 만들고, 폰은 그 프로필이 발급한 페어링 코드로 합류한다.**
같은 프로필(+ 같은 `save_slot_id`)이면 게임과 폰이 **같은 기억**을 공유한다. 플레이어가 여러
명이면 각 PC가 각자 등록해 각자 프로필을 갖는다(GameClient 는 프로필당 하나).

```
게임(PC)                                폰(모바일)
   │ ① register-game (부트스트랩 토큰)
   │──────────► profile 생성 + device_token 획득
   │ ② pairing-codes (게임 토큰)
   │──────────► 8자리 코드 (5분, 1회용)
   │  ─ ─ ─ 화면에 코드 표시 ─ ─ ─►
   │                                      │ ③ pair (코드 입력, 인증 불필요)
   │                                      │──────► 같은 profile 의 device_token 획득
   │ ④ chat (Bearer)                      │ ④ chat (Bearer)
   └──────────────► 마코 ◄────────────────┘   (기억 공유)
```

## 사전 준비

- 서버 베이스 경로: `http://<host>:8000/api/v1` (헬스체크만 `/health`).
- **부트스트랩 토큰**: 서버 `.env` 의 `DEV_GAME_DEVICE_TOKEN`. 게임 클라이언트와 **사전에
  공유**하는 고정 비밀값이다. 최초 등록(①)에만 쓰고, 이후에는 발급받은 `device_token` 을 쓴다.
- 모든 ID(`request_id` 등)는 `^[A-Za-z0-9][A-Za-z0-9._:-]*$`, 1~128자.
- 본문은 JSON, **모르는 필드는 거절**된다(`extra="forbid"` → 400).

---

## ① 게임(PC) — 프로필 생성 & 로그인

```http
POST /api/v1/devices/register-game
Authorization: Bearer <DEV_GAME_DEVICE_TOKEN>
Content-Type: application/json

{ "request_id": "<이 설치본의 고정 안정 ID>" }
```

응답 `200`:

```json
{
  "profile_id": "profile-…",
  "device": { "device_id": "device-…", "role": "GameClient", "created_at": "…" },
  "device_token": "token-….<base64>"
}
```

- `device_token` = 이 게임의 진짜 로그인 토큰. **저장**하고 이후 모든 요청에
  `Authorization: Bearer <device_token>` 로 쓴다.
- **`request_id` 는 그 설치본의 고정 ID**로. 같은 값으로 다시 부르면 새 프로필을 만들지
  않고 **같은 토큰을 그대로 반환**한다(멱등) — 토큰 분실 시 복구 수단.
- 서로 다른 `request_id` = 각자 새 프로필(= 다른 플레이어).

| 실패 | 의미 · 대처 |
|---|---|
| `401 UnauthorizedDevice` | 부트스트랩 토큰 없음/불일치 → `.env` 값과 바이트 단위로 일치시킨다 |
| `503 AuthenticationUnavailable` | 서버에 `DEVICE_CREDENTIAL_PEPPER`/`DEV_GAME_DEVICE_TOKEN` 미설정 → 서버 설정 |

## ② 게임 — 페어링 코드 발급 (폰 붙일 때만)

```http
POST /api/v1/devices/pairing-codes
Authorization: Bearer <게임 device_token>
Content-Type: application/json

{ "request_id": "<요청 고유 ID>" }
```

응답 `200`:

```json
{ "pairing_code": "48213905", "expires_at": "…" }
```

- 8자리 숫자, 기본 **300초(5분)** 만료, **1회용**.
- WebClient 토큰으로 부르면 `403 DeviceRoleNotAllowed`.

## ③ 폰 — 코드로 합류 (인증 불필요)

게임 화면의 8자리 코드를 폰에 입력해 호출한다. **Bearer 토큰이 필요 없다.**

```http
POST /api/v1/devices/pair
Content-Type: application/json

{ "request_id": "<폰 요청 고유 ID>", "pairing_code": "48213905" }
```

응답 `200`:

```json
{
  "profile_id": "profile-…",
  "device": { "device_id": "device-…", "role": "WebClient", "created_at": "…" },
  "device_token": "token-….<base64>"
}
```

- `profile_id` 는 **게임과 동일** → 기억 공유. `device_token` 을 저장해 폰의 로그인에 쓴다.

| 실패 | 의미 |
|---|---|
| `400 InvalidPairingCode` | 코드 불일치/형식 위반 |
| `410 ExpiredPairingCode` | 만료 → 새 코드 발급 |
| `409 UsedPairingCode` | 이미 쓴 코드 → 새 코드 발급 |
| `403 DeviceLimitExceeded` | 프로필 기기 상한(기본 20) 초과 → 기존 기기 해지 |

## ④ 대화

게임/폰 각자의 `device_token` 으로:

```http
POST /api/v1/chat
Authorization: Bearer <device_token>
Content-Type: application/json

{ "request_id": "…", "session_id": "…", "save_slot_id": "slot-1",
  "companion_id": "mako", "user_message": "안녕", "surface": "game" }
```

- 폰이면 `"surface": "mobile"` — **말투만** 바뀐다. 응답 모양·허용 명령은 동일.
- 같은 `profile_id` + `save_slot_id` 면 게임과 폰이 같은 대화/기억을 공유한다.
- 실시간이 필요하면 `WS /api/v1/chat` — 헤더 대신 프레임 봉투에 `token` 필드로 넣는다
  ([websocket-client-guide.md](websocket-client-guide.md)).

---

## 기기 관리

| 요청 | 인증 | 용도 |
|---|---|---|
| `GET /api/v1/devices` | GameClient | 이 프로필의 기기 목록 |
| `GET /api/v1/devices/me` | WebClient | 내 기기 정보 |
| `DELETE /api/v1/devices/me` | WebClient | 폰 스스로 해지 |
| `DELETE /api/v1/devices/{device_id}` | GameClient | 게임이 특정 폰(WebClient) 해지 |

## 자주 막히는 곳

- **폰만 단독으로는 시작 불가.** 게임이 먼저 프로필을 만들어야 폰이 붙는다. ("모바일/PC 구분
  없는 직접 등록"은 현재 범위 밖 — `docs/temporary-scaffolds.md` §2 참조.)
- **`register-game` 이 401** → 대개 부트스트랩 토큰(`DEV_GAME_DEVICE_TOKEN`) 값 불일치/누락.
  `Authorization: Bearer <값>` 형식으로, `.env` 값과 정확히 같게(공백·따옴표·개행 주의).
- **페어링 코드 5분 만료 · 1회용.** 폰 여러 대는 코드를 여러 번 발급.
- **한 프로필당 기기 20대**(`MAX_DEVICES_PER_PROFILE`), GameClient 는 프로필당 1개.

## 한 번에 돌려보기

로컬에서 전체 흐름을 curl 로 확인하려면
[`scripts/onboard_smoke.sh`](../scripts/onboard_smoke.sh) 를 실행한다:

```bash
DEV_GAME_DEVICE_TOKEN=$(grep '^DEV_GAME_DEVICE_TOKEN=' .env | cut -d= -f2) \
  BASE=http://127.0.0.1:8000 bash scripts/onboard_smoke.sh
```
