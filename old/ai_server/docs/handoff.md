# 인수인계 · 온보딩

**인계자:** Hans (승형) → **인수자:** 오병일
**인계일:** 2026-08-04
**대상 브랜치:** `feat/device-auth-profiles` (main 병합 전)

이 문서 하나로 서버를 **처음 띄우고**, **무엇이 되어 있는지 알고**, **이어서 개발**할 수 있게
쓴다. 앞부분(§1~§4)은 온보딩, 뒷부분(§5~§9)은 인수인계다.

---

## 목차

| 절 | 무엇 |
|---|---|
| §1 | 30분 안에 서버 띄우기 |
| §2 | 이 서버가 무엇인지 — 한 장 요약 |
| §3 | 코드 지도 — 어디를 열어야 하나 |
| §4 | 바꾸려면 어디를 여나 (작업별 색인) |
| §5 | 완료된 것 — 명세 대비 대조표 |
| §6 | **명세와 다른 4가지** (가장 중요) |
| §7 | 남은 일 — 우선순위와 착수점 |
| §8 | 설계 결정과 그 이유 — 되돌리기 전에 읽을 것 |
| §9 | 운영·함정 — 실제로 물릴 만한 것들 |

---

## 1. 30분 안에 서버 띄우기

### 1-1. 준비

Python 3.13 과 [uv](https://docs.astral.sh/uv/) 가 필요하다. Windows PowerShell 기준.

```powershell
git clone <repo> ; cd ai_companion_server
uv sync --dev
Copy-Item .env.example .env
```

### 1-2. `.env` 에서 반드시 채워야 하는 두 값

```dotenv
DEVICE_CREDENTIAL_PEPPER=<아무 긴 랜덤 문자열>
DEV_GAME_DEVICE_TOKEN=<아무 긴 랜덤 문자열>
```

- `DEVICE_CREDENTIAL_PEPPER` — 디바이스 토큰·페어링 코드를 HMAC 으로 저장할 때 쓰는 키.
  **비어 있으면 서버는 뜨지만 인증이 필요한 모든 요청이 503 `AuthenticationUnavailable`
  로 실패한다.** 헬스체크만 통과하므로 "서버는 떴는데 채팅이 다 503" 이면 십중팔구 이것이다.
  **이 값을 바꾸면 기존 디바이스 토큰이 전부 무효가 되고 장기기억 스코프 키도 달라진다**
  (§9-3).
- `DEV_GAME_DEVICE_TOKEN` — 첫 게임 클라이언트를 등록할 때만 쓰는 부트스트랩 토큰.
  아직 인증된 신원이 하나도 없는 상태를 푸는 열쇠다.

### 1-3. DB 만들고 실행

```powershell
uv run alembic upgrade head                              # 테이블 생성 + 게임 데이터 시드
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`alembic upgrade head` 는 **반드시 필요하다.** DB 는 첫 쓰기에 자동 생성되지 않는다.
이 한 번으로 디바이스·게임데이터·Offline_Task·장기기억 테이블이 다 생기고, 아이템 27종·
레시피 13종·제련 4종·적 3종이 시드된다.

### 1-4. 살아 있는지 확인

```powershell
curl http://127.0.0.1:8000/health
# {"service":"mako-companion","status":"ok","llm_provider":"local"}
```

`llm_provider` 는 **폴백까지 반영한 실제 선택**을 보고한다. `LLM_PROVIDER=openai` 인데 키가
없으면 여기에 `"mock"` 이 찍힌다 — 설정값이 아니라 지금 실제로 무엇이 답하고 있는지다.

### 1-5. 첫 대화까지 (페어링 3단계)

채팅은 **디바이스 토큰 없이는 못 부른다.** 순서가 정해져 있다.

아래는 실제로 돌려서 확인한 명령이다. PowerShell 에서는 `curl` 이 `Invoke-WebRequest` 의
별칭이라 따옴표 이스케이프가 지저분해진다 — `Invoke-RestMethod` 를 쓰면 JSON 이 바로
객체로 온다.

```powershell
$base = "http://127.0.0.1:8000"
$boot = (Get-Content .env | Select-String '^DEV_GAME_DEVICE_TOKEN=').ToString().Split('=',2)[1]

# ① 게임 클라이언트 등록 — 호출마다 새 프로필을 만든다 (§9-1)
$game = Invoke-RestMethod -Method Post -Uri "$base/api/v1/devices/register-game" `
  -Headers @{Authorization="Bearer $boot"} -ContentType 'application/json' `
  -Body '{"request_id":"boot-1"}'
# $game.device_token, $game.profile_id, $game.device.role 을 받는다

# ② 페어링 코드 발급 (게임 토큰으로)
$code = Invoke-RestMethod -Method Post -Uri "$base/api/v1/devices/pairing-codes" `
  -Headers @{Authorization="Bearer $($game.device_token)"} -ContentType 'application/json' `
  -Body '{"request_id":"pair-1"}'

# ③ 모바일이 그 코드로 페어링 (인증 불필요)
$web = Invoke-RestMethod -Method Post -Uri "$base/api/v1/devices/pair" `
  -ContentType 'application/json' `
  -Body (@{request_id="pair-2"; pairing_code=$code.pairing_code} | ConvertTo-Json)
```

이제 `$game.device_token`(GameClient)과 `$web.device_token`(WebClient) 두 개가 생겼다. 채팅:

```powershell
$chat = Invoke-RestMethod -Method Post -Uri "$base/api/v1/chat" `
  -Headers @{Authorization="Bearer $($game.device_token)"} -ContentType 'application/json' `
  -Body '{"request_id":"r1","session_id":"s1","save_slot_id":"slot1","companion_id":"mako",
          "user_message":"따라와","allowed_commands":["Command.Follow"]}'

$chat.display_text                  # 준비됐어, 바로 옆에서 잘 따라갈게.
$chat.command_candidates[0].type    # Command.Follow
```

> **자동 검증 스크립트가 있다.** `scripts/verify_spec.py` 가 위 흐름 전체 + Offline_Task +
> 오류 처리까지 26개 항목을 자동으로 돈다. 서버를 띄운 뒤 `uv run python scripts/verify_spec.py`.
> 대상 포트는 각 파일 상단의 `BASE` 상수다(`verify_memory.py` 는 `DB` 경로도 있다).

### 1-6. LLM 공급자 — 기본값이 두 개다 (주의)

**`.env.example` 의 기본값과 코드의 기본값이 다르다.**

| | 값 | 언제 적용되나 |
|---|---|---|
| `.env.example:17` | `LLM_PROVIDER=local` | `.env` 를 복사해 쓸 때 (= 위 §1-1 절차) |
| `app/settings.py:34` | `mock` | `.env` 에 그 줄이 없을 때 (테스트·CI) |

그래서 §1-1 대로 하면 **처음부터 실제 로컬 LLM 에 붙는다.** 그 서버가 안 뜨면 조용히
`mock` 으로 떨어져 정규식 대사가 나온다 — "왜 답이 딱딱하지?" 의 첫 확인은 `/health` 의
`llm_provider` 다.

외부 호출 없이 개발하려면 `.env` 에서 `LLM_PROVIDER=mock` 으로 바꾼다.

```dotenv
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://mtvs2026.work/v1
LOCAL_LLM_MODEL=balanced-q4-k-m-mtp
```

> `.env.example:45` 의 `COMPANION_PROMPT_VERSION=companion-v1` 도 코드 기본값
> (`companion-v2`)과 어긋나 있다. 응답 메타데이터의 표시값일 뿐 동작에는 영향이 없지만,
> 둘을 맞춰 두는 편이 낫다.

장기기억 의미 검색까지 켜려면 (임베딩은 **다른 서버**다):

```dotenv
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_MODEL=bge-m3-embed
LOCAL_EMBEDDING_BASE_URL=http://comfy1_0.mtvs2026.work/v1
LOCAL_EMBEDDING_USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36
LOCAL_EMBEDDING_DIMENSIONS=
EMBEDDING_TIMEOUT_SECONDS=10
```

`LOCAL_EMBEDDING_USER_AGENT` 는 장식이 아니다 — 그 호스트 앞의 Cloudflare 가 브라우저가
아닌 요청을 403 으로 막는다(§9-5). `LOCAL_EMBEDDING_DIMENSIONS` 를 비워 두는 것도 의도다.
bge-m3 는 차원 축소를 지원하지 않아 값을 보내면 거부된다.

### 1-7. 검증 명령 세 줄

```powershell
uv run pytest          # 399개
uv run ruff check .
uv run mypy            # pyproject 의 files= 가 대상을 정한다. 경로를 붙이지 말 것
```

셋 다 통과해야 한다. GitHub Actions(`.github/workflows/quality.yml`)가 PR 마다 같은 셋을 돈다.

---

## 2. 이 서버가 무엇인지 — 한 장 요약

한국어 생존 크래프팅 게임의 동료 **마코(마코, Mako)** 를 서빙한다.

**하나의 파이프라인, 두 개의 창구.** 마코는 게임 안(플레이어 옆에 서서)과 모바일 채팅
두 곳에서 답하지만, 라우팅·사실 검증·기억은 **완전히 같은 코드**를 쓴다. 창구는 **말투만**
가른다. 이걸 나누면 같은 문장이 창구에 따라 다른 명령이 되기 시작한다.

**코드가 사실을 정하고, LLM 은 표현만 한다.** 레시피·적 약점·세계관은 검증된 저장소가
문자열을 만들고, LLM 은 그것을 자연스러운 한국어로 다시 쓸 뿐이다. `sanitize` 가
**확정 사실에 없는 숫자를 뱉으면 대사를 통째로 거부**하고 폴백 문장으로 떨어뜨린다.
그래서 마코는 제작 수량을 지어내지 못한다.

**한 턴의 비용은 LLM 호출 2~3회다.** 1단계 분류(무슨 종류의 말인가) → 필요하면 2단계
분류(어떤 명령인가) → 대사 생성. 기억 추출·요약·통합은 **백그라운드 루프**가 하므로
응답 지연에 들어가지 않는다.

```
게임/모바일 ──HTTP or WS──> app/routes ──> app/service.py ──> app/brain/
                                          (전송·신원·스코프)   (판단·대사·기억)
                                                │
                                          app/db/ (SQLite)
```

---

## 3. 코드 지도

### 두 개의 반쪽

| | `app/brain/` | `app/` 나머지 |
|---|---|---|
| 하는 일 | 의도 분류, 대사, 명령 후보, 기억 | HTTP/WS, 인증, 스코프, 오류 봉투, 로깅 |
| 아는 것 | `app.models`, `app.settings`, `app.gamedata` | 전부 |
| **모르는 것** | **FastAPI, Starlette, SQLAlchemy, 라우트, 요청 컨텍스트** | — |

> **`app/brain/` 의 import 규칙은 자동으로 강제되지 않는다.** 패키지를 합치면서 강제 테스트를
> 없앴다. 손으로 지킨다. 확인:
> ```powershell
> Get-ChildItem app/brain -Recurse -Include *.py |
>   Select-String -Pattern "sqlalchemy|fastapi|starlette|app\.db"
> # 아무것도 출력되지 않아야 한다
> ```
> `-Include *.py` 를 빼면 `app/brain/CLAUDE.md` 가 걸린다 — 그 문서가 본문에서 금지 목록을
> 설명하기 때문이지, 규칙 위반이 아니다.

### 파일별 한 줄

**전송 가장자리**

| 파일 | 역할 |
|---|---|
| `app/main.py` | `create_app()` — 전부 조립. **DB 를 먼저 만들고 서비스에 넘긴다** |
| `app/models.py` | 채팅 계약(`ChatRequest`/`ChatResponse`/`CommandType`). **유일한 권위** |
| `app/service.py` | `ChatRequest`↔`CompanionTurn` 번역, 스코프 키 2개 생성, 명령 ID·만료 스탬프 |
| `app/settings.py` | 모든 환경변수. pydantic-settings |
| `app/dependencies.py` | 토큰 검증, DB 세션, `CompanionService` 주입 |
| `app/credentials.py` | HMAC 토큰·페어링 코드 해시 |
| `app/pairing_service.py` | 등록·페어링·해지 로직, 디바이스 상한 |
| `app/offline_task_service.py` | 작업 생성·조회·상태 전이, 역할 검사 |
| `app/embedding.py` | OpenAI/Local/Mock 임베딩 공급자 **조립** (brain 밖) |
| `app/episodic_memory_store.py` | `LongTermStore` 의 SQLite 구현 |
| `app/errors.py` + `errors_http.py` | 오류 → 균일 봉투. **HTTP 와 WS 가 같은 표를 쓴다** |
| `app/routes/` | `chat` / `ws_chat` / `system` / `devices` / `offline_tasks` / `situations` / `admin` 7개 라우터 |
| `app/db/` | SQLAlchemy 모델 + repository 4종(device / episodic_memory / offline_task / save_slot) |
| `app/identity.py` | `AuthenticatedDevice`, `DeviceRole`(`GameClient`/`WebClient`) |
| `app/pairing_models.py` / `offline_task_models.py` | 디바이스·작업 계약 (채팅 계약과 별개) |
| `app/middleware.py` / `logging.py` | 요청 컨텍스트, 구조화 로그 |
| `app/gamedata/dataset.py` | **순수 게임 데이터.** 마이그레이션 시드와 brain 이 같이 읽는다 |

**두뇌**

| 파일 | 역할 |
|---|---|
| `app/brain/companion.py` | `CompanionBrain` — `respond`(그래프 실행), `react`(상황 이벤트, 라우팅 없음), 기억 회수, 증류 큐와 백그라운드 루프 |
| `app/brain/graph.py` | LangGraph `StateGraph` — 라우팅 노드와 조건부 엣지 |
| `app/brain/situation.py` | 상황 이벤트(`POST /situations`) 전용 프롬프트 조립 — `gametime.py` 급의 작은 잎 |
| `app/brain/llm.py` | `LLMProvider` 인터페이스 + Mock/OpenAI/Local. **`Settings` 를 읽는 유일한 두뇌 모듈** |
| `app/brain/dialogue.py` | 장면 지시, `sanitize`(숫자 가드), `SURFACE_PROFILES`(창구별 말투) |
| `app/brain/memory.py` | 장기기억 모델·순위·병합. `LongTermStore` 프로토콜 |
| `app/brain/transcript.py` | 대화 원문 JSONL (증류의 원본) |
| `app/brain/store.py` | 대화 기억(되묻기 슬롯 + 최근 몇 턴) |
| `app/brain/recipes.py` / `enemies.py` / `lore.py` | 검증된 사실 저장소 |
| `app/brain/resources.py` | 채집 가능 자원 허용목록 + 수량 상한 |
| `app/brain/embedding.py` | **인터페이스만.** 구현은 `app/embedding.py` |

### 기억 세 층 (헷갈리기 쉬움)

| | L0 전사 | L1 대화 기억 | L2 장기기억 |
|---|---|---|---|
| 어디 | `data/transcripts/*.jsonl` | 프로세스 메모리 | SQLite `episodic_memories` |
| 키 | `conversation_key` | `conversation_key` | `player_key` |
| 수명 | `TRANSCRIPT_RETENTION_DAYS`(30) | 유휴 30분 | 영구 (32개 상한만) |
| 언제 씀 | 매 턴, 응답 후 | 매 턴, 동기 | **백그라운드 루프만** |
| 프롬프트 블록 | 없음 | `[최근 대화]` | `[기억]` |

**L2 는 L0 에서 증류된다.** 턴 카운터가 아니라 커서라서 재시도·재개·재증류가 가능하다.

---

## 4. 바꾸려면 어디를 여나

| 하고 싶은 것 | 열 파일 |
|---|---|
| 요청/응답 필드 추가 | `app/models.py` → `app/service.py` 번역 → `app/brain/contract.py` |
| 새 명령 종류 추가 | `app/models.py:CommandType` → `app/brain/intent.py:CommandLabel` → `graph.py:_COMMANDS` → `dialogue.py` 의 `DialogueScene`+`SCENE_GUIDE` **넷 다** |
| 마코 말투 바꾸기 | `app/brain/dialogue.py:SURFACE_PROFILES` |
| 장면별 대사 지시 | `app/brain/dialogue.py:SCENE_GUIDE` |
| 프롬프트 자체 | `app/brain/llm.py` 의 `_*_PROMPT` 상수들 |
| 아이템/레시피/적 추가 | `app/gamedata/dataset.py` + 새 마이그레이션 |
| 새 테이블 | `app/db/models.py` + `migrations/versions/0006_*.py` |
| 새 엔드포인트 | `app/routes/` 에 라우터 → `app/main.py` 에 등록 |
| 새 오류 코드 | `app/errors.py` 에 클래스+코드 → `errors_http.py` 에 **한 줄** (HTTP/WS 공용) |
| 환경변수 추가 | `app/settings.py` + `.env.example` **둘 다** |
| 기억 순위 규칙 | `app/brain/memory.py:rank` / `_score` / `strength` |

### 마이그레이션 추가하는 법

```powershell
uv run alembic revision -m "설명"     # migrations/versions/ 에 파일 생성
# down_revision 을 직전 리비전으로 맞춘다 (현재 최신은 "0005")
uv run alembic upgrade head
uv run alembic downgrade -1           # 되돌아가는지도 확인할 것
```

마이그레이션 테스트는 **subprocess 로 돈다**(`tests/test_*_migration.py`). Alembic 의
`env.py` 가 같은 프로세스의 logger 설정을 오염시킨 전례가 있어서다.

---

## 5. 완료된 것 — 명세 대비 대조표

2026-08-04 실서버 검증 기준. 격리 DB 로 실제 HTTP 를 쏴서 26/26 통과했다
(`scripts/verify_spec.py`).

### 5-1. Request JSON 포맷

명세의 **모든 필드를 받는다.**

| 명세 필드 | 상태 | 비고 |
|---|---|---|
| `schema_version` | ✅ | `1` 만 유효 |
| `request_id` | ✅ | 응답에 그대로 반향. **멱등성은 없다**(§7-4) |
| `profile_id` | ✅ | 선택. 보내면 인증 신원과 대조, 다르면 403 |
| `save_slot_id` | ✅ | 필수. 장기기억 스코프의 한 축 |
| `companion_id` | ✅ | `"mako"` 만 유효 |
| `device_id` | ✅ | 선택. 대조용 |
| `session_id` | ✅ | 필수. 대화 스코프 |
| `interaction_mode` | ⚠️ | **이름이 `surface`, 값이 다르다** → §6-1 |
| `message_id` | ✅ | 응답에 반향 |
| `user_message` | ✅ | 1~2000자 |
| `time_context` | ✅ | source/day/hour/period 4필드 전부 |
| `recent_event_ids` | ⚠️ | **받아서 검증만, 해석 안 함** → §6-4 |
| `game_context` | ✅ | `location_id` 사용. 비밀스러운 키는 거부 |
| `allowed_commands` | ✅ | **명령 게이트. 여기 없으면 절대 방출 안 됨** |

모르는 필드는 `400 InvalidRequest` 로 거부한다(`extra="forbid"`).

### 5-2. 디바이스별 chat 컨텍스트 구분 ✅

같은 `"따라와"` 에 창구별로 다르게 답한다 (실측):

| 창구 | 응답 |
|---|---|
| `game` | 준비됐어. 네 발걸음에 맞춰서 뒤따라갈게. |
| `mobile` | 응, 바로 뒤에서 따라갈게. |

거절 문구도 창구별로 다르다 — 게임에서는 "가능한 일은 따라오기, 대기, 작업 중지뿐이다"
지만 모바일에서는 그게 **거짓 사실**이라 다른 문장을 쓴다.

### 5-3. 디바이스 인증 ✅

- 게임 등록 → 페어링 코드(8자리, 300초) → 모바일 페어링
- 프로필당 상한 `MAX_DEVICES_PER_PROFILE`(기본 20). 초과 시 `403 DeviceLimitExceeded`
- 토큰은 HMAC 으로 저장 — DB 가 유출돼도 원본 토큰은 복원되지 않는다
- 무인증 401 / 타인 프로필 주장 403 / 해지된 토큰 401 — 전부 실측 확인

### 5-4. 게임 데이터 ✅

DB 행 수가 명세와 **정확히 일치**한다.

| 테이블 | 행 수 | 명세 |
|---|---|---|
| `items` | 27 | 27 ✅ |
| `recipes` | 13 | 13 ✅ |
| `smelting_recipes` | 4 | 4 ✅ |
| `enemies` | 3 | 3 ✅ |
| `locations` | **0** | 좌표 미전달 → §6-2 |

### 5-5. Offline_Task ✅

`task_type` 3종(`Gathering`/`Crafting`/`Scouting`)과 `status` 4종
(`Pending`/`InProgress`/`Completed`/`Claimed`)이 명세와 같다.

```
모바일(WebClient)이 생성 → Pending
게임(GameClient)이  start → InProgress → complete → Completed → claim → Claimed
```

- 순서 위반 → `409` (DB 조건부 UPDATE 로 원자적 강제, 동시 요청에도 안전)
- 모바일이 전이 시도 → `403`
- 같은 `request_id` 재전송 → 같은 작업 반환 (**작업 생성은 멱등하다**. 채팅과 다르다)

### 5-6. RAG · 장기기억 ✅

3턴 대화 후 백그라운드 루프가 실제로 기억을 뽑은 결과 (실측):

```
profile  중요도 6  bge-m3-embed  어둠 속에서는 손을 떠는 습관이 있다.
profile  중요도 6  bge-m3-embed  해가 지기 전에 야영지로 돌아오는 것을 선호한다.
episode  중요도 5  bge-m3-embed  어둠을 무서워하는 플레이어를 위해 낮 위주로 움직이기로 약속했다.
```

새 세션에서 **단어가 하나도 겹치지 않는** `"나 지금 좀 무서운데 같이 있어 줄래?"` 를 보내자
기억 3개가 전부 회수되고 마코가 `"당연하지, 내가 바로 옆에 있을게."` 라고 이어받았다.
키워드 검색이었다면 걸리지 않았을 조합이다 — 이게 임베딩이 사는 값이다.

**임베딩이 없거나 실패해도 회수는 절대 실패하지 않는다.** 키워드+시간 감쇠로 자동 폴백한다.

### 5-7. 페르소나 일관성 ✅

`app/brain/companions.py` 의 `COMPANION_PROFILES` + `llm.py` 의 프롬프트 상수가 페르소나를
고정한다. 프롬프트 버전은 `COMPANION_PROMPT_VERSION` 으로 응답 메타데이터에 실린다.

---

## 6. 명세와 다른 4가지 — **먼저 읽을 것**

여기가 이 문서에서 가장 중요한 절이다. 넘겨받은 뒤 "왜 안 되지?" 할 만한 것들.

### 6-1. `interaction_mode` 가 아니라 `surface` 다 (결정 필요)

| | 명세 | 서버 |
|---|---|---|
| 필드명 | `interaction_mode` | `surface` |
| 값 | `"InGame"` / `"Offline"` (또는 `Cutscene`/`Menu`) | `"game"` / `"mobile"` |
| 생략 시 | — | `"game"` |

동작은 같다 — 값 이름만 다르다. **클라이언트가 어느 쪽을 보낼지 정해 주셔야 한다.**

서버를 명세에 맞추려면 (30분 작업):

1. `app/models.py:Surface` 의 값을 `"InGame"`/`"Offline"` 으로 바꾸거나 별칭을 추가
2. `ChatRequest.surface` 를 `interaction_mode` 로 이름 변경 (또는 `alias` 추가)
3. `app/brain/dialogue.py:SURFACE_PROFILES` 의 키는 그대로 — enum 값만 따라간다
4. `tests/` 에서 `surface` 를 쓰는 곳 갱신

명세의 `Cutscene`/`Menu` 까지 받으려면 `SURFACE_PROFILES` 에 항목을 추가해야 한다.
**항목을 빠뜨리면 mypy 가 잡아 준다** — 일부러 파생 없이 전부 나열해 뒀다.

### 6-2. `Location` 은 0행이다

좌표 데이터를 받지 못해 테이블만 있고 비어 있다. `LocationModel` 은 정의만 있고 앱
코드 어디서도 조회하지 않는다.

**세계관 대사는 지금 `app/brain/lore.py` 에 하드코딩된 1건**(`region_abandoned_mining_village`)
으로 대신하고 있다. `game_context.location_id` 가 없으면
`COMPANION_DEFAULT_LOCATION_ID` 설정값으로 대체한다 — 이건 임시 발판이고 제거 절차가
`docs/temporary-scaffolds.md` §1 에 있다.

좌표 행이 오면 `"철광석 어디서 나와?"` 같은 위치 기반 RAG 를 붙일 수 있다.

> ERD 의 `Location` 정의에 문제가 하나 있다: nullable FK 를 복합 PK 에 넣었다. 좌표를
> 넣기 전에 정리가 필요하다. `docs/game-data.md` 참고.

### 6-3. 아이템·적의 `description` 27종이 대사에 안 쓰인다

DB `items.description` 에는 다 들어가 있지만 **마코 프롬프트에는 아이템 이름만 간다.**
`recipes.py` 가 쓰는 건 `name_ko` 와 `aliases` 뿐이다.

그래서 `"철광석이 뭐야?"` 같은 순수 설명 질문은 지금 레시피 경로로 가거나 대답하지
못한다. 붙이려면:

1. `app/brain/` 에 `ItemRepository` 를 만들어 `fact_for(query)` 로 설명을 확정 사실화
2. `graph.py` 에 `item` 노드 + `TopIntent` 에 항목 추가
3. `dialogue.py:SCENE_GUIDE` 에 장면 지시 추가

기존 `recipes.py`/`enemies.py` 가 정확히 같은 모양이라 그대로 베끼면 된다. 반나절 작업.

### 6-4. CRUD — 게임·모바일 계약과 관리자 CRUD 는 별개 표면이다

게임/모바일이 쓰는 계약(`routes/devices.py`, `routes/offline_tasks.py`)은 여전히 목적별로만
필요한 만큼만 있다:

| 대상 | 있는 것 | 없는 것 |
|---|---|---|
| Offline_Task | 생성, 목록, 상태 전이 3종 | 개별 조회, 수정, 삭제 |
| 디바이스 | 등록, 페어링, 목록, 해지 | 수정 |

이와 별개로 `/api/v1/admin`(`app/routes/admin.py`)에 11개 테이블(profiles/devices/
pairing_codes/save_slots/items/recipes/smelting_recipes/enemies/locations/
episodic_memories/offline_tasks) 전체를 다루는 관리자 전용 CRUD 가 있다 — 고정
`ADMIN_API_TOKEN` 뒤에 있고, 자격 증명 해시(`token_hash`/`code_hash` 등)는 절대 노출·수정
불가능하며, 자식 행이 남아 있으면 삭제를 409 로 거부한다(연쇄 삭제 없음).

게임 데이터(items/recipes/smelting_recipes/enemies)는 이제 앱 시작 시점에 DB 를 한 번 읽어
마코 대사에 반영한다(`app/db/game_data_loader.py`, `app/main.py`) — **다음 재시작부터**
반영되고 핫 리로드는 아니다. 관리자 CRUD로 게임 데이터를 고치면 그게 유일한 편집 경로다.
마이그레이션 0002 의 `dataset.py` 시드는 여전히 최초 시드 소스로 남아 있다.

또 `recent_event_ids` 는 **받아서 형식 검증만 하고 해석하지 않는다.** 이벤트 ID 카탈로그
(ID → 사람이 읽을 설명)를 주시면 대화에 반영할 수 있다. 절차는
`docs/temporary-scaffolds.md` §3.

---

## 7. 남은 일 — 우선순위

### 7-1. `interaction_mode` 이름 정하기 (반나절, **클라이언트 연동 전 필수**)

§6-1. 클라이언트와 서버가 서로 다른 필드명을 쓰면 첫 요청부터 400 이다.

### 7-2. `Location` 좌표 받고 위치 RAG (2~3일, 데이터 대기)

§6-2. 좌표 행이 선행 조건이다.

### 7-3. 아이템 설명 질의응답 (반나절)

§6-3. 데이터는 이미 있다. 배선만 하면 된다.

### 7-4. 요청 멱등성과 감사 기록 (2~3일, **운영 전 권장**)

**지금 같은 `request_id` 로 채팅을 두 번 보내면 LLM 을 두 번 호출하고 서로 다른 답을
받는다.** 네트워크 재전송이 곧 중복 과금이자 중복 대사다.

의도적으로 뺀 것이다 — 초기 개발 속도를 위해 `ChatRequestModel`/`MessageModel` 을
되살리지 않았다. 배경과 복구 절차는 `docs/temporary-scaffolds.md` §2.

Offline_Task 생성은 이미 멱등하니(`uq_offline_tasks_creation_request`) 그 패턴을 채팅에
적용하면 된다.

### 7-5. 페어링 rate limiting (1일, 운영 전 권장)

8자리 페어링 코드에 시도 횟수 제한이 없다. TTL 300초 안에 무제한 시도가 가능하다.
내부 개발 환경에서는 문제가 아니지만 공개 배포 전에는 필요하다.

### 7-6. `Claimed` 이후 보상 원장 (미정)

지금 `Claimed` 는 **상태 표시일 뿐**이고 실제 보상 지급 원장이나 이벤트 보고가
연결돼 있지 않다. 인게임 인벤토리 연동 설계가 선행돼야 한다.

### 7-7. 교전 중 지시대명사 (미정)

`"쟤 약점 뭐야?"` 는 지금 안 된다. 적 저장소가 **현재 발화**만 보기 때문이다.
게임이 교전 중인 적 ID 를 `game_context` 로 보내 주면 붙일 수 있다.

### 7-8. Chat_Buffer 를 SQL 로 (선택)

ERD 의 `Chat_Buffer` 는 지금 JSONL 파일(`data/transcripts/`)이다. 순번·커서·보존기간·
찢긴 줄 복구까지 잘 돌고 있고, **검색 대상이 아니라 증류의 입력**일 뿐이라 옮길 실익이
크지 않다. 옮긴다면 별도 판단이 필요하다.

---

## 8. 설계 결정과 그 이유 — 되돌리기 전에 읽을 것

여기 적힌 것들은 "그냥 그렇게 됐다" 가 아니라 **이유가 있어서 그렇게 했다.** 바꾸기 전에
왜 그런지 알고 바꾸는 게 좋다.

### 8-1. `surface` 는 말투만 가른다 — 절대 권한을 가르지 않는다

**"모바일이니까 명령을 못 낸다" 가 아니라 "모바일 클라이언트가 빈 `allowed_commands` 를
보낸다" 이다.** 이 둘은 결과가 같지만 미래가 다르다.

모바일에서 작업 지시가 생기는 날, 전자로 짰다면 `if surface == mobile` 조건문을 전부
되돌려야 한다. 후자로 짜면 클라이언트가 목록을 채우기만 하면 된다.

**그래서 `SURFACE_PROFILES` 밖에 `surface == mobile` 분기를 만들지 말 것.**

### 8-2. 명령 허용목록을 두 곳에서 검사한다

`graph.py` 가 결정하고 `service.py:_assert_within_allowlist` 가 다시 확인한다. 중복처럼
보이지만 아니다 — **두뇌는 같은 프로세스·같은 저장소에 있으니 신뢰 검증이 아니라
회귀 방어다.** 그래프에 버그가 생겼을 때 게임으로 새어 나가지 않게 막는다.

지우지 말 것. 서버가 아니라 게임을 보호한다.

### 8-3. 기억은 확정 사실이 될 수 없다

회수된 기억은 `[기억]` 블록으로만 들어가고 `facts` 에는 **절대** 들어가지 않는다.
그래서 `build_memory` 는 **숫자가 든 문장을 아예 저장하지 않는다.**

이유: `sanitize` 가 "확정 사실에 없는 숫자" 를 거부하므로, 숫자 든 기억이 회수되면
그 턴의 대사가 통째로 폴백으로 떨어진다. 기억 하나가 매 턴 대사를 망치는 것을 막으려면
저장 시점에 거르는 게 맞다.

### 8-4. 중요도 1~10 을 점수에 그대로 넣지 않는다

ERD 는 중요도를 1~10 으로 정의한다. 그런데 점수 계산에 10 을 그대로 넣으면
**키워드 일치 2점이 소음이 된다** — 중요한 기억이 아무 상관 없어도 항상 이긴다.

그래서 `_IMPORTANCE_WEIGHT = 0.3` 을 곱해 힘의 천장을 5.5 로 되돌렸다. 기존 순위 균형이
그대로 유지된다. **이 상수를 지우면 검색이 망가진다.**

### 8-5. 임베딩 실패는 예외가 아니라 `None` 이다

공급자 없음 / 예외 / 타임아웃 / 모델 불일치 / 차원 불일치 — 전부 `None` 을 돌려주고
키워드 검색으로 폴백한다. **회수는 절대 실패하지 않는다.**

벡터 없는 기억에는 `_SEMANTIC_FLOOR` 를 준다. 0 을 주면 임베딩 도입 이전에 쌓인 기억이
조직적으로 밀려난다 — "모르는 것" 과 "무관한 것" 은 다르다.

### 8-6. 임베딩 서버에 대사 서버의 키를 보내지 않는다

`LOCAL_EMBEDDING_BASE_URL` 이 `LOCAL_LLM_BASE_URL` 과 **같을 때만** 대사용 키를 물려받는다.
다르면 `LOCAL_EMBEDDING_API_KEY` 를 따로 써야 한다.

이유: 다른 호스트에 남의 서버 키를 보내는 건 자격증명 유출이다.

### 8-7. 대화 원문은 구조화 로그에 절대 안 들어간다

`_log_step` 과 요청 컨텍스트 미들웨어는 **단계 이름과 소요 시간만** 남긴다. 플레이어가
한 말은 로그 수집기에 도달하지 않는다. 이건 절대 규칙이다.

**"서버가 대화를 저장하지 않는다" 와는 다른 얘기다** — `data/transcripts/` 에는 원문이
일부러 남는다(장기기억이 거기서 증류된다). 두 개의 별개 규칙이고, 로깅 쪽이 더 엄격하다.

### 8-8. 상한은 설정이 아니라 상수다

`MAX_MEMORIES_PER_PLAYER = 32`, `MAX_MEMORY_TEXT = 120`, `HALF_LIFE_DAYS = 30.0` 등은
`memory.py` 의 상수다. 환경변수로 올릴 수 있는 한계는 한계가 아니다.

### 8-9. 스코프 키는 JSON 직렬화한다

`_player_key` 는 `json([profile_id, save_slot_id])` 의 HMAC 이다. 문자열 연결이 아니다 —
값에 `:` 이 들어갈 수 있어서 `"a:b"+"c"` 와 `"a"+"b:c"` 가 충돌하면 안 되기 때문이다.

### 8-10. 대사 프롬프트에만 컨텍스트가 간다

`surface`, `game_time`, 최근 대화, 기억 — 전부 `say()` 에만 들어간다.
**분류기(`classify_top`/`classify_command`)는 절대 못 본다.**

이유: 세 턴 전의 `"따라와"` 가 지금 명령을 발화시키면 안 된다.

---

## 9. 운영·함정

### 9-1. 게임 클라이언트는 **프로필당** 하나뿐 (2026-08-05부터 서버당 하나가 아니다)

`register-game` 은 호출마다 **새 프로필 + 새 GameClient** 를 만든다(`request_id` 가 같으면
멱등 — 기존 디바이스를 그대로 돌려주고 새 프로필을 만들지 않는다). 제한은 "서버당 1개"에서
"프로필당 1개"로 바뀌었으므로, 같은 DB 에 반복 호출해도 `409 DeviceAlreadyRegistered` 는
나지 않는다(자세한 배경은 `docs/temporary-scaffolds.md` "2026-08-05: 다중 플레이어 등록").
그래도 테스트 간 완전히 격리하고 싶으면(장기기억·전사까지 새로) 아래처럼 DB 자체를
새로 만들면 된다.

```powershell
$env:DATABASE_URL        = "sqlite+aiosqlite:///./data/test2/companion.db"
$env:LONG_TERM_MEMORY_DIR = "data/test2/memories"     # 비어 있는 디렉터리를 가리킬 것
$env:TRANSCRIPT_DIR       = "data/test2/transcripts"
New-Item -ItemType Directory -Force data/test2/memories | Out-Null
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8001
```

`LONG_TERM_MEMORY_DIR` 도 같이 옮기는 게 중요하다. **`0005` 마이그레이션이 그 디렉터리의
기존 JSON 기억을 새 DB 로 가져오기 때문**에, 기본값을 그대로 두면 실제 플레이어 기억이
테스트 DB 로 복사된다.

`migrations/env.py` 가 `Settings()` 를 읽으므로 환경변수 override 가 마이그레이션에도 통한다.

### 9-2. `alembic upgrade head` 를 잊으면 DB 가 안 생긴다

첫 쓰기에 자동 생성되지 않는다. 게임 데이터 시드도 이때 들어간다.

### 9-3. `DEVICE_CREDENTIAL_PEPPER` 를 바꾸면 두 가지가 깨진다

1. 기존 디바이스 토큰이 전부 무효 → 전부 다시 페어링해야 한다
2. **`player_key` 가 달라져 기존 장기기억을 못 찾는다** (데이터는 남아 있지만 연결이 끊긴다)

배포 후에는 바꾸지 말 것.

### 9-4. `mock` 은 장기기억을 만들지 않는다

`LLM_PROVIDER=mock` 은 기억 추출 세 메서드가 전부 빈 결과를 돌려준다. 일부러 그렇다 —
무엇이 기억할 만한지는 정규식이 흉내낼 수 있는 판단이 아니고, 가짜 규칙을 만들면 실제
공급자와 다른 것을 저장하게 된다.

**그래서 "기억이 안 쌓이는데요" 의 첫 확인은 `LLM_PROVIDER` 다.**

### 9-5. 임베딩 서버가 403 을 낸다면

`comfy1_0.mtvs2026.work` 앞의 Cloudflare 가 브라우저 아닌 요청을 `403 error code: 1010`
으로 막는다. `LOCAL_EMBEDDING_USER_AGENT` 에 브라우저 UA 를 넣어야 통과한다.

HTTPS 는 인증서 hostname 이 맞지 않아 실패한다 — HTTP 로 붙어야 한다.
**장기적으로는 인증서/Cloudflare 설정 정리가 필요하다.**

### 9-6. `TRANSCRIPT_ENABLED=false` 로 끄면 새 기억이 안 생긴다

장기기억은 전사에서 증류되므로, 읽을 로그가 없으면 추출이 돌지 않는다.
이미 있는 기억의 회수는 계속된다.

### 9-7. `data/` 는 커밋되지 않는다

플레이어와 나눈 말에서 나온 내용이라 `.gitignore` 에 있다. `*.db` 도 마찬가지다.
**서버를 옮길 때 `data/` 를 같이 옮기지 않으면 기억이 사라진다.**

### 9-8. 증류는 즉시 돌지 않는다

트리거는 둘이다 — `LONG_TERM_QUIET_SECONDS`(기본 90초) 동안 조용하거나,
`LONG_TERM_EXTRACT_EVERY_N_TURNS`(기본 3) **왕복**이 밀리거나. 전사에는 한 왕복이
두 항목(플레이어 한 마디 + 마코 한 마디)으로 남으므로 코드의 임계값은 `× 2` 한
**6항목 = 3왕복**이다(`companion.py:410`).

세션 요약은 더 늦다 — `LONG_TERM_SESSION_END_SECONDS`(기본 600초).

테스트할 때 답답하면 `LONG_TERM_QUIET_SECONDS=20 LONG_TERM_TICK_SECONDS=5` 로 낮추면 된다.

### 9-9. `uv run mypy` 에 경로를 붙이지 말 것

`pyproject.toml` 의 `files=` 가 대상을 정한다. 경로를 주면 그 설정을 덮어써서, 패키지가
늘어날 때 조용히 검사에서 빠진다. 실제로 그런 적이 있다.

### 9-10. WebSocket 은 매 프레임 토큰을 보낸다

브라우저 `WebSocket` 은 핸드셰이크에 커스텀 헤더를 못 싣는다. 그래서 HTTP 의
`Authorization` 대신 봉투에 `token` 을 함께 보낸다:

```json
{"type": "chat", "token": "<device_token>", "payload": { ...ChatRequest... }}
```

**어떤 실패도 연결을 끊지 않는다.** 오류는 `{"type":"error","payload":{...}}` 로 온다.

---

## 부록 A. 문서 지도

| 문서 | 언제 읽나 |
|---|---|
| **이 문서** | 처음, 그리고 막힐 때 |
| `README.md` | 실행·API 요약 |
| `CLAUDE.md` | 저장소 전체 규칙과 구조 |
| `app/CLAUDE.md` | 전송 계층을 만질 때 |
| `app/brain/CLAUDE.md` | 두뇌를 만질 때 — **가장 상세하다** |
| `tests/CLAUDE.md` | 테스트를 쓸 때 |
| `docs/api-endpoints.md` | **엔드포인트 전체 명세.** 클라이언트 연동의 기준 |
| `docs/websocket-client-guide.md` | WS 클라이언트를 만들 때 |
| `docs/websocket-manual-test-spec.md` | WS를 손으로 눌러가며 확인할 때 |
| `docs/game-data.md` | 게임 데이터·ERD 대조, 한국어 별칭 검수표 |
| `docs/temporary-scaffolds.md` | **임시로 넣은 것과 일부러 뺀 것의 목록 + 제거 절차** |
| `docs/backlog/future_features.md` | 미구현 후보 |
| `docs/current/`, `docs/archive/` | **레거시.** 과거 계약 — 참고용일 뿐 규범이 아니다 |

> `docs/current/` 의 `POST /v1/companion/message` 는 **옛 계약**이다. 현행은
> `POST /api/v1/chat` 이고 유일한 권위는 `app/models.py` 다.

## 부록 B. 코딩 규칙

- 4칸 들여쓰기, 타입 주석, 100자 줄
- Ruff: `E,F,I,B,UP,ASYNC,C4,PTH,N,T20,RUF` — `T20` 은 `print` 금지다
- MyPy strict. **저장소 전체가 항상 ruff·mypy clean 이어야 한다**
- 주석·docstring 은 **한국어**로, 주변 모듈에 맞춘다
- 커밋은 Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`)
- 계약 버전은 URL 접두사(`/api/v1`)에 있다

## 부록 C. 검증 스크립트

| 스크립트 | 무엇 |
|---|---|
| `scripts/verify_spec.py` | 페어링→채팅→Offline_Task→오류 26개 항목 자동 검증 |
| `scripts/verify_memory.py` | 실제 LLM+임베딩으로 기억 증류·회수 확인 |

둘 다 서버를 먼저 띄운 뒤 돌린다. 대상 포트는 각 파일 상단의 `BASE` 상수이고,
`verify_memory.py` 는 DB 를 직접 열어 기억 행을 확인하므로 `DB` 경로 상수도 있다.
`.env` 의 `DEV_GAME_DEVICE_TOKEN` 을 읽으므로 그 값이 채워져 있어야 한다.

## 부록 D. 인계 시점의 상태

```
브랜치      feat/device-auth-profiles
최신 커밋   6046eae feat: add episodic memory RAG
커밋 수     76
테스트      399 passed
ruff        All checks passed
mypy        Success, no issues found in 54 source files
실서버 검증 26/26 통과 (2026-08-04)
마이그레이션 0001~0005
```

**main 에 병합되지 않았다.** 병합 전에 §6-1(필드명)을 정하는 게 좋다 — 계약 변경이라
나중에 하면 클라이언트도 같이 고쳐야 한다.

---

## 질문이 생기면

이 문서에 없는 것은 대부분 다음 셋 중 하나에 있다:

1. **동작이 궁금하다** → `docs/api-endpoints.md` (요청/응답 예시가 기능별로 다 있다)
2. **왜 이렇게 짰는지 궁금하다** → 해당 디렉터리의 `CLAUDE.md`
3. **왜 이건 안 하는지 궁금하다** → `docs/temporary-scaffolds.md`

그래도 없으면 코드의 주석을 볼 것. 이 저장소는 **왜 그런지를 주석에 적는** 규칙으로
써 왔다.
