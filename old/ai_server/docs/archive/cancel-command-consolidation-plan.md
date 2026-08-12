# CANCEL 라벨 통합과 명령 테이블 대칭화 (1단계)

> **진행 순서**: 이 문서가 **1단계**다. 완료 후
> [langgraph-companion-refactor-plan.md](langgraph-companion-refactor-plan.md)(2단계)를 진행한다.
> 2단계는 **동작 보존 리팩토링**이므로, 동작 변경은 반드시 이 문서에서 먼저 끝내야 한다.

## Context

`CommandLabel.CANCEL`이 현재 **빈 약속(empty promise)** 상태다. 대사는 나가지만 아무 일도
일어나지 않는다.

### 무엇이 문제인가

CANCEL은 두 딕셔너리에 **비대칭적으로** 등록돼 있다:

- [service.py:44](../../app/infrastructure/ai/companion/service.py#L44) — `_COMMAND_SCENE`에 **있음**
  → `"알겠어. 요청을 취소할게."` 대사가 나감
- [service.py:34-38](../../app/infrastructure/ai/companion/service.py#L34-L38) — `_COMMAND_TYPE_MAP`에 **없음**
  → [`_build_command`](../../app/infrastructure/ai/companion/service.py#L185-L187)가 `None` 반환

결과적으로 `"됐어"` / `"취소"` / `"나중에 하자"`를 말하면 마코는 취소하겠다고 대답하지만
**클라이언트는 아무 명령도 받지 못하고, 서버에도 취소할 상태가 없다.** 완전한 no-op이다.

`_handle_command`는 대사를 만든 뒤 명령을 따로 만들며, 대사 생성이 명령 생성 결과를 참조하지
않는다([service.py:143-144](../../app/infrastructure/ai/companion/service.py#L143-L144)). 그래서
명령이 `None`이 되어도 대사는 그대로 "하겠다"고 말한다.

### 왜 생겼나

[service.py:5](../../app/infrastructure/ai/companion/service.py#L5)에 명시돼 있듯 **재질의
(clarification)가 이번 범위에서 제외**됐다. CANCEL은 원래 `"무엇을 캐면 될까?"` 재질의에
`"됐어"`로 답하면 pending 상태를 지우는 라벨이었다. 재질의 상태가 사라지면서 **취소할 대상
자체가 없어진 잔재**다.

### 왜 위험한가

`STOP_CURRENT_TASK`와 플레이어 의미가 충돌한다:

| 발화 | 라벨 | 실제 결과 |
|---|---|---|
| `"그만"` | `STOP_CURRENT_TASK` | `Command.CancelCurrent` 방출 ✅ |
| `"됐어"` / `"취소"` | `CANCEL` | 아무것도 안 함 ❌ |

마코가 따라오는 중에 `"됐어"`라고 하면 플레이어 의도는 "그만 따라와"인데, 마코는 취소했다고
말하고 계속 따라온다.

**테스트 공백이 이를 은폐하고 있다.** [test_llm.py:155](../../tests/test_llm.py#L155)는
`"취소" → CommandLabel.CANCEL` 분류만 검증하고, **서비스 레벨에서 CANCEL이 무엇을 내놓는지는
테스트가 전혀 없다.**

> **allowlist 미포함 케이스와 혼동하지 말 것.** `allowed_commands`에 없어 명령이 `None`이 되는
> 것은 [test_companion_ai_service.py:74-81](../../tests/test_companion_ai_service.py#L74-L81)이
> **의도된 동작으로 명시 고정**해 둔 상태이며, 이 문서는 그것을 바꾸지 않는다. 결정적 차이:
> allowlist 문제는 클라이언트가 해당 명령을 허용하면 해결되지만, CANCEL은 클라이언트가
> `Command.CancelCurrent`를 허용해도 **서버가 구조적으로 명령을 만들 수 없다.**

---

## 설계 결정: 라벨을 하나로 합친다

**`CommandLabel.CANCEL`을 제거하고 `STOP_CURRENT_TASK`로 통합한다.**

### 근거

1. **공용 계약에 cancel 계열 `CommandType`이 하나뿐이다.**
   [ai.py:61-68](../../app/application/models/ai.py#L61-L68)에 `Command.CancelCurrent` 단 하나.
   라벨 두 개가 같은 곳으로 수렴하는데 분류 단계에서 나눌 이유가 없다.

2. **CANCEL의 고유 작용 대상이 사라졌다.** 원래 구분은 작용 대상이 달라서 성립했다 —
   `STOP_CURRENT_TASK`는 **클라이언트의 작업 상태**(`active_task`)에, `CANCEL`은 **서버의 대화
   상태**(pending 재질의)에 작용했다. 재질의가 빠지면서 후자가 없어졌다.

3. **(핵심) 둘의 구분은 발화가 아니라 서버 상태로 결정되어야 한다.**

   | 상황 | `"됐어"`의 의미 |
   |---|---|
   | 마코가 `"무엇을 캐면 될까?"` 물은 직후 | 재질의 취소 (세계에 영향 없음) |
   | 마코가 나무 캐는 중 | 작업 중지 (`Command.CancelCurrent`) |

   같은 단어인데 의미가 다르다. LLM이 발화만 보고 맞히는 건 원리적으로 불가능하고, 서버는
   pending 여부를 **이미 확실히 안다.** 분류기에게 시킬 일이 아니다.

   실제로 [llm.py:71](../../app/infrastructure/ai/companion/llm.py#L71)의
   `classify_top(text, *, clarification_pending: bool)` 시그니처가 이 방향을 암시한다 —
   pending 플래그를 분류기에 넘기게 되어 있으나 현재는 항상 `False`로 전달된다
   ([service.py:95](../../app/infrastructure/ai/companion/service.py#L95)).

### 재질의가 복귀해도 라벨을 다시 늘리지 않는다

라벨은 하나로 두고 **서버 상태로 분기**한다:

```python
if label is CommandLabel.STOP_CURRENT_TASK:
    if pending_clarification:        # ← 발화가 아니라 상태로 분기
        clear_pending()
        scene, command = "cancel", None        # 어조만 다르고 세계엔 영향 없음
    else:
        scene, command = "stop_current_task", build(CANCEL_CURRENT)
```

그래서 **`DialogueScene`의 `"cancel"`은 제거하지 않고 남긴다.** 이 저장소엔 이미 같은 관행이
있다 — [dialogue.py:22-23](../../app/infrastructure/ai/companion/dialogue.py#L22-L23)의
`event_completed` / `event_failed`도 이벤트가 범위 밖이라 미사용 상태로 유지돼 있다.

### 기각한 대안

| 대안 | 기각 사유 |
|---|---|
| `_COMMAND_TYPE_MAP`에 `CANCEL: CANCEL_CURRENT` 한 줄 추가 | 최소 변경이지만 같은 `CommandType`으로 가는 라벨 2개가 남아, 분류기가 무의미한 구분을 계속한다 |
| CANCEL을 `unsupported`로 보냄 | `"지금은 취소할 요청이 없어"`는 정직하지만, 작업 중지 의도였을 때 불친절하다 |
| 현행 유지, 재질의 복귀까지 보류 | 그때까지 `"됐어"`가 계속 no-op으로 남는다 |

---

## 변경 사항

### 1. `intent.py` — 라벨 제거

[intent.py:31](../../app/infrastructure/ai/companion/intent.py#L31)의 `CANCEL = "cancel"` 삭제.
`CommandClassification`은 이 enum을 그대로 쓰므로 **구조화 출력 스키마에서도 자동으로 빠진다**
(실제 LLM이 더 이상 `cancel`을 반환할 수 없게 된다).

### 2. `command_intent.py` — `CANCEL_PATTERN`을 `_STOP`에 흡수

[command_intent.py:9](../../app/infrastructure/ai/companion/command_intent.py#L9)의 모듈 상수
`CANCEL_PATTERN`을 삭제하고, 그 어휘를
[`_STOP`](../../app/infrastructure/ai/companion/command_intent.py#L29)에 합친다:

```python
_STOP = re.compile(
    r"(?:이제 |작업 |하던 거 )?(?:멈춰|그만|중지해)(?: 줘| 주세요)?"
    r"|됐어|취소|나중에 하자"
)
```

`classify_simple_command`가 `_STOP.fullmatch(normalized)`로 쓰므로 `A|B` 교대는 "전체가 A 또는
전체가 B"로 동작한다. 기존 `CANCEL_PATTERN`도 `fullmatch`로만 쓰였으므로 매칭 범위가 동일하다.

### 3. `llm.py` — Mock 분기와 프롬프트 정리

- [llm.py:9-15](../../app/infrastructure/ai/companion/llm.py#L9-L15) — import에서 `CANCEL_PATTERN` 제거
- [llm.py:97-101](../../app/infrastructure/ai/companion/llm.py#L97-L101) `classify_top` —
  `or CANCEL_PATTERN.fullmatch(normalized)` 절 삭제. `"취소"`가 이제 `classify_simple_command`에서
  `STOP_CURRENT_TASK`로 잡히므로 첫 조건이 이를 흡수한다(→ `TopIntent.COMMAND` 유지)
- [llm.py:119-120](../../app/infrastructure/ai/companion/llm.py#L119-L120) `classify_command` —
  `CANCEL` 반환 분기 삭제
- [llm.py:20](../../app/infrastructure/ai/companion/llm.py#L20) `_TOP_ROUTER_PROMPT` —
  `command` 설명의 나열은 `"따라오기, 대기, 작업 중지·취소, 자원 채집 요청"`으로 조정
- [llm.py:32](../../app/infrastructure/ai/companion/llm.py#L32) `_COMMAND_ROUTER_PROMPT` —
  `- cancel: ...` 줄 삭제, `stop_current_task` 설명을
  `"현재 수행 중인 작업을 멈추거나 직전 요청을 취소하라는 명령"`으로 확장

### 4. `service.py` — 명령 테이블 대칭화 (근본 원인 제거)

CANCEL을 빼고 나면 `_COMMAND_SCENE`과 `_COMMAND_TYPE_MAP`의 **키 집합이 정확히 일치한다**
(`FOLLOW_PLAYER`, `WAIT`, `STOP_CURRENT_TASK`). 두 테이블이 갈라져 있었다는 것이 이 버그의
근본 원인이므로, **하나로 합쳐 비대칭을 구조적으로 불가능하게 만든다:**

```python
# 명령 라벨 → (공용 CommandType, 대사 장면, 폴백 대사)
# 한 테이블에 묶어 "대사는 있는데 명령이 없는" 비대칭을 원천 차단한다.
_COMMANDS: dict[CommandLabel, tuple[CommandType, DialogueScene, str]] = {
    CommandLabel.FOLLOW_PLAYER: (
        CommandType.FOLLOW, "follow_player", "알겠어. 따라갈게."),
    CommandLabel.WAIT: (
        CommandType.HOLD_POSITION, "wait", "알겠어. 여기서 기다릴게."),
    CommandLabel.STOP_CURRENT_TASK: (
        CommandType.CANCEL_CURRENT, "stop_current_task", "알겠어. 지금 하던 일을 멈출게."),
}
```

- `_handle_command`의 `if label in _COMMAND_SCENE` → `if label in _COMMANDS`
- `_build_command`는 `_COMMANDS[label][0]`을 쓰며, **allowlist 검사만 남는다**
  (`command_type is None` 분기는 사라진다 — 매핑 누락이 타입 수준에서 불가능해졌다)

> 이 단계는 선택이 아니라 권장이다. 라벨만 제거하고 테이블 두 개를 남기면 **다음에 새 명령을
> 추가할 때 같은 비대칭이 재발할 수 있다.**

### 5. 대사 상수

`_COMMAND_SCENE`의 `"알겠어. 요청을 취소할게."`는 사라진다. 재질의 복귀 시 `scene="cancel"`과
함께 되살린다(위 "재질의가 복귀해도" 절 참고). `SCENE_GUIDE["cancel"]`은 그대로 둔다.

---

## 테스트 보강

이 변경의 핵심 산출물이다. **현재 서비스 레벨 CANCEL 테스트가 0개**라 no-op이 조용히 유지됐다.

### 수정

| 파일 | 변경 |
|---|---|
| [test_llm.py:155](../../tests/test_llm.py#L155) | `("취소", CommandLabel.CANCEL)` → `("취소", CommandLabel.STOP_CURRENT_TASK)` |

[test_llm.py:131](../../tests/test_llm.py#L131)의 `("취소", TopIntent.COMMAND)`는 **변경 없이 통과해야
한다** — 통합 후에도 `"취소"`는 여전히 command 경로다. 이것이 3번 변경의 회귀 안전망이다.

### 신규 (`tests/test_companion_ai_service.py`)

1. **취소 어휘가 실제로 명령을 방출한다** (핵심 공백 해소):
   ```python
   @pytest.mark.parametrize("text", ["됐어", "취소", "나중에 하자"])
   async def test_cancel_utterances_emit_cancel_current(text: str) -> None:
       service = make_service()
       result = await service.generate_chat(
           make_request(text, allowed_commands=[CommandType.CANCEL_CURRENT])
       )
       assert len(result.command_candidates) == 1
       assert result.command_candidates[0].type is CommandType.CANCEL_CURRENT
   ```
2. **`"그만"`과 `"됐어"`가 동일한 `CommandType`을 낸다** — 통합 의도를 고정하는 회귀 테스트.
3. **취소 어휘도 allowlist를 존중한다** — `allowed_commands=[]`이면 대사만 나가고
   `command_candidates == []`(기존 allowlist 동작과 일관).

### 추가 안전망

[test_llm.py:145-160](../../tests/test_llm.py#L145-L160)의 `classify_command` 파라미터 표에
`"됐어"`, `"나중에 하자"`를 `STOP_CURRENT_TASK` 기대값으로 추가한다(정규식 흡수 검증).

---

## 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `app/infrastructure/ai/companion/intent.py` | `CommandLabel.CANCEL` 제거 |
| `app/infrastructure/ai/companion/command_intent.py` | `CANCEL_PATTERN` 삭제, `_STOP`에 어휘 흡수 |
| `app/infrastructure/ai/companion/llm.py` | import·Mock 분기 2곳 정리, 라우터 프롬프트 2곳 수정 |
| `app/infrastructure/ai/companion/service.py` | `_COMMAND_SCENE`+`_COMMAND_TYPE_MAP` → `_COMMANDS` 단일 테이블, `_build_command` 단순화 |
| `tests/test_llm.py` | `"취소"` 기대값 수정, `"됐어"`/`"나중에 하자"` 케이스 추가 |
| `tests/test_companion_ai_service.py` | 취소 어휘 명령 방출 테스트 신규 3종 |
| `CLAUDE.md` | `CANCEL_PATTERN` 언급과 `CommandLabel` 목록에서 cancel 제거 |

**변경 없음**: `dialogue.py`(`"cancel"` scene은 재질의용으로 보존), `recipes.py`, `lore.py`,
`facts.py`, `app/application/**`, `app/api/**`. 공용 계약(`CommandType`, `AIServiceResult`)은
손대지 않는다.

---

## 검증

```powershell
uv run pytest                 # 신규 취소 테스트 통과 + 기존 테스트 회귀 없음
uv run ruff check .
uv run mypy app               # CommandLabel.CANCEL 잔존 참조를 타입 수준에서 검출
```

`mypy`가 이 변경의 주요 안전망이다 — enum 멤버를 제거하면 남은 참조가 전부 타입 오류로 드러난다.

수동 확인(mock 공급자, 결정론):

1. `"됐어"` → `command_candidates`에 `Command.CancelCurrent`, 대사는 `"알겠어. 지금 하던 일을 멈출게."`
2. `"그만"` → 위와 **동일한 결과**
3. `"따라와"` / `"여기서 기다려"` → 기존과 동일(회귀 없음)

---

## 이 변경이 2단계(LangGraph)에 주는 이득

[langgraph-companion-refactor-plan.md](langgraph-companion-refactor-plan.md)의 `route_by_command`가
단순해진다. 통합 전에는 `_COMMAND_SCENE`에는 있고 `_COMMAND_TYPE_MAP`에는 없는 CANCEL 때문에
"대사는 있고 명령은 없는" 특수 케이스를 그래프에 그대로 옮겨야 했다. 통합 후에는 단일
`_COMMANDS` 테이블 하나로 분기가 끝나므로 **그 주의사항이 통째로 사라진다.**

또한 2단계가 "기존 테스트 무변경 통과"를 동작 보존 게이트로 쓰는데, 이 문서에서 추가한 취소
테스트가 그 안전망에 포함되어 **게이트가 더 촘촘해진다.**
