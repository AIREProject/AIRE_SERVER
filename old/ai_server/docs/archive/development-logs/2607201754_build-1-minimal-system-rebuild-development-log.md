# 2607201754 Build 1 최소 시스템 재구성 개발 기록

- 기록일: 2026-07-20 17:54
- 기록 유형: 기능 개발 완료 기록
- 변경 범위: `/v1` 최소 API, 마코 대화와 명령, 자원 재질의, 작업 결과 이벤트,
  문서·의존성·테스트 재구성
- 구현 기준: Build 1 최소 시스템 재구성 작업 트리
- API/스키마 버전: URL 경로 `/v1`만 사용
- 후속 범위: `docs/backlog/future_features.md`에 기록

## 1. 완료 상태 요약

기존의 요청 저장·조회 중심 계약을 폐기하고 Build 1에 필요한 두 엔드포인트만 남겼다.

```text
POST /v1/companion/message
POST /v1/companion/event
```

메시지 API는 한국어 텍스트로 일반 대화, 철 도끼 제작법, 알려진 지역 세계관, 따라오기,
대기, 활성 작업 중지와 일반 나무·돌 채집을 처리한다. 작업 이벤트 API는 클라이언트가 보낸
나무·돌 채집 성공 또는 실패 결과를 결정론적 대사로 변환한다.

캐릭터 이름은 마코로 통일했다. 명령, 재질의, 레시피, 세계관과 작업 결과는 정적 데이터와
결정론적 템플릿을 사용하고, 일반 대화만 Mock 또는 OpenAI LLM을 사용한다. 공개 응답에는
provider, model, trace ID와 내부 fact를 노출하지 않는다.

## 2. 공개 계약과 주요 결정

`client_context`는 다음 선택 필드만 허용한다.

- `location_id`: 안전한 정적 세계관 설명 선택
- `active_task`: 중지할 작업의 `id`와 `type`
- `clarification_id`: 직전 자원 종류 재질의 답변

공개 Pydantic 모델은 추가 필드를 금지한다. 따라서 `target`, `target_id`, 주변 자원 목록,
snapshot과 선택 대상 정보가 들어오면 FastAPI 기본 검증 응답인 HTTP 422로 종료한다.

허용 Action은 `follow_player`, `wait`, `stop_current_task`, 그리고 `resource_type`이
`wood` 또는 `stone`인 `gather_resource`뿐이다. 나무와 돌은 자원 종류이며 특정 월드
엔티티가 아니다. 가까운 엔티티 탐색, 경로 탐색, 접근 가능성 판단, `task_id` 생성과
`IDLE/RUNNING/COMPLETED/FAILED/CANCELLED` 상태 관리는 클라이언트 책임으로 정했다.

## 3. 재질의와 미지원 처리

`저것 좀 캐 줘`처럼 자원 종류가 불명확한 요청은 Action 없이 다음 두 UI 문자열을 하나의
배열로 반환한다.

```json
{ "options": ["나무", "돌"] }
```

서버는 재질의 ID와 `original_intent=gather_resource`, `missing_field=resource_type`만
프로세스 메모리에 유지한다. 후속 `나무`와 `돌`은 각각 `wood`와 `stone`으로 변환한다.
다른 답변은 재질의를 반복하지 않고 지원 표현을 안내한 뒤 종료한다. 취소나 새 명확한 명령은
후속 요청에 재질의 ID가 없더라도 단일 세션의 pending 상태를 삭제한다. 서버 재시작 시 상태가
사라지는 것은 Build 1의 의도된 제한이다.

돌 블록, 부싯돌, 광석, 다른 명시적 자원, 정확한 수량, 가방이 찰 때까지와 목표 제작물만큼
채집은 미지원으로 종료한다. 철 도끼 레시피는 재료와 작업대 설명만 제공하며 현재 제작 가능
판정은 하지 않는다.

## 4. 제거한 기존 구성

다음 기존 계약과 운영 구성은 Build 1에서 제거했다.

- health, capabilities, 요청 결과 조회, SSE 엔드포인트
- API/schema/game-data 버전 헤더와 trace/request 응답 헤더
- 요청 멱등 저장소, 재질의 DB 영속화와 상태/TTL/후보 ID 모델
- SQLAlchemy, asyncpg, Alembic, PostgreSQL과 Docker Compose 구성
- Mock Client snapshot 검증기와 `TARGET_*` 오류
- 수량 모드, 세부 돌 후보, `target_id`와 주변 자원 접근성 검사

`pyproject.toml`, `.env.example`과 `uv.lock`에서 제거된 구성과 의존성을 정리했다. OpenAI와
Mock LLM 공급자 설정은 유지했다.

## 5. 문서와 변경 파일

현행 구현 기준은 `docs/current/`의 네 문서로 축소했다.

| 파일/영역 | 변경 내용 |
|---|---|
| `src/ai_companion_server/domain.py` | 최소 요청·응답·Action·이벤트 모델 |
| `src/ai_companion_server/service.py` | 메시지 라우팅, 재질의 메모리, 이벤트 처리 |
| `src/ai_companion_server/command_intent.py` | Build 1 명령과 미지원 자원/수량 판별 |
| `src/ai_companion_server/main.py` | 두 공개 `/v1` POST 엔드포인트 |
| `src/ai_companion_server/llm.py` | 마코 일반 대화용 Mock/OpenAI 공급자 |
| `src/ai_companion_server/recipes.py` | 철 도끼 정적 설명 |
| `src/ai_companion_server/lore.py` | `location_id` 기반 안전한 정적 설명 |
| `tests/test_api.py` | Build 1 공개 계약 회귀 테스트 19건 |
| `docs/current/` | 범위, API 계약, 런타임 흐름, 테스트 체크리스트 |
| `docs/backlog/future_features.md` | 타겟·수량·영속화 등 미구현 기능 |

기존 상세 목표 설계는 `docs/archive/target_architecture_reference.md`로 이동하고 현재 구현 계약이
아니라는 표시를 추가했다. 기존 개발 로그는 당시 결정의 기록으로 그대로 보존했다.

## 6. 검증 결과

```text
uv run pytest
19 passed, 1 warning

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 11 source files

uv lock --check
Resolved 42 packages

git diff --check
통과
```

경고 한 건은 Starlette TestClient의 향후 `httpx2` 전환을 알리는 deprecation 경고이며 테스트
실패에는 영향을 주지 않는다.

## 7. 후속 범위 및 주의점

화면 선택 대상, 핑, Entity Resolver, 엔티티 ID, 인벤토리 연동, 수량 채집, 세부 자원,
제작 가능 판정, 다중 세션, 재질의 영속화와 운영용 조회/스트리밍 API는 Build 1 범위가 아니다.
실제 클라이언트 요구가 확정된 뒤 별도 마일스톤과 공개 계약으로 추가해야 한다.

현재 재질의 저장소는 서버 프로세스 하나와 게임 세션 하나를 전제로 한다. 프로세스 재시작과
다중 worker에서는 pending 재질의 공유를 보장하지 않는다.
