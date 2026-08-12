# 2607231008 LLM Stage 2 Command Pipeline 개발 기록

- 기록일: 2026-07-23 10:08
- 기록 유형: 기능 개발 완료 기록
- 변경 범위: 명령 분류 타입, 공급자별 LLM 명령 분류, Stage 2 디스패치,
  Stage 3 결정론적 채집 인자 해소와 회귀 테스트
- 구현 기준: `docs/plans/stage2_command_pipeline_plan.md` 작업 트리
- API/스키마 버전: 공개 `/v1` 계약 변경 없음, 내부 명령 구조화 출력 스키마 추가
- 후속 범위: Phase 3 현행 런타임 문서 갱신과 라우터 계획 정리

## 1. 완료 상태 요약

Stage 1 Top Router가 `COMMAND`로 분류한 발화를 기존 정규식 브리지로 직접 처리하던 흐름을
LLM 기반 Stage 2 Command Router와 결정론적 Stage 3 인자 해소 구조로 전환했다.

```text
Stage 1: COMMAND
→ Stage 2: classify_command()
→ FOLLOW_PLAYER | WAIT | STOP_CURRENT_TASK | GATHER_RESOURCE | CANCEL | UNKNOWN
→ 단순 명령은 템플릿 Action으로 변환
→ 채집 명령은 Stage 3 정규식 안전장치로 자원·지원 범위 해소
→ 기존 MessageResponse 계약으로 반환
```

LLM은 명령 계열만 분류한다. 실제 Action 모델, 활성 작업 ID, 채집 자원 종류, 수량 지원 여부,
재질의 생성과 오류 응답은 기존 결정론적 코드가 담당한다. 따라서 LLM이 실행 인자나 게임
사실을 직접 생성하지 않는다.

Mock 공급자는 기존 정규식 우선순위를 그대로 재현하므로 `tests/test_api.py`의 공개 API
회귀 테스트는 수정 없이 통과했다.

## 2. 구현 범위와 주요 결정

### 내부 명령 분류 계약

공개 API 모델과 분리된 `CommandLabel`과 `CommandClassification`을 추가했다. 명령 라벨은
다음 여섯 가지다.

```text
FOLLOW_PLAYER
WAIT
STOP_CURRENT_TASK
GATHER_RESOURCE
CANCEL
UNKNOWN
```

`CommandClassification`은 Pydantic `extra="forbid"` 설정과 enum을 사용해 strict JSON
Schema를 제공한다. `GATHER_RESOURCE`에는 자원 종류나 수량을 포함하지 않으며 해당 값은
Stage 3에서 해소한다.

### 공급자별 명령 분류

- `LLMProvider` 공통 인터페이스에 비동기 `classify_command(text)`를 추가했다.
- `MockLLMProvider`는 기존 `CommandIntentParser`와 채집 판별 메서드에 위임한다.
- Mock은 `그만`을 취소가 아닌 작업 중지로 유지하기 위해 STOP 판정을 CANCEL보다 먼저 한다.
- `LocalLLMProvider`는 OpenAI 호환 Chat Completions의 strict JSON Schema 출력을 사용한다.
- `OpenAIProvider`는 Responses API의 strict JSON Schema 출력을 사용한다.
- 두 실제 공급자는 기존 `CLASSIFY_TEMPERATURE`와 `CLASSIFY_MAX_TOKENS` 설정을 재사용한다.
- 네트워크 오류, 빈 응답, 잘못된 JSON 또는 schema 검증 실패 시 Mock 명령 분류로 폴백한다.

새 환경 변수나 외부 의존성은 추가하지 않았다.

### Stage 2 서비스 디스패치

`RequestService._handle_command()`를 비동기로 전환하고 `classify_command()` 결과에 따라
처리한다.

| 라벨 | 처리 결과 |
|---|---|
| `FOLLOW_PLAYER` | 기존 follow 대사와 `FollowAction` 반환 |
| `WAIT` | 기존 wait 대사와 `WaitAction` 반환 |
| `STOP_CURRENT_TASK` | 활성 작업이 있으면 `StopAction`, 없으면 `UNSUPPORTED_REQUEST` |
| `GATHER_RESOURCE` | Stage 3 `_resolve_gather()`로 전달 |
| `CANCEL` | 취소 대사 반환과 pending 재질의 삭제 |
| `UNKNOWN` | 일반 `UNSUPPORTED_REQUEST` 반환 |

확정된 단순 명령, 지원하는 채집 명령과 취소는 기존 동작과 같은 시점에 pending 재질의를
삭제한다. 지원하지 않는 채집 또는 알 수 없는 명령은 pending 상태를 임의로 삭제하지 않는다.

### Stage 3 결정론적 채집 해소

`_resolve_gather()`는 명령 분류 이후에도 기존 정규식 안전장치를 적용한다.

1. 수량 지정 또는 미지원 자원이 있으면 미지원 응답을 반환한다.
2. 일반 나무·돌이 명확하면 `GatherAction`을 반환하고 pending을 삭제한다.
3. 대상이 모호하면 새 clarification ID와 `나무`, `돌` 선택지를 발급한다.
4. 어느 경로에도 해당하지 않으면 일반 미지원 응답을 반환한다.

기존 재질의 선해소, 단일 사용 clarification ID, 공개 오류 코드
`UNSUPPORTED_REQUEST` 계약은 변경하지 않았다.

## 3. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/ai_companion_server/intent.py` | `CommandLabel`, `CommandClassification` 추가 |
| `src/ai_companion_server/llm.py` | 명령 라우터 prompt와 Mock/Local/OpenAI `classify_command()` 구현 |
| `src/ai_companion_server/service.py` | 비동기 Stage 2 dispatch와 Stage 3 `_resolve_gather()` 구현 |
| `tests/test_llm.py` | Mock 명령 매핑, 경계 우선순위, Local 구조화 출력과 폴백 테스트 |
| `tests/test_service.py` | 강제 명령 라벨 dispatch, 취소, UNKNOWN, 채집 4경로 테스트 |

`domain.py`, 공개 endpoint, Action 종류, 오류 envelope, 환경 설정과 dependency lock은
변경하지 않았다. 계획에 따라 `docs/current/` 문서도 이번 단계에서는 수정하지 않았다.

## 4. 검증 결과

```text
uv run pytest
54 passed, 1 warning

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 12 source files

git diff --check
통과
```

관련 파일만 먼저 실행한 테스트도 54건 모두 통과했다. 경고 한 건은 기존 Starlette
TestClient의 `httpx` 관련 deprecation 경고이며 테스트 실패에는 영향을 주지 않는다.

## 5. 후속 범위 및 주의점

- `docs/current/03_runtime_flow.md` 등 현행 계약 문서에 완성된 2단계 라우터 흐름 반영
- `docs/plans/llm_two_stage_router_plan.md`와 Stage 2 계획 문서의 완료 상태 정리
- 실제 Local/OpenAI 공급자의 한국어 명령 분류 품질과 JSON Schema 지원 여부 운영 환경 확인
- 필요 시 Stage 1·2 분류 관측성 및 평가 데이터셋 추가

정규식 파서는 제거 대상이 아니다. Mock 공급자의 결정론적 분류, 실제 공급자 장애 폴백,
Stage 3의 수량·자원 계약 안전장치로 계속 사용한다.
