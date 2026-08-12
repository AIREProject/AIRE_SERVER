# 동료 대사를 고정 템플릿에서 LLM 생성으로 전환

## Context

현재 서버에서 LLM은 **의도 분류만** 담당하고, 실제 플레이어에게 보이는 대사는 거의 전부
하드코딩된 문자열이다.

- [service.py:102-124](src/ai_companion_server/service.py#L102-L124) — 명령 확인 대사 4종이 리터럴
- [service.py:175-189](src/ai_companion_server/service.py#L175-L189) — 채집 대사 2종이 리터럴
- [service.py:76-84](src/ai_companion_server/service.py#L76-L84) — 레시피/로어는 저장소 `fact.text`를 **그대로** dialogue로 반환
- [service.py:201-214](src/ai_companion_server/service.py#L201-L214) — 이벤트 결과 대사가 f-string
- [service.py:34-36](src/ai_companion_server/service.py#L34-L36), `_unsupported` — 미지원 안내 문구가 리터럴

즉 LLM이 문장을 만드는 곳은 `TopIntent.CONVERSATION` 하나뿐이다(`generate_conversation`).
결과적으로 마코의 말투가 기계적이고, 같은 명령에 항상 똑같은 문장이 나온다.

**목표**: 모든 응답 대사를 LLM이 생성하게 하되, **게임 사실은 계속 코드(저장소·파서·Action)가
소유**한다. 레시피·로어·채집 수량 같은 사실은 저장소에서 꺼낸 "확정 사실"로 LLM에 주입하고,
LLM은 그 사실을 **말투로 옮기는 역할만** 한다. Action, `clarification.options`, `error.code`,
`error.message`는 전부 결정론적으로 유지된다.

**사용자 확정 사항**
1. 적용 범위: 메시지 경로 전체 + `/v1/companion/event` 작업 결과 대사까지 **모든 대사**
2. 호출 구조: 분류와 생성을 분리 — 요청당 LLM 호출 1회 추가 허용(명령 경로 최대 3회)
3. `dialogue`만 LLM 생성, `error.message`는 고정 문구 유지

---

## 설계

### 핵심 아이디어: "장면(scene) + 확정 사실 + 폴백 템플릿"

대사가 필요한 모든 지점을 `DialogueSpec` 하나로 표현한다. 각 spec은 **지금 코드에 있는 그
문자열을 `fallback`으로 그대로 들고 있다.** 따라서

- LLM 호출 실패·검증 실패 → 현재와 100% 동일한 문구로 복구
- `MockLLMProvider` → 항상 `fallback` 반환 ⇒ 기존 테스트가 그대로 통과, 결정론 유지

> **`fallback`은 프롬프트에 절대 넣지 않는다.** 완성된 문장을 프롬프트에 보여 주면 LLM이
> 그것을 거의 그대로 베껴 지금과 다를 바 없는 응답이 나온다. `fallback`은 오직 코드 경로
> (Mock 공급자, 예외·검증 실패 복구)에서만 쓰이는 값이다.
>
> 프롬프트에는 두 가지만 들어간다:
> - **지시** — `SCENE_GUIDE`의 "이 장면에서 무엇을 전달해야 하는가"(완성 문장이 아니라 목표)
> - **데이터** — `facts`의 확정 사실 (서술형 사실 조각이지, 그대로 읽을 대사가 아니다)
>
> 말투 일관성은 폴백 문장이 아니라 **페르소나 서술 + 폴백과 겹치지 않는 few-shot 예시
> 2개**로 잡는다. 예시를 폴백과 다른 문장으로 두어야 특정 문구로 수렴하지 않는다.

### 1. 새 모듈 `src/ai_companion_server/dialogue.py`

```python
DialogueScene = Literal[
    "follow_player", "wait", "stop_current_task", "cancel",
    "gather_wood", "gather_stone", "gather_ambiguous",
    "recipe", "lore", "conversation",
    "unsupported", "event_completed", "event_failed",
]

@dataclass(frozen=True, slots=True)
class DialogueSpec:
    scene: DialogueScene
    fallback: str            # 코드 전용 복구 문자열. 프롬프트에 넣지 않는다
    user_text: str | None = None
    facts: tuple[str, ...] = ()   # 저장소/파서가 확정한 사실. 이 밖은 말하면 안 됨
```

- `SCENE_GUIDE: dict[DialogueScene, str]` — 장면별 **목표 지시**(완성 대사가 아니다):

  | scene | SCENE_GUIDE |
  |---|---|
  | `follow_player` | 플레이어를 따라가기 시작한다는 것을 알린다. |
  | `wait` | 지금 이 자리에서 기다리겠다는 것을 알린다. |
  | `stop_current_task` | 하던 작업을 지금 중단한다는 것을 알린다. |
  | `cancel` | 직전 요청을 없던 일로 하겠다는 것을 알린다. |
  | `gather_wood` / `gather_stone` | 근처에서 나무/돌을 찾아 채집하러 간다는 것을 알린다. |
  | `gather_ambiguous` | 무엇을 캘지 되묻는다. 나무와 돌 중에서만 고르게 한다. |
  | `recipe` | 확정 사실의 제작법을 전한다. 재료·수량·제작 장소를 빠뜨리지 않는다. |
  | `lore` | 확정 사실의 지역 이야기를 전한다. |
  | `conversation` | 플레이어의 말에 가볍게 반응한다. |
  | `unsupported` | 확정 사실에 적힌 이유로 그 요청은 도울 수 없다고 알리고, 할 수 있는 일을 짧게 안내한다. |
  | `event_completed` / `event_failed` | 채집 결과를 확정 사실 그대로 보고한다. |
- `def sanitize(text: str, spec: DialogueSpec) -> str | None` — **환각 가드**. 실패 시 `None`
  (호출부가 `fallback` 사용):
  - 공백 제거 후 빈 문자열이거나 200자 초과 → 거부
  - 개행을 공백으로 접고 연속 공백 정리
  - `scene != "conversation"`이면, 출력에 등장한 모든 숫자열이 `facts`에 등장한 숫자열의
    부분집합이어야 함 → `철괴 3개`를 `4개`로 바꾸거나 `돌 10개`를 `12개`로 바꾸는 사고를 차단
- `async def render(llm: LLMProvider, spec: DialogueSpec) -> str` — `llm.generate_dialogue(spec)`
  를 `try/except`로 호출하고 `sanitize`를 통과한 값 또는 `spec.fallback` 반환. 서비스는 항상
  이 함수만 쓴다(공급자 종류와 무관하게 가드가 걸린다).

### 2. `llm.py` — `generate_conversation` → `generate_dialogue`로 통합

`LLMProvider` 인터페이스에서 `generate_conversation(user_text)`를 제거하고
`generate_dialogue(spec: DialogueSpec) -> str`로 교체한다. 일반 대화는 `scene="conversation"`,
`facts=()`인 spec 하나일 뿐이므로 메서드가 둘로 갈릴 이유가 없다.

- 공통 시스템 프롬프트 상수 `_DIALOGUE_PROMPT` 추가. 페르소나 + 금지 사항 + **폴백과 겹치지
  않는 톤 예시**로 구성한다:
  > 너는 생존 게임의 AI 동료 마코다. 한국어 반말로 따뜻하고 짧게 한두 문장만 말한다.
  > `[지시]`가 요구하는 내용을 전달하되 문장은 매번 새로 만든다. 정해진 문구를 반복하지 않는다.
  > `[확정 사실]`에 적힌 내용만 사용하고, 없는 게임 정보·수치·아이템·장소를 절대 지어내지
  > 않는다. 사실이 비어 있으면 사실 언급 없이 상황에만 반응한다.
  > 되묻지 않는다(단, 지시가 되물으라고 하면 예외). 이모지와 따옴표를 쓰지 않는다.
  > 말투 예시(내용은 무시하고 어조만 참고): `발 맞춰 갈 테니까 앞장서.` / `그건 내 손을 좀 벗어나네.`
- 사용자 메시지 조립 헬퍼 `_dialogue_user_message(spec)` — **완성 문장은 넣지 않는다**:
  ```text
  [지시] {SCENE_GUIDE[spec.scene]}
  [확정 사실]
  - {facts[0]}
  - {facts[1]}
  [플레이어] {user_text}      # user_text가 있을 때만
  ```
  `facts`가 비면 `[확정 사실] 없음`으로 적어 "사실 언급 금지"를 명확히 한다.
- `MockLLMProvider.generate_dialogue`: `scene == "conversation"`이면 기존 두 문구
  (`"별말을 다 해..."` / `"안녕! 오늘은..."`), 그 외에는 `spec.fallback` 반환.
- `OpenAIProvider.generate_dialogue`: 기존 `DialogueOutput` JSON schema 구조화 출력 유지,
  `temperature`/`max_output_tokens`를 새 설정값으로 지정, 실패 시 `self._fallback`에 위임.
- `LocalLLMProvider.generate_dialogue`: 기존 chat completions 형태 유지하되 하드코딩된
  `temperature=0.6, max_tokens=512`를 설정값으로 교체.

### 3. `config.py` — 대사 전용 파라미터

`Settings`에 추가(분류용 `classify_*`와 대칭):

```python
dialogue_temperature: float = 0.6
dialogue_max_tokens: int = 160
```

`.env.example`에도 두 키를 주석과 함께 추가한다.

### 4. `service.py` — 모든 응답을 `dialogue.render`로 통과

`RequestService`의 모든 응답 생성 지점을 spec 조립 + `await render(...)` 형태로 바꾼다.
분기 조건·Action·`clarification`·`error`는 **손대지 않는다.**

`facts`에는 **완성된 대사가 아니라 서술형 사실 조각**을 넣는다(예: `"수량 지정 채집은 지원하지
않는다"`). 그래야 LLM이 문장을 베끼지 않고 사실만 가져다 쓴다.

| 위치 | scene | facts | fallback (프롬프트 미포함, 현행 문자열 그대로) |
|---|---|---|---|
| `_handle_command` FOLLOW | `follow_player` | — | `알겠어. 따라갈게.` |
| `_handle_command` WAIT | `wait` | — | `알겠어. 여기서 기다릴게.` |
| `_stop_response` | `stop_current_task` | — | `알겠어. 지금 하던 일을 멈출게.` |
| `_handle_command` CANCEL | `cancel` | — | `알겠어. 요청을 취소할게.` |
| `_gather_response` wood/stone | `gather_wood`/`gather_stone` | — | `알겠어. 근처의 나무를 찾아볼게.` 등 |
| `_resolve_gather` ambiguous | `gather_ambiguous` | `("고를 수 있는 자원은 나무와 돌뿐이다",)` | `무엇을 캐면 될까?` |
| `TopIntent.RECIPE` 성공 | `recipe` | `(fact.text,)` | `fact.text` |
| `TopIntent.LORE` 성공 | `lore` | `(fact.text,)` | `fact.text` |
| `TopIntent.CONVERSATION` | `conversation` | — | 기존 mock 인사 문구 |
| `_unsupported` 전 호출부 | `unsupported` | 아래 참조 | 각 호출부의 현행 안내 문구 |

`_unsupported` 호출부별 facts (안내 문구를 사실 조각으로 다시 씀):

| 호출부 | facts |
|---|---|
| 수량·미지원 자원 채집 | `("수량을 지정한 채집은 지원하지 않는다", "일반 나무와 돌 채집만 가능하다")` |
| 활성 작업 없는 중지 | `("지금 진행 중인 작업이 없다",)` |
| 레시피 미발견 | `("확인된 제작법 정보가 없다",)` |
| 로어 미발견 | `("이 위치에 대해 확인된 이야기가 없다",)` |
| 재질의 종료 | `("재질의를 종료했다", "채집은 나무와 돌만 가능하다")` |
| 기본 미지원 | `("가능한 일은 따라오기, 대기, 작업 중지, 나무와 돌 채집뿐이다",)` |

`recipe`/`lore`의 `fact.text`는 저장소에 문장 형태로 저장돼 있어 결과가 원문과 가까워질 수
있다. 이는 **정보 정확성 측면에서 의도된 것**이며, `SCENE_GUIDE`가 "전한다"로 지시하므로 말투와
문장 구성은 마코 쪽으로 바뀐다. 원문 그대로 읽히는 게 거슬리면 후속 작업으로 저장소 fact를
문장 대신 구조화 필드(재료·수량·제작 장소)로 쪼개면 되지만, 이번 변경 범위에는 넣지 않는다.

세부 사항:

- `_gather_response`, `_stop_response`, `_unsupported`는 `@staticmethod`에서 **async 인스턴스
  메서드**로 바뀐다(`self._llm` 필요). `_resolve_gather`, `_resolve_clarification`도 async가 된다.
- `_unsupported(request, dialogue)` → `_unsupported(request, message, facts)`로 바뀐다:
  `error.message`와 `ErrorBody.code`는 넘겨받은 **고정 문구 그대로**(= 현행 문자열, 폴백으로도
  재사용), `dialogue`만 LLM 생성값. `facts`는 위 표의 사실 조각이라 LLM이 지원 범위를 지어낼 수 없다.
- `handle_event`는 `@staticmethod` → **`async def` 인스턴스 메서드**. facts에
  `f"{name} {amount}개를 채집했다"`(성공) / `f"{name} 채집에 실패했다"`(실패)를 넣고, 숫자 가드가
  수량 변조를 막는다. `main.py:43`의 `return service.handle_event(payload)`를 `await`로 수정.
- 기존 모듈 상수 `_UNSUPPORTED_FALLBACK`은 그대로 재사용한다.

### 5. 문서 갱신 (구현 계약이므로 필수)

- [01_current_scope.md:12-14](docs/current/01_current_scope.md#L12-L14) — "명령·재질의·작업 결과의
  대사와 Action은 결정론적 템플릿" → "Action·선택지·오류 코드는 결정론적이고, 모든 대사는
  저장소가 확정한 사실을 근거로 공급자가 생성하며 실패 시 템플릿으로 복구한다"로 수정
- [03_runtime_flow.md:13-16](docs/current/03_runtime_flow.md#L13-L16) — "LLM은 의도 분류만 담당"
  단락을 새 구조(분류 + 사실 주입 대사 생성 + sanitize 가드 + 템플릿 폴백)로 다시 씀. 24행
  이벤트 흐름 문장도 "템플릿 대사만 반환" → 사실 기반 생성으로 수정
- [05_player_qa_catalog.md](docs/current/05_player_qa_catalog.md) — 2절의 "Mock 응답 대사" 주석을
  일반 대화뿐 아니라 **모든 장면**에 적용된다고 확장(실제 공급자에서는 문구가 달라질 수 있음)
- `CLAUDE.md` Architecture 절 — "dialogue and actions ... always come from fixed templates"
  문장과 `llm.py` 설명(`generate_conversation`)을 새 구조로 갱신, `dialogue.py` 항목 추가

### 6. 테스트

- `tests/test_api.py` — **수정 없이 통과해야 한다.** Mock이 `fallback`을 그대로 반환하므로
  기존 정확 문자열 단언이 유지된다. 이것이 회귀 안전망 역할을 한다.
- `tests/test_service.py` — `ForcedTopProvider`의 `generate_conversation` 오버라이드를
  `generate_dialogue`로 교체. 추가 테스트:
  - 레시피 경로에서 `generate_dialogue`가 호출되고 `spec.facts`에 저장소 fact가 들어간다
  - LLM이 예외를 던지는 공급자를 주입해도 dialogue가 기존 템플릿과 동일하다
  - 미지원 응답에서 `error.message`는 고정 문구이고 `dialogue`만 LLM 값이다
- `tests/test_llm.py` — `generate_conversation` 관련 두 테스트를 `generate_dialogue(spec)` 기준으로
  수정(시스템 프롬프트/temperature/max_tokens 단언 갱신). **추가 회귀 테스트: 전송된 messages
  어디에도 `spec.fallback` 문자열이 들어 있지 않다** — 폴백이 프롬프트로 새는 것을 영구 차단한다.
- 신규 `tests/test_dialogue.py` — `sanitize` 단위 테스트:
  빈 문자열/200자 초과 거부, 개행 정리, `facts`에 `3`,`2`가 있을 때 `"철괴 4개"` 출력 거부·
  `"철괴 3개"` 출력 허용, `conversation` 장면은 숫자 가드 면제.

---

## 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `src/ai_companion_server/dialogue.py` | **신규** — `DialogueSpec`, `SCENE_GUIDE`, `sanitize`, `render` |
| `src/ai_companion_server/llm.py` | `generate_conversation` → `generate_dialogue`, 3개 공급자 모두 구현 |
| `src/ai_companion_server/service.py` | 모든 응답 생성이 `render` 경유, 다수 메서드 async 전환 |
| `src/ai_companion_server/main.py` | `await service.handle_event(payload)` |
| `src/ai_companion_server/config.py`, `.env.example` | `dialogue_temperature`, `dialogue_max_tokens` |
| `docs/current/01,03,05`, `CLAUDE.md` | 계약 문서 갱신 |
| `tests/test_service.py`, `test_llm.py`, `test_dialogue.py`(신규) | 위 참조. `test_api.py`는 무변경 |

`domain.py`(공개 응답 모델), `recipes.py`, `lore.py`, `command_intent.py`, `intent.py`는 **변경 없음** —
사실과 분류의 소유권을 그대로 코드에 남긴다는 것이 이 설계의 핵심이다.

---

## 검증

```powershell
uv run pytest                       # test_api.py가 무변경 통과 = 폴백 경로 회귀 없음
uv run ruff check .
uv run mypy src                     # async 전환된 시그니처 전파 확인
```

실제 LLM 대사 확인(로컬 공급자 키가 있을 때):

```powershell
# .env 에 LLM_PROVIDER=local + LOCAL_LLM_API_KEY 설정 후
uv run uvicorn ai_companion_server.main:app --reload
```

```powershell
$body = '{"request_id":"req_1","text":"철 도끼 어떻게 만들어?","client_context":{"location_id":"region_abandoned_mining_village","active_task":null,"clarification_id":null}}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/companion/message -Method Post -ContentType 'application/json' -Body $body
```

수동 확인 항목:
0. **`"따라와"`를 3회 보내 응답 문장이 서로 다른지** 확인한다. 세 번 모두 `알겠어. 따라갈게.`가
   나오면 폴백이 프롬프트로 새고 있거나 LLM 호출이 실패해 폴백으로 떨어지는 것이다
1. 레시피 응답 문장이 매번 달라지되 **철괴 3개 / 나무 2개 / 작업대**가 그대로 유지된다
2. `"나무를 모아 줘"` 응답의 `action`은 `{"type":"gather_resource","resource_type":"wood"}` 불변,
   `dialogue`만 자연스러워진다
3. `"나무 10개 모아 줘"` 응답의 `error.message`는 고정 문구, `dialogue`는 생성 문구
4. `POST /v1/companion/event`(`amount: 10`) 응답 대사에 **10 이외의 숫자가 나오지 않는다**
5. `.env`에서 `LOCAL_LLM_BASE_URL`을 잘못된 주소로 바꿔 재실행 → 모든 응답이 기존 템플릿
   문구로 정확히 복구되고 5xx가 나지 않는다
