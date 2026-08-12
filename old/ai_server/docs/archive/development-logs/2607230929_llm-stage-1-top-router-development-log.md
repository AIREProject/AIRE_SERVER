# 2607230929 LLM Stage 1 Top Router 개발 기록

- 기록일: 2026-07-23 09:29
- 기록 유형: 기능 개발 완료 기록
- 변경 범위: 최상위 의도 타입, 공급자별 LLM 분류, 정규식 폴백, 서비스 디스패치,
  분류 설정과 회귀 테스트
- 구현 기준: `docs/plans/llm_two_stage_router_plan.md` Phase 1 작업 트리
- API/스키마 버전: 공개 `/v1` 계약 변경 없음, 내부 구조화 출력 스키마 추가
- 후속 범위: Stage 2 명령 분류와 Stage 3 인자 해소

## 1. 완료 상태 요약

기존에 명령·레시피·세계관·대화를 정규식 우선순위로 직접 판별하던 메시지 처리 흐름을
LLM 기반 Stage 1 Top Router로 전환했다. 최상위 라우터는 사용자 발화를 다음 내부 의도 중
하나로 분류한다.

```text
COMMAND | RECIPE | LORE | CONVERSATION | UNKNOWN
```

LLM은 의도 분류만 담당한다. 레시피와 세계관은 기존 저장소, 명령과 Action은 결정론적
파서와 템플릿, 일반 대화만 기존 생성 메서드를 사용한다. 따라서 LLM 분류 도입 후에도 게임
사실과 실행 Action을 LLM이 직접 생성하지 않는다.

Stage 2는 아직 구현하지 않았다. `COMMAND`로 분류된 요청은 기존 정규식 명령 파이프라인에
연결하는 임시 브리지를 사용하므로 기존 follow, wait, stop, gather, 취소, 재질의와 미지원
처리가 유지된다.

## 2. 구현 범위와 주요 결정

### 내부 분류 계약

공개 API 모델과 분리된 `TopIntent`와 `TopClassification`을 추가했다.
`TopClassification`은 Pydantic의 `extra="forbid"` 설정과 enum을 사용해 공급자에 전달할
JSON Schema를 만든다. 내부 분류 결과는 공개 응답 envelope에 노출하지 않는다.

### 공급자별 분류

- `MockLLMProvider`는 기존 명령 파서와 공용 정규식을 사용해 기존 처리 우선순위를
  결정론적으로 재현한다.
- `LocalLLMProvider`는 OpenAI 호환 Chat Completions의
  `response_format.type=json_schema`, `strict=true`로 구조화 출력을 요청한다.
- `OpenAIProvider`는 Responses API의 JSON Schema 구조화 출력을 사용한다.
- 네트워크 오류, 호환되지 않는 서버 응답, 빈 응답 또는 JSON 검증 실패는 요청 전체를
  실패시키지 않고 Mock 분류로 폴백한다.

로컬 LLM의 구조화 출력은 JSON 문법을 프롬프트에서 일일이 설명하는 방식이 아니다. 서버가
`response_format.json_schema`를 지원하면 추론 시 허용 토큰을 제한하는 constrained
decoding을 사용한다. 이는 모델 가중치를 변경하지 않는다. 로컬 서버가 해당 기능을
지원하지 않거나 스키마를 지키지 않으면 검증 실패 후 정규식 폴백으로 종료한다.

### 서비스 디스패치와 재질의

유효한 pending 재질의 답변인 `나무`와 `돌`은 LLM 호출 전에 기존 방식으로 해소한다. 그 밖의
발화는 `classify_top()`을 한 번 호출하고 분류 결과에 따라 명령 브리지, 레시피 저장소,
세계관 저장소, 일반 대화 생성 또는 미지원 응답으로 보낸다. 해소되지 않은 pending 상태의
존재 여부도 분류 호출에 전달한다.

기존 정규식 상수는 서비스 클래스에서 `command_intent.py`로 이동해 Mock 분류와 명령
브리지가 동일한 패턴을 공유하도록 했다.

### 설정

다음 선택 설정을 추가했다.

| 환경 변수 | 기본값 | 용도 |
|---|---:|---|
| `CLASSIFY_TEMPERATURE` | `0.0` | 분류 호출의 결정성 설정 |
| `CLASSIFY_MAX_TOKENS` | `20` | 분류 구조화 출력의 최대 토큰 수 |

기존 작업 트리에 있던 기본 로컬 공급자와 로컬 서버 URL 변경은 수정하거나 되돌리지 않고
보존했다. 개발 기록에는 실제 API 키나 서버 URL을 남기지 않는다.

## 3. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/ai_companion_server/intent.py` | 내부 Top Intent enum과 구조화 출력 모델 추가 |
| `src/ai_companion_server/llm.py` | 공통 분류 인터페이스와 Mock/Local/OpenAI 구현, 폴백 추가 |
| `src/ai_companion_server/service.py` | Stage 1 분류 호출과 결과별 디스패치, 명령 브리지 추출 |
| `src/ai_companion_server/command_intent.py` | Mock과 서비스가 공유하는 카테고리 정규식 이동 |
| `src/ai_companion_server/config.py` | 분류 temperature와 최대 토큰 설정 추가 |
| `.env.example` | 신규 분류 환경 변수의 안전한 기본값 추가 |
| `tests/test_llm.py` | Mock 라벨 매핑, 로컬 구조화 출력과 실패 폴백 테스트 |
| `tests/test_service.py` | 강제 분류 공급자를 통한 레시피·대화 디스패치 테스트 |

공개 요청·응답 모델, 엔드포인트, Action 종류와 오류 envelope는 변경하지 않았다.

## 4. 검증 결과

```text
uv run pytest
35 passed, 1 warning

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 12 source files

git diff --check
통과
```

경고 한 건은 기존 Starlette TestClient의 `httpx` 관련 deprecation 경고이며 테스트 실패에는
영향을 주지 않는다. 기존 `tests/test_api.py` 19건도 수정 없이 통과했다.

## 5. 후속 범위 및 주의점

- Stage 2 LLM 명령 분류: `FOLLOW_PLAYER`, `WAIT`, `STOP_CURRENT_TASK`,
  `GATHER_RESOURCE`
- Stage 3 결정론적 자원 인자 해소와 Stage 2 연결
- Stage 2 완료 후 `docs/current/03_runtime_flow.md` 등 현행 계약 문서 갱신
- 실제 로컬 LLM 서버의 JSON Schema constrained decoding 지원 여부 운영 환경 확인
- Stage 2 완료 후 임시 정규식 명령 브리지 제거 여부 결정

현재 단계에서 정규식 파서는 장애 폴백과 Mock 백엔드 역할도 하므로 제거 대상이 아니다.
`docs/plans/llm_two_stage_router_plan.md` 역시 Stage 2까지 완료되기 전에는 비규범적 계획
문서로 유지한다.
