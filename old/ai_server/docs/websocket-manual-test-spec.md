# WebSocket 수동 테스트 명세 (원격 서버 대상)

`WS /api/v1/chat`을 사람이 손으로 한 단계씩 눌러가며 확인하기 위한 절차다. **테스트
머신이 서버가 도는 머신과 다르다고 가정한다** — 여기서는 `https://api.mtvs2026.work`에
붙는다. 서버를 직접 기동할 수 있는(같은 머신·같은 저장소) 상황이면 이 문서 대신
`uv run python scripts/verify_spec.py`(자동 스모크 테스트, PASS/FAIL 로그)나
`uv run pytest tests/test_ws_chat_api.py`(회귀 테스트)가 더 빠르다.

봉투·필드의 권위 있는 정의는 [`websocket-client-guide.md`](websocket-client-guide.md)와
`app/models.py`다. 여기서는 그 계약을 원격에서 손으로 한 단계씩 실행하는 순서만 다룬다.

> [!WARNING]
> **`https://api.mtvs2026.work`는 실제로 응답하는 배포본이다(Cloudflare 뒤에 떠 있음).**
> `register-game`을 호출하면 그 배포본의 DB에 **진짜 새 프로필**이 생긴다(2026-08-05부터
> 호출마다 새 프로필 — `docs/handoff.md` §9-1). 테스트 목적이면 같은 `request_id`를
> 고정해 재실행하고(멱등이라 새 프로필이 안 생긴다), 의미 없이 반복 호출하지 않는다.
> 부트스트랩 토큰(`DEV_GAME_DEVICE_TOKEN`)은 그 배포본에 한해 **무제한 프로필 생성
> 권한**이므로(`docs/temporary-scaffolds.md` "새로 커진 신뢰 함의") 로그·화면 공유에 남기지
> 않는다.

---

## 0. 준비물

- 테스트 대상: `https://api.mtvs2026.work` — 이미 떠 있는 원격 배포본이다. **로컬에서
  `uvicorn`을 새로 띄우지 않는다.**
- **프로토콜은 `https://`/`wss://`뿐이다.** `ws://api.mtvs2026.work`처럼 평문으로 붙으면
  TLS 핸드셰이크가 없어 그냥 실패한다.
- 부트스트랩 토큰(`DEV_GAME_DEVICE_TOKEN`)이 필요하다. 이 문서를 읽는 쪽은 그 서버의
  `.env`에 접근할 수 없는 게 보통이므로, **서버를 운영하는 쪽에게 안전한 채널로 전달받아**
  아래 §1의 `BOOT` 변수에 직접 채운다(서버에 SSH로 붙을 수 있다면 `.env`에서
  `grep '^DEV_GAME_DEVICE_TOKEN=' .env`로 직접 읽어도 된다).
- 도구: `curl`(페어링 단계용) + WS 클라이언트 하나.
  - **브라우저 개발자 도구 콘솔** — 설치할 것 없음, 가장 빠름 (§3-A)
  - **Python `websockets`** — `uv run python`으로 바로 씀, `uv sync --dev`에 이미 포함
    (`uvicorn[standard]`의 의존성) (§3-B). 저장소가 없는 머신이면
    `pip install websockets`로 대체한다.
  - **`websocat`/`wscat`** — 있으면 편하지만 필수 아님 (§3-C)

이 문서의 명령은 대상이 `https://api.mtvs2026.work`라고 가정한다. 다른 원격 호스트를
테스트하려면 `BASE`/`WS_BASE` 두 값만 바꿔 읽는다(로컬로 돌아가려면
`http://127.0.0.1:8000` / `ws://127.0.0.1:8000`).

---

## 1. 토큰 두 가지 — 절대 섞으면 안 된다

이 절차에서 가장 흔한 실수는 **부트스트랩 토큰을 채팅에 그대로 쓰는 것**이다. 둘은
생김새(둘 다 긴 문자열)만 비슷할 뿐 용도가 완전히 다르다.

| | 부트스트랩 토큰 (`BOOT`) | 디바이스 토큰 (`device_token`) |
|---|---|---|
| 어디서 오나 | `.env`의 `DEV_GAME_DEVICE_TOKEN` — 서버 운영자에게 전달받음 | `register-game`(또는 `pair`) **응답**의 `device_token` 필드 |
| 어디에 쓰나 | `POST /devices/register-game` 호출의 `Authorization` 헤더, **딱 그 한 곳** | 이후 모든 요청 — WS 프레임의 `token` 필드, HTTP `Authorization` 헤더 |
| 잘못 쓰면 | — | 채팅 `token`에 부트스트랩 값을 넣으면 `devices` 테이블에 없는 값이라 `UnauthorizedDevice`로 거절된다 |
| 유출 위험 | **크다** — 이 값을 쥔 누구든 그 배포본에 무제한으로 새 프로필을 만들 수 있다(§ 상단 경고) | 작다 — 디바이스 하나 몫의 권한뿐이고 해지 가능 |

즉 **`BOOT`은 딱 한 번, WS/채팅에는 `device_token`만** 쓴다. 아래 §1-1에서 그 교환을
한 번 하고 나면 이후로는 `device_token`만 다룬다.

### 1-1. 디바이스 토큰 발급

WS도 HTTP와 마찬가지로 디바이스 토큰 없이는 아무것도 못 한다. 가장 빠른 길은
게임 클라이언트 토큰 하나만 발급받는 것이다(모바일 페어링까지는 필요 없다 — 그건
`device-onboarding.md` 참고).

```bash
BASE="https://api.mtvs2026.work"
BOOT="<서버 운영자에게 전달받은 DEV_GAME_DEVICE_TOKEN>"

RESPONSE=$(curl -s -X POST "$BASE/api/v1/devices/register-game" \
  -H "Authorization: Bearer $BOOT" -H "Content-Type: application/json" \
  -d '{"request_id":"ws-manual-test-1"}')
echo "$RESPONSE"

TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json;print(json.load(sys.stdin)['device_token'])")
echo "device_token: $TOKEN"
```

**기대 결과:** `200`과 함께 `device_token`, `profile_id`가 담긴 JSON, 그리고 `$TOKEN`에
그 `device_token`이 그대로 들어간다(같은 셸 세션이면 §3-C에서 바로 재사용 가능).

```json
{"request_id":"ws-manual-test-1","profile_id":"profile-...","device":{"role":"GameClient",...},"device_token":"token-....<base64>"}
```

`$TOKEN`(= `device_token`) 값을 아래 모든 WS 프레임의 `token` 필드에 쓴다. **`$BOOT`는
여기서 다시 등장하지 않는다.** (같은 `request_id`로 다시 호출하면 새 프로필을 만들지
않고 같은 토큰을 돌려준다 — 재실행해도 안전하다. `docs/handoff.md` §9-1.)

> `401`이 나면 `$BOOT`가 비었거나 틀린 것이다(오탈자 흔함 — 붙여넣기 확인). `503`이면
> 서버 쪽 `DEVICE_CREDENTIAL_PEPPER`가 비어 있는 것 — 로컬에서 고칠 수 있는 게 아니라
> 서버 운영자에게 알려야 한다. `521`/`522`/`523`/`525`처럼 `cf-ray` 헤더가 붙은 5xx는
> **Cloudflare가 오리진 서버에 못 붙었다는 뜻**이라 우리 쪽 요청 문제가 아니다.

---

## 2. 체크리스트

아래 순서대로 하나씩 실행하며 표에 체크한다. §3에 각 방법(브라우저/Python/CLI)별
구체적인 명령이 있다 — 여기서는 "무엇을 보내고 무엇을 기대하는가"만 정리한다.

| # | 보내는 프레임 | 기대하는 응답 | 확인 포인트 |
|---|---|---|---|
| 1 | 정상 chat (`user_message: "따라와"`, `allowed_commands: ["Command.Follow"]`) | `type: chat_response` | `payload.command_candidates[0].type == "Command.Follow"` |
| 2 | 같은 연결로 두 번째 chat (`request_id` 다르게) | `type: chat_response` | `payload.request_id`가 이번 요청과 일치 — 이전 응답과 안 섞임 |
| 3 | `token` 필드를 아예 빼고 chat 전송 | `type: error` | `payload.error.code == "UnauthorizedDevice"`, **연결은 안 끊김** |
| 4 | 존재하지 않는 토큰으로 chat 전송 | `type: error` | 위와 동일 |
| 5 | `type`을 `"chat"`이 아닌 값으로 전송 | `type: error` | `payload.error.code == "InvalidRequest"` |
| 6 | JSON이 아닌 텍스트 그대로 전송(`send_text`) | `type: error` | `InvalidRequest`, 연결 유지 |
| 7 | `payload`에 정의 안 된 필드 추가 | `type: error` | `InvalidRequest` (`extra="forbid"`) |
| 8 | 3~7 중 아무거나 보낸 **직후** 같은 연결로 정상 chat 재전송 | `type: chat_response` | **핵심 항목.** 실패해도 소켓이 살아있다는 것의 증명 |
| 9 | 명령이 필요한데 정보가 부족한 말(예: `"저것 좀 캐 줘"`, `allowed_commands: ["Command.GatherResource"]`) | `type: chat_response`, `command_candidates: []` | 되묻기(ask-back) — 마코가 "뭘?" 하고 되물음 |
| 10 | 9에 이어서 같은 연결·같은 `session_id`/`save_slot_id`/`companion_id`로 답(예: `"나무"`) | `type: chat_response` | `command_candidates[0].type == "Command.GatherResource"` — 되묻기 상태가 프레임을 넘어 이어짐 |
| 11 | `type: "situation"` (`payload.situation: ["적이 나타났다"]`) | `type: situation_response` | `payload.display_text`가 비어있지 않음, `command_candidates` 필드 자체가 없음 |

**8번이 이 문서에서 가장 중요한 항목이다.** WS는 "오류가 나도 연결을 끊지 않는다"가
계약이므로(`websocket-client-guide.md` §5), 3~7번 각각의 오류 프레임 뒤에 정상 프레임이
여전히 처리되는지 반드시 확인한다.

각 응답을 기다릴 때 **타임아웃을 로컬 테스트보다 넉넉히**(예: 30초가 아니라 45~60초)
잡는다. 원격 호출은 Cloudflare를 경유해 로컬 직결보다 왕복이 더 걸린다.

---

## 3. 방법별 실행

### 3-A. 브라우저 콘솔 (가장 빠름)

> [!WARNING]
> **아무 페이지에서나 되지는 않는다.** 이미 열려 있는 사이트의 콘솔에서 실행하면 그
> 사이트의 CSP(`Content-Security-Policy`)가 적용된다 — `connect-src`가 `https:`만
> 허용하고 `wss:`를 따로 안 열어 두면(흔한 설정) `new WebSocket("wss://...")`가 CSP
> 위반으로 조용히 막힌다("`connect-src` 지시자를 위반...리소스 로드를 차단" 같은 콘솔
> 오류). CSP는 스킴을 정확히 매칭하므로 `https:`를 허용해도 `wss:`는 별도다.
>
> **주소창에 `about:blank`를 입력해 빈 탭을 새로 연 다음**(또는 `data:text/html,`)
> 그 탭의 콘솔에서 실행한다 — 빈 페이지는 CSP가 없다. 그래도 막히면 §3-B(Python)로
> 넘어가는 게 더 빠르다.

> [!WARNING]
> **`device_token` 발급은 브라우저에서 직접 못 한다.** 이 서버는 `CORSMiddleware`가
> 없다(`app/main.py`) — 게임/모바일 네이티브 클라이언트가 대상이라 브라우저 origin의
> `fetch`/`XHR`를 애초에 허용하지 않는다. `register-game`에 `Authorization` 헤더를
> 실으면 브라우저가 먼저 `OPTIONS`로 프리플라이트를 보내는데, 서버가
> `Access-Control-Allow-Origin`을 안 주니 `CORS Missing Allow Origin`으로 막히고
> 본요청은 나가지도 않는다. **`device_token`은 §1-1(curl)이나 §3-B(Python)로 먼저
> 발급받아** 아래 스크립트의 `TOKEN`에 붙여 넣는다 — 반대로 순수 `WebSocket` 연결
> 자체는 CORS 대상이 아니라서(프리플라이트 없음) 문제없이 된다.

빈 탭에서 개발자 도구(F12) → Console 을 열고 아래를 붙여넣는다. `TOKEN`만
1단계에서 발급받은 `device_token`으로 바꾼다. **`var`로 선언했다** — 콘솔은 같은 탭에서
`let`/`const`를 다시 선언하면 `Uncaught SyntaxError: redeclaration of let TOKEN`으로
막힌다(그 탭의 JS 스코프가 세션 내내 유지되기 때문). `var`는 재선언이 허용되므로,
토큰을 바꿔서 블록 전체를 다시 붙여넣어도(새 연결이 열린다) 되고, 연결은 유지한 채
토큰만 바꾸고 싶으면 `TOKEN = "..."` 한 줄만 다시 실행해도 된다.

```javascript
var TOKEN = "여기에_device_token"; // §1-1/§3-B로 발급받은 값 — BOOT가 아니다
var ws = new WebSocket("wss://api.mtvs2026.work/api/v1/chat");

ws.onopen = () => console.log("connected");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
ws.onclose = (e) => console.log("closed", e.code, e.reason);
ws.onerror = (e) => console.log("error", e);

function send(payload, token = TOKEN) {
  ws.send(JSON.stringify({ type: "chat", token, payload }));
}

function body(msg, extra = {}) {
  return {
    request_id: "manual-" + Date.now(),
    session_id: "manual-session",
    save_slot_id: "slot-1",
    companion_id: "mako",
    user_message: msg,
    ...extra,
  };
}
```

연결되면(`connected` 로그) 체크리스트를 하나씩 실행한다:

```javascript
// 1번 항목
send(body("따라와", { allowed_commands: ["Command.Follow"] }));

// 3번 항목 — token을 아예 뺌
ws.send(JSON.stringify({ type: "chat", payload: body("안녕") }));

// 4번 항목 — 존재하지 않는 토큰
ws.send(JSON.stringify({ type: "chat", token: "token-does-not-exist.invalid-secret", payload: body("안녕") }));

// 5번 항목
ws.send(JSON.stringify({ type: "not-chat", payload: {} }));

// 8번 항목 — 위 오류 프레임들 직후에도 여전히 처리되는지
send(body("괜찮아?"));
```

각 `send`/`ws.send` 뒤에 콘솔에 찍히는 `onmessage` 로그를 표와 대조한다.

### 3-B. Python `websockets`

한 세션 안에서 순서대로 여러 프레임을 보내고 싶을 때 더 편하다. `BOOT`만 채우면
스크립트가 `device_token`을 직접 발급받아 쓰므로 따로 복사할 값이 없다. 파일로 저장해
`uv run python ws_manual_test.py`로 돌리거나, `uv run python -i`로 실행해 REPL에서
이어서 타이핑해도 된다.

```python
import asyncio, json, urllib.request, websockets

BASE = "https://api.mtvs2026.work"
BOOT = "여기에_DEV_GAME_DEVICE_TOKEN"  # register-game 호출에만 쓰고 다시는 안 씀

_req = urllib.request.Request(
    f"{BASE}/api/v1/devices/register-game",
    data=json.dumps({"request_id": "ws-manual-test-1"}).encode(),
    headers={
        "Authorization": f"Bearer {BOOT}",
        "Content-Type": "application/json",
        # Cloudflare가 브라우저 UA가 아닌 요청을 403 "error code: 1010"으로 막는다
        # (docs/handoff.md §9-5와 같은 원인). urllib 기본 UA(Python-urllib/...)가 걸린다.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    },
    method="POST",
)
TOKEN = json.load(urllib.request.urlopen(_req))["device_token"]
print("device_token:", TOKEN)

WS_URI = f"{BASE.replace('https://', 'wss://')}/api/v1/chat"

def body(msg, **extra):
    payload = {
        "request_id": "manual-1",
        "session_id": "manual-session",
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "user_message": msg,
    }
    payload.update(extra)
    return payload

async def run():
    async with websockets.connect(WS_URI, open_timeout=15) as ws:
        # 1번 항목
        await ws.send(json.dumps({"type": "chat", "token": TOKEN,
            "payload": body("따라와", allowed_commands=["Command.Follow"])}))
        print(await asyncio.wait_for(ws.recv(), timeout=45))

        # 3번 항목 — token 없이
        await ws.send(json.dumps({"type": "chat", "payload": body("안녕")}))
        print(await asyncio.wait_for(ws.recv(), timeout=45))

        # 4번 항목 — 존재하지 않는 토큰
        await ws.send(json.dumps({"type": "chat", "token": "token-does-not-exist.invalid-secret",
            "payload": body("안녕")}))
        print(await asyncio.wait_for(ws.recv(), timeout=45))

        # 8번 항목 — 위 오류들 직후에도 여전히 처리되는지
        await ws.send(json.dumps({"type": "chat", "token": TOKEN,
            "payload": body("괜찮아?", request_id="manual-2")}))
        print(await asyncio.wait_for(ws.recv(), timeout=45))

asyncio.run(run())
```

`websockets` 패키지가 없는 환경(이 저장소 밖)이면 `pip install websockets`만 추가로
필요하다 — `urllib.request`는 표준 라이브러리라 그 외 의존성은 없다.

### 3-C. `websocat` / `wscat` (설치돼 있다면)

프레임을 한 줄씩 직접 치는 대화형 방식. §1-1을 같은 터미널 세션에서 먼저 실행해 뒀다면
`$TOKEN`에 `device_token`이 이미 들어 있으므로, 아래 JSON에서 `<device_token>` 자리에
그 값을 붙여 넣기만 하면 된다(셸이 `$TOKEN`을 자동으로 펼쳐 주지는 않으니 직접 치환).

```bash
websocat wss://api.mtvs2026.work/api/v1/chat
```

```json
{"type":"chat","token":"<device_token>","payload":{"request_id":"r1","session_id":"s1","save_slot_id":"slot-1","companion_id":"mako","user_message":"따라와","allowed_commands":["Command.Follow"]}}
```

`wscat -c wss://api.mtvs2026.work/api/v1/chat`도 동일하게 쓴다(`npm i -g wscat` 필요).

---

## 4. 문제 해결

| 증상 | 원인 | 확인 |
|---|---|---|
| 연결 자체가 안 열림(`connection refused`/타임아웃) | 방화벽·VPN이 아웃바운드 443을 막음, 또는 DNS 실패 | 먼저 `curl -s https://api.mtvs2026.work/health` 로 HTTP가 되는지 확인(`-I`/HEAD는 이 경로에서 405가 나니 `GET`으로) — 이것도 안 되면 WS 이전에 네트워크 문제 |
| `ws://`로 붙으면 즉시 실패 | 원격은 TLS 필수 — 평문 WS 업그레이드가 안 됨 | `wss://`로 바꾼다 |
| 브라우저 콘솔에서 `NS_ERROR_CONTENT_BLOCKED`/`connect-src` CSP 오류 | 콘솔을 열어 둔 사이트의 CSP가 `wss:`로의 연결을 안 허용 | §3-A 경고대로 `about:blank` 빈 탭에서 다시 실행, 안 되면 §3-B(Python)로 |
| 브라우저 `fetch()`로 `register-game` 호출 시 `CORS Missing Allow Origin`/`NetworkError` | 서버에 `CORSMiddleware`가 없다 — 브라우저 origin 호출 자체를 지원하지 않음(의도된 설계, 게임/모바일 네이티브 클라이언트 전용) | 브라우저에서 `device_token`을 직접 발급받으려 하지 않는다 — §1-1(curl)이나 §3-B(Python)로 발급받아 §3-A의 `TOKEN`에 붙여 넣는다. `WebSocket` 연결 자체는 CORS 대상이 아니라 문제없다 |
| 응답에 `cf-ray` 헤더가 있는 5xx(521/522/523/525 등) | Cloudflare는 살아 있지만 오리진(실제 앱 서버)에 연결 실패 | 우리 쪽 요청 문제가 아니다 — 서버 운영자에게 오리진 상태 확인 요청 |
| `curl`은 되는데 Python(`urllib`/`requests`) 요청만 `403`, 본문이 `error code: 1010` | Cloudflare가 브라우저 UA가 아닌 요청을 차단(`docs/handoff.md` §9-5의 임베딩 서버와 같은 원인) — `urllib` 기본 UA(`Python-urllib/3.x`)가 걸린다 | 요청에 브라우저 `User-Agent` 헤더를 실어 보낸다 — §3-B 스크립트에 이미 반영돼 있음 |
| 첫 메시지 보내자마자 소켓이 그냥 끊김 | 대개 클라이언트 쪽 문제(JSON 직렬화 실패 등) — 서버는 어떤 처리 실패에도 연결을 안 끊는다 | 보낸 raw 문자열이 유효한 JSON 객체인지 확인 |
| 모든 chat이 `UnauthorizedDevice` | 토큰이 비었거나 오타, 해지된 토큰 — **또는 `BOOT`(부트스트랩)를 그대로 넣은 경우가 제일 흔하다** | §1의 표로 어떤 토큰을 쓰고 있는지 다시 확인, §1-1을 다시 실행해 새 `device_token` 발급 |
| 브라우저 콘솔에서 `invalid assignment to const 'TOKEN'` 또는 `redeclaration of let TOKEN` | `let`/`const`로 선언한 걸 같은 탭에서 재할당하거나 다시 선언함 — 콘솔은 탭이 살아있는 동안 스코프가 유지된다 | §3-A는 `var`로 선언돼 있어 재할당·재선언 둘 다 에러 없이 된다. 그래도 나면 `about:blank` 탭을 새로 열어 처음부터 다시 |
| 모든 요청이 `AuthenticationUnavailable`(503) | 서버 쪽 `DEVICE_CREDENTIAL_PEPPER`가 비어 있음 — 로컬에서 고칠 수 없음 | 서버 운영자에게 확인 요청 |
| 응답이 로컬보다 눈에 띄게 느림(수 초~수십 초) | Cloudflare 경유 + 로컬 LLM 직결이 아니라서 왕복이 늘어난다(정상 범위일 수 있다) | 반복해서 계속 느리면 서버 운영자에게 문의, 한두 번 튀는 건 정상 |
| 되묻기(9~10번)가 이어지지 않음 | `session_id`/`save_slot_id`/`companion_id` 중 하나가 두 프레임에서 다름 | 넷(프로필+세 값)이 정확히 같은지 확인 — `websocket-client-guide.md` "대화가 갈리는 기준" |
| `register-game`이 `409` | 이제는 나지 않는다(2026-08-05부터 호출마다 새 프로필) — `409`가 나온다면 `request_id`를 재사용했는데 이미 다른 역할로 등록된 경우뿐 | `docs/handoff.md` §9-1 |

---

## 5. 참고

- 봉투/필드 전체 계약: [`websocket-client-guide.md`](websocket-client-guide.md)
- 페어링 전체 흐름(모바일 포함): [`device-onboarding.md`](device-onboarding.md)
- 부트스트랩 토큰의 신뢰 범위(왜 함부로 다뤄야 하는지): `docs/temporary-scaffolds.md`
  "2026-08-05: 다중 플레이어 등록"
- 같은 절차를 로컬 서버 대상으로 실행할 때: 이 문서에서 `BASE`/`WS_BASE`를
  `http://127.0.0.1:8000` / `ws://127.0.0.1:8000`으로만 바꾸면 된다(§0의 로컬 준비물은
  루트 `CLAUDE.md`의 실행 커맨드 참고)
- 이 문서를 코드로 그대로 옮긴 자동 버전(로컬 서버 대상): `scripts/verify_spec.py`(§8-c
  구간), `tests/test_ws_chat_api.py`
- 실제 함정 목록: `docs/handoff.md` §9(운영·함정), 특히 §9-10(WS는 매 프레임 토큰)
