# 2607201351 Mock Client 계약 검증기 개발 기록

- 기록일: 2026-07-20 13:51
- 기록 유형: 기능 개발 완료 기록
- 구현 기준: Mock Client 계약 검증기 작업 트리
- 변경 범위: 독립 Action Intent 검증, tool별 schema, 중복 방지, 최신 snapshot 검증,
  서버 요청 fallback 및 계약 테스트
- API/스키마 버전: `1` / `1.0` 유지
- migration 및 신규 환경 변수: 없음

## 1. 완료 상태 요약

서버의 `ActionIntent` Pydantic 모델을 가져오지 않고 원시 mapping을 검증하는
`ActionContractValidator`를 추가했다. 검증기는 `follow_player`, `wait`,
`gather_resource` Action Intent를 capabilities와 로컬 schema 양쪽에 대조하고, 최신
snapshot으로 최종 실행 가능 여부를 판정한다.

```text
원시 Action Intent
→ 공통 계약
→ capabilities 및 로컬 schema
→ tool parameters
→ 중복
→ 만료
→ 최신 snapshot
→ TASK_STARTED 또는 TASK_FAILED
```

이 모듈은 합성 결과만 만들며 실제 이동·대기·채집, 게임 이벤트 전송 또는 snapshot과
인벤토리 변경을 수행하지 않는다.

## 2. 공통 검증 순서와 실패 계약

검증 순서는 다음과 같이 코드에 고정했다.

1. 공통 필수 필드, `type == tool`, timezone이 있는 날짜,
   `issued_at < expires_at`, `client_validation_required=true`
2. capabilities의 `supported_tools`와 클라이언트에 등록된 tool schema
3. tool별 parameters
4. 이미 시작된 `action_id`
5. 만료
6. 동료 및 tool별 최신 snapshot 조건

공통 필드 또는 parameters가 맞지 않으면 `ACTION_INVALID`, capabilities나 로컬
schema에서 지원하지 않으면 `ACTION_NOT_SUPPORTED`를 반환한다. 그 밖의 실패 코드는
다음과 같다.

```text
ACTION_ALREADY_EXECUTED
ACTION_EXPIRED
COMPANION_INCAPACITATED
TARGET_NOT_FOUND
TARGET_UNREACHABLE
INVENTORY_FULL
```

모든 결과에는 `request_id`, `action_id`, 결정론적 `task_id` 상관관계가 포함된다.
공통 계약이 유효하지 않아 일부 ID를 읽을 수 없는 경우에도 로컬 실패 결과를 안전하게
만들 수 있도록 대체 상관관계 값을 사용한다.

## 3. Tool별 계약

| tool | parameters | 최신 snapshot 조건 |
|---|---|---|
| `follow_player` | 빈 객체 | 동료가 명령 수신 가능하고 행동 불능이 아님 |
| `wait` | `duration_mode=until_new_command` | 동료가 명령 수신 가능하고 행동 불능이 아님 |
| `gather_resource` | 대상 ID와 수량 모드 | 동료 상태, 대상 존재·접근성, 인벤토리 수용 가능 |

`gather_resource`는 기존 수량 계약을 그대로 적용한다.

- `exact`: 양의 정수 `quantity` 필수
- `some`: `quantity` 금지
- `until_inventory_full`: `quantity` 금지
- `goal_required`: 비어 있지 않은 `goal_ref` 필수, `quantity` 금지

tool별 허용 필드 이외의 parameters도 `ACTION_INVALID`로 거부한다.

## 4. 중복 방지와 상태 관리

검증기 인스턴스는 수락된 Action의 `action_id → task_id`를 프로세스 메모리에 보관한다.
동시 전달에서도 하나의 Action만 시작되도록 잠금 안에서 최종 확인과 기록을 수행한다.

- `TASK_STARTED`를 만들 때만 `action_id`를 기록한다.
- snapshot 조건 등으로 거부된 ID는 기록하지 않는다.
- 거부 원인이 회복되고 아직 만료되지 않았다면 같은 Action을 다시 검증할 수 있다.
- 이미 수락한 Action은 새 작업을 만들지 않고 `ACTION_ALREADY_EXECUTED`와 최초
  `task_id`를 반환한다.

저장소 영속화와 여러 프로세스 사이의 공유는 실제 게임 클라이언트 통합 범위로
남겼다.

## 5. 서버 요청 fallback

`MockServerRequestAdapter`는 요청 라이브러리에 종속되지 않는 작은 HTTP response
protocol을 사용한다. 자동 재시도 없이 다음 로컬 결과로 종료한다.

| 원인 | fallback code | `retryable` |
|---|---|---|
| timeout | `TIMEOUT` | `true` |
| HTTP 503/504 | `SERVER_ERROR` | `true` |
| 구조화 오류의 `retryable=true` | `SERVER_ERROR` | `true` |
| HTTP 500 및 그 밖의 서버 오류 | `SERVER_ERROR` | `false` |

HTTP 성공 상태여도 `ok=false`이거나 구조화된 `error`가 있으면 서버 오류로 처리한다.
fallback에는 사용자 안내 문구가 포함되며 Action은 포함하지 않는다. adapter는
accepted-action 저장소와 snapshot에 접근하지 않으므로 fallback으로 인한 상태 변경이
없다. 재시도를 선택할 경우 같은 `request_id`를 사용해야 한다.

## 6. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/ai_companion_server/client_contract.py` | 독립 검증기, 결과 모델, 중복 저장소, 요청 adapter |
| `tests/mock_client.py` | 기존 helper를 새 검증기 조립 계층으로 축소 |
| `tests/test_client_contract.py` | tool, 우선순위, 중복, snapshot, fallback 계약 테스트 |
| `tests/test_gather_resource.py` | 유효한 만료 Action fixture로 기존 회귀 보정 |
| `docs/04_api_contract.md` | 신규 코드, 검증 순서, 중복 및 fallback 공개 계약 |

API 버전, schema 버전, 데이터베이스 구조와 환경 변수는 변경하지 않았다.

## 7. 검증 결과

```text
uv run pytest
185 passed, 2 skipped, 1 warning

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 13 source files

uv lock --check
Resolved 48 packages

git diff --check
통과
```

스킵된 두 건은 기존 PostgreSQL integration 및 live LLM opt-in 테스트다. 경고 한 건은
기존 Starlette/httpx deprecation 경고다.

## 8. 범위 밖 및 후속 작업

- 실제 게임 행동 실행과 게임 상태 변경
- `POST /v1/companion/events` 전송 및 작업 진행·완료·취소 수명주기
- 자동 재시도와 backoff
- accepted-action 저장소 영속화 및 다중 프로세스 공유
- 실제 게임 클라이언트 엔진과의 통합

이번 구현은 클라이언트 최종 계약 판정과 안전한 로컬 종료까지만 담당한다.
