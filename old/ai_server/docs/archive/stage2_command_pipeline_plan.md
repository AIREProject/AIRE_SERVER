# Stage 2 — Command Pipeline 구현 계획

> **상태: 구현 완료 (2026-07-23).** 커밋 `fd78610`에서 Stage 2·3이 구현되었고 규범적
> 서술은 `docs/current/`로 승격되었다. 이 문서는 당시 계획을 보존하는 아카이브 기록이며
> 현행 구현 기준이 아니다.

이 문서는 Stage 2 착수 시점의 **설계 계획**이다. 규범적 구현 기준은 `docs/current/`가
유지한다. 전체 2단계 라우터 설계는 `docs/archive/llm_two_stage_router_plan.md`를 참고한다.

## 전제 (현재 상태)

Stage 1(Top Router)은 구현·커밋되어 있다(`feat: add LLM stage 1 top router`).
`service.RequestService._handle_command`는 아직 **정규식 브리지**로 남아 있으며, Stage 2는
이 브리지를 LLM 명령 분류기로 교체하는 작업이다.

## 핵심

`_handle_command`(정규식 브리지)를 **LLM 명령 분류(`classify_command`) + 결정론적 인자
해소(Stage 3)**로 교체한다. Stage 1과 동일하게 Mock 공급자는 정규식에 위임하므로 기존
테스트는 무수정 통과한다.

## 1. `intent.py` — Stage 2 분류 타입 추가

Stage 1과 대칭 구조로 추가한다.

```python
class CommandLabel(StrEnum):
    FOLLOW_PLAYER = "follow_player"
    WAIT = "wait"
    STOP_CURRENT_TASK = "stop_current_task"
    GATHER_RESOURCE = "gather_resource"
    CANCEL = "cancel"
    UNKNOWN = "unknown"          # 명령형이지만 매핑 불가 → unsupported

class CommandClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: CommandLabel
```

- `command_intent.CommandIntent`(follow/wait/stop, 파서 내부용)는 그대로 두고, **분류
  출력용 enum은 별도(`CommandLabel`)**로 둔다.
- Stage 3에서 자원을 해소하므로 `GATHER_RESOURCE`는 자원 종류를 담지 않는다.
- `CANCEL`은 재질의 취소 제어의 자연스러운 자리라 여기에 포함한다(대안: Stage 1 이전
  결정론 처리 — 현재 구조 변경을 최소화하려고 Stage 2 라벨로 채택).

## 2. `llm.py` — `classify_command` 추가

`LLMProvider`에 추상 메서드를 추가한다.

```python
async def classify_command(self, text: str) -> CommandLabel
```

- **MockLLMProvider**: 브리지 우선순위를 그대로 재현한다(순서 중요).
  1. `parse()` == FOLLOW/WAIT/STOP → 해당 라벨 *(`그만`이 STOP으로 잡히도록 CANCEL보다 먼저)*
  2. `parse()`가 `GatherResourceIntent` **또는** `is_ambiguous_gather` **또는**
     `is_unsupported_gather` → `GATHER_RESOURCE`
  3. `CANCEL_PATTERN.fullmatch` → `CANCEL`
  4. 그 외 → `UNKNOWN`
- **LocalLLMProvider / OpenAIProvider**: 신설 `_COMMAND_ROUTER_PROMPT`(6개 라벨 정의)로
  structured-output 호출(`CommandClassification` 스키마, `classify_temperature`/
  `classify_max_tokens` 재사용). 실패/파싱 오류 시 `mock.classify_command` 폴백. Stage 1
  구현 패턴을 그대로 복제한다.

## 3. `service.py` — 브리지 → 2단계 dispatch 교체

`_handle_command`를 **async**로 바꾸고(`handle_message`에서 `await`), 정규식 사슬 대신
아래로 교체한다.

```
label = await self._llm.classify_command(request.text)
FOLLOW_PLAYER / WAIT → 템플릿 Action + self._pending.clear()
STOP_CURRENT_TASK    → active_task 있으면 stop, 없으면 "지금 중지할 작업이 없어" + clear
GATHER_RESOURCE      → self._resolve_gather(request)   # Stage 3
CANCEL               → "요청을 취소할게" + clear
UNKNOWN              → 일반 unsupported 폴백
```

기존 `_command_response`/`_gather_response`/`_resolve_clarification`는 재사용한다.
**pending 클리어 시점을 현행과 동일하게 유지**해야 `test_cancel_and_new_command`가
통과한다(명령 확정·취소 시 clear, unsupported 시 유지).

## 4. `service.py` — Stage 3: `_resolve_gather` (결정론적)

`GATHER_RESOURCE` 확정 후 자원/수량을 판정한다(엄격 계약이라 정규식 안전장치 유지).

```
if is_unsupported_gather → "수량 지정 없이 일반 나무나 돌 채집만" unsupported
elif parse() == GatherResourceIntent(wood/stone) → gather Action + clear
elif is_ambiguous_gather → 재질의 발급(clarification_id 생성 + pending 저장)
else → 일반 unsupported 폴백
```

## 5. 테스트

- 기존 `tests/test_api.py` **무수정 통과** 확인(Mock=정규식 위임).
- 신규 유닛:
  - `classify_command` 매핑표(대표 발화 → 기대 `CommandLabel`) 파라미터화. `그만→STOP`,
    `취소→CANCEL`, `저것 좀 캐 줘→GATHER_RESOURCE` 등 경계 포함.
  - 라벨 강제 fake provider로 dispatch 검증(예: `STOP` + active_task 없음 → unsupported,
    `CANCEL` → pending clear).
  - `_resolve_gather` 단위 검증(wood/stone/ambiguous/unsupported 4경로).
  - Local provider `classify_command` 구조화 파싱 성공 + 실패 시 폴백.
- `uv run pytest` / `ruff check .` / `mypy src` strict 통과.

## 이 단계에서 하지 않는 것

- Stage 3의 정규식 안전장치는 유지한다(수량/미지원 자원 판정은 계약상 결정론이 안전).
- 문서(`03_runtime_flow.md`/`CLAUDE.md`) 갱신과 정규식 브리지 흔적 정리는 Phase 3에서
  일괄 진행한다.

## 회귀 방지 핵심

- Mock `classify_command`에서 **STOP 판정을 CANCEL보다 먼저** 둔다(`그만` 처리).
- **pending 클리어 타이밍**을 현행과 바이트 단위로 일치시킨다.
- `UNKNOWN`/unsupported 경로의 `error.code = UNSUPPORTED_REQUEST`를 유지한다.
