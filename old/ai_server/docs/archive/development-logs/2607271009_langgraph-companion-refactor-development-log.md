# 2607271009 마코 라우팅 LangGraph StateGraph 리팩토링 개발 기록

- 기록일: 2026-07-27 10:09
- 기록 유형: 동작 보존(behavior-preserving) 리팩토링 완료 기록
- 변경 범위: `_route` 계열 조건 분기를 `graph.py`의 LangGraph `StateGraph`로 이관,
  `service.py`를 그래프 어댑터로 축소, 그래프 단위 테스트 추가, 계획 문서 아카이브
- 구현 기준: `docs/archive/langgraph-companion-refactor-plan.md` (2단계)
- 선행 단계: `docs/archive/cancel-command-consolidation-plan.md` (1단계, 완료 상태에서 착수)
- 기준 커밋: `9d76fa6`
- API/스키마 버전: 공개 `POST /api/v1/chat`, 공용 `CommandType`, `AIService` Protocol 계약 변경 없음
- 후속 범위: allowlist 기반 동적 라우팅(계획서 "향후 확장" 절, 이번 범위 제외)

## 1. 완료 상태 요약

마코 두뇌의 라우팅을 `CompanionAIService._route`의 조건 분기 파이프라인에서 LangGraph
`StateGraph`로 옮겼다. 흐름 자체는 이전부터 이미 "노드 + 조건부 엣지" 형태였으므로,
이번 작업은 그 구조를 명시적인 그래프 자료구조로 드러낸 것이다.

**LLM 호출 계층과 사실 소유권은 건드리지 않았다.** `LLMProvider` 인터페이스, OpenAI
Responses API의 strict JSON schema 구조화 출력, 모든 공급자 실패를 조용히 mock으로 흡수하는
폴백 규칙, 저장소·파서·`dialogue.render`의 사실 소유권이 전부 그대로다. LangChain의 채팅
모델 래퍼(`ChatOpenAI` 등)는 도입하지 않았다.

동작 보존 게이트를 두 겹으로 확인했다.

1. 기존 146개 테스트가 **수정 없이** 통과했다.
2. mock 공급자 기준 20개 발화에 대해 리팩토링 전후의 `display_text`와 명령 후보 타입을
   스냅샷으로 떠서 대조했고, **완전히 동일**했다.

전체 167개 테스트(기존 146 + 신규 21)가 통과했으며 `ruff check .`, `mypy app`,
`uv lock --check`가 모두 깨끗하다.

## 2. 설계 결정

### LangGraph만 도입하고 LLM 추상화는 교체하지 않음

`LLMProvider`를 `ChatOpenAI` 등 LangChain 래퍼로 바꾸면 다음이 전부 깨진다.

- Responses API + strict JSON schema 구조화 출력
- 공급자 실패를 mock으로 흡수해 **AI 장애가 요청 전체를 실패시키지 않는다**는 불변식
- mypy strict + pydantic 계약(`AIServiceRequest` / `AIServiceResult`)

LangGraph는 순수 파이썬 async 함수를 노드로 쓸 수 있으므로 오케스트레이션만 교체하고 위
불변식은 그대로 보존했다. `langchain-core`는 전이 의존성으로만 존재하며 직접 import하지
않는다.

### 의존성 주입은 클로저로

노드에 저장소·공급자를 넘기는 방법으로 `RunnableConfig["configurable"]` 대신
**빌더 함수 + 클로저**를 택했다. `RunnableConfig` 경유는 값이 `Any`로 흐려져 mypy strict의
이점을 잃는다. `build_companion_graph(llm, recipes, lore, *, command_ttl_seconds)`가
의존성을 캡처한 노드를 정의하고 컴파일된 그래프를 반환한다.

### 라우팅 함수의 반환 타입을 `Literal`로 고정

조건부 엣지 함수의 반환 타입을 `str`이 아니라 목적지 이름의 `Literal` 유니온으로 선언했다.

```python
TopRoute = Literal["command_classify", "recipe", "lore", "conversation", "unsupported"]
CommandRoute = Literal["movement_command", "gather", "unsupported"]
```

두 가지 이득이 있다.

- mypy가 오타난 목적지 이름을 잡는다.
- `add_conditional_edges`에 `path_map`을 따로 주지 않아도 LangGraph가 타입 힌트에서 도달
  가능한 노드를 추론한다. 추론이 안 되면 모든 노드로 엣지를 그어 다이어그램이 무의미해진다.

### 상태와 부분 갱신을 별도 TypedDict로 분리

`CompanionState`는 `request`·`text`를 필수로, 라우팅 중간값과 출력 누산기를 `NotRequired`로
갖는다. 노드 반환값에는 `total=False`인 별도 `CompanionUpdate`를 썼다.

노드는 전체 상태가 아니라 자기가 채운 필드만 반환하는데, 반환 타입을 `CompanionState`로
적으면 필수 필드 누락으로 타입 오류가 난다. `dict[str, Any]`로 뭉개는 대신 갱신 전용 타입을
따로 두어 `# type: ignore` 없이 strict를 통과했다.

### 그래프는 `__init__`에서 한 번만 컴파일

컴파일된 그래프는 불변이라 동시 요청에 안전하게 공유된다. 요청마다 `.compile()`하는 방식은
채택하지 않았다. `allowed_commands` 조합이 최대 128가지지만 **노드 집합은 어차피 동일하고
목적지만 달라지므로** 위상을 바꿔 얻는 것이 없고, 컴파일 비용과 캐싱 부담만 는다.

## 3. 구현 내용

### 노드 구성

`_route` / `_handle_command` / `_gather_dialogue`의 분기를 8개 노드로 전개했다. 각 노드는
기존 로직을 **그대로 이식**했고 새 판단 로직은 넣지 않았다.

| 노드 | 이식 출처 | 역할 |
|---|---|---|
| `classify_top` | `_route` 진입부 | `llm.classify_top` |
| `command_classify` | `_handle_command` 진입부 | `llm.classify_command` |
| `movement_command` | `_handle_command` + `_build_command` | `_COMMANDS` 조회, allowlist 검사, 후보 생성 |
| `gather` | `_gather_dialogue` 전체 | `resolve_gather`로 wood/stone/ambiguous/미지원 |
| `recipe` | `_route` RECIPE 분기 | `recipes.fact_for` (성공/미발견 내부 분기) |
| `lore` | `_route` LORE 분기 | `lore.fact_for` (성공/미발견 내부 분기) |
| `conversation` | `_route` CONVERSATION 분기 | 인사/감사 폴백 |
| `unsupported` | `_route` fallthrough + `_handle_command` else | `_UNSUPPORTED_FALLBACK` |

`classify_top`과 `command_classify`를 제외한 6개가 터미널 노드이며 모두 `END`로 이어진다.
recipe/lore의 "미발견", gather의 "미지원"은 `unsupported` **장면**을 쓰지만 사실과 폴백
대사가 다르므로 각 노드 내부 분기로 남겼다. 공용 `unsupported` 노드로 합치지 않았다.

대사는 전부 기존 `dialogue.render(llm, DialogueSpec(...))`를 경유한다. 노드 안에서
`sanitize` 가드와 폴백 경계가 그대로 작동하므로 실패 흡수 불변식이 보존된다.

### `service.py` 축소

221줄에서 61줄로 줄었다. `_route`, `_handle_command`, `_gather_dialogue`, `_build_command`,
`_say`가 모두 제거되고 `generate_chat` 하나만 남았다.

```python
final = await self._graph.ainvoke(
    {"request": request, "text": request.user_message}
)
```

`AIServiceUnavailableError` 변환 경계는 `generate_chat`에 그대로 남겼다. 모듈 상수
`_COMMANDS`, `_UNSUPPORTED_FALLBACK`과 `_location_id` 헬퍼는 노드가 쓰므로 `graph.py`로
옮겼다.

명령 후보가 없을 때 `command` 키 자체가 상태에 없으므로 `final.get("command")`로 읽는다.
`display_text`는 모든 터미널 노드가 채우므로 `final["display_text"]`로 직접 읽는다.

### 의존성 배선

`app/api/dependencies/ai.py`의 `CompanionAIService(build_llm_provider(...), ...)` 호출은
그대로다. 그래프 구성이 `__init__` 내부에서 일어나므로 DI·포트·API 관점에서 변화가 없다.

## 4. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/infrastructure/ai/companion/graph.py` | **신규 261줄** — `CompanionState`/`CompanionUpdate`, `build_companion_graph`, 8개 노드, 라우팅 함수, 이동된 상수·헬퍼 |
| `app/infrastructure/ai/companion/service.py` | `_route` 계열 제거, `generate_chat`이 `self._graph.ainvoke` 호출 (221 → 61줄) |
| `tests/test_companion_graph.py` | **신규 131줄** — 라우팅 함수 단위 테스트, 터미널 노드 대사 보장, 예외 변환 경계 |
| `pyproject.toml` / `uv.lock` | `langgraph>=1.2.9` 추가, 락 갱신 |
| `CLAUDE.md` | Architecture 절에 StateGraph 기반 디스패치 서술 추가, `graph.py` 책임 항목 추가 |
| `docs/plans/README.md` / `docs/archive/README.md` | 계획 완료·아카이브 반영 |
| `docs/plans/langgraph-companion-refactor-plan.md` | `docs/archive/`로 이동 |

**변경 없음**: `llm.py`, `dialogue.py`, `intent.py`, `command_intent.py`, `recipes.py`,
`lore.py`, `facts.py`, `app/application/**`, `app/api/**`, `Contracts/`, DB 스키마.
사실·분류·대사 소유권을 그대로 코드에 남긴다는 것이 이 설계의 핵심이다.

### 계획서와 달라진 점

- **mypy override 불필요.** 계획서는 `[[tool.mypy.overrides]] module = "langgraph.*"`에
  `ignore_missing_imports`가 필요할 수 있다고 봤으나, langgraph 1.2.9는 `py.typed`를
  포함하고 strict 모드가 그대로 통과한다. `pyproject.toml`의 mypy 설정은 손대지 않았다.
- **버전 하한**이 계획서의 `>=0.2`가 아니라 실제 해석 결과인 `>=1.2.9`다.
- **`docs/current/*`는 갱신하지 않았다.** 계획서 6단계에 포함돼 있었으나 해당 문서들은
  선행 독립형 `/v1/companion/*` 계약을 설명하는 레거시 문서이고 이미 경고 배너를 달고 있다.
  현행 흐름을 그 문서에 써넣으면 레거시 문서를 현행 계약처럼 보이게 만든다. 대신
  `CLAUDE.md`의 Architecture 절을 갱신했다.

## 5. 검증 결과

```text
uv run pytest
167 passed          # 기존 146 무변경 통과 + 신규 21

uv run ruff check .
All checks passed!

uv run mypy app
Success: no issues found in 56 source files

uv lock --check
Resolved 73 packages
```

### 동작 보존 스냅샷 대조

기존 테스트 통과만으로는 대사 문구까지 고정되지 않으므로, mock 공급자(결정론적) 기준으로
20개 발화의 `display_text`와 명령 후보 타입을 JSON 스냅샷으로 떴다. 이후 리팩토링 전
`service.py`를 git에서 되돌리고 `graph.py`를 치운 상태로 같은 스크립트를 다시 실행해
대조했다.

대조 대상 발화는 이동 명령 3종, 취소 어휘 3종, allowlist 거부 2종, 채집
wood/stone/ambiguous/미지원 4종, 제작법 적중·미적중 2종, 세계관 적중·미적중·컨텍스트 없음
3종, 대화 2종, unknown 1종이다.

```text
diff before.json after.json
차이 없음
```

`command_id`와 타임스탬프는 본질적으로 매 호출 달라지므로 대조에서 제외하고 명령 **타입**만
비교했다. `memory_candidates` 길이(항상 0)도 함께 확인했다.

### 신규 테스트

`tests/test_companion_graph.py`에 21개 케이스를 추가했다.

1. `route_by_top`이 5개 `TopIntent` 각각에 대해 올바른 목적지를 반환한다.
2. `route_by_command`가 5개 `CommandLabel` 각각에 대해 올바른 목적지를 반환한다.
3. 10개 대표 발화에 대해 그래프 최종 상태에 `display_text`가 항상 채워진다
   (모든 터미널 노드가 대사를 보장한다는 불변식).
4. 분류 단계에서 예외를 던지는 공급자를 주입해도 `generate_chat`이
   `AIServiceUnavailableError`로 변환한다(경계 보존).

라우팅 함수가 순수 함수라 그래프를 띄우지 않고 상태 dict만 만들어 단위로 검증한다.

## 6. 후속 작업

이번 범위에서 제외한 확장은 계획서 "향후 확장" 절에 정리돼 있으며, 현재 구조가 이를
수용하도록 열어 두었다.

- **allowlist 기반 동적 라우팅** — 가장 가깝다. 현재는 allowlist 검사가 대사 생성 **이후**
  `movement_command` 노드 안에서 일어나므로, 명령이 방출되지 않아도 대사는 `"알겠어.
  따라갈게."`라고 말한다. 검사를 `route_by_command`로 당기면 `"지금은 그건 못 하겠어."` 같은
  정직한 대사가 가능하다. `CompanionState`가 `request`를 통째로 들고 있고 조건부 엣지 함수는
  매 실행마다 평가되므로 **그래프를 다시 만들 필요가 없다.** 이번에 넣지 않은 이유는 대사
  문구가 바뀌어 동작 보존 게이트를 깨기 때문이다.
- **다중턴 clarification 메모리** — `graph.compile(checkpointer=...)` + `thread_id`로
  `gather_ambiguous` 뒤의 `나무`/`돌` 응답을 이어받는 흐름.
- **채집 실행 루프 / 이벤트 처리** — gather → 작업 상태 조회 → 재질의의 순환 엣지.
- **memory candidate 추출** — 터미널 이후 병렬 노드.

**기각된 대안**: Stage 2의 `CommandClassification` enum을 allowlist에 맞춰 동적으로
축소하는 방식은 채택하지 않는다. 라벨을 빼면 LLM이 기권하지 않고 남은 라벨 중에서 억지로
고르기 때문이다. `follow_player`를 제거하면 `"따라와"`가 `wait`로 자신 있게 오분류된다.
**의도는 전체 라벨로 정확히 분류하고, 허용 여부는 코드에서 거른다.**

어느 방식이든 `app/application/chat_service.py`의 2차 검증(allowlist 밖 명령이 새면
`AIServiceInvalidOutputError`)은 유지한다. 라우터 분기는 UX 개선이지 보안 경계의 대체가
아니다.
