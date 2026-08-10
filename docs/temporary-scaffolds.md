# 현재 제한과 임시 경계

이 문서는 코드가 현재 의도적으로 제공하지 않는 보증과 확장 전에 다시 결정해야 할 경계를
기록합니다. 구현된 것처럼 추측하지 않기 위한 문서입니다.

## 1. `COMPANION_DEFAULT_LOCATION_ID`

현재 lore 응답은 `game_context.location_id`를 사용합니다. UE가 위치를 보내지 않는 동안
`.env`의 `COMPANION_DEFAULT_LOCATION_ID`를 개발용 대체 위치로 사용할 수 있습니다.

```dotenv
COMPANION_DEFAULT_LOCATION_ID=region_abandoned_mining_village
```

규칙:

- Request에 `location_id`가 있으면 request 값이 우선합니다.
- 알 수 없는 `location_id`를 임의의 다른 장소로 바꾸지 않습니다.
- UE가 stable location ID를 항상 보내기 시작하면 이 설정은 비웁니다.

## 2. Identity, 기억과 데이터 보존

현재 제품 경로는 고정 공개 identity를 사용합니다.

- `AIRE_GAME` → `GameClient`
- `AIRE_WEB` → `WebClient`
- 공통 Profile → `AIRE_OPEN`
- 공통 Save Slot → `demo-slot-1`
- Companion → `mako`

두 client가 같은 기억과 Offline Task를 공유하는 것이 의도된 단일 플레이어 기준입니다.
다중 사용자, 계정 선택, Save Slot 선택과 Companion 선택은 현재 범위에 없습니다.

기존 random Device 등록·pairing 코드는 호환성을 위해 남아 있지만 제품 client는 사용하지
않습니다. Pepper가 비어 있으면 고정 demo key를 사용하며, `register-game` 경로만 별도의
`DEV_GAME_DEVICE_TOKEN`이 필요합니다.

대화 관련 저장은 세 층입니다.

| 층 | 저장 위치 | 수명 |
|---|---|---|
| 최근 대화/되묻기 | process memory | 서버 재시작 전까지 |
| 원문 Transcript | `data/transcripts/*.jsonl` | 기본 30일 |
| 장기기억 | SQLite `episodic_memories` | 명시적으로 삭제할 때까지 |

현재 일반 사용자용 Memory 목록·삭제·초기화 API는 없습니다. Admin API만 DB Memory CRUD를
제공합니다.

## 3. `recent_event_ids`

ChatRequest는 최대 32개의 `recent_event_ids`를 받지만 현재 다음 동작은 하지 않습니다.

- Event 존재 확인
- Profile/Save/Companion scope 확인
- Event 저장
- Prompt fact 승격
- Command Result와 연결

UE Event ingest와 Command Result API가 생기기 전까지 형식 검증용 field입니다.

## 4. Chat/Situation 멱등성

Chat과 Situation의 `request_id`는 body/header 상관관계와 로그 추적에 사용되며 결과를 저장해
재사용하는 멱등 key가 아닙니다.

- Timeout 뒤 같은 request를 자동 재전송하지 않습니다.
- 사용자가 새로 보내면 새 request/message ID를 만듭니다.
- Offline Task 생성은 request ID 기반 중복 방지가 있지만 상태 전환은 별도 멱등 receipt가
  없습니다.

## 5. Offline Task settlement

현재 Offline Task의 `Claimed`는 서버 상태입니다. 다음 기능은 없습니다.

- UE Inventory 지급 transaction
- settlement ID
- apply operation ID
- UE 적용 receipt
- revision conflict 처리

따라서 Task 완료를 보고 UE Inventory에 보상을 적용하는 exact-once 계약으로 사용하지 않습니다.

## 6. World Context

ChatRequest의 `game_context`는 최대 32개 property를 받을 수 있지만 현재 서비스가 직접 사용하는
값은 사실상 `location_id`입니다.

다음 World 상태를 LLM이 구조적으로 이해하는 계약은 아직 없습니다.

- 주변 나무·광물
- 주변 적과 stable entity ID
- 사용 가능한 Workbench
- 현재 WorkOrder
- MAKO/Storage Inventory snapshot

UE에서 값을 임의 key로 보내기만 해서는 서버 Prompt가 자동으로 사용하지 않습니다.

## 7. Health와 운영 readiness

`GET /health`는 항상 설정의 LLM provider 이름과 `status=ok`를 반환합니다. 다음 항목을 검사하지
않습니다.

- DB migration revision
- DB query 성공
- 실제 LLM endpoint 연결
- Embedding endpoint 연결

운영 확인은 `alembic current`, 실제 Chat, provider server log를 함께 사용합니다.

## 8. Process와 SQLite

최근 대화, lock과 기억 증류 cursor 일부가 process-local입니다. SQLite도 단일 파일을 사용합니다.
현재 운영은 Uvicorn worker 1개를 기준으로 합니다.

다중 worker, 여러 server replica 또는 공유 network filesystem으로 확장하기 전에 다음을 다시
설계해야 합니다.

- conversation store
- background memory queue/cursor
- distributed lock
- PostgreSQL 또는 다른 shared DB
- transcript 동시 쓰기

## 9. LLM fallback

OpenAI/Local provider의 분류·대사·기억 호출이 실패하면 Mock fallback으로 복구됩니다. 사용자
경험은 유지되지만 `/health`와 HTTP 200만으로 실제 provider 성공을 판단할 수 없습니다.

확인은 [LLM 설정](llm-setup.md)의 절차를 따릅니다.

## 10. 현재 우선순위

1. UE `AIRE_GAME` HTTP Chat 정합화
2. Web `AIRE_WEB` Mobile Chat 정합화
3. UE Command Gateway
4. World Context v1
5. Inventory Save/Sync와 Game State API
6. Offline Task settlement receipt

이 문서의 제한이 해소되면 해당 절을 삭제하거나 현행 계약 문서로 승격합니다.
