# 2607231328 확정 사실 기반 LLM 대사 전환 개발 기록

- 기록일: 2026-07-23 13:28
- 기록 유형: 기능 개발 완료 기록
- 변경 범위: 전체 메시지 및 이벤트 대사 생성, 환각 가드, 템플릿 폴백, 공급자 설정,
  서비스 비동기 전환, 계약 문서와 회귀 테스트
- 구현 기준: `docs/plans/fact-grounded-llm-dialogue-plan.md`
- API/스키마 버전: 공개 `/v1` 요청·응답 계약 변경 없음
- 후속 범위: 실제 OpenAI/로컬 공급자 환경에서 대사 변동성과 사실 유지 수동 검증

## 1. 완료 상태 요약

동료 마코의 대사를 고정 템플릿 중심 구조에서 LLM 생성 구조로 전환했다. 일반 대화뿐 아니라
따라오기, 대기, 작업 중지, 취소, 채집, 재질의, 제작법, 지역 이야기, 미지원 안내와 채집 결과
이벤트까지 플레이어에게 보이는 모든 `dialogue`가 공통 생성 경계를 통과한다.

LLM은 게임 사실이나 실행 결과를 결정하지 않는다. 저장소, 명령 파서, 요청 컨텍스트와
Action이 먼저 사실을 확정하고, LLM은 해당 사실을 마코의 말투로 옮긴다. 다음 공개 값은 기존과
같이 코드가 결정론적으로 소유한다.

```text
action
clarification.options
error.code
error.message
```

공급자 호출 실패, 빈 출력, 길이 초과 또는 숫자 사실 검증 실패 시에는 각 장면에 보존한 기존
대사 템플릿으로 복구한다. 기본 `MockLLMProvider`도 일반 대화를 제외한 장면에서 이 폴백을
그대로 반환하므로 기존 API 테스트의 정확 문자열 계약이 유지된다.

## 2. 구현 범위와 주요 결정

### 공통 대사 계약

새 `dialogue.py`에 `DialogueSpec`과 장면 타입을 추가했다. 각 spec은 다음 정보만 갖는다.

| 필드 | 역할 |
|---|---|
| `scene` | 현재 대사의 목적을 나타내는 장면 |
| `fallback` | 공급자 또는 검증 실패 시 사용할 기존 대사 |
| `user_text` | 필요한 경우에만 전달하는 플레이어 원문 |
| `facts` | 저장소와 코드가 확정한 사실 조각 |

장면별 `SCENE_GUIDE`는 완성된 문장이 아니라 전달 목표만 설명한다. `fallback`은 코드의 복구
경로에서만 사용하며 공급자 프롬프트에는 포함하지 않는다. 이를 통해 실제 공급자가 기존 문장을
복사하지 않고 같은 사실을 다양한 문장으로 표현할 수 있게 했다.

### 출력 검증과 폴백

모든 공급자 출력은 서비스에 전달되기 전에 `dialogue.render()`와 `sanitize()`를 통과한다.

- 개행과 연속 공백을 한 줄로 정규화한다.
- 빈 문자열과 200자를 초과한 대사를 거부한다.
- 일반 대화를 제외한 장면에서는 출력의 모든 숫자가 `facts`에 있는 숫자의 부분집합인지
  확인한다.
- 공급자 예외나 검증 실패는 API 오류로 전파하지 않고 `fallback`으로 복구한다.

숫자 가드는 제작법의 재료 수량이나 채집 결과 수량이 바뀌는 사고를 막는다. 예를 들어 확정
사실이 `돌 10개`이면 공급자가 `돌 12개`라고 생성한 결과는 폐기한다.

### 공급자 인터페이스

`LLMProvider.generate_conversation(user_text)`를 제거하고
`generate_dialogue(spec)`로 통합했다.

- `MockLLMProvider`는 일반 대화에서 기존 인사·감사 문구를 반환하고 다른 장면은
  `spec.fallback`을 반환한다.
- `OpenAIProvider`는 Responses API의 `DialogueOutput` JSON Schema를 유지하면서 공통
  시스템 프롬프트, 장면 지시와 확정 사실을 전달한다.
- `LocalLLMProvider`는 OpenAI 호환 Chat Completions 형식을 유지하면서 같은 공통 프롬프트를
  사용한다.
- 실제 공급자 호출 실패 시 Mock 공급자로 복구하고, 서비스의 공통 `render` 경계에서도 다시
  검증한다.

대사 프롬프트에는 마코의 한국어 반말 페르소나, 짧은 길이, 확정 사실 밖의 게임 정보 금지,
장면 지시 외 재질의 금지와 말투 예시를 넣었다.

### 서비스 전체 대사 전환

`RequestService`의 모든 대사 생성 지점을 `DialogueSpec` 조립과 `await render(...)` 호출로
변경했다.

- 명령 대사는 follow, wait, stop, cancel과 wood/stone 채집 장면으로 구분한다.
- 모호한 채집은 나무와 돌만 선택할 수 있다는 사실을 주입한다.
- 제작법과 지역 이야기는 저장소의 `fact.text`를 확정 사실로 전달한다.
- 미지원 응답은 호출부별 지원 범위와 실패 이유를 사실 조각으로 전달한다.
- 미지원 응답의 `dialogue`만 생성값으로 바꾸고 `error.message`는 기존 고정 문구를 유지한다.
- 채집 완료/실패 이벤트는 자원 이름과 실제 수량 또는 실패 사실을 주입한다.

대사 생성이 필요해진 `_resolve_gather`, `_resolve_clarification`, `_stop_response`,
`_gather_response`, `_unsupported`와 `handle_event`는 비동기 인스턴스 메서드로 전환했다.
이에 맞춰 `/v1/companion/event` 엔드포인트도 `service.handle_event()`를 `await`한다.

### 설정

분류 설정과 독립된 대사 생성 설정을 추가했다.

| 환경 변수 | 기본값 | 용도 |
|---|---:|---|
| `DIALOGUE_TEMPERATURE` | `0.6` | 대사 표현의 변동성 |
| `DIALOGUE_MAX_TOKENS` | `160` | 대사 생성 최대 토큰 수 |

OpenAI와 로컬 공급자 모두 같은 설정을 사용한다. `.env.example`에는 실제 키 없이 안전한
기본값과 설명만 추가했다.

## 3. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/ai_companion_server/dialogue.py` | 장면, spec, 지시, sanitize와 render 폴백 경계 추가 |
| `src/ai_companion_server/llm.py` | `generate_dialogue` 인터페이스와 세 공급자 구현 |
| `src/ai_companion_server/service.py` | 전체 응답 대사의 사실 기반 생성 및 async 전환 |
| `src/ai_companion_server/main.py` | 이벤트 서비스 호출을 `await`하도록 변경 |
| `src/ai_companion_server/config.py` | 대사 temperature와 최대 토큰 설정 추가 |
| `.env.example` | 대사 생성 환경 변수 추가 |
| `tests/test_dialogue.py` | 길이·공백·숫자 가드와 폴백 단위 테스트 추가 |
| `tests/test_llm.py` | 새 공급자 인터페이스, 설정값과 폴백 프롬프트 비노출 테스트 |
| `tests/test_service.py` | 사실 전달, 예외 폴백과 고정 오류 메시지 테스트 |
| `docs/current/01_current_scope.md` | 결정론적 값과 사실 기반 대사 생성 범위 갱신 |
| `docs/current/03_runtime_flow.md` | 분류·대사 생성·검증·폴백 런타임 흐름 갱신 |
| `docs/current/05_player_qa_catalog.md` | 모든 예시가 Mock 폴백 문구임을 명시 |
| `CLAUDE.md` | 새 대사 계층과 공급자 책임, async 이벤트 흐름 반영 |

`domain.py`, `recipes.py`, `lore.py`, `command_intent.py`와 `intent.py`는 변경하지 않았다.
공개 응답 모델과 게임 사실·분류의 소유권을 기존 코드에 남기기 위한 결정이다.

## 4. 검증 결과

```text
uv run pytest -q
70 passed, 1 warning

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 13 source files

git diff --check
통과
```

기존 `tests/test_api.py`는 수정하지 않았으며 전체 테스트에서 그대로 통과했다. 경고 한 건은
기존 Starlette TestClient의 `httpx` 관련 deprecation 경고로 이번 변경과 무관하다.

## 5. 미수행 검증과 후속 확인

실제 OpenAI 또는 로컬 LLM 공급자를 호출하는 수동 검증은 API 키와 실행 중인 외부 서버가
필요해 이번 작업에서 수행하지 않았다. 실제 공급자 환경에서는 다음을 확인해야 한다.

- 동일한 따라오기 요청을 반복했을 때 문장이 달라지는지
- 철 도끼 제작법의 철괴 3개, 나무 2개와 작업대 사실이 유지되는지
- 채집 Action과 미지원 오류 메시지는 그대로이고 `dialogue`만 달라지는지
- 이벤트 수량 이외의 숫자가 생성되면 폴백으로 복구되는지
- 공급자 URL 또는 인증이 잘못되어도 기존 템플릿으로 복구되고 5xx가 발생하지 않는지

실제 대사 품질이 안정적이지 않으면 게임 사실 소유권을 LLM으로 옮기지 않고
`SCENE_GUIDE`, 페르소나 프롬프트와 대사 생성 설정만 조정해야 한다.
