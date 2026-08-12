# 2607181749 GATHER_RESOURCE Mock Action Intent 개발 기록

- 기록일: 2026-07-18 17:49
- 기록 유형: 기능 개발 완료 기록
- 구현 기준: `feat: add gather resource action intent` (본 커밋)
- 변경 범위: 결정론적 채집 명령, 수량 슬롯, 재질의 Action 발급, 원자 저장,
  테스트용 Mock Client 계약 및 회귀 테스트
- API/스키마 버전: `1` / `1.0` 유지
- migration 및 신규 환경 변수: 없음

## 1. 완료 상태 요약

명확한 한국어 채집 명령과 기존 돌 재질의 해결 결과를 하나의 결정론적
`gather_resource` Action Intent 생성 경로로 연결했다.

```text
명확한 채집 명령 → 대상·수량 해석 → 공통 Action 생성 → ACTION_READY
모호한 돌 명령 → 수량 보존 → 돌 종류 선택 → 슬롯 병합 → ACTION_READY
```

재질의 해결에서는 `RESOLVED` 상태와 요청 결과를 원자적으로 저장한다. 서버는 실제
채집, 자원 노드 선택 또는 경로 탐색을 하지 않는다. 이 경로는 LLM을 호출하지 않고
`provider=builtin`, `model=command-template-v1`을 사용한다.

## 2. 직접 명령과 Action Intent 계약

| 대표 표현 | `target_id` |
|---|---|
| `나무`, `목재` | `resource_wood` |
| `돌 원석`, `돌원석` | `resource_stone` |
| `돌 블록`, `돌블록` | `item_stone_block` |
| `부싯돌` | `resource_flint` |

채집 동사는 `모아`, `캐`, `가져와` 계열을 지원한다. 특정 종류 없이 `돌`만 지시하면
기존 대상 선택 재질의로 전환한다.

```text
나무 20개 모아 줘 → exact, quantity=20
목재 좀 모아 → some
가방 찰 때까지 목재 모아 → until_inventory_full
```

양의 정수는 `exact`, 수량 생략 또는 `좀`은 `some`이다. 이번 파서는
`goal_required`를 생성하지 않는다.

Action Intent는 `type=tool=gather_resource`, 10초 TTL,
`client_validation_required=true`를 사용한다. `parameters.target_id`와
`parameters.quantity_mode`가 필수이며, `exact`일 때만 양의 정수 `quantity`를
허용한다. `goal_required`는 `goal_ref`를 요구하도록 스키마에서 검증한다.

`source_request_id`는 Action을 실제 발급한 요청 ID다. 재질의 경로에서는 최초 명령이
아니라 후보를 선택한 후속 요청 ID가 된다. capabilities의 지원 tool 목록은 다음이다.

```json
["follow_player", "wait", "gather_resource"]
```

## 3. 돌 재질의 해결과 저장

모호한 돌 명령의 `PendingClarification.confirmed_slots`에 수량 모드와 필요한 수량을
보존한다. `돌 20개 모아 줘 → 돌 원석`에서는 수량 20이 유지되고, 수량을 생략한
명령은 `quantity_mode=some`으로 해결된다. 자연어 선택과 구조화 `candidate_id`는
같은 Action Intent 계약을 만든다.

해결 응답은 다음 불변 조건을 가진다.

- `route=CLARIFICATION_RESPONSE`
- `status=ACTION_READY`
- `action.tool=gather_resource`
- `clarification=null`
- PendingClarification 상태는 `RESOLVED`

활성 재질의 중 새 직접 채집 명령이 들어오면 기존 건을 `CANCELLED`로 닫는다. 기존
만료, 취소, 잘못된 후보와 최대 횟수 오류 계약은 유지한다.

기존 `confirmed_slots_json`과 `missing_slots_json`을 사용하므로 migration은 없다.
SQLAlchemy 저장소도 해결된 두 JSON 필드를 갱신하도록 보완했다. 후속 요청의
`ACTION_READY` 결과와 `RESOLVED` 상태는 `put_atomic()` 한 번으로 저장한다.

같은 `request_id`를 재전송하면 저장된 trace와 Action 전체를 재사용하므로 새
`action_id`를 만들지 않는다. 전체 snapshot은 재질의 상태나 완료 로그에 저장하지
않는다.

## 4. 테스트용 Mock Client 계약

`tests/mock_client.py`에 프로덕션 코드와 분리된 helper를 추가했다. 최신 snapshot에서
지원 tool, 만료, 동료의 명령 수신/행동 불능 상태, 동일 target 존재와 접근성,
인벤토리 수용 가능 여부를 읽기만 한다. 실제 채집이나 상태 변경은 수행하지 않는다.

수락은 합성 `TASK_STARTED`, 거부는 합성 `TASK_FAILED`를 반환하며 `request_id`,
`action_id`, `task_id` 상관관계를 포함한다. 거부 코드는 다음과 같다.

```text
ACTION_NOT_SUPPORTED
ACTION_EXPIRED
COMPANION_INCAPACITATED
TARGET_NOT_FOUND
TARGET_UNREACHABLE
INVENTORY_FULL
```

중복 `action_id`, 범용 tool schema 검증, timeout fallback은 후속 범위다.

## 5. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/ai_companion_server/command_intent.py` | 채집 대상·수량 파서 |
| `src/ai_companion_server/domain.py` | Action 파라미터 조건부 검증 |
| `src/ai_companion_server/service.py` | 공통 Action 생성과 재질의 해결 |
| `src/ai_companion_server/storage.py` | 해결 슬롯 JSON 원자 갱신 |
| `src/ai_companion_server/main.py` | capabilities 갱신 |
| `tests/mock_client.py` | 테스트 전용 클라이언트 helper |
| `tests/test_gather_resource.py` | 직접 명령, 재질의, 멱등성, Mock 계약 |
| `tests/test_command_intent.py` | 채집 파서 회귀 |
| `tests/test_clarification.py` | 수량 보존과 ACTION_READY 계약 |
| `tests/test_api.py` | capabilities 계약 |
| `tests/test_postgres_integration.py` | 해결 슬롯 DB 복원 검증 |
| `docs/04_api_contract.md` | 공개 계약과 Mock 결과 흐름 |

## 6. 검증 결과

```text
uv run pytest
156 passed, 2 skipped, 1 warning

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 12 source files

uv lock --check
Resolved 48 packages

git diff --check
통과
```

스킵된 2개는 PostgreSQL integration과 live LLM 테스트다. PostgreSQL 실행 환경은
없었지만 해결 슬롯 복원 검증을 통합 테스트 코드에 추가했다. 경고 1건은 기존
Starlette/httpx deprecation 경고다.

## 7. 범위 밖 및 후속 작업

- 실제 `/v1/companion/events` 수신 API
- 실제 채집, 경로 탐색, 자원 노드 선택과 게임 상태 변경
- 작업 진행·완료·취소 처리
- 중복 `action_id`와 범용 tool schema 검증
- client timeout fallback
- 한국어 대표 표현 이외의 자연어 확장

프로덕션 재사용 검증기와 작업 이벤트 수명주기는 후속 “Mock Client 계약 검증기”
범위에서 다룬다.
