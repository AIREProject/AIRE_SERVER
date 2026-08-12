# 2607181631 재질의 계약 개발 기록

- 기록일: 2026-07-18 16:31
- 기록 유형: 기능 개발 완료 기록
- 구현 기준: `f14a641 feat: add clarification lifecycle`
- 변경 범위: 모호한 돌 채집 명령의 재질의 생성, 후속 선택 연결, 수명주기 저장,
  오류 envelope, capability, migration 및 회귀 테스트
- API/스키마 버전: `1` / `1.0` 유지
- 후속 범위: 해결된 선택으로 `GATHER_RESOURCE` Action Intent 생성

## 1. 완료 상태 요약

`POST /v1/companion/requests`의 `COMMAND` 경로에서 “돌 가져와”, “돌 모아 줘”처럼
대상 종류를 확정할 수 없는 명령을 실행하지 않고 정형 `NEEDS_CLARIFICATION` 응답으로
전환하는 수직 슬라이스를 구현했다.

```text
모호한 돌 채집 명령
→ COMMAND 판정
→ 돌 종류가 지정되지 않았는지 결정론적으로 검사
→ 기존 활성 재질의 취소
→ PendingClarification과 요청 결과를 원자적으로 저장
→ action=null / NEEDS_CLARIFICATION 반환
→ 자연어 또는 candidate_id 후속 입력
→ 후보 및 요청 문맥 검증
→ RESOLVED 저장
→ action=null / COMPLETED 확인 대사 반환
```

이번 단계에서는 선택을 확정하는 데까지만 처리한다. 선택 완료 후에도 실제 채집
Action Intent는 만들지 않으며, 게임 클라이언트가 실행할 행동도 발급하지 않는다.

## 2. 공개 요청·응답 계약

### 요청 모델

`CompanionRequest`에 다음 선택 필드를 추가했다.

```json
{
  "clarification_id": "clarify_<generated-id>",
  "clarification_response": {
    "candidate_id": "resource_stone"
  }
}
```

`clarification_response`는 `clarification_id`와 함께 있을 때만 허용한다. 구조화 선택 없이
`input.text`로 자연어 후속 응답을 보내는 경로도 계속 지원한다.

### 응답 모델

`RequestStatus`에 `NEEDS_CLARIFICATION`을 추가했다. 기존 자유 형식
`RequestData.clarification`은 다음 정형 모델로 변경했다.

```json
{
  "clarification_id": "clarify_<generated-id>",
  "type": "TARGET_SELECTION",
  "prompt": "어떤 종류의 돌을 말하나요?",
  "expected_response_type": "CANDIDATE_SELECTION",
  "options": [
    {
      "candidate_id": "resource_stone",
      "display_name": "돌 원석",
      "description": null
    },
    {
      "candidate_id": "item_stone_block",
      "display_name": "돌 블록",
      "description": null
    },
    {
      "candidate_id": "resource_flint",
      "display_name": "부싯돌",
      "description": null
    }
  ],
  "clarification_count": 1,
  "max_clarification_count": 2,
  "expires_at": "<timezone-aware UTC datetime>"
}
```

생성 응답은 다음 불변 조건을 가진다.

- `status=NEEDS_CLARIFICATION`
- `route.name=COMMAND`
- `action=null`
- 후보 순서는 돌 원석, 돌 블록, 부싯돌로 고정
- `provider=builtin`
- `model=clarification-template-v1`
- 기본 TTL은 300초
- 대사는 짧은 내장 템플릿이며 LLM을 호출하지 않음

`GET /v1/capabilities`에는 `CLARIFICATION_RESPONSE` 경로와 다음 제한을 노출한다.

```json
{
  "max_active_clarifications_per_companion": 1
}
```

## 3. 결정론적 생성과 후보 매칭

다음 조건을 모두 만족하면 새 재질의를 만든다.

- 입력에 “돌”이 포함됨
- “가져와” 또는 “모아” 계열 명령이 포함됨
- 돌 원석, 돌 블록, 부싯돌 중 특정 종류가 이미 명시되지 않음

후속 자연어는 후보 ID, 표시 이름, 고정 별칭으로만 매칭한다.

| candidate_id | 표시 이름 | 대표 별칭 |
|---|---|---|
| `resource_stone` | 돌 원석 | 돌원석, 원석 |
| `item_stone_block` | 돌 블록 | 돌블록, 석재 블록 |
| `resource_flint` | 부싯돌 | 부시돌, flint |

둘 이상의 후보가 동시에 나타나거나 어느 후보도 확정되지 않으면 추측하지 않는다.
구조화 `candidate_id`는 활성 재질의 후보 목록과 정확히 일치해야 한다.

성공 시 최신 후속 요청의 snapshot을 경계 안에서 받지만 전체 snapshot은 재질의 상태에
저장하지 않는다. 원 요청에서 복원한 `intent=GATHER_RESOURCE` 슬롯과 후속 요청에서
선택한 `resource_type`만 내부 확정 슬롯으로 유지한다.

## 4. 수명주기와 우선순위

저장 상태는 다음과 같다.

```text
ACTIVE
├─ 올바른 선택 → RESOLVED
├─ 명시적 취소 → CANCELLED
├─ FOLLOW/WAIT 전환 → CANCELLED
├─ 새 돌 재질의로 교체 → CANCELLED
├─ 만료 후 접근 → EXPIRED
└─ 최대 횟수 초과 → FAILED
```

정책은 다음과 같다.

- 동일 세션·동료당 활성 재질의는 최대 1개다.
- 초기 `clarification_count`는 1, 최대값은 2다.
- 첫 번째 불명확한 후속 입력은 같은 ID와 같은 만료 시각으로 count를 2로 올려 다시 묻는다.
- count가 2인 상태에서 다시 확정하지 못하면 `FAILED`로 닫는다.
- 후보 목록에 없는 구조화 ID는 count를 소비하지 않는다.
- TTL은 실패나 재질의 때 연장하지 않는다.
- “됐어”, “취소”, “나중에 하자”는 현재 문맥의 활성 재질의만 취소한다.
- 명확한 FOLLOW/WAIT는 요청에 오래된 `clarification_id`가 붙어 있어도 우선하며,
  현재 활성 재질의를 취소한 뒤 정상 `ACTION_READY` 응답을 반환한다.
- Recipe, Lore, Conversation 요청은 활성 재질의를 유지하고 독립적으로 처리한다.
- 새 모호한 돌 명령은 기존 활성 건을 취소하고 새 ID를 만든다.
- 자연어 후속 입력은 `clarification_id` 없이도 현재 문맥의 후보를 하나로 확정할 수 있으면
  활성 상태에 연결한다.

## 5. 저장소와 원자성

`storage.py`에 다음 내부 모델을 추가했다.

- `ClarificationStatus`
- `PendingCandidate`
- `PendingClarification`
- SQLAlchemy `ClarificationRecord`

저장하는 문맥은 다음과 같다.

- clarification ID
- 원 요청 ID와 원문
- session/player/companion ID
- 원래 route
- 확정 슬롯과 누락 슬롯
- 후보와 별칭
- count와 최대 count
- 생성·만료 시각
- 상태와 해결된 candidate ID

전체 클라이언트 snapshot은 저장하지 않는다.

`RequestStore.put_atomic()`은 요청 결과 저장과 재질의 생성·갱신을 하나의 연산으로
묶는다. 적용 대상은 생성, 재시도, 해결, 취소, 만료, 횟수 초과 및 FOLLOW/WAIT 전환이다.
저장 실패 시 성공 응답을 반환하지 않고 기존 `STORAGE_UNAVAILABLE` 경계로 전환한다.

메모리 저장소는 `asyncio.Lock`으로 동시 변경을 직렬화한다. PostgreSQL 저장소는 단일
트랜잭션, 문맥별 advisory transaction lock, 활성 상태 partial unique index를 함께
사용한다.

## 6. PostgreSQL migration

새 Alembic revision은 다음 파일이다.

```text
alembic/versions/20260718_0002_add_pending_clarifications.py
```

생성하는 테이블은 `pending_clarifications`이며 다음 인덱스를 포함한다.

| 인덱스 | 목적 |
|---|---|
| `ix_pending_clarifications_context_status` | 세션·동료의 활성 상태 조회 |
| `ix_pending_clarifications_status_expires` | 상태 및 만료 시각 조회 |
| `uq_pending_clarifications_active_context` | 세션·동료당 ACTIVE 1개 보장 |

마지막 인덱스는 `status = 'ACTIVE'`인 행에만 적용되는 PostgreSQL partial unique
index다. Alembic head는 `20260718_0002`다.

## 7. 오류 계약과 멱등성

재질의 도메인 오류는 공통 API envelope로 반환한다.

| 상황 | 코드 | HTTP |
|---|---|---:|
| ID 없음 | `CLARIFICATION_NOT_FOUND` | 404 |
| 만료 | `CLARIFICATION_EXPIRED` | 409 |
| 이미 해결 | `CLARIFICATION_ALREADY_RESOLVED` | 409 |
| 취소·실패 등 비활성 상태 | `CLARIFICATION_NOT_ACTIVE` | 409 |
| session/player/companion 불일치 | `CLARIFICATION_CONTEXT_MISMATCH` | 409 |
| 후보에 없는 구조화 선택 | `INVALID_CLARIFICATION_RESPONSE` | 422 |
| 최대 재질의 횟수 초과 | `CLARIFICATION_LIMIT_EXCEEDED` | 422 |

오류 요청도 `StoredRequest`에 `FAILED` 및 `error_code`로 저장한다. 같은
`request_id`를 재전송하거나 GET/SSE로 조회하면 저장된 trace ID와 동일한 오류
envelope를 다시 사용한다. 내부 예외나 저장소 정보는 응답에 노출하지 않는다.

## 8. 설정

다음 설정을 추가했다.

```text
CLARIFICATION_TTL_SECONDS=300
```

Pydantic 설정 허용 범위는 1~3600초다. `.env.example`에는 안전한 기본값 300을
기록했다.

## 9. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/ai_companion_server/domain.py` | 상태 enum, 요청 선택 모델, 정형 공개 재질의 모델 |
| `src/ai_companion_server/config.py` | TTL 설정과 범위 검증 |
| `src/ai_companion_server/service.py` | 생성·매칭·해결·취소·만료·오류 및 우선순위 |
| `src/ai_companion_server/storage.py` | 내부 상태 모델, 메모리/SQL 원자 저장 |
| `src/ai_companion_server/main.py` | 오류 envelope, capability, GET/SSE 오류 재사용 |
| `.env.example` | TTL 기본값 |
| `alembic/versions/20260718_0002_add_pending_clarifications.py` | 테이블과 인덱스 |
| `tests/test_clarification.py` | 공개 계약, 수명주기, 오류, 동시성 회귀 |
| `tests/test_postgres_integration.py` | migration, CRUD, 재시작 후 활성 상태 복원 |

## 10. 검증 결과

최종 품질 게이트 결과는 다음과 같다.

```text
uv run pytest -q
132 passed, 2 skipped, 1 warning

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 12 source files

uv lock --check
Resolved 48 packages

uv run alembic heads
20260718_0002 (head)

git diff --check
통과
```

스킵된 2개는 `TEST_DATABASE_URL` 또는 live OpenAI 설정이 필요한 테스트다. 현재
환경에는 PostgreSQL URL이 없어 새 PostgreSQL 통합 경로는 실행되지 않았지만, 테스트
코드는 migration의 테이블·인덱스와 저장소 재생성 후 활성 상태 복원을 검증하도록
확장했다.

경고 1건은 기존 FastAPI TestClient의 Starlette/httpx deprecation 경고다.

## 11. 후속 범위 및 주의점

다음 단계의 핵심은 `RESOLVED` 상태의 확정 슬롯을 실제 채집 행동 계약으로 연결하는
것이다.

```text
original intent=GATHER_RESOURCE
+ resolved resource_type
+ 최신 snapshot 검증
→ GATHER_RESOURCE Action Intent
→ 클라이언트 검증 및 실행
```

후속 구현 시 다음 경계를 유지해야 한다.

- 이번 구현의 해결 응답에는 의도적으로 action이 없다.
- snapshot 전체를 pending clarification이나 로그에 저장하지 않는다.
- 후보를 확정하지 못하면 추측 실행하지 않는다.
- Action Intent 발급 전에 최신 위치, 접근 가능성, 인벤토리와 작업 가능 상태를
  클라이언트 snapshot으로 검증한다.
- PostgreSQL 변경을 실제 환경에 배포하기 전에
  `uv run alembic upgrade head`와 `uv run pytest -m integration`을 실행한다.
