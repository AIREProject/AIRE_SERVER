# 2607172048 AI 동료 서버 개발 완료 기록

- 기록일: 2026-07-17 20:48
- 기록 유형: 개발 완료 기록
- 변경 범위: AI 서버 정보 기능, 세계관 접근 제어, 평가 데이터셋 및 회귀 테스트
- 구현 기준: `9477270 feat: add evaluation datasets and regression tests`
- 1단계 기준 커밋: `562c78a feat: harden companion server foundation`
- 2단계 기준 커밋: `ec60bee feat: add minimal lore pipeline`
- 기준 문서: `01_mvp_scope.md`, `03_system_architecture.md`, `04_api_contract.md`, `07_ai_development_milestones_and_weekly_plan.md`

## 1. 완료 상태 요약

FastAPI 기반 최소 수직 슬라이스와 운영 기반 보강이 완료됐다.

```text
CompanionRequest
→ HTTP 크기·버전·입력 검증
→ 규칙 기반 Route 판정
→ Recipe 또는 Lore Repository 조회
→ Lore 접근 필터 및 검색
→ Dialogue Fact 생성
→ Mock 또는 OpenAI 대사 생성
→ 결과와 처리 메타데이터 저장
→ 공통 API 응답·추적 헤더 반환
```

검증된 대표 흐름은 다음과 같다.

```text
“철 도끼 어떻게 만들어?”
→ RECIPE
→ recipe_iron_axe 조회
→ 철괴 3개, 나무 2개, 작업대 사실 생성
→ 사실에 근거한 한국어 대사 반환
→ request_id로 결과 재조회 및 SSE 최종 이벤트 확인
```

검증된 Lore 흐름은 다음과 같다.

```text
“이 마을은 왜 버려진 거야?”
→ LORE
→ snapshot에서 지역·진행도·발견 flag 추출
→ 접근 가능한 문서만 필터
→ 키워드·별칭 검색
→ 최대 3개 Lore DialogueFact 생성
→ evidence_ids/evidence_text에 근거를 남긴 대사 반환
```

1단계 변경은 커밋 `562c78a`, 2단계 변경은 커밋 `ec60bee`, 평가·회귀 테스트 완료 변경은 커밋 `9477270`에 들어 있다.

이 문서는 AI 서버 개발 완료 시점의 기록이다. 클라이언트 행동 실행, 채집, 전투, 위험 경고와 기억 시스템을 포함하는 전체 게임 개발 완료를 의미하지 않는다.

## 2. 구현된 범위

### API와 HTTP 계약

| Method | Endpoint | 현재 동작 |
|---|---|---|
| `GET` | `/v1/health` | 전체 준비 상태와 API·DB·LLM component 상태 반환 |
| `GET` | `/v1/capabilities` | 버전, 지원 route, 도구, 입력 제한 반환 |
| `POST` | `/v1/companion/requests` | 검증, 라우팅, 대사 생성, 결과 저장 |
| `GET` | `/v1/companion/requests/{request_id}` | 저장된 최종 결과 조회 |
| `GET` | `/v1/companion/requests/{request_id}/stream` | 저장된 최종 결과 1건을 SSE로 반환 |

`/v1/health`와 `/v1/capabilities`를 제외한 Companion API에는 다음 요청 헤더가 필수다.

```text
X-API-Version: 1
X-Schema-Version: 1.0
X-Game-Data-Version: 2026.07.16.1
```

모든 `/v1` 응답에는 `X-API-Version`, `X-Schema-Version`, 가능한 경우 `X-Request-Id`, `X-Trace-Id`가 붙는다.

다음 오류는 모두 `ApiResponse.error` 봉투로 반환한다.

| 오류 코드 | HTTP | 의미 |
|---|---:|---|
| `INVALID_REQUEST` | 400 | 검증 오류, 잘못된 JSON, 허용되지 않은 method |
| `API_VERSION_NOT_SUPPORTED` | 400 | API 버전 누락 또는 불일치 |
| `SCHEMA_VERSION_NOT_SUPPORTED` | 400 | 스키마 버전 누락 또는 불일치 |
| `GAME_DATA_VERSION_MISMATCH` | 409 | 게임 데이터 버전 누락 또는 불일치 |
| `REQUEST_NOT_FOUND` | 404 | 경로 또는 요청 ID 없음 |
| `PAYLOAD_TOO_LARGE` | 413 | 요청 본문 262,144바이트 초과 |
| `INTERNAL_ERROR` | 500 | 예상하지 못한 내부 처리 실패 |
| `STORAGE_UNAVAILABLE` | 503 | 저장소 읽기·쓰기 실패 |

검증 오류의 `details.field`는 `input.text` 같은 JSON 경로다. 내부 예외 메시지와 stack은 응답에 노출하지 않는다.

### 입력 제한

- 전체 요청 본문: 최대 262,144바이트
- `input.text`: 공백 제거 후 1~1,000자
- `Content-Length`와 실제 수신 바이트를 모두 JSON 파싱 전에 검사
- SSE 요청 ID가 없으면 스트림을 열지 않고 JSON 404 반환

### 도메인과 저장소

- 실제 route 판정: `COMMAND`, `RECIPE`, `LORE`, `CONVERSATION`, `UNKNOWN`
- `GAME_HELP`, `MEMORY_RECALL`, `CLARIFICATION_RESPONSE`는 enum만 있으며 실제 라우팅되지 않음
- 외부 응답 타입 `RequestData`와 내부 저장 타입 `StoredRequest`를 분리
- 저장 필드: request/trace ID, status, route, provider, model, latency, error code, result, created time
- 같은 `request_id` 재요청 시 최초 결과와 저장된 `trace_id` 재사용
- 처리 실패는 가능한 경우 `FAILED`와 `INTERNAL_ERROR`로 저장
- 저장소 자체 실패는 저장을 재시도하지 않고 503 반환

### 레시피

- 철 도끼, 모닥불, 기본 붕대 3개가 코드에 존재
- 이름·별칭 기반 단순 Entity Resolver
- 재료, 수량, 제작 시설, 근거 ID를 `DialogueFact`로 생성
- 근거가 없으면 존재하지 않는 레시피를 창작하지 않음

### 세계관 최소 파이프라인

- endpoint를 새로 만들지 않고 기존 `POST /v1/companion/requests`의 `LORE` 분기에 연결
- 기준 지역 `region_abandoned_mining_village` 1개와 12개 JSON fixture를 `src/ai_companion_server/data/lore/abandoned_mining_village.json`에서 로드
- `LoreDocument`, `LoreSnapshotContext`, `LoreAccessFilter`, `LoreRepository`는 `src/ai_companion_server/lore.py`에 있음
- snapshot에서 읽는 내부 경로는 `location.current_region_id`, `progression.stage`, `progression.discovery_flags`, `progression.discovered_lore_ids`
- 접근 조건은 동일 지역, 최소 진행도, 필수 discovery flag, `spoiler_level=1` 문서의 discovered lore ID를 모두 만족해야 함
- 접근 필터가 검색보다 먼저 실행되며, 검색 결과는 결정론적으로 최대 3개
- 근거가 없거나 snapshot 컨텍스트가 없으면 facts를 만들지 않고 Mock은 모른다는 응답을 반환
- `DialogueFact.evidence_text`를 선택 필드로 추가했으며 기존 schema `1.0`과 레시피 fact를 유지
- Mock은 허용된 `evidence_text`를 조합하고, OpenAI에는 허용된 facts만 전달함. fixture 문장 자체는 테스트용 근거 데이터이며 LLM 답변 템플릿이 아님
- Lore 데이터는 현재 메모리 로드 JSON fixture이며 DB, embedding, vector store는 아직 사용하지 않음

### LLM

- 기본 provider: 결정론적 `mock`
- Mock 메타데이터: `provider=mock`, `model=deterministic-v1`
- OpenAI Responses API와 strict JSON Schema 구조화 출력 사용
- `DialogueOutput`은 `extra="forbid"`로 OpenAI strict schema의 `additionalProperties=false` 요구를 충족
- OpenAI 실패 후 Mock을 사용하면 실제 provider/model과 `LLM_FALLBACK_USED` 기록
- health는 유료 probe를 보내지 않고 설정 유효성과 마지막 실제 호출 상태만 사용

실제 API smoke test 결과:

```text
provider=openai
model=gpt-5-nano
status=COMPLETED
error_code=null
dialogue 생성 성공
```

로컬 `.env`의 `LLM_PROVIDER`는 여전히 `mock`이다. 실제 OpenAI를 계속 사용하려면 명시적으로 `openai`로 바꾸고 프로세스를 재시작해야 한다. API 키는 절대 커밋하지 않는다.

### DB와 migration

- DB 미설정: 의도적인 인메모리 모드
- DB 설정 후 연결 실패: 환경과 무관하게 인메모리 fallback
- fallback 시 요청은 계속 처리하지만 health는 `status=degraded`, `ready=false`
- 앱 lifespan에서 `Base.metadata.create_all()`을 호출하지 않음
- `alembic/versions/20260717_0001_create_companion_requests.py`가 최초 테이블 생성
- PostgreSQL 17에서 `upgrade head → schema/CRUD → downgrade base → re-upgrade` 통합 테스트 통과

기존 `create_all` 기반 로컬 볼륨의 자동 변환은 지원하지 않는다. 빈 DB를 기준으로 migration을 적용한다.

## 3. 최종 주요 모듈과 검증 책임

| 경로 | 현재 책임 | 유지보수 검증 책임 |
|---|---|---|
| `src/ai_companion_server/main.py` | HTTP 경계, lifespan, 상태, endpoint 조립 | `LoreRepository.from_fixture()` 초기화는 유지하고 평가 fixture는 endpoint에 직접 연결하지 않음 |
| `src/ai_companion_server/domain.py` | 외부·내부 Pydantic 모델 | `DialogueFact.evidence_text`와 기존 schema 1.0 호환성을 회귀 검증 |
| `src/ai_companion_server/service.py` | route, fact 조회, LLM 호출, 저장 | 평가 case의 기대 route/evidence와 실제 결과를 비교 |
| `src/ai_companion_server/lore.py` | snapshot 접근 필터, 키워드 검색, Lore fact 변환 | 현재 키워드 baseline을 고정하고 semantic/vector 검색 도입 전후를 비교 |
| `src/ai_companion_server/llm.py` | Mock/OpenAI 생성과 fallback | provider/model/error code와 구조화 출력 실패를 회귀 검증 |
| `src/ai_companion_server/storage.py` | 요청 결과 저장 | 중복 request와 trace 재사용 회귀 검증, Lore 원문 저장 금지 |
| `tests/test_contract.py` | HTTP·오류·크기·버전 계약 | 평가 테스트가 공통 계약을 우회하지 않도록 유지 |
| `tests/test_service.py`, `tests/test_lore.py` | 서비스·접근 필터·LLM fact 격리 | case ID 기반 parameterized 회귀 테스트의 기반으로 사용 |
| `tests/test_postgres_integration.py` | migration과 CRUD | Lore fixture 평가와 분리; DB 동작 회귀만 유지 |

의존성 방향 `main → service → domain/repository/llm`을 유지한다. FastAPI와 OpenAI SDK 타입을 Lore repository나 접근 필터로 유출하지 않는다.

## 4. 실행과 검증

### 기본 Mock 모드

```powershell
uv sync --dev
Copy-Item .env.example .env
uv run uvicorn ai_companion_server.main:app --reload
```

Swagger UI는 `http://127.0.0.1:8000/docs`에서 확인한다.

### PostgreSQL 모드

```powershell
docker compose up -d postgres
```

`.env` 설정:

```dotenv
DATABASE_URL=postgresql+asyncpg://companion:companion@localhost:5432/companion
```

서버 시작 전에 migration을 적용한다.

```powershell
uv run alembic upgrade head
uv run uvicorn ai_companion_server.main:app --reload
```

Docker Desktop에서 Compose 컨테이너의 `5432:5432` 게시가 실제 바인딩되지 않는 현상이 한 번 있었다. PostgreSQL 통합 테스트는 별도 임시 PostgreSQL 17 컨테이너의 `127.0.0.1:55432`를 사용해 통과했다. 로컬 접속이 실패하면 먼저 `docker compose port postgres 5432`와 `docker inspect`의 port binding을 확인한다.

### Companion 요청 예시

```powershell
$body = Get-Content .\tests\fixtures\recipe_request.json -Raw
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/v1/companion/requests `
  -ContentType 'application/json' `
  -Headers @{
    'X-API-Version' = '1'
    'X-Schema-Version' = '1.0'
    'X-Game-Data-Version' = '2026.07.16.1'
  } `
  -Body $body
```

### 필수 검증

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy src
uv lock --check
```

현재 결과:

- 일반 테스트: 101 passed, PostgreSQL integration·live LLM 각 1 skipped
- 별도 PostgreSQL 17 integration: 1 passed
- Ruff: passed
- mypy strict: passed
- `uv lock --check`: passed
- 알려진 경고: FastAPI TestClient의 `httpx` deprecation 경고 1건

통합 테스트 실행:

```powershell
$env:TEST_DATABASE_URL='postgresql+asyncpg://companion:companion@127.0.0.1:5432/companion_test'
uv run pytest -m integration -q
```

## 5. 2단계 완료 — 세계관 최소 파이프라인

### 데이터 범위

- 기준 지역 1개
- 세계관 문서 10~20개
- JSON fixture로 코드와 분리
- 진행 단계 최소 2개
- 스포일러 단계 최소 2개

현재 fixture의 각 문서는 다음 정보를 가진다.

```text
lore_id
region_id
title 또는 subject
keywords / aliases
required_discovery_flags
minimum_progression 또는 progression 범위
spoiler_level
evidence_text
```

현재 기준 fixture는 기존 시나리오에 나온 버려진 광산 마을의 12개 문서다. `evidence_text`는 작성자가 입력한 검증 근거이며 답변 템플릿으로 취급하지 않는다. Mock 모드에서는 결정론성을 위해 이 근거를 조합하고, OpenAI 모드에서는 허용된 facts만 사용해 대사를 생성한다.

문서 원문 전체를 로그나 저장 메타데이터에 넣지 않는다. 외부 응답 facts에는 필요한 `evidence_text`와 `evidence_ids`만 담는다.

### 권장 처리 순서

```text
LORE route
→ 요청 snapshot에서 progression·discovery flags 추출
→ LoreAccessFilter로 접근 불가 문서 제거
→ 허용된 문서만 키워드 검색
→ 상위 근거를 DialogueFact로 변환
→ 필터된 fact만 LLM에 전달
→ 근거 없음이면 모른다는 결정론적 응답
```

검색보다 접근 필터가 반드시 먼저다. 검색 결과를 만든 뒤 스포일러를 제거하면 ranking, prompt 또는 로그를 통해 금지 정보가 노출될 수 있다.

### 현재 구현 구조

```text
src/ai_companion_server/lore.py
  LoreDocument
  LoreRepository
  LoreAccessFilter
  LoreRepository.facts_for()

data 또는 src package 하위 fixture
  `src/ai_companion_server/data/lore/abandoned_mining_village.json`

tests/test_lore.py
  허용 / 차단 / 근거 없음 / 별칭 / 다른 지역
```

현재 구현은 앱 lifespan에서 JSON fixture를 메모리에 로드한다. DB 테이블과 vector store는 추가하지 않았다. 향후 일반 Markdown 문서나 YAML front matter를 원천 형식으로 사용하더라도 `LoreDocument`로 변환하는 ingest 단계를 두고, `LoreRepository.facts_for()` 경계를 유지한다. 벡터 검색을 도입할 때도 snapshot 접근 필터와 metadata pre-filter를 검색보다 먼저 실행해야 한다.

### 완료 조건

- 허용된 질문은 `evidence_ids`와 함께 1~3문장으로 답한다.
- 미발견·진행도 미달·스포일러 문서는 facts와 LLM prompt에 들어가지 않는다.
- 근거가 없거나 접근 불가능하면 세계관 사실을 창작하지 않는다.
- Mock 테스트는 결정론적이다.
- 실제 OpenAI 테스트는 기본 pytest와 분리하고 키가 있을 때만 실행한다.
- 기존 API 버전·오류·본문 크기·trace 계약이 그대로 통과한다.

현재 2단계 검증 결과는 `29 passed, 1 skipped`이며 Ruff, strict mypy, `uv lock --check`도 통과했다.

## 6. 3단계 완료 — 평가 데이터셋과 회귀 테스트

3단계는 Router 분류 품질과 레시피·세계관 사실성을 사례 단위로 측정하고 외부 dependency 실패를 회귀 테스트로 고정한 완료 단계다.

시작 기준은 `ec60bee`, 최종 구현 기준은 `9477270`이다. 평가 테스트는 운영 pipeline의 repository와 접근 필터를 그대로 사용한다.

### 평가 데이터셋

- Router 발화 약 100개를 route별 fixture로 작성
- 레시피 질문 약 30개를 이름, 별칭, 없는 항목, 모호한 항목으로 구분
- 세계관 질문 약 30개를 허용, 접근 차단, 근거 없음으로 구분
- 각 사례에 안정적인 case ID와 기대 route·근거·차단 사유를 부여
- 한국어를 기본으로 하되 현재 지원하는 영어 키워드 사례도 일부 포함

최종 fixture 형태:

```text
case_id
input_text
expected_route
expected_evidence_ids
expected_error 또는 expected_safety_behavior
snapshot/progression/discovery_flags
notes
```

Lore case의 `snapshot`은 현재 구현이 읽는 다음 구조를 사용한다.

```json
{
  "location": {"current_region_id": "region_abandoned_mining_village"},
  "progression": {
    "stage": 1,
    "discovery_flags": ["read_mine_warning"],
    "discovered_lore_ids": ["lore_mine_warning"]
  }
}
```

`LoreRepository.search()`는 접근 가능한 문서만 대상으로 제목·subject·alias·keyword를 검색하고 최대 3개를 결정론적으로 반환한다. 지역이 없거나 snapshot 값이 잘못되면 보수적으로 빈 facts가 된다. 따라서 기대값에는 단순 route뿐 아니라 `expected_evidence_ids`, 접근 차단 사유, snapshot 컨텍스트를 반드시 기록한다.

### 회귀 테스트 범위

- Router 오분류와 confidence 경계
- 레시피 이름·별칭·없는 항목·모호한 항목
- Lore 접근 허용·진행도 미달·미발견·스포일러 차단·근거 없음
- LLM timeout과 provider 예외
- OpenAI 구조화 출력의 잘못된 JSON 또는 schema 불일치
- DB 검색·저장 실패
- 중복 `request_id`와 저장된 trace 재사용
- fallback 시 실제 provider/model과 `LLM_FALLBACK_USED` 기록
- 오류 응답에 내부 예외·원문 입력·전체 snapshot이 노출되지 않는지 확인

추가로 현재 구현의 한계를 baseline으로 기록한다.

- 검색은 embedding이 아닌 공백 제거·casefold 기반 키워드/별칭 검색이다.
- Lore 접근 판정은 `region_id`, `minimum_progression`, `required_discovery_flags`, `spoiler_level=1`의 discovered ID를 모두 사용한다.
- 결과 facts에는 `kind=LORE`, `subject_id=lore_id`, `evidence_ids=[lore_id]`, 선택적 `evidence_text`가 들어간다.
- 검색 품질 개선을 구현할 때는 기존 keyword baseline과 새 결과를 같은 case ID로 비교한다.

### 테스트 운영 원칙

- Mock 기반 기본 테스트는 항상 결정론적으로 실행한다.
- 실제 OpenAI 테스트는 등록된 `live_llm` marker와 `RUN_LIVE_LLM=1` opt-in으로 기본 테스트에서 분리한다.
- API 키가 없으면 실제 OpenAI 테스트만 skip하고 기본 회귀 테스트는 모두 실행한다.
- 실제 OpenAI 테스트 결과는 대사 문구 전체 일치가 아니라 schema, provider, model, fallback 여부와 안전 조건을 검증한다.
- 외부 API 상태가 일반 `pytest` 결과를 비결정적으로 만들지 않게 한다.
- 실패 사례는 case ID로 추적해 수정 전후 결과를 비교할 수 있게 한다.
- 평가 결과에는 전체 정확도뿐 아니라 route별 confusion, Lore 차단 0건, 레시피 사실 오류 0건, fallback 발생 여부를 집계한다.

### 완료 조건

- 전체 Mock 회귀 테스트가 로컬 또는 CI 단일 명령으로 실행된다.
- Router 오분류와 레시피·세계관 사실성 오류를 case ID로 추적할 수 있다.
- 접근 불가능한 Lore가 facts, prompt, 응답에 포함되지 않는다.
- 외부 API 장애와 잘못된 구조화 출력에서 안전한 fallback이 재현된다.
- 실제 OpenAI smoke/integration 테스트가 기본 테스트와 분리되어 선택 실행된다.
- 기존 HTTP 계약, migration, 저장 메타데이터 테스트가 계속 통과한다.

### 3단계 구현 결과

- `tests/fixtures/evaluation/`에 Router 100건, Recipe 30건, Lore 30건의 JSONL fixture를 추가했다.
- 모든 사례는 안정적인 case ID, 기대 route/evidence, 금지 evidence, safety behavior, severity, tag를 가진다.
- Router 결과는 100/100 정답으로 accuracy 100%, macro F1 100%, P0 recall 100%, 안전 치명 오류 0건이다.
- Recipe 30건에서 재료·수량·시설·evidence 오류와 없는·모호한 레시피 창작이 0건이다.
- Lore 30건에서 접근 불가 evidence의 facts·LLM 입력·대사 노출이 0건이다.
- timeout, provider 예외, 빈 출력, 잘못된 JSON, schema 불일치가 모두 결정론적 Mock fallback으로 재현된다.
- 저장 실패, 중복 request, trace 재사용, API·로그 내부 정보 비노출 회귀를 case ID로 고정했다.
- 기본 검증 결과는 `101 passed, 2 skipped`이며 skip은 PostgreSQL integration과 opt-in live LLM 각 1건이다.
- Ruff, strict mypy, `uv lock --check`가 모두 통과했다.

실제 OpenAI 검증은 다음처럼 명시적으로 실행한다.

```powershell
$env:RUN_LIVE_LLM='1'
uv run pytest -m live_llm -q
```

## 7. 유지보수 시 주의할 점

1. `snapshot`은 여전히 외부 자유 형식 `dict[str, Any]`다. 평가 fixture는 `LoreSnapshotContext.from_snapshot()`이 읽는 경로만 사용하고 외부 요청 schema를 성급히 바꾸지 않는다.
2. `DialogueFact.evidence_text`는 선택 필드로 schema `1.0`에 추가되어 있다. 평가에서 facts 전체를 문자열 일치시키지 말고 `kind`, `evidence_ids`, 접근 안전성, provider/model을 검증한다.
3. `RequestService`는 이미 Lore repository를 주입받는다. 평가 코드에서 repository나 접근 필터를 우회해 기대값을 만들지 않는다.
4. OpenAI fallback 성공은 API 성공과 다르다. 저장 레코드의 `provider`, `model`, `error_code`를 확인해 실제 OpenAI 사용 여부를 판정한다.
5. `get_settings()`와 전역 `app`은 import 시 설정을 잡는다. `.env`의 provider나 DB를 바꾼 뒤에는 서버 프로세스를 재시작한다.
6. DB schema 변경이 필요하면 기존 revision을 수정하지 말고 새 Alembic revision을 추가한다.
7. 요청 로그에는 사용자 원문, 전체 snapshot, lore 원문을 넣지 않는다. request/trace/route/provider/model/latency/error code만 유지한다.
8. SSE는 아직 실제 token streaming이 아니다. Lore 구현 범위를 streaming 개선으로 넓히지 않는다.
9. 기본 테스트는 외부 API 없이 실행되어야 한다. 실제 OpenAI는 `live_llm`으로만 선택 실행하고 API 키가 없으면 해당 테스트만 skip한다.
10. 평가 fixture를 추가할 때 미발견 Lore의 `evidence_text`나 ID가 기대 prompt/facts에 섞이지 않는지 별도 assertion을 둔다.

## 8. 남아 있는 기술 부채와 범위 밖 항목

- Router는 명시적 우선순위의 키워드 기반이며 현재 100건 평가 세트에 맞춰 정량 baseline을 고정함
- 레시피는 3개만 코드에 하드코딩
- Lore는 1개 지역·12개 JSON fixture와 키워드/별칭 검색만 제공하며 semantic/vector 검색은 없음
- Lore fixture를 Markdown/YAML 원천 문서에서 자동 import하는 도구가 아직 없음
- `COMMAND`는 ActionIntent를 생성하지 않음
- SSE는 완료 이벤트 1건만 반환
- `live_llm` marker는 등록됐으며 실제 호출은 비용과 외부 변동성 때문에 opt-in으로만 실행
- LLM 오류 원인은 외부 응답에서 숨기며 내부에는 `LLM_FALLBACK_USED`만 기록
- 장기 기억, 음성, Redis, worker, GraphRAG는 현재 범위 밖
