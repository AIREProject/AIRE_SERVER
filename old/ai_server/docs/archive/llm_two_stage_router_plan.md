# LLM 2단계 의도 라우터 리팩토링 계획

> **상태: 구현 완료 (2026-07-23).** Stage 1·2·3이 모두 반영되었고 규범적 서술은
> `docs/current/`(특히 `03_runtime_flow.md`)로 승격되었다. 이 문서는 당시 계획을 보존하는
> 아카이브 기록이며 현행 구현 기준이 아니다.

이 문서는 리팩토링 착수 시점의 **설계 계획**이다. 규범적 구현 기준은 `docs/current/`가
유지한다.

## 배경

현재 라우팅은 `service.py`의 `handle_message`가 정규식으로 결정론적으로 처리하고, LLM은
가벼운 대화(`_CONVERSATION`)에만 도달한다. 목표는 **사용자 의도 "분류(routing)"를 LLM이
담당**하도록 바꾸되, 게임 사실 무결성 불변식을 유지하는 것이다.

## 핵심 원칙

> LLM은 **의도 분류만** 담당한다. 대사·행동 생성은 계속 결정론적 템플릿/저장소가 담당한다.

- 레시피·세계관·명령 응답은 고정 템플릿·저장소에서만 나온다(LLM이 게임 사실을 지어내지 않음).
- 정규식 파서(`command_intent.py`)는 삭제하지 않고 **결정론적 폴백 + Mock 백엔드**로 유지한다.
- LLM 호출 실패는 요청 전체를 실패시키지 않는다(정규식 폴백).

## 전체 2단계 라우터 구조

```
사용자 발화
    ↓
[Stage 1] Top Router  ── 항상 1회 호출
    ├─ COMMAND ──────────┐
    ├─ RECIPE            │
    ├─ LORE             (저장소/템플릿/대화로 종료)
    ├─ CONVERSATION      │
    └─ UNKNOWN ──────────┘
                          ↓ (COMMAND일 때만 2번째 호출)
[Stage 2] Command Pipeline
    ├─ FOLLOW_PLAYER
    ├─ WAIT
    ├─ STOP_CURRENT_TASK
    └─ GATHER_RESOURCE ──▶ [Stage 3] 인자 해소(결정론적)
                              ├─ wood / stone → Action
                              ├─ 불명확 → 재질의(clarification)
                              └─ 수량·미지원 자원 → UNSUPPORTED
```

### 확정된 설계 결정

- **호출 방식**: 2회 조건부 호출. Stage 1은 항상, Stage 2는 `COMMAND`일 때만. 대화·레시피·
  세계관은 1회로 종료한다.
- **enum 범위**: Build 1 지원 라벨만 정의한다. `HARVEST/FETCH/CRAFT/COMBAT_*` 등 미구현
  명령과 Stage 1의 `GAME_HELP`는 제외한다. 확장 시 enum·스키마에 라벨을 추가한다.
- **Stage 1 enum**: `COMMAND, RECIPE, LORE, CONVERSATION, UNKNOWN`
- **Stage 2 enum**: `FOLLOW_PLAYER, WAIT, STOP_CURRENT_TASK, GATHER_RESOURCE`

## 구현 순서

1. **Phase 1 — Top Router (Stage 1)** ← 최우선, 본 문서의 상세 계획
2. Phase 2 — Command Pipeline (Stage 2) + 인자 해소(Stage 3)
3. Phase 3 — 문서(`03_runtime_flow.md`/`CLAUDE.md`) 갱신, 정규식 브리지 제거

---

# Phase 1: Top Router 상세 구현 계획

핵심은 **Stage 2가 아직 없으므로 `COMMAND` 라벨을 기존 정규식 명령 로직으로 임시
연결(bridge)**해, 기존 테스트를 100% 유지하며 점진 도입하는 것이다.

## 1. `intent.py` 신설 (내부 분류 타입)

공개 응답 envelope에 내부 정보가 새지 않도록 `domain.py`와 분리한 내부 모듈로 둔다.

```python
class TopIntent(StrEnum):
    COMMAND = "command"
    RECIPE = "recipe"
    LORE = "lore"
    CONVERSATION = "conversation"
    UNKNOWN = "unknown"

class TopClassification(BaseModel):      # 구조화 출력 스키마
    model_config = ConfigDict(extra="forbid")
    intent: TopIntent
```

pydantic이 `intent`를 `{"enum": [...]}`로 생성하므로 로컬 모델 guided decoding용
json_schema로 그대로 쓸 수 있다.

## 2. `llm.py` — 분류 메서드 추가

`LLMProvider` 인터페이스에 추가한다.

```python
async def classify_top(self, text: str, *, clarification_pending: bool) -> TopIntent
```

- **MockLLMProvider**: `CommandIntentParser`와 카테고리 정규식(`_RECIPE/_LORE/_CONVERSATION/
  _CANCEL`)에 위임해 결정론적으로 반환한다. 기존 우선순위를 재현한다.
  1. `parse != None` or `is_ambiguous_gather` or `is_unsupported_gather` or `_CANCEL` → `COMMAND`
  2. `_RECIPE` → `RECIPE`
  3. `_LORE` → `LORE`
  4. `_CONVERSATION` → `CONVERSATION`
  5. 그 외 → `UNKNOWN`
- **LocalLLMProvider**: `chat.completions` + `response_format` json_schema(`TopClassification`),
  `temperature=0`, 작은 `max_tokens`, 카테고리 정의 한국어 프롬프트. 실패/파싱 오류 시
  `MockLLMProvider.classify_top` 폴백.
- **OpenAIProvider**: Responses API + 동일 스키마, 실패 시 동일 폴백.

> mock 분류가 카테고리 정규식을 재사용해야 하므로, service에 있던 `_RECIPE/_LORE/
> _CONVERSATION/_CANCEL` 상수를 `command_intent.py` 등 공용 위치로 옮겨 mock과 service가
> 공유한다.

## 3. `service.py` — Stage 1만 배선

`handle_message`를 재구성한다.

```
1. pending 재질의 결정론적 해소 (기존 그대로) → 되면 반환
2. top = await self._llm.classify_top(text, clarification_pending=<pending 존재?>)
3. dispatch:
   COMMAND      → self._handle_command(request)   # 기존 명령 블록 추출(브리지)
   RECIPE       → 레시피 저장소 조회 (없으면 unsupported)
   LORE         → 세계관 저장소 조회 (없으면 unsupported)
   CONVERSATION → await self._llm.generate_conversation(...)
   UNKNOWN      → clarification_id 있으면 "선택 종료" / 아니면 일반 fallback
```

`_handle_command`는 현재의 `command-parse → cancel → unsupported gather → ambiguous
gather` 순서를 그대로 옮긴 메서드다. Stage 2에서 교체될 임시 자리표시이며, 이를 통해
follow/wait/stop/gather/재질의/취소/미지원 응답이 기존과 동일하게 유지된다.

유효한 pending 답변(`나무`/`돌`)은 1단계에서 결정론적으로 해소되어 반환되므로 `classify_top`
호출을 아끼고, 해소되지 않는 답변만 분류로 흘려 새 명령·취소·"선택 종료"를 판정한다.

## 4. `config.py` — 분류 파라미터 (선택)

로컬/OpenAI 분류 호출용으로 `classify_temperature: float = 0.0`,
`classify_max_tokens: int = 20`을 추가한다. Stage 1만이면 최소 구성이다.

## 5. 테스트

- 기존 `tests/test_api.py` 전부 **무수정 통과**(mock=정규식 위임이라 동작 동일).
- 신규 유닛:
  - `classify_top` 라벨 매핑(대표 발화 → 기대 `TopIntent`) 파라미터화.
  - 라벨을 강제 반환하는 fake provider로 dispatch 정확성 검증(예: `RECIPE` 강제 시 저장소
    경로, `CONVERSATION` 강제 시 `generate_conversation` 호출).
  - Local provider 구조화 출력 파싱 성공 + 실패 시 정규식 폴백 검증(모킹).
- `ruff check .` / `mypy src` strict 통과.

## 검증 명령

```powershell
uv run pytest
uv run ruff check .
uv run mypy src
```

## 이 단계에서 하지 않는 것

- Stage 2(`CommandIntent`)·Stage 3 인자 해소는 정규식 브리지에 그대로 남긴다.
- 문서(`03_runtime_flow.md`/`CLAUDE.md`) 갱신은 Stage 2까지 끝난 뒤 일괄 반영한다.
