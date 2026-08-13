# 01. 구현 지도

## 1. 한 줄 구조

`ai_server`는 FastAPI transport, 기기 인증, SQLite 영속화, Mako 두뇌, LLM provider,
대화 전사와 기억 증류를 한 프로세스에 조립한 모듈러 모놀리스다.

```text
FastAPI routes
  -> request/auth/session dependencies
  -> CompanionService / PairingService / OfflineTaskService / AdminCrudService
  -> CompanionBrain and repositories
  -> SQLite, JSONL transcript, OpenAI-compatible LLM/embedding provider
```

## 2. 조립과 의존 방향

| 영역 | 책임 | 코드 근거 |
|---|---|---|
| 앱 조립 | 설정, DB, game dataset, service, middleware, router 조립 | [`app/main.py`](../ai_server/app/main.py) |
| HTTP/WS | 요청 모델 수신, 인증 의존성, request ID 일치 확인 | [`app/routes/`](../ai_server/app/routes) |
| 경계 번역 | `ChatRequest`를 `CompanionTurn`으로 변환하고 응답/Command 후보 조립 | [`app/service.py`](../ai_server/app/service.py) |
| 두뇌 | 의도 분류, 대사, 작업기억, 전사, 장기기억 증류 | [`app/brain/companion.py`](../ai_server/app/brain/companion.py), [`app/brain/graph.py`](../ai_server/app/brain/graph.py) |
| 인증 | Bearer 조회, HMAC 검증, role과 profile/device 신원 확정 | [`app/dependencies.py`](../ai_server/app/dependencies.py), [`app/credentials.py`](../ai_server/app/credentials.py) |
| 영속화 | 기기, 페어링, 세이브, 게임 데이터, 기억, Offline Task | [`app/db/models.py`](../ai_server/app/db/models.py), [`migrations/versions/`](../ai_server/migrations/versions) |
| 운영 경계 | body 제한, timeout, request ID, 구조화 오류와 로그 | [`app/middleware.py`](../ai_server/app/middleware.py), [`app/errors_http.py`](../ai_server/app/errors_http.py) |

의존 방향은 대체로 `routes -> services -> brain/repositories`다. 두뇌가 FastAPI route를 직접
참조하지 않는 점은 유지할 가치가 있다. 다만 `CompanionService.from_settings()`가 LLM,
embedding, transcript, memory DB adapter, game repository를 모두 직접 조립해 composition root와
application service의 책임이 섞여 있다.

## 3. Chat 요청 흐름

1. `RequestContextMiddleware`가 `X-Request-ID`, body 크기, 전체 HTTP timeout을 적용한다.
2. route가 body의 `request_id`와 header를 대조한다.
3. Bearer token의 lookup ID로 DB row를 찾고 HMAC hash, 폐기 여부를 검증한다.
4. 인증된 profile/device와 body의 선택적 주장값을 대조한다.
5. save slot을 생성 또는 조회하고 `CompanionTurn`을 만든다.
6. 장기기억을 회수하고 conversation 단위 lock 안에서 LangGraph를 실행한다.
7. 응답 대사와 deterministic action을 작업기억과 JSONL transcript에 기록한다.
8. action이 allowlist 안이면 TTL이 있는 `CommandCandidate`로 반환한다.
9. WebClient의 채집 action이면 Command 대신 DB Offline Task를 생성한다.
10. background loop가 transcript에서 장기기억을 추출·요약·통합한다.

주요 근거는 [`routes/chat.py`](../ai_server/app/routes/chat.py),
[`service.py`](../ai_server/app/service.py),
[`brain/companion.py`](../ai_server/app/brain/companion.py)다.

## 4. 상태와 저장 위치

| 상태 | 저장 위치 | 범위 | 재시작/다중 worker 특성 |
|---|---|---|---|
| 최근 대화와 pending clarification | 프로세스 메모리 | profile + save + companion + session HMAC key | 재시작 시 유실, worker 간 공유 안 됨 |
| 증류 대기 cursor | 프로세스 메모리 | conversation key | 재시작 시 유실, transcript 자동 복구 없음 |
| 원문 transcript | JSONL 파일 | conversation key | 디스크 영속, 프로세스 간 file lock 없음 |
| episodic memory | SQLite | profile + save HMAC `player_key` | companion 구분 없음 |
| 기기/페어링/save slot | SQLite | profile 중심 | migration과 DB availability 필요 |
| Offline Task | SQLite | profile + save slot | 생성만 request ID 멱등 |
| game dataset | 시작 시 DB snapshot, 실패 시 정적 dataset | 앱 인스턴스 | hot reload 없음, DB 실패를 fallback으로 숨김 |

## 5. 실제 API 표면

### 일반 API

| Method | Path | 인증 | 상태 |
|---|---|---|---|
| GET | `/health` | 없음 | 설정값만 보고 항상 `ok` |
| POST | `/api/v1/chat` | Device Bearer | HTTP Chat |
| WS | `/api/v1/chat` | frame별 token | Chat/Situation 요청-응답 |
| POST | `/api/v1/situations` | Device Bearer | 자유 문장 상황 기반 선제 대사 |

### 기기 API

| Method | Path | 인증/권한 |
|---|---|---|
| POST | `/api/v1/devices/register-game` | 고정 bootstrap Bearer |
| POST | `/api/v1/devices/pairing-codes` | GameClient |
| POST | `/api/v1/devices/pair` | 8자리 pairing code |
| GET | `/api/v1/devices` | GameClient |
| GET | `/api/v1/devices/me` | WebClient |
| DELETE | `/api/v1/devices/me` | WebClient |
| DELETE | `/api/v1/devices/{device_id}` | GameClient, 같은 profile |

### Offline Task API

`/api/v1/tasks` 아래에 생성, 목록, `start`, `complete`, `claim`, `collect`가 있다. WebClient는
생성/collect, GameClient는 start/complete/claim을 담당한다.

### Admin API

`/api/v1/admin` 아래 profiles, devices, pairing codes, save slots, items, recipes,
smelting recipes, enemies, locations, episodic memories, offline tasks CRUD와 bulk create가 있다.
모든 경로는 하나의 고정 Admin Bearer 뒤에 있다.

## 6. 데이터 모델

SQLite table은 다음 11개다.

- `profiles`
- `devices`
- `pairing_codes`
- `save_slots`
- `items`
- `recipes`
- `smelting_recipes`
- `enemies`
- `locations`
- `episodic_memories`
- `offline_tasks`

기기/페어링/Offline Task에는 unique constraint와 조건부 update가 있어 같은 코드 사용이나 상태
전이 경쟁을 비교적 잘 막는다. 반면 Chat request, Message, Event, Command Result, audit trail
table은 없다.

## 7. 테스트와 품질 자동화

- GitHub Actions는 Python 3.13, locked dependency sync, Ruff, MyPy, pytest를 실행하도록 정의돼 있다.
- 정적 집계상 테스트 파일 38개와 test 함수 369개가 있다.
- 요청 제한, 인증, 역할, 페어링 경쟁, Offline Task 경쟁, LLM fallback, 기억, migration 일부를
  폭넓게 다룬다.
- 일반 test fixture는 실제 migration이 아니라 `Base.metadata.create_all()`을 사용하는 경로가 있어
  migration 기반 프로세스 부팅을 전체적으로 보장하지 않는다.

근거: [`.github/workflows/quality.yml`](../ai_server/.github/workflows/quality.yml),
[`tests/conftest.py`](../ai_server/tests/conftest.py).

