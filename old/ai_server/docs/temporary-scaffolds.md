# 임시 발판(temporary scaffolds)

클라이언트가 아직 준비되지 않아 **서버가 임시로 대신 채우는 값**들의 목록이다. 하나같이
"상대편이 준비되면 지운다"는 조건을 달고 들어온 코드이므로, 여기에 **지우는 절차를 미리
적어 둔다**. 조건이 충족됐는데 아무도 기억하지 못해 영구히 남는 것을 막는 것이 이 문서의
유일한 목적이다.

각 항목은 다음을 갖춘다.

- **왜 있나** — 없으면 무엇이 안 되는지
- **언제 지우나** — 제거를 촉발하는 사건
- **지우는 법** — 파일별 정확한 변경
- **지웠는지 확인** — 잔재가 없음을 기계적으로 확인하는 방법

---

## 1. `COMPANION_DEFAULT_LOCATION_ID` — 세계관 질문용 대체 위치

**추가일** 2026-07-29 · **상태** 활성

### 왜 있나

세계관(lore) 응답만 `game_context.location_id` 에 의존한다. 제작법·명령은 발화 텍스트만으로
처리되므로 영향이 없다. 게임 클라이언트가 아직 `location_id` 를 실어 보내지 않아
`LoreRepository.fact_for(None)` 이 항상 `None` 을 돌려주고, 세계관 질문이 전부
`unsupported` 장면("지금 위치에 대해 확인된 이야기는 아직 없어")으로 떨어져 **시험 자체가
불가능**했다.

그래서 `CompanionService` 가 `game_context` 에 위치가 **없을 때만** 설정값으로 대신 채운다.

```
COMPANION_DEFAULT_LOCATION_ID=region_abandoned_mining_village
```

비워 두면(기본값) 동작은 이 설정이 없던 때와 완전히 같다.

### 설계상 지켜지는 것

- **게임이 보낸 위치가 항상 이긴다.** 목록에 없는 위치를 보내도 대체하지 않는다 — 거기는
  진짜로 확인된 이야기가 없는 곳이고, 다른 곳 이야기를 들려주면 "검증된 사실만 말한다"는
  전제가 깨진다. 대체는 위치가 아예 없을 때만 일어난다.
- **`app/brain/` 은 건드리지 않았다.** 마코 입장에서는 위치가 실려 온 평범한 턴이라,
  제거해도 두뇌 쪽 코드는 원래 그대로다.
- **알려진 약점:** `CompanionService` 는 `LoreRepository._LORE` 를 모르므로 오타 난 위치 ID를
  검증할 수 없다. 증상은 "설정을 안 켠 것"과 구분되지 않는다. 임시 발판이라 감수한 선택이며,
  검증을 넣으려면 서비스가 세계관 테이블을 알아야 해서 이 방식의 이점이 사라진다.

### 언제 지우나

**게임 클라이언트가 `game_context.location_id` 를 실어 보내기 시작하면.** 이때 서버가 값을
채울 이유가 사라진다. 그 전에는 지우면 안 된다 — 세계관 시험이 다시 막힌다.

확인 방법: 클라이언트에서 세계관 질문을 한 번 보내고 `COMPANION_DEFAULT_LOCATION_ID` 를
**비운 채로** 위치 기반 응답이 나오면 조건 충족이다.

### 지우는 법

네 파일이다. 원래 형태로 되돌리는 것이지 새로 쓰는 것이 아니다.

1. **[app/settings.py](../app/settings.py)** — `companion_default_location_id` 필드와 그 위
   주석 4줄을 삭제.

2. **[app/service.py](../app/service.py)** — 네 군데.
   - `__init__` 의 `default_location_id: str | None = None` 인자
   - `self._default_location_id = ...` 대입과 그 위 주석
   - `from_settings` 의 `default_location_id=settings.companion_default_location_id,` 줄
   - `_location_id` 를 정적 메서드로 복원:

   ```python
   @staticmethod
   def _location_id(game_context: dict[str, JsonValue]) -> str | None:
       """game_context 에서 세계관 조회 키로 쓸 location_id 를 안전하게 추출한다."""

       value: Literal[False] | JsonValue = game_context.get("location_id", False)
       return value if isinstance(value, str) else None
   ```

3. **[.env.example](../.env.example)** — `COMPANION_DEFAULT_LOCATION_ID` 줄과 그 위 주석 2줄
   삭제. 각자의 로컬 `.env` 에서도 지운다.

4. **[tests/test_companion_ai_service.py](../tests/test_companion_ai_service.py)** —
   `test_lore_falls_back_to_default_location_when_game_sends_none` 와
   `test_default_location_does_not_override_location_from_game` 두 테스트 삭제,
   `make_service` 의 `default_location_id` 매개변수 제거.
   `test_lore_uses_location_from_game_context` 는 **남긴다** — 임시 발판과 무관하게 정상
   경로를 지키는 테스트다.

5. **이 문서** — 이 절을 삭제하고, 남는 항목이 없으면 문서째 지운 뒤
   [docs/README.md](README.md) 의 참조 줄과 `CLAUDE.md` 의 참조 줄도 함께 지운다.

### 지웠는지 확인

```powershell
# 아무것도 잡히지 않아야 한다
Select-String -Path app,tests,.env.example,docs -Pattern "default_location_id|DEFAULT_LOCATION_ID" -Recurse

uv run ruff check .
uv run mypy
uv run pytest
```

---

## 2. `player_name` — 인증 대신 쓰는 자기 신고 신원

**추가일** 2026-07-29 · **상태** 복구됨(2026-07-31, `feat/device-auth-profiles` 브랜치)

### 2026-07-31: 복구됨

아래 "지우는 법"이 예고한 절차를 그대로 실행했다. `ChatRequest.player_name` 은 사라졌고,
`app/dependencies.py:authenticate_device_token` 이 Bearer 토큰을 검증해 만든
`AuthenticatedDevice` 가 신원이다. `_conversation_key`/`_player_key` 는 HMAC 이 됐고
스코프도 바뀌었다 — `player_name`+`session_id` 대신
`(profile_id, save_slot_id, companion_id, session_id)` / `(profile_id, save_slot_id)`.
`git show cd0be55:app/infrastructure/database/` 의 SQLAlchemy 모델·리포지토리와
`migrations/`, `alembic.ini` 를 되살려 `app/db/` 로 옮겼다. 상세 설계는
`feat/device-auth-profiles` 브랜치의 계획 문서를 참조.

**되살리지 않은 것, 의도적으로.** `cd0be55` 에는 `CompanionModel`/`ConversationModel`/
`MessageModel`/`ChatRequestModel`(요청 멱등성 + 감사 기록)도 있었다. 이번 복구는 인증과
디바이스 캡(프로필당 `MAX_DEVICES_PER_PROFILE`, 기본 20 — 초과 시 거부, 자동 해지 없음)만
다뤘고, 멱등성·감사 기록은 범위 밖이다. **같은 `request_id` 를 두 번 보내면 지금도 LLM 을
두 번 호출하고 서로 다른 응답을 받는다.** 이것도 필요해지면 위 네 모델과
`chat_repository.py` 를 별도로 되살려야 한다.

**요청량 제한도 여전히 없다.** 인증이 돌아왔어도 이건 복구되지 않았다. 특히
`POST /api/v1/devices/pair` 는 **인증 없이 열려 있고** 코드가 8자리 숫자뿐이라, 시도
횟수에 제한이 없다는 점이 이 엔드포인트에서 가장 아프다. 지금 이것을 붙들고 있는 것은
코드 수명(`PAIRING_CODE_TTL_SECONDS`, 기본 300초)뿐이다. 사설망 전제가 깨지는 날
IP/프로필 단위 제한을 페어링 경로부터 넣어야 한다.

**동시성은 이번에 함께 고쳤다.** 두 불변식 모두 "읽어서 확인하고 나중에 쓴다" 로
구현하면 조용히 깨진다 — 디바이스 캡은 실제로 재현됐다(캡 3에 동시 요청 3건이 모두 통과해
4대). 지금은 캡 판정 전에 프로필 행을 잠그고(`lock_profile`), 코드 사용 처리는
`WHERE used_at IS NULL` 조건부 UPDATE 로 판정과 기록을 한 문장에 담는다. 회귀 테스트는
`tests/test_device_concurrency.py` 에 있고, 코드 1회 사용 쪽은 SQLite 의 쓰기 직렬화가
창을 우연히 닫아 버리므로 barrier 로 창을 강제로 연다 — 그 테스트가 없으면 다른 DB 로
옮겼을 때 깨지는 것을 아무도 모른다.

**기존 장기기억·전사 파일은 조회 불가능해졌다.** 키 스코프가 `player_name` 기반에서
인증된 `profile_id`+`save_slot_id` 기반으로 바뀌어 역산이 불가능하다 — 옮길 수 있는
매핑 자체가 없다. 이 데이터는 `.gitignore` 된 개발용 데이터고 사설망을 벗어난 적이
없다는 이 절의 위험 수용 전제(아래) 그대로이므로, 마이그레이션 스크립트를 쓰지 않고
`data/memories`·`data/transcripts` 를 비우는 쪽을 택했다. 복구 불가능한 개발용 데이터를
위해 마이그레이션을 쓰는 비용이 얻는 것보다 크다는 판단이다.

### 2026-08-05: 다중 플레이어 등록

아래 "언제 지우나" 의 트리거 중 하나(**"한 대의 서버를 여러 플레이어가 동시에 쓰기
시작할 때"**)를 의도적으로 넘었다. GameClient 제한을 **서버당 1개 → 프로필당 1개**로
바꿨다. 이제 `POST /api/v1/devices/register-game` 은 호출마다 **새 프로필 + 새 GameClient**
를 만든다(`request_id` 멱등성은 유지 — 같은 request_id 는 기존 디바이스를 그대로 돌려주고
두 번째 프로필을 만들지 않는다). 구현은 `game_registration_key` 를 리터럴
`"single-game-client"` 대신 각 GameClient 의 `profile_id` 로 채워, 단일 컬럼 유니크 제약
(`uq_devices_game_registration_per_profile`, 마이그레이션 `0006`)이 "프로필당 GameClient
하나"를 강제하게 한 것이다. 런타임(채팅·태스크·메모리·페어링)은 이미 `profile_id` 로 완전히
분리돼 있어 바뀐 곳이 없다 — 바뀐 것은 부트스트랩 계층이 프로필을 몇 개 만드느냐뿐이다.

**새로 커진 신뢰 함의.** 공유 부트스트랩 토큰 `DEV_GAME_DEVICE_TOKEN` 이 이제 **무제한
프로필 생성 권한**이 됐다. 전에는 유출돼도 전역 차단 때문에 GameClient 하나가 상한이었지만,
이제 토큰을 쥔 누구든 프로필/게임월드를 무한히 만들 수 있고 **요청량 제한은 여전히 없다**
(위 `/pair` 의 rate-limit 부재가 `/register-game` 으로도 확장된다). 사설망 전제가 이전보다
더 큰 역할을 하며, 서버를 사설망 밖으로 내보내는 날에는 토큰별/IP별 rate-limit 를 부트스트랩
경로에도 반드시 넣어야 한다. **"모바일/PC 구분 없는 직접 등록"은 이번에 하지 않았다** —
모바일은 계속 페어링 코드로 기존 프로필에 합류해 기억을 공유한다(역할 구조 유지).

이하는 제거 전 기록이다 — 왜 없앴는지, 무엇을 포기했는지, 무엇이 트리거였는지는
그대로 유효한 역사이므로 남겨 둔다.

### 원래 상태 기록 (2026-07-29 ~ 2026-07-31)

### 왜 있나

내부 개발이 끝나지 않은 단계에서 디바이스 토큰 발급 → 페어링 코드 → Bearer 인증을 매번
거치는 비용이 얻는 것보다 컸다. 마코의 대사·명령 판단을 시험하는 데 신원 증명은 아무것도
기여하지 않는데, 서버를 띄우기 전에 `alembic upgrade head` 를 돌리고 토큰을 발급받아야
했다.

그래서 인증을 **없앴다**. 요청 본문의 `player_name` 이 그대로 신원이고, 아무도 그것을
검증하지 않는다. 이름은 `session_id` 와 함께 `conversation_key` 로 해시되어 대화를 가르는
용도로만 쓰인다.

### 무엇을 포기했나

정직하게 적어 둔다. 되돌릴 때 무엇을 다시 얻는지가 명확해야 한다.

- **아무나 아무 이름이나 댈 수 있다.** 남의 이름을 넣으면 그 사람의 대화 기억을 그대로
  읽고 이어 쓴다. 접근 제어가 존재하지 않는다.
- **요청량 제한이 없다.** 유료 LLM 공급자를 켠 채 노출하면 비용이 무제한이다.
- **멱등성이 사라졌다.** DB 와 함께 지웠다. 같은 `request_id` 를 두 번 보내면 두 번 다
  LLM 을 호출하고 서로 다른 응답을 받는다. 클라이언트 재전송은 중복 명령이 될 수 있다.
- **감사 기록이 없다.** 요청과 응답은 어디에도 남지 않는다.

**그래서 이 서버는 신뢰할 수 없는 네트워크에 노출하면 안 된다.** 로컬 또는 사설망 전용이다.

### 2026-07-30: 장기기억이 이 절의 트리거를 발화시켰다

아래 "언제 지우나" 의 네 번째 조건("대화 기록을 보존해야 할 요구가 생길 때")이 실제로
발생했다. 마코가 세션과 재시작을 넘어 플레이어를 기억하는 기능(`app/brain/memory.py`)이
들어왔고, 그 기억은 `LONG_TERM_MEMORY_DIR` 아래 **플레이어별 JSON 파일로 영속된다.**

**노출이 어떻게 나빠졌나.** 지금까지 사칭의 피해는 "남의 진행 중인 대화에 끼어들기" 였고,
프로세스 수명과 30분 유휴 TTL 이 그 상한이었다. 장기기억에는 그 상한이 없다 — 남의 이름을
입력한 사람은 그 사람에 대해 마코가 알게 된 것을 **영구히 읽고, 오염시킬 수 있다.** 검증되지
않은 자기 신고 이름이 영구 저장소의 색인이 되었다는 뜻이다.

**그런데도 인증을 지금 되살리지 않는 이유.** 인증 복구는 이 작업의 범위 밖이고, 이 서버가
사설망 전용이라는 전제는 그대로다. **위험을 알고 수용한 결정이며, 전제가 깨지는 순간
아래 절차를 밟아야 한다.** 이 결정은 다음 조건 중 하나라도 성립하면 무효다 — 서버가
사설망 밖으로 나가거나, 서로 모르는 플레이어들이 한 서버를 함께 쓰기 시작하는 경우.

**완화 장치(인증을 대신하지는 못한다).**

- 기억은 `player_key = sha256([player_name])` 로 색인되므로 파일 이름에 이름이 드러나지
  않는다. 이것은 **파일 경로 안전성과 길이 고정**을 위한 것이지 비밀 유지가 아니다 —
  같은 이름을 입력하면 같은 키가 나온다.
- 기억은 대사 프롬프트의 `[기억]` 블록으로만 들어가고 `[확정 사실]` 이 되지 않는다.
  오염된 기억이 게임 사실을 바꾸지는 못한다.
- 숫자를 담은 기억은 저장 자체가 거부되고, 플레이어당 개수·길이 상한이 있다.
- `LONG_TERM_MEMORY_ENABLED=false` 로 기능 전체를 끌 수 있다. 끄면 회수도 추출도 하지
  않고 파일도 만들지 않는다.

인증을 되살릴 때 `_player_key` 도 `_conversation_key` 와 함께 HMAC 이 되어야 하고, 그때는
기존 기억 파일의 키가 전부 달라져 사실상 초기화된다는 점을 계산에 넣어야 한다.

### 2026-07-30: 전사(transcript) — 같은 조건이 한 단계 더 나빠졌다

장기기억 v2 에서 **오간 말을 그대로 남기는 전사 층**(`app/brain/transcript.py`)이 들어왔다.
증류(장기기억)를 턴 주기에서 떼어 내려면 커서가 가리킬 원본 로그가 있어야 했기 때문이고,
이 층 없이는 3의 배수로 끝나지 않은 대화의 기억이 통째로 유실됐다.

**노출이 어떻게 더 나빠졌나.** 위 항목에서 검증되지 않은 이름 밑에 영속된 것은 **증류된
32줄**이었다. 이제 **대화 원문 전체**가 같은 조건에 놓인다 — `TRANSCRIPT_DIR` 아래
`conversation_key` 별 JSONL 파일이고, 남의 이름과 세션을 아는 사람은 그 대화를 그대로
읽을 수 있다. 위험의 **종류**는 같고 **양**이 늘었다.

**구조화 로그 금지는 그대로다.** `app/CLAUDE.md` 의 "Conversation text is never logged" 는
폐기된 것이 아니라 둘로 갈렸다 — (1) 구조화 로그 스트림(`_log_step`, request-context
미들웨어)에는 여전히 대화가 한 글자도 들어가지 않는다, (2) 전사는 **의도된 별도 저장소**이며
보존 정책과 노출 위험은 이 절이 관리한다. 로그 수집기로 대화가 흘러가는 일은 없다.

**완화 장치(역시 인증을 대신하지 못한다).**

- `TRANSCRIPT_RETENTION_DAYS`(기본 30) 가 지난 전사는 백그라운드 루프가 지운다. 장기기억과
  달리 전사에는 **만료가 있다.**
- `TRANSCRIPT_ENABLED=false` 로 끌 수 있다. 끄면 파일도 만들지 않는다. 다만 **새 장기기억도
  생기지 않는다** — 증류는 전사에 대한 커서 작업이라 읽을 로그가 없다(이미 있는 기억의
  회수는 계속된다).
- 파일 이름은 `conversation_key = sha256([player_name, session_id])` 라 이름이 드러나지
  않는다. 역시 경로 안전성과 길이 고정을 위한 것이지 비밀 유지가 아니다.

인증을 되살릴 때 전사 파일도 기억 파일과 같은 처지가 된다 — 키가 달라져 조회되지 않고,
옮길 가치가 없으면 `TRANSCRIPT_DIR` 를 비운다.

### 설계상 지켜지는 것

- **명령 단언은 살아 있다.** `CompanionService._assert_within_allowlist` 가 `allowed_commands`
  밖의 명령을 여전히 거절한다. 이것은 신원과 무관하게 게임을 보호하는 방어선이라 인증과
  함께 지우지 않았다.
- **`game_context` 비밀값 차단도 살아 있다.** `FORBIDDEN_AI_CONTEXT_KEYS` 가 막는 것은 인증
  자격증명이 아니라 "LLM 프롬프트에 들어가면 안 되는 값" 이고, 그 위험은 남는다.
- **`app/brain/` 은 여전히 불투명한 키만 받는다.** 이제 둘이다(`conversation_key`,
  `player_key`) 하지만 어느 쪽도 이름이 아니고, 두 값 모두 `app/service.py` 한 곳에서
  파생된다. 인증을 되살릴 때 고칠 곳은 그 두 함수뿐이고 두뇌 쪽 코드는 바뀌지 않는다.

### 언제 지우나

**다음 중 하나라도 해당되면.**

- 서버를 로컬/사설망 밖으로 내보낼 때
- ~~한 대의 서버를 여러 플레이어가 동시에 쓰기 시작할 때~~ → **2026-08-05 발화됨.** 위
  "2026-08-05: 다중 플레이어 등록" 기록 참조. 인증 스캐폴드 전체 교체가 아니라 GameClient
  제한만 프로필당 1개로 풀었고, 부트스트랩 토큰의 rate-limit 부재는 남아 있다.
- 유료 LLM 공급자를 켠 채로 상시 기동할 때
- ~~대화 기록을 보존해야 할 요구가 생길 때~~ → **2026-07-30 발화됨.** 장기기억과 전사,
  두 번의 결정 기록을 참조.

### 지우는 법

**되돌리는 것이 아니라 다시 만드는 것이다.** 지워진 구현은
`git show cd0be55:app/` 에서 꺼내 볼 수 있다(이 발판을 넣기 직전 커밋).

되살릴 범위는 필요에 따라 다르므로 순서만 적는다.

1. **신원** — `ChatRequest.player_name` 을 지우고, 인증된 신원을 주는 FastAPI 의존성을 다시
   넣는다(`git show cd0be55:app/api/dependencies/auth.py`). `CompanionService.create_response`
   가 그 신원을 인자로 받게 하고, `_conversation_key` 와 `_player_key` 의 스코프를 신원
   기반으로 바꾼다. 두 함수의 해시를 HMAC 으로 되돌린다 — 그때는 키가 진짜로 신원을
   감춰야 한다. **기존 장기기억 파일은 키가 달라져 더 이상 조회되지 않는다.** 옮길
   가치가 있으면 이름→새 키로 파일을 다시 명명하는 일회성 작업이 필요하고, 그럴 가치가
   없으면 `LONG_TERM_MEMORY_DIR` 를 비운다.
2. **영속화** — 멱등성과 감사 기록이 필요하면 `git show cd0be55:app/infrastructure/database/`
   의 리포지토리와 `migrations/`, `alembic.ini` 를 되살린다. `pyproject.toml` 에
   `sqlalchemy`, `alembic`, `aiosqlite` 를 다시 넣는다.
3. **WebSocket** — 지금은 `/api/v1/chat` 하나다. 인증 수단이 둘(헤더/첫 메시지)로 갈리면
   그때 다시 두 엔드포인트로 나눈다. 그 분기가 있었던 이유가 그것뿐이었다.
4. **문서** — README 의 경고 박스, 이 절, `CLAUDE.md` 의 해당 서술을 함께 갱신한다.

### 지웠는지 확인

```powershell
# 아무것도 잡히지 않아야 한다
Select-String -Path app,tests,docs,README.md -Pattern "player_name" -Recurse

uv run ruff check .
uv run mypy
uv run pytest
```

---

## 3. `recent_event_ids` — 이벤트 카탈로그가 오기 전 수신 전용 필드

**추가일** 2026-08-03 · **상태** 활성(수신·검증만)

### 왜 있나

클라이언트 계약이 최근 게임 이벤트 ID 목록을 보내기 시작했다. 현재 서버에는 이벤트
카탈로그가 없고, `recent_event_ids`를 해석할 경로도 없다. `Offline_Task`는 ERD 컬럼이
확정되어 `app/db/`와 `/api/v1/tasks`에 구현됐지만, 이 필드와 Offline_Task의 관계는 아직
확정하지 않았다. Item/Recipe/Smelting/Enemies 게임 마스터 데이터는 `app/gamedata/`와
SQLite에 들어왔다. 우선 `ChatRequest`가 이 필드를 거부하지 않도록 최대 32개의 중복 없는
ID를 검증한다.

### 현재 하지 않는 일

ID를 대사 프롬프트에 그대로 넣지 않는다. ID만으로는 마코가 말할 한국어 사실을 만들 수 없고,
날것의 식별자를 LLM에 보내면 이벤트 내용을 지어낼 수 있다. 이벤트 ID는 현재 `CompanionTurn`
이나 `DialogueSpec`으로 전달되지 않는다. `Offline_Task`의 `task_id`도 작업 상태 API의
식별자로만 쓰며, 이 필드의 이벤트 설명으로 자동 변환하지 않는다.

### 언제 승격하나

이벤트 ID에서 검증된 설명을 조회할 카탈로그가 도착하면 `LoreRepository`와 같은 검증된 사실
경로를 별도로 설계하고, 그때 `recent_event_ids`를 `CompanionTurn`/`DialogueSpec`의 사실
블록으로 배선한다. 카탈로그가 계약에서 빠지면 이 필드와 검증도 함께 제거한다.

> [!NOTE]
> **`POST /api/v1/situations` 는 이 필드의 승격판이 아니다 — 신뢰 모델이 다른 별개의
> 확정된 계약이다.** 상황 이벤트는 이벤트 *ID* 대신 클라이언트가 쓴 상황 산문을 직접
> 받아, 검증하지 않은 채 그대로 `[상황]` 프롬프트 블록에 싣는다(`app/brain/CLAUDE.md`
> "Situation events"). `recent_event_ids`가 여기서 막혀 있는 이유(카탈로그 없이는 ID만으로
> 한국어 사실을 만들 수 없다)는 상황 이벤트에는 애초에 적용되지 않는다 — 상황 이벤트는
> 서버가 사실을 조회하지 않고 클라이언트가 이미 쓴 문장을 그대로 옮긴다. 이벤트 카탈로그가
> 나중에 생기더라도 `POST /api/v1/situations`가 대체되거나 여기 임시 발판 목록에 합류하는
> 것은 아니다.

---

## 4. ERD 기억 테이블과 현재 서버 저장소의 차이 — RAG 반영 완료

RAG 작업에서 `Chat_Buffer`와 `Episodic_Memory`를 같은 것으로 섞지 않고 수명을 분리했다.

- `Chat_Buffer`에 가까운 원문 전사: `TRANSCRIPT_DIR` 아래 대화별 JSONL. 플레이어/마코
  speaker와 timestamp를 갖고, 장기기억 추출의 원본이다. 보존기간 정책도 그대로 유지한다.
- 한 턴 안의 작업 기억: `InMemoryConversationStore`의 프로세스 메모리. 최근 대화와
  되묻기 슬롯만 들고 재시작하면 사라진다.
- `Episodic_Memory`에 대응하는 장기기억: `episodic_memories` SQLite 테이블. 인증된
  프로필·세이브 슬롯에서 만든 HMAC `player_key`로 스코프하고, 중요도 1~10, 선택적
  정규화 임베딩 JSON, 임베딩 모델명, 회수 이력을 저장한다.

기존 `LONG_TERM_MEMORY_DIR`의 v1/v2 JSON은 `0005` 마이그레이션 때 행으로 복사하고
원본은 삭제하지 않는다. 이전 1~3 중요도는 두 배로 환산한다. 파일 이름이 현재 HMAC
`player_key`인 경우에는 그대로 같은 세이브 슬롯으로 이어지며, 과거 인증 전의 이름 기반
파일처럼 현재 키를 복원할 수 없는 파일은 원본을 보존한 채 자동으로 연결하지 않는다.

질의와 기억에 같은 모델의 임베딩이 있으면 키워드·시간 감쇠와 의미 유사도를 함께 사용한다.
임베딩 공급자가 없거나 실패하거나 모델/차원이 맞지 않으면 키워드+시간 감쇠로 자동 폴백한다.
기본 `mock` 설정은 외부 호출을 하지 않는다.

**이번에 남은 것:** `Location`은 실제 좌표 행이 0개라 위치 기반 RAG를 아직 붙이지 않았다.
좌표와 적/아이템 FK의 의미가 확정되면 별도 작업으로 추가한다. `recent_event_ids`도
이벤트 카탈로그가 도착하기 전까지는 계속 수신·중복 검증만 한다.
