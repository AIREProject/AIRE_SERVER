# 260724 CANCEL 라벨 통합과 명령 테이블 대칭화 개발 기록

- 기록일: 2026-07-24
- 기록 유형: 명령 동작 버그 수정 및 내부 모델 정리 완료 기록
- 변경 범위: CANCEL 라벨 제거, 취소 발화의 작업 중지 통합, Mock/실제 LLM 라우터
  프롬프트 정리, 명령 매핑 단일화, 서비스 회귀 테스트와 개발 문서 갱신
- 구현 기준: `docs/plans/cancel-command-consolidation-plan.md`
- API/스키마 버전: 공개 `POST /api/v1/chat` 및 공용 `CommandType` 계약 변경 없음
- 후속 범위: `docs/plans/langgraph-companion-refactor-plan.md`에 따른 2단계 동작 보존 리팩토링

## 1. 완료 상태 요약

내부 `CommandLabel.CANCEL`을 제거하고 `"됐어"`, `"취소"`, `"나중에 하자"`를 기존
`STOP_CURRENT_TASK` 명령으로 통합했다. 이제 이 발화들은 `"그만"`과 동일하게
`Command.CancelCurrent` 후보를 생성하며, 요청의 `allowed_commands`에
`Command.CancelCurrent`가 없을 때만 기존 정책대로 대사만 반환한다.

통합 전에는 CANCEL이 대사 테이블에는 등록돼 있지만 공용 명령 매핑에는 없었다. 따라서
마코가 `"알겠어. 요청을 취소할게."`라고 말하면서도 클라이언트에는 아무 명령을 보내지 않는
빈 약속 상태였다. 작업 중인 마코에게 `"됐어"`라고 말해도 실제 작업은 계속될 수 있었다.

이번 변경으로 명령 라벨, 대사 장면과 공용 `CommandType`의 관계를 하나의 `_COMMANDS`
테이블에 묶었다. 앞으로 실행 가능한 명령을 추가할 때 대사만 등록하고 명령 매핑을 빠뜨리는
비대칭을 구조적으로 방지한다.

전체 146개 테스트가 통과했으며, 변경 파일은 Ruff와 mypy 검사를 모두 통과했다.

## 2. 설계 결정

### CANCEL을 별도 라벨로 유지하지 않음

공용 계약에는 취소 계열 명령이 `Command.CancelCurrent` 하나뿐이다. 별도 CANCEL 라벨이
가리키던 서버 측 pending 재질의 상태도 현재 통합 서버에는 없다. 따라서 같은 공용 명령으로
수렴하는 두 라벨을 LLM이 발화만 보고 구분하게 하지 않고 `STOP_CURRENT_TASK` 하나로 합쳤다.

통합 후의 대표 매핑은 다음과 같다.

| 발화 | 내부 라벨 | 허용 시 방출 명령 |
|---|---|---|
| `"그만"` | `STOP_CURRENT_TASK` | `Command.CancelCurrent` |
| `"됐어"` | `STOP_CURRENT_TASK` | `Command.CancelCurrent` |
| `"취소"` | `STOP_CURRENT_TASK` | `Command.CancelCurrent` |
| `"나중에 하자"` | `STOP_CURRENT_TASK` | `Command.CancelCurrent` |

향후 재질의 상태가 복귀하면 라벨을 다시 나누지 않는다. 같은
`STOP_CURRENT_TASK` 라벨을 받은 뒤 서버가 pending 상태를 확인해 다음처럼 분기하는 것이
기준이다.

- pending 재질의가 있으면 pending만 지우고 세계에 영향을 주는 명령은 내보내지 않는다.
- pending 재질의가 없으면 현재 작업 중지용 `Command.CancelCurrent`를 내보낸다.

이 미래 경로를 위해 `dialogue.py`의 `"cancel"` 장면은 제거하지 않았다. 현재는 사용되지
않지만, 재질의 취소 대사 전용 장면으로 다시 사용할 수 있다.

### 명령 메타데이터를 단일 테이블로 통합

기존 `_COMMAND_TYPE_MAP`과 `_COMMAND_SCENE`을 다음 정보를 함께 소유하는 `_COMMANDS`로
교체했다.

```text
CommandLabel → (CommandType, DialogueScene, fallback dialogue)
```

`FOLLOW_PLAYER`, `WAIT`, `STOP_CURRENT_TASK`는 모두 한 엔트리에서 공용 명령 타입과 대사를
결정한다. `GATHER_RESOURCE`와 `UNKNOWN`은 실행 가능한 공용 명령이 아니므로 이 테이블에
들어가지 않고 기존 별도 대사 경로를 유지한다.

`_build_command()`는 더 이상 누락된 매핑을 허용하지 않는다. `_handle_command()`가
`_COMMANDS` 멤버임을 확인한 라벨만 전달하므로, `_COMMANDS[label][0]`으로 명령 타입을
직접 읽고 allowlist 검사만 수행한다.

## 3. 구현 내용

### 라벨과 결정론적 분류

`intent.py`의 `CommandLabel.CANCEL`을 삭제했다. `CommandClassification`이 이 enum을
구조화 출력 스키마로 사용하므로 OpenAI와 로컬 LLM의 JSON Schema에서도 `"cancel"` 값이
자동으로 제거된다.

`command_intent.py`의 독립 `CANCEL_PATTERN`을 삭제하고 해당 어휘를
`CommandIntentParser._STOP`에 흡수했다. `classify_simple_command()`가 `fullmatch()`로
비교하므로 기존 취소 패턴과 동일하게 문장 전체가 취소 표현일 때만 중지 명령으로 분류한다.

### LLM 라우터

Mock 공급자의 최상위 분류는 취소 전용 조건을 제거했다. 취소 표현이 이제
`classify_simple_command()`에서 `STOP_CURRENT_TASK`로 잡히므로 기존 command 경로를 그대로
통과한다. 명령 분류의 `CommandLabel.CANCEL` 반환 분기도 함께 제거했다.

실제 LLM 공급자가 사용하는 프롬프트도 새 라벨 체계에 맞췄다.

- 최상위 command 설명을 `작업 중지·취소`로 묶었다.
- `stop_current_task`를 현재 작업 중지 또는 직전 요청 취소 명령으로 설명했다.
- 별도 `cancel` 라벨 설명을 삭제했다.

### 서비스 명령 생성

`service.py`의 `_COMMAND_TYPE_MAP`과 `_COMMAND_SCENE`을 `_COMMANDS` 하나로 합쳤다.
`_handle_command()`는 이 테이블에서 장면과 폴백 대사를 얻고, `_build_command()`는 같은
엔트리에서 공용 `CommandType`을 얻는다.

취소 표현은 Mock 공급자에서 다음 결과를 만든다.

```text
display_text = "알겠어. 지금 하던 일을 멈출게."
command_candidates[0].type = Command.CancelCurrent
```

단, `Command.CancelCurrent`가 `allowed_commands`에 없으면 `display_text`만 반환하고
`command_candidates`는 빈 배열이다. 이는 기존 allowlist 정책을 변경하지 않은 것이다.

### 테스트 보강

분류 테스트에 `"됐어"`, `"취소"`, `"나중에 하자"`가 모두
`CommandLabel.STOP_CURRENT_TASK`로 분류되는 케이스를 추가했다. 로컬 LLM의 구조화 출력
스키마 기대값에서도 `"cancel"`을 제거했다. `"취소"`가 최상위에서 계속
`TopIntent.COMMAND`로 분류되는 기존 테스트는 유지했다.

서비스 테스트에는 다음 회귀 안전망을 추가했다.

1. 세 취소 표현이 실제로 `Command.CancelCurrent` 후보를 하나씩 방출한다.
2. `"그만"`과 `"됐어"`가 동일한 `CommandType`으로 수렴한다.
3. 세 취소 표현도 allowlist가 비어 있으면 대사만 반환한다.

## 4. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/infrastructure/ai/companion/intent.py` | `CommandLabel.CANCEL` 제거 |
| `app/infrastructure/ai/companion/command_intent.py` | `CANCEL_PATTERN` 제거, 취소 어휘를 `_STOP`에 통합 |
| `app/infrastructure/ai/companion/llm.py` | 취소 전용 Mock 분기 제거, 라우터 프롬프트와 스키마 입력 정리 |
| `app/infrastructure/ai/companion/service.py` | 두 명령 딕셔너리를 단일 `_COMMANDS` 테이블로 통합 |
| `tests/test_llm.py` | 취소 어휘 분류와 구조화 출력 enum 기대값 갱신 |
| `tests/test_companion_ai_service.py` | 명령 방출, 동일 타입 수렴, allowlist 회귀 테스트 추가 |
| `CLAUDE.md` | 현재 라벨 목록과 취소 발화의 `stop_current_task` 통합 동작 반영 |

공용 계약인 `app/application/models/ai.py`, `Contracts/`, API 계층과 DB 스키마는 변경하지
않았다. `dialogue.py`의 `"cancel"` 장면도 향후 재질의 상태 복귀를 위해 보존했다.

## 5. 검증 결과

```text
uv run pytest tests/test_llm.py tests/test_companion_ai_service.py
48 passed

uv run pytest
146 passed, 1 warning

uv run ruff check app/infrastructure/ai/companion tests/test_llm.py tests/test_companion_ai_service.py
All checks passed!

uv run mypy app/infrastructure/ai/companion tests/test_llm.py tests/test_companion_ai_service.py
Success: no issues found in 11 source files

git grep -E "CANCEL_PATTERN|CommandLabel\.CANCEL|_COMMAND_SCENE|_COMMAND_TYPE_MAP" -- app tests CLAUDE.md
일치 항목 없음

git diff --check
통과
```

테스트 경고 한 건은 기존 Starlette TestClient의 `httpx` 관련 deprecation 경고이며 이번
변경과 무관하다.

### 저장소 전체 정적 검사 상태

`uv run ruff check .`은 이번 변경 범위 밖의 기존 오류 37건으로 실패했다. 주요 유형은 AI_RE
상류 코드의 import 정렬, `datetime.UTC` 권고, FastAPI `Depends`의 B008, 예외 체이닝과 미사용
import이며, 기존 코드를 상류와 가깝게 유지한다는 저장소 방침에 따라 이번 작업에서 수정하지
않았다.

`uv run mypy app`도 기존 2개 파일에서 4건으로 실패했다.

- `app/application/chat_service.py`: 반복문 변수 타입이 `CommandCandidate`로 고정돼
  `MemoryCandidate` 처리에서 발생하는 타입 오류 3건
- `app/api/routes/devices.py`: `SqlAlchemyDeviceRepository`의 반환 모델과
  `DeviceRepository` Protocol 간 타입 불일치 1건

변경 대상인 companion 패키지와 관련 테스트만 분리해 검사했을 때는 mypy 오류가 없었으며,
삭제한 CANCEL 관련 심볼의 잔존 참조도 없음을 확인했다.

## 6. 후속 작업

다음 단계는 `docs/plans/langgraph-companion-refactor-plan.md`의 동작 보존 리팩토링이다.
이번에 추가한 취소 회귀 테스트를 그대로 게이트로 사용해야 한다.

2단계 리팩토링에서도 다음 동작은 유지한다.

- `"됐어"`, `"취소"`, `"나중에 하자"`, `"그만"`은 모두
  `Command.CancelCurrent`로 수렴한다.
- 명령 후보는 요청 allowlist에 있을 때만 방출한다.
- gather와 unknown은 `_COMMANDS`에 넣지 않고 기존 대사 전용 경로를 유지한다.
- 사용되지 않는 `"cancel"` 대사 장면은 향후 pending 재질의 취소를 위해 보존한다.
