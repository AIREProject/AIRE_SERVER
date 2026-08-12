# 마코 라우팅을 LangGraph StateGraph로 리팩토링 (LangGraph-only, 2단계)

> **선행 조건**: [cancel-command-consolidation-plan.md](cancel-command-consolidation-plan.md)(1단계)가
> **먼저 완료되어야 한다.** 이 문서는 **동작 보존(behavior-preserving) 리팩토링**이고, 그 안전망은
> "기존 테스트가 무변경으로 통과한다"는 것이다. 동작 변경을 여기에 섞으면 안전망이 무의미해지므로
> 알려진 동작 결함(`CommandLabel.CANCEL`의 no-op)은 1단계에서 이미 해소된 상태를 전제한다.
>
> 1단계 완료 후 이 문서가 전제하는 상태:
> - `CommandLabel.CANCEL`이 제거되고 `STOP_CURRENT_TASK`로 통합되어 있다
> - `_COMMAND_SCENE` / `_COMMAND_TYPE_MAP`이 단일 테이블 `_COMMANDS`로 합쳐져 있다
> - 취소 어휘의 서비스 레벨 테스트가 추가되어 동작 보존 게이트에 포함되어 있다

## Context

현재 마코 두뇌는 [service.py](../../app/infrastructure/ai/companion/service.py)의
`CompanionAIService._route`가 조건 분기 파이프라인으로 직접 구현한다.

- [service.py:89-132](../../app/infrastructure/ai/companion/service.py#L89-L132) — `_route`:
  `classify_top` 결과(`TopIntent`)로 command / recipe / lore / conversation / unknown 분기
- [service.py:134-156](../../app/infrastructure/ai/companion/service.py#L134-L156) — `_handle_command`:
  `classify_command` 결과(`CommandLabel`)로 이동 명령 / 채집 / 미지원 분기
- [service.py:158-178](../../app/infrastructure/ai/companion/service.py#L158-L178) — `_gather_dialogue`:
  `CommandIntentParser.resolve_gather`로 wood / stone / ambiguous / unsupported 해소
- [service.py:180-209](../../app/infrastructure/ai/companion/service.py#L180-L209) —
  `_build_command`(명령 후보 생성), `_say`(대사 렌더 경계)

즉 흐름은 이미 **노드 + 조건부 엣지의 그래프**다. 이 계획은 그 흐름을 LangGraph `StateGraph`로
옮기되, **LLM 호출 계층(`LLMProvider`)과 사실 소유권(저장소·파서·`dialogue.render`)은 그대로
둔다.** LangChain의 채팅 모델 래퍼(`ChatOpenAI` 등)는 **도입하지 않는다.**

**사용자 확정 사항**
1. **LangGraph만** 도입한다. LangChain LLM 추상화로 교체하지 않는다.
2. 이번 산출물은 **계획서 하나**다(구현은 별도 승인 후 진행).

### 왜 LangGraph-only인가 (건드리면 안 되는 불변식)

`LLMProvider`를 `ChatOpenAI` 등으로 교체하면 다음이 전부 깨진다. LangGraph는 순수 파이썬
함수를 노드로 쓸 수 있으므로, 오케스트레이션만 바꾸고 아래는 **그대로 보존**한다.

- OpenAI **Responses API + strict JSON schema** 구조화 출력([llm.py:150-227](../../app/infrastructure/ai/companion/llm.py#L150-L227))
- **모든 공급자 실패를 조용히 mock으로 흡수**하는 폴백 규칙 — AI 장애가 요청 전체를 실패시키지
  않는다는 불변식([llm.py:172-175](../../app/infrastructure/ai/companion/llm.py#L172-L175) 등,
  [dialogue.py:75-82](../../app/infrastructure/ai/companion/dialogue.py#L75-L82))
- mypy strict + pydantic 계약(`AIServiceRequest`/`AIServiceResult`, `AIService` Protocol)

**이번 리팩토링은 동작 보존(behavior-preserving)이다.** 기존 테스트
([tests/test_companion_ai_service.py](../../tests/test_companion_ai_service.py),
`test_llm.py`, `test_dialogue.py`)는 **수정 없이 그대로 통과**해야 한다. 이것이 회귀 안전망이다.

---

## 설계

### 0. 의존성

```toml
# pyproject.toml [project].dependencies
langgraph = ">=0.2"
```

- `langgraph`는 `langchain-core`를 전이 의존성으로 끌어온다. **우리는 `langchain-core`를 직접
  import하지 않는다**(채팅 모델·프롬프트 템플릿 미사용). 의존성 트리에만 존재한다.
- `uv add langgraph` → `uv lock` 갱신. `uv lock --check`가 통과해야 한다.
- **mypy strict 리스크**: langgraph의 타입 스텁이 불완전할 수 있다. 필요 시
  `[[tool.mypy.overrides]] module = "langgraph.*"`에 `ignore_missing_imports = true`를 추가한다
  (우리 코드 경계에는 명시적 타입을 유지하고, 라이브러리 내부만 무시).

### 1. 상태 정의 — `graph.py` (신규)

`app/infrastructure/ai/companion/graph.py`를 신설한다. 상태는 요청 입력 + 라우팅 중간값 +
출력 누산기로 구성한다.

```python
from typing import NotRequired, TypedDict

class CompanionState(TypedDict):
    # 입력 (그래프 시작 시 채워짐)
    request: AIServiceRequest
    text: str
    # 라우팅 중간값
    top_intent: NotRequired[TopIntent]
    command_label: NotRequired[CommandLabel]
    # 출력 누산기 (터미널 노드가 채움)
    display_text: NotRequired[str]
    command: NotRequired[CommandCandidate | None]
```

- 상태는 dict여야 하므로 `AIServiceRequest`(pydantic 모델)는 **값으로** 넣는다.
- 각 노드는 **부분 상태 dict를 반환**하고 LangGraph가 병합한다(예: `{"top_intent": ...}`).
- 병렬 분기가 없으므로 리듀서(`Annotated[..., add]`)는 필요 없다.

### 2. 노드 = 저장소·공급자를 캡처한 클로저

의존성 주입은 **빌더 함수 + 클로저**로 한다(가장 타입 안전하고 테스트 쉬움).
`RunnableConfig["configurable"]` 주입 방식은 타입이 약해지므로 채택하지 않는다.

```python
def build_companion_graph(
    llm: LLMProvider,
    recipes: RecipeRepository,
    lore: LoreRepository,
    *,
    command_ttl_seconds: float,
) -> "CompiledStateGraph[CompanionState, ...]":

    async def classify_top_node(state: CompanionState) -> dict:
        intent = await llm.classify_top(state["text"], clarification_pending=False)
        return {"top_intent": intent}

    async def command_classify_node(state: CompanionState) -> dict:
        return {"command_label": await llm.classify_command(state["text"])}

    async def movement_command_node(state: CompanionState) -> dict:
        # follow / wait / stop / cancel — _COMMAND_SCENE + _build_command 그대로
        ...

    async def gather_node(state: CompanionState) -> dict: ...      # _gather_dialogue 이식
    async def recipe_node(state: CompanionState) -> dict: ...      # recipes.fact_for + render
    async def lore_node(state: CompanionState) -> dict: ...        # lore.fact_for + render
    async def conversation_node(state: CompanionState) -> dict: ...# 인사/감사 폴백 + render
    async def unsupported_node(state: CompanionState) -> dict: ... # unsupported render

    graph = StateGraph(CompanionState)
    graph.add_node("classify_top", classify_top_node)
    graph.add_node("command_classify", command_classify_node)
    graph.add_node("movement_command", movement_command_node)
    graph.add_node("gather", gather_node)
    graph.add_node("recipe", recipe_node)
    graph.add_node("lore", lore_node)
    graph.add_node("conversation", conversation_node)
    graph.add_node("unsupported", unsupported_node)

    graph.set_entry_point("classify_top")
    graph.add_conditional_edges("classify_top", route_by_top)
    graph.add_conditional_edges("command_classify", route_by_command)
    for terminal in ("movement_command", "gather", "recipe",
                     "lore", "conversation", "unsupported"):
        graph.add_edge(terminal, END)

    return graph.compile()
```

각 노드는 현재 service.py 로직을 **그대로 이식**한다(새 판단 로직 없음):

| 노드 | 이식 출처 | facts / scene 소유권 |
|---|---|---|
| `classify_top` | `_route` 진입부 | `llm.classify_top` |
| `command_classify` | `_handle_command` 진입부 | `llm.classify_command` |
| `movement_command` | `_handle_command` 141-145 + `_build_command` | 1단계의 단일 테이블 `_COMMANDS` 재사용 |
| `gather` | `_gather_dialogue` 전체 | `CommandIntentParser.resolve_gather` |
| `recipe` | `_route` RECIPE 분기 99-108 | `recipes.fact_for` (성공/미발견 내부 분기) |
| `lore` | `_route` LORE 분기 109-118 | `lore.fact_for` (성공/미발견 내부 분기) |
| `conversation` | `_route` CONVERSATION 분기 119-125 | 인사/감사 폴백 |
| `unsupported` | `_route` fallthrough 127-132, `_handle_command` else | `_UNSUPPORTED_FALLBACK` |

**대사는 전부 기존 `dialogue.render(llm, DialogueSpec(...))` 경유**([dialogue.py:75-82](../../app/infrastructure/ai/companion/dialogue.py#L75-L82))
— 폴백·sanitize 가드가 노드 안에서 그대로 작동하므로 실패 흡수 불변식이 보존된다.

### 3. 라우팅 함수 (조건부 엣지)

노드가 아니라 순수 분기 함수다. 반환값은 다음 노드 이름.

```python
def route_by_top(state: CompanionState) -> str:
    return {
        TopIntent.COMMAND: "command_classify",
        TopIntent.RECIPE: "recipe",
        TopIntent.LORE: "lore",
        TopIntent.CONVERSATION: "conversation",
        TopIntent.UNKNOWN: "unsupported",
    }[state["top_intent"]]

def route_by_command(state: CompanionState) -> str:
    label = state["command_label"]
    if label in _COMMANDS:               # follow / wait / stop (취소 어휘 포함)
        return "movement_command"
    if label is CommandLabel.GATHER_RESOURCE:
        return "gather"
    return "unsupported"                 # UNKNOWN 등
```

> **1단계 덕분에 단순해진 부분**: 통합 전에는 `CommandLabel.CANCEL`이 `_COMMAND_SCENE`에는 있고
> `_COMMAND_TYPE_MAP`에는 없어서, "대사는 만들되 명령 후보는 `None`"이라는 특수 케이스를 그래프에
> 그대로 옮겨야 했다. 1단계에서 라벨이 통합되고 테이블이 `_COMMANDS` 하나로 합쳐지면서 이 분기가
> 사라졌다. 이제 `movement_command` 노드는 **항상** 대사와 명령을 함께 만들며, 명령이 `None`이 되는
> 경우는 allowlist 미포함 하나뿐이다(기존 의도된 동작,
> [test_companion_ai_service.py:74-81](../../tests/test_companion_ai_service.py#L74-L81)이 고정).

### 4. `service.py` — 그래프를 감싸는 얇은 어댑터로 축소

`CompanionAIService`의 **공개 계약(`AIService` Protocol의 `generate_chat`)은 불변**이다. 내부만 바꾼다.

- `__init__`에서 `build_companion_graph(...)`를 **한 번** 호출해 `self._graph`에 컴파일된 그래프 저장.
- `generate_chat`은 그대로 유지하되, 내부 `_route` 대신 그래프를 호출:

```python
async def generate_chat(self, request: AIServiceRequest) -> AIServiceResult:
    try:
        final = await self._graph.ainvoke(
            {"request": request, "text": request.user_message}
        )
    except Exception as error:  # noqa: BLE001
        raise AIServiceUnavailableError from error
    command = final.get("command")
    return AIServiceResult(
        request_id=request.request_id,
        display_text=final["display_text"],
        command_candidates=[command] if command is not None else [],
        memory_candidates=[],
        metadata=self._metadata,
    )
```

- `_route`, `_handle_command`, `_gather_dialogue`, `_build_command`, `_say`는 **`graph.py`로 이동**하며
  service.py에서 제거된다. 모듈 상수(1단계에서 통합된 `_COMMANDS`, `_UNSUPPORTED_FALLBACK`)와
  `_location_id` 헬퍼는 노드가 쓰므로 `graph.py`로 옮기거나 공용 위치에 둔다.
- `AIServiceUnavailableError` 변환 경계는 `generate_chat`에 그대로 남는다.

### 5. 의존성 배선 — 변화 없음

[ai.py:33-41](../../app/api/dependencies/ai.py#L33-L41)의 `CompanionAIService(build_llm_provider(...), ...)`
호출은 **그대로**다. 그래프 구성은 `CompanionAIService.__init__` 내부에서 일어나므로 외부(DI/포트/API)
관점에서 아무 변화가 없다.

---

## 향후 확장 (이번 범위 아님, 구조만 열어 둠)

LangGraph 도입의 실질 가치는 아래 상태·순환 흐름에서 나온다. 이번엔 넣지 않되, 위 구조가 이를
자연스럽게 수용하도록 설계한다.

- **allowlist 기반 동적 라우팅** — 아래 별도 절 참고. 셋 중 가장 가깝고 구조 변경도 작다.
- **다중턴 clarification 메모리**: `graph.compile(checkpointer=...)` + `thread_id`로
  `gather_ambiguous` 뒤 `나무`/`돌` 응답을 이어받는 흐름. 현재 인메모리 `_pending` 방식을 대체.
- **채집 실행 루프 / 이벤트 처리**: gather → 작업 상태 조회 → 재질문의 순환 엣지.
- **memory candidate 추출**: 터미널 이후 병렬 노드로 `memory_candidates` 생성.

### allowlist 기반 동적 라우팅

요청마다 달라지는 `request.allowed_commands`를 라우터가 직접 읽어 목적지를 바꾼다.
**그래프를 다시 만들 필요는 없다** — 조건부 엣지 함수는 매 실행마다 현재 state를 받아 평가되고,
`CompanionState`는 이미 `request`를 통째로 들고 있다. 컴파일은 `__init__`에서 한 번뿐이다.

```python
def route_by_command(state: CompanionState) -> str:
    label = state["command_label"]
    if label is CommandLabel.GATHER_RESOURCE:
        return "gather"

    entry = _COMMANDS.get(label)
    if entry is None:
        return "unsupported"

    command_type, _, _ = entry
    if command_type not in state["request"].allowed_commands:
        return "command_unavailable"   # ← allowlist 기반 동적 분기 (신규 노드)
    return "movement_command"
```

**동기**: 현재는 allowlist 검사가 대사 생성 **이후**에 일어나
([service.py:143-144](../../app/infrastructure/ai/companion/service.py#L143-L144),
`_build_command` 내부) 대사가 명령 생성 결과를 참조하지 못한다. 그래서 명령이 방출되지 않아도
대사는 "하겠다"고 말한다. 검사를 라우터로 당기면 정직한 대사가 가능해진다:

| | 대사 | 명령 |
|---|---|---|
| 현재 | `"알겠어. 따라갈게."` | 없음 ← 거짓말 |
| 개선 | `"지금은 그건 못 하겠어."` | 없음 ← 사실 |

부수 효과로 **이 분기가 그래프 다이어그램에 노드로 드러난다.** 현재는 `_build_command` 안에
숨어 있는 조건이다.

**기존 테스트와 호환된다.**
[test_companion_ai_service.py:74-81](../../tests/test_companion_ai_service.py#L74-L81)의 단언은
`assert result.display_text`(truthy)와 `command_candidates == []` 둘뿐이라 **대사 내용을 고정하지
않는다.** 즉 이 테스트가 고정하는 것은 "명령을 방출하지 않는다"이지 문구가 아니므로, 대사를
정직하게 바꿔도 그대로 통과한다.

**하지 말 것 — 요청마다 recompile**: `StateGraph`를 새로 만들어 매 요청 `.compile()` 하는 방식은
권하지 않는다. `CommandType`이 7종이라 allowlist 조합이 최대 128가지인데, **노드 집합은 어차피
동일하고 목적지만 달라진다.** 위상을 바꿔 얻는 것이 없고 컴파일 비용과 캐싱 관리 부담만 는다.
컴파일된 그래프가 불변이라 동시 요청에 안전하게 공유되는 이점도 잃는다.

**기각 — 분류기 스키마를 allowlist로 좁히기**: Stage 2의 `CommandClassification` enum을 allowlist에
맞춰 동적으로 축소하는 것도 기술적으로는 가능하다
([llm.py:186-196](../../app/infrastructure/ai/companion/llm.py#L186-L196)의 `model_json_schema()`
자리에 축소 스키마 주입). 그러나 **라벨을 빼면 LLM이 기권하는 게 아니라 남은 라벨 중에서 억지로
고른다.** `follow_player`를 제거하면 `"따라와"`가 `wait`나 `gather_resource`로 자신 있게 오분류된다.
"허용되지 않음"과 "다른 명령임"은 전혀 다른데 스키마 축소는 이 둘을 뭉갠다. 로컬 모델에서는
요청마다 제약 디코딩 문법을 다시 컴파일하는 비용도 붙는다.

> **원칙**: 의도는 전체 라벨로 정확히 분류하고, 허용 여부는 코드에서 거른다. 현재 구조가 이미
> 이렇게 되어 있으며, 라우터 분기는 그 필터를 대사 생성 **앞으로** 당길 뿐이다.

**안전망 불변**: 어느 방식이든
[chat_service.py:124-130](../../app/application/chat_service.py#L124-L130)의 2차 검증(allowlist 밖
명령이 새면 `AIServiceInvalidOutputError`)은 유지한다. 라우터 분기는 UX 개선이지 보안 경계의
대체가 아니다.

---

## 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `app/infrastructure/ai/companion/graph.py` | **신규** — `CompanionState`, `build_companion_graph`, 노드/라우팅 함수, 이동된 상수·헬퍼 |
| `app/infrastructure/ai/companion/service.py` | `_route` 계열 메서드 제거, `generate_chat`이 `self._graph.ainvoke` 호출 |
| `pyproject.toml` / `uv.lock` | `langgraph` 의존성 추가, 락 갱신 |
| `pyproject.toml`(mypy) | 필요 시 `langgraph.*` `ignore_missing_imports` |
| `CLAUDE.md` | Architecture 절 라우팅 서술을 "StateGraph 기반 디스패치"로 갱신 |
| `docs/current/*` | 런타임 흐름 문서에 그래프 구조 반영(구현·검증 후 `current/` 승격) |

**변경 없음**: `llm.py`, `dialogue.py`, `intent.py`, `command_intent.py`, `recipes.py`, `lore.py`,
`facts.py`, `app/application/**`, `app/api/**`. 사실·분류·대사 소유권을 그대로 코드에 남긴다는 것이
이 설계의 핵심이다.

---

## 테스트 전략

- **`tests/test_companion_ai_service.py` — 수정 없이 통과해야 한다.** 발화→(대사, 명령 후보) 매핑이
  회귀 안전망. 그래프 경유로 바뀌어도 결과가 동일해야 한다. **1단계에서 추가한 취소 어휘 테스트
  3종이 여기 포함되어 게이트가 더 촘촘해진 상태다.**
- **`tests/test_llm.py`, `tests/test_dialogue.py` — 무변경.** 공급자·렌더 계층을 안 건드린다.
- **`tests/test_companion_chat_api.py`, `test_companion_chat_integration.py` — 무변경 통과.**
- 신규 `tests/test_companion_graph.py` (선택):
  - `route_by_top` / `route_by_command`가 각 라벨에 대해 올바른 노드 이름을 반환한다(순수 함수 단위).
  - `classify_top`이 예외를 던지는 공급자를 주입해도 `generate_chat`이 `AIServiceUnavailableError`로
    변환한다(경계 보존).
  - 그래프 최종 상태에 `display_text`가 항상 채워진다(모든 터미널 노드가 대사를 보장).

---

## 리스크와 트레이드오프

| 리스크 | 완화 |
|---|---|
| 동작 보존 실패(응답 문자열·명령 후보가 미묘하게 달라짐) | 기존 테스트 무변경 통과를 게이트로 사용. 노드는 로직 이식만 하고 새 판단 금지 |
| mypy strict가 langgraph 타입과 충돌 | 라이브러리 경계만 `ignore_missing_imports`, 우리 노드 시그니처는 명시적 타입 유지 |
| 신규 의존성(`langgraph` + 전이 `langchain-core`)의 무게 | LLM 래퍼는 미사용, 오케스트레이션만 사용. `uv lock --check`로 재현성 고정 |
| 단순 5-way 분기에는 과한 추상화 | 향후 clarification·gather 루프 확장을 전제로 정당화(위 "향후 확장") |

---

## 검증 (구현 단계에서 실행)

```powershell
uv add langgraph
uv lock --check
uv run pytest                 # 기존 companion 테스트 전부 무변경 통과 = 동작 보존
uv run ruff check .
uv run mypy app               # 노드/상태 타입, 그래프 경계 확인
```

동작 보존 수동 확인(mock 공급자 기준, 결정론):

```powershell
uv run uvicorn app.main:app --reload
```

1. `"따라와"` → `command_candidates`에 `Command.Follow`, 대사 정상
2. `"여기서 기다려"` → `Command.HoldPosition`
3. `"그만"` → `Command.CancelCurrent`
4. `"나무를 모아 줘"` → 명령 후보 없음, gather 대사(현행과 동일)
5. `"철 도끼 어떻게 만들어?"` → recipe 대사, 명령 후보 없음
6. 리팩토링 전/후 동일 입력에 대해 `display_text`·`command_candidates`가 **동일**한지 대조

---

## 단계별 진행 (구현 승인 후)

0. **선행**: [1단계](cancel-command-consolidation-plan.md) 완료 및 `uv run pytest` 통과 확인.
   1단계가 끝나지 않았다면 여기서 멈춘다 — 동작 보존 게이트의 기준선이 확정되지 않은 상태다.
1. `langgraph` 추가 + `uv lock`, mypy override(필요 시).
2. `graph.py` 신설 — 상태/노드/라우팅/빌더. 상수·헬퍼 이동.
3. `service.py`를 그래프 어댑터로 축소. `generate_chat`만 남기고 `_route` 계열 제거.
4. `uv run pytest`로 **기존 테스트 무변경 통과** 확인(동작 보존 게이트).
5. `ruff` / `mypy` 통과.
6. `CLAUDE.md`·`docs/current/*` 서술 갱신, 이 계획을 `archive/`로 이동.
