# LLM으로 대체된 정규식 파이프라인 제거 계획

## 배경

2단계 LLM 라우터(Stage 1 `classify_top`, Stage 2 `classify_command`)가 도입되면서, 기존 정규식
의도 파서가 담당하던 **명령 분류** 역할은 프로덕션 경로에서 LLM으로 완전히 대체되었다. 그러나
`command_intent.py`의 옛 자료구조(`CommandIntent`, `GatherResourceIntent`, `parse()`)는 그대로
남아 있고, `service.py`는 LLM이 이미 결정한 `CommandLabel`을 다시 `CommandIntent`로 변환한 뒤
`_command_response`에서 또 한 번 분기하는 왕복 경로를 유지하고 있다. 이 왕복은 순수한 잔재이며,
`_resolve_gather`가 호출하는 `parse()`의 follow/wait/stop 분기는 그 호출 지점에서 도달 불가능한
죽은 코드다.

목표는 LLM이 대체한 정규식 경로만 제거하고, 여전히 설계상 필요한 두 가지는 손상 없이 유지하는
것이다. 공개 동작은 바뀌지 않으며 기존 테스트는 수정 없이 그대로 통과해야 한다.

### 유지 결정

- `MockLLMProvider`의 Stage 1/2 정규식 분류: **유지**. Mock은 기본 공급자이자 실제 공급자 실패 시
  폴백이고, `tests/test_api.py` 전체가 Mock 위에서 돌아간다.
- Stage 3 채집 자원 해소의 결정론적 정규식: **유지**. `current/03_runtime_flow.md`가 명시한 설계로,
  자원 종류를 LLM이 지어내지 못하게 막는 장치다.

## 제거 대상

| 대상 | 위치 | 제거 근거 |
|---|---|---|
| `CommandIntent` StrEnum | `command_intent.py:20-25` | `intent.CommandLabel`과 완전 중복 |
| `GatherResourceIntent` 데이터클래스 | `command_intent.py:28-32` | 단일 필드 래퍼. 두 호출 지점 모두 즉시 언랩 |
| `_SIMPLE_COMMANDS` 매핑 | `service.py:37-42` | `CommandLabel` → `CommandIntent` → 재분기 왕복 |
| `_command_response`의 `isinstance`/`CommandIntent` 분기 | `service.py:156-183` | LLM 라벨로 직접 디스패치 가능 |
| `parse()`의 follow/wait/stop 분기 (Stage 3 호출 시) | `command_intent.py:51-56` | `_resolve_gather`에서 도달 불가 |
| `_resolve_gather`의 중복 `normalize()` 3회 + 수량 재검사 | `service.py:119-144` | 같은 문자열을 세 번 정규화, 수량 검사 2회 |
| `is_ambiguous_gather`의 `has_no_resource` 검사 | `command_intent.py:74-77` | 단일 패스에서는 선행 return이 이미 배제 |
| `CANCEL_PATTERN`의 `그만` 대안 + 잉여 `$` | `command_intent.py:9` | `_STOP`이 항상 먼저 매치해 도달 불가. `fullmatch`와 `$` 중복 |

## 구현

### 1. `command_intent.py` — 두 역할로 정리

`CommandIntentParser` 클래스명은 유지한다. `CLAUDE.md`, `AGENTS.md`, `docs/`가 다수 참조하므로
개명은 불필요한 문서 변경을 만든다. 내부만 재구성한다.

**제거**: `CommandIntent`, `GatherResourceIntent`, `parse()`, `is_ambiguous_gather()`,
`is_unsupported_gather()`, 그리고 `from enum import StrEnum` / `from dataclasses import dataclass`.

**신규 (a) — Stage 3 전용, 프로덕션 경로**

```python
# 채집 발화가 아니면 None, 그 외에는 해소 결과를 반환한다.
GatherResolution = Literal["wood", "stone", "ambiguous", "unsupported"]

@classmethod
def resolve_gather(cls, text: str) -> GatherResolution | None:
    normalized = cls.normalize(text)
    if cls._GATHER_VERB.search(normalized) is None:
        return None
    # 수량 지정과 일부 자원은 아직 서버가 지원하지 않는다.
    if cls._QUANTITY.search(normalized) or cls._UNSUPPORTED_RESOURCE.search(normalized):
        return "unsupported"
    if "나무" in normalized or "목재" in normalized:
        return "wood"
    if "돌" in normalized:
        return "stone"
    if cls._AMBIGUOUS_REFERENCE.search(normalized) or cls._BARE_GATHER.fullmatch(normalized):
        return "ambiguous"
    return None
```

순서가 중요하다. `부싯돌`은 `"돌" in normalized`를 만족하므로 미지원 검사가 반드시 먼저 와야 한다
(현행 `parse()`와 동일한 순서).

**신규 (b) — Mock/폴백 전용**

`parse()`의 follow/wait/stop 분기를 `intent.CommandLabel`을 직접 반환하도록 옮긴다. 이것으로
중복 enum이 사라진다.

```python
@classmethod
def classify_simple_command(cls, text: str) -> CommandLabel | None:
    """자원 인자가 필요 없는 명령을 판별한다. Mock 공급자와 폴백 전용이다."""
```

`command_intent.py` → `intent.py` 임포트가 새로 생기지만 `intent.py`는 로컬 임포트가 없으므로
순환은 없다. `normalize()`는 `service._resolve_clarification`이 사용하므로 그대로 공개 유지한다.

### 2. `service.py` — LLM 라벨로 직접 디스패치

- 임포트를 `from .command_intent import CommandIntentParser` 하나로 축소.
- `_SIMPLE_COMMANDS` 딕셔너리 삭제.
- `_handle_command`에서 `CommandLabel`로 직접 분기해 대사와 Action을 만든다. **동작 보존 주의**:
  현행은 단순 명령과 취소에서만 `self._pending.clear()`를 호출하고 `UNKNOWN`에서는 호출하지
  않는다. 또 `STOP_CURRENT_TASK`는 `active_task`가 없어 미지원으로 끝나더라도 clear는 이미
  수행된 상태다. 이 순서를 유지할 것.
- `_command_response`는 `CommandIntent`/`GatherResourceIntent`를 받지 않게 되므로 삭제하고,
  stop 전용 헬퍼(`active_task` 확인 + `StopAction`)만 남긴다. `_gather_response`는 그대로 재사용.
- `_resolve_gather`는 단일 호출로 축소:

```python
def _resolve_gather(self, request: MessageRequest) -> MessageResponse:
    resolution = CommandIntentParser.resolve_gather(request.text)
    if resolution == "wood" or resolution == "stone":
        self._pending.clear()
        return self._gather_response(request.request_id, resolution)
    if resolution == "unsupported":
        return self._unsupported(
            request, "지금은 수량 지정 없이 일반 나무나 돌 채집만 도와줄 수 있어."
        )
    if resolution == "ambiguous":
        ...  # 기존 clarification 발급 로직 그대로
    return self._unsupported(request, _UNSUPPORTED_FALLBACK)
```

`==` 비교로 `Literal` 유니온이 좁혀지므로 `_gather_response`의 `Literal["wood", "stone"]`
시그니처에 `cast` 없이 통과한다 (MyPy strict).

### 3. `llm.py` — `MockLLMProvider` 위임 갱신

임포트에서 `CommandIntent`, `GatherResourceIntent` 제거. 두 분류기를 새 API로 다시 쓴다.

```python
async def classify_top(self, text, *, clarification_pending):
    del clarification_pending
    normalized = CommandIntentParser.normalize(text)
    if (
        CommandIntentParser.classify_simple_command(text) is not None
        or CommandIntentParser.resolve_gather(text) is not None
        or CANCEL_PATTERN.fullmatch(normalized)
    ):
        return TopIntent.COMMAND
    ...  # RECIPE / LORE / CONVERSATION 분기는 그대로

async def classify_command(self, text):
    label = CommandIntentParser.classify_simple_command(text)
    if label is not None:
        return label
    if CommandIntentParser.resolve_gather(text) is not None:
        return CommandLabel.GATHER_RESOURCE
    if CANCEL_PATTERN.fullmatch(CommandIntentParser.normalize(text)):
        return CommandLabel.CANCEL
    return CommandLabel.UNKNOWN
```

우선순위(단순 명령 → 채집 → 취소 → unknown)는 현행과 동일하게 유지한다.

### 4. 문서 갱신

- `CLAUDE.md` Architecture의 `command_intent.py` 항목: `CommandIntentParser`가 이제 Stage 3
  `resolve_gather`와 Mock 전용 `classify_simple_command` 두 가지만 제공한다는 점, `CommandIntent`와
  `GatherResourceIntent`가 사라졌다는 점을 반영한다.
- `AGENTS.md`의 대응 문장도 동일하게 수정한다.
- `current/03_runtime_flow.md`: 정규식이 이제 Mock/폴백과 Stage 3 두 곳에만 남는다는 점을 한 문장
  수준으로 보강한다. 기존 서술 자체는 여전히 정확하다.
- `current/`의 나머지 문서는 공개 동작 기준이고 동작이 바뀌지 않으므로 수정하지 않는다.

## 검증

공개 동작이 하나도 바뀌지 않는 리팩터링이므로, **기존 테스트 파일을 전혀 수정하지 않고 전부
통과하는 것**이 핵심 수용 기준이다.

```powershell
uv run pytest
uv run ruff check .
uv run mypy src
```

특히 다음이 회귀 없이 통과해야 한다.

- `test_api.py::test_unsupported_gather_variants` — 6개 변형 전부 `UNSUPPORTED_REQUEST`. 그중
  `풀을 캐 줘`는 `resolve_gather`가 `None`을 반환해 `_UNSUPPORTED_FALLBACK` 대사로 가고, 나머지
  5개는 `"unsupported"`로 수량·미지원 자원 대사로 간다 (현행과 동일한 두 갈래).
- `test_api.py::test_cancel_and_new_command_clear_clarification` — `_pending.clear()` 호출 시점을
  바꾸지 않았는지 확인하는 가장 민감한 테스트.
- `test_llm.py::test_mock_provider_classifies_command` — 특히 `그만` → `STOP_CURRENT_TASK`
  (`CANCEL_PATTERN`에서 `그만`을 지워도 `_STOP`이 먼저 매치하므로 유지되어야 함), `취소` → `CANCEL`.
- `test_llm.py::test_local_*_falls_back_on_invalid_output` — 폴백 경로가 새 Mock 구현으로도
  `따라와` → `COMMAND`, `그만` → `STOP_CURRENT_TASK`를 반환하는지 확인.

추가로 `CommandIntentParser.resolve_gather`에 대한 파라미터화 단위 테스트를 `tests/test_service.py`에
덧붙인다 (`나무를 모아 줘`→`wood`, `부싯돌을 캐 줘`→`unsupported`, `저것 좀 캐 줘`→`ambiguous`,
`풀을 캐 줘`→`None`, `따라와`→`None`). 단일 패스 재작성의 등가성을 직접 고정하는 유일한 테스트다.
