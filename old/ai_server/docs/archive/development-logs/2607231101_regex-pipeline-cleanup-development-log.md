# 2607231101 정규식 파이프라인 정리 개발 기록

- 기록일: 2026-07-23 11:01
- 기록 유형: 리팩터링 완료 기록
- 변경 범위: 중복 명령 의도 자료구조 제거, Stage 2 직접 디스패치,
  Stage 3 채집 해소 단일화, Mock/폴백 분류 위임과 관련 문서·테스트
- 구현 기준: `docs/archive/regex_pipeline_cleanup_plan.md` 작업 트리
- API/스키마 버전: 공개 `/v1` 계약 및 내부 구조화 출력 스키마 변경 없음
- 후속 범위: 없음

## 1. 완료 상태 요약

2단계 LLM 라우터 도입 후 프로덕션 경로에 남아 있던 정규식 명령 파서 왕복을 제거했다.
Stage 2가 반환한 `CommandLabel`은 이제 별도의 `CommandIntent`로 변환하지 않고 서비스에서
직접 Action과 대사로 디스패치한다.

정규식이 필요한 두 역할은 유지했다. 기본 Mock 공급자와 실제 공급자 장애 폴백은
`classify_simple_command()`로 follow, wait, stop 명령을 결정론적으로 분류한다. Stage 3는
`resolve_gather()`로 채집 자원, 모호한 요청, 미지원 수량·자원을 단일 패스에서 해소한다.
공개 요청·응답, Action, clarification과 오류 동작은 변경하지 않았다.

## 2. 구현 범위와 주요 결정

### 중복 자료구조와 죽은 분기 제거

- `CommandIntent` enum과 단일 필드 `GatherResourceIntent` 래퍼를 제거했다.
- `CommandIntentParser.parse()`, `is_ambiguous_gather()`,
  `is_unsupported_gather()`를 제거했다.
- 서비스의 `_SIMPLE_COMMANDS` 매핑과 `_command_response()` 왕복을 제거했다.
- stop 처리는 활성 작업 확인과 `StopAction` 생성만 담당하는 `_stop_response()`로 분리했다.

단순 명령과 취소에서 pending clarification을 삭제하던 기존 시점을 유지했다.
`STOP_CURRENT_TASK`는 활성 작업이 없어 미지원 응답으로 끝나더라도 pending을 먼저 삭제하며,
`UNKNOWN`은 pending을 임의로 삭제하지 않는다.

### Stage 3 채집 해소 단일화

`resolve_gather()`는 정규화된 문자열을 한 번만 검사하고 다음 결과를 반환한다.

```text
wood | stone | ambiguous | unsupported | None
```

수량과 미지원 자원 검사를 wood/stone 검사보다 먼저 수행한다. 따라서 `부싯돌`처럼 문자열에
`돌`이 포함된 미지원 자원이 일반 stone으로 잘못 해소되지 않는다. `풀을 캐 줘`처럼 채집
형태지만 알려진 자원이나 모호한 참조가 없는 요청은 `None`으로 남아 기존 일반 미지원 대사를
사용한다.

### Mock과 폴백 동작 보존

`MockLLMProvider`는 단순 명령, 채집, 취소 순서로 새 파서 API에 위임한다. 취소 패턴에서는
선행 stop 패턴으로 항상 처리되던 `그만` 대안과 `fullmatch()`에 불필요한 끝 앵커를 제거했다.
이에 따라 `그만`은 계속 `STOP_CURRENT_TASK`, `취소`는 계속 `CANCEL`로 분류된다.

## 3. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/ai_companion_server/command_intent.py` | 중복 intent 타입과 기존 파서 분기 제거, 단순 명령 분류와 단일 패스 채집 해소 추가 |
| `src/ai_companion_server/service.py` | `CommandLabel` 직접 디스패치, 채집 해소 단일 호출, stop 전용 응답 헬퍼 적용 |
| `src/ai_companion_server/llm.py` | Mock/폴백 분류를 새 파서 API로 전환 |
| `tests/test_service.py` | `resolve_gather()`의 wood, unsupported, ambiguous, `None` 경계 회귀 테스트 추가 |
| `AGENTS.md` | `command_intent.py`의 새 책임과 제거된 중복 타입 반영 |
| `CLAUDE.md` | 아키텍처 설명을 단순 명령 분류와 Stage 3 해소 역할로 갱신 |
| `docs/current/03_runtime_flow.md` | 정규식이 Mock/폴백과 Stage 3에만 남는다는 런타임 설명 보강 |

환경 변수, dependency, 공개 endpoint와 Pydantic 요청·응답 모델은 변경하지 않았다.

## 4. 검증 결과

```text
uv run pytest
59 passed, 1 warning

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 12 source files

git diff --check
통과
```

기존 공개 API 테스트를 포함한 전체 테스트가 통과했다. 경고 한 건은 기존 Starlette
TestClient의 `httpx` 관련 deprecation 경고이며 이번 변경으로 발생한 실패는 아니다.

## 5. 후속 범위 및 주의점

이번 계획의 구현 범위에는 남은 작업이 없다. 정규식 전체를 제거한 것은 아니며 다음 두
경계는 설계상 계속 유지한다.

- 외부 API 없이 동작하는 기본 Mock 공급자와 실제 공급자 실패 시 분류 폴백
- LLM이 채집 자원이나 수량을 지어내지 못하게 하는 Stage 3 결정론적 해소

향후 명령 종류나 지원 자원을 확장할 때는 실제 LLM 라벨 계약, Mock 분류와 Stage 3 해소를
각각의 책임에 맞게 함께 갱신해야 한다.
