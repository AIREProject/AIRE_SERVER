# 현재 제한과 임시 경계

이 문서는 코드가 현재 의도적으로 제공하지 않는 보증과 확장 전에 다시 결정해야 할 경계를
기록합니다. 구현된 것처럼 추측하지 않기 위한 문서입니다.

## 1. `COMPANION_DEFAULT_LOCATION_ID`

AX-I05부터 lore 응답과 대사 facts는 strict `GameContextV1.location_id`를 사용합니다.
`location_id=null`인 개발 요청에 한해서만 `.env`의 `COMPANION_DEFAULT_LOCATION_ID`를 대체
위치로 사용할 수 있습니다. 이 fallback은 외부 계약의 위치 사실을 임의로 바꾸는 기능이
아닙니다.

```dotenv
COMPANION_DEFAULT_LOCATION_ID=forest_camp
```

규칙:

- Request의 typed `location_id`가 non-null이면 그 값이 우선합니다.
- `location_id=null`일 때만 설정값을 사용하며, 알 수 없는 ID를 임의의 다른 장소로 바꾸지
  않습니다.
- 운영 클라이언트가 항상 위치 stable ID를 보내면 이 개발 fallback은 비웁니다.

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

대화 관련 저장은 다음 경계로 바뀌었습니다.

| 층 | 저장 위치 | 수명 |
|---|---|---|
| canonical 대화/원문 | SQLite `conversations`/`messages` | 기본·최대 7일 |
| 최근 되묻기 cache | process memory | DB 완료 뒤 갱신, 서버 재시작 전까지 |
| 개발 Transcript | `data/transcripts/*.jsonl` | 기본 off, opt-in 최대 24시간 |
| 기존 장기기억 | SQLite `episodic_memories` | P2에서는 조회만 유지 |

일반 사용자는 `/api/v1/memories`에서 인증된 save-slot/companion scope의 Active memory를
조회하고 정정·삭제·reset할 수 있습니다. 삭제는 `Archived` tombstone 전이이며 canonical source
원문을 변경하지 않습니다. legal erasure와 Admin Memory CRUD는 현재 범위 밖입니다.

## 3. `recent_event_ids`

ChatRequest는 최대 32개의 `recent_event_ids`를 받지만 현재 다음 동작은 하지 않습니다.

- Event 존재 확인
- Profile/Save/Companion scope 확인
- Event를 Chat prompt fact로 자동 승격
- Prompt fact 승격
- Command Result와 연결

Event ingest와 Command Result 저장 API는 구현됐지만, `recent_event_ids`를 Message history나
prompt fact로 연결하는 작업은 P3 source-memory 범위입니다.

## 4. Chat/Situation 멱등성

Chat은 profile/save/companion/request ID와 canonical JSON digest를 durable operation으로
저장합니다. 같은 payload는 HTTP/WS에서 최초 결과를 재생하고 다른 payload는 409입니다.
원문 purge 후 같은 digest는 410이며 다른 digest는 계속 409입니다.

- 실패한 Pending Chat은 같은 request로 재개할 수 있습니다.
- 사용자가 새로 보내면 새 request/message ID를 만듭니다.
- Situation은 canonical source와 이 멱등 계약에 포함하지 않습니다.
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

## 6. World Context v1 (AX-I05 확정 로컬 계약)

`ChatRequest.game_context`는 더 이상 임의 key를 받는 generic object가 아니다. `surface=game`
에서는 다음 7개 최상위 field가 모두 필요한 `GameContextV1`이고, `surface=mobile`에서는
생략 또는 `null`만 허용한다. `{}`와 기존 자유 형식 object는 호환하지 않는다.

- `schema_version=1`, `location_id`, `threat`, `nearby_resources`, `available_workstations`,
  `current_work`, `inventories`
- `location_id`, `threat.nearest_kind`, `current_work`만 `null`을 허용한다.
- stable ID는 1~128자와 `[A-Za-z0-9][A-Za-z0-9._:-]*`를 따르며, 임의 key, UObject/class
  path, credential key는 거부한다. ID catalogue 존재 여부는 이 단계에서 조회하지 않는다.
- `threat.count`는 0~32이고 `present == (count > 0)`이며 count 0이면 nearest kind는
  `null`이다.
- 주변 resource는 중복 없는 최대 8종, 종류별 count 1~32이고 workstation tag는 중복 없는
  최대 8개다.
- `current_work` type은 `Crafting | Harvesting | StorageTransfer`, state는
  `Requested | Moving | Working | PausedByCombat`다. 종료된 work는 `null`이다.
- inventory는 MAKO/Shared Storage 중복 없는 최대 2개다. free slots는 각각 0~20/0~50,
  container별 item kind는 최대 16종, item 합계는 각각 1,980/4,950 이하이며 생략 시
  `truncated=true`를 표시한다.
- compact UTF-8 직렬화 결과는 8KiB 이하이며 초과 시 `400 InvalidRequest`다. 전체 HTTP
  body 256KiB 제한 초과는 `413 RequestTooLarge`다.

배열 순서는 의미가 없고 Backend가 stable ID 기준으로 정렬해 prompt facts를 만든다. 이
Context는 관측 facts를 대사 생성에 제공하는 경계일 뿐이며, Backend가 Context만으로
Command 후보를 추가·제거하거나 `CraftItem`/gameplay를 실행하지 않는다. Command 통합과
실행 권한은 AX-I06 범위다.

이 계약은 현재 `AIRE_SERVER/` 로컬 구현 문서의 목표다. 2026-08-13 현재 배포
`/openapi.json`은 여전히 `game_context`를 generic object로 노출하므로 배포 반영이나 runtime
smoke 성공을 의미하지 않는다.

AX-I05 로컬 구현과 Backend test/lint/type Gate는 완료했다. 이후 서버에 접근할 수 없어 배포
적용은 별도 운영 체크로 남겼다. strict 전환은 기존 `{}` Game 요청과 호환되지 않으므로 AX-I04
producer 준비 없이 Backend만 선배포하지 않는다.

현재 일반 플레이맵과 fallback 예시는 `forest_camp`다. 권위 센서가 없을 때 AX-I04가 보내는
`nearest_kind=null`, resource/workstation 빈 배열은 정상이며, Backend가 향후 맵이나 센서 ID를
추측해 채우지 않는다.

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
3. UE Command Gateway (AX-I06)
4. Inventory Save/Sync와 Game State API
5. Offline Task settlement receipt

이 문서의 제한이 해소되면 해당 절을 삭제하거나 현행 계약 문서로 승격합니다.
