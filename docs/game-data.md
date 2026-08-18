# 게임 데이터 관리

현재 게임 데이터의 권위는 구형 ERD 이미지나 SQL 초안이 아니라 코드와 migration입니다.

1. 값의 기본 출처: `app/gamedata/dataset.py`
2. DB Schema와 seed: `migrations/versions/0002_game_data.py` 이후 migration
3. 런타임 로딩: `app/db/game_data_loader.py`, `app/main.py`

## 1. 기본 데이터

Migration `0002` 적용 후 기본 행 수는 다음과 같습니다.

| 데이터 | 행 수 | 용도 |
|---|---:|---|
| Item | 27 | 재료·결과물 ID, 이름과 설명 |
| Recipe | 13 | 일반 제작법 |
| Smelting Recipe | 4 | 연료를 사용하는 제련법 |
| Enemy | 3 | 적 이름·별칭·약점·공략 사실 |
| Location | 0 | 위치 데이터 확장용 빈 테이블 |

실제 값은 `app/gamedata/dataset.py`를 확인합니다.

## 2. DB 생성

```powershell
uv run alembic upgrade head
```

이 명령은 게임 데이터뿐 아니라 Profile, Device, Save Slot, Offline Task와 장기기억 테이블도
현재 revision까지 생성합니다.

현재 Schema의 주요 테이블:

```text
profiles
save_slots
devices
pairing_codes
items
recipes
smelting_recipes
enemies
locations
offline_tasks
episodic_memories
alembic_version
```

## 3. 서버가 데이터를 읽는 시점

서버는 시작할 때 DB의 Item/Recipe/Enemy 데이터를 한 번 읽어 in-memory `GameDataSet`을
구성합니다.

- Admin API로 데이터를 바꿔도 실행 중인 MAKO 대사에는 즉시 반영되지 않습니다.
- 변경 뒤 서버를 재시작해야 합니다.
- Migration이 안 됐거나 seed가 부분적으로 비어 있으면 정적 `DATASET`으로 폴백할 수 있습니다.

따라서 Admin API 성공만 확인하지 말고 서버 재시작 뒤 Chat으로 제작법·적 공략을 확인합니다.

## 4. 데이터를 변경하는 방법

### 4.1 코드 기준 데이터 변경

기본 배포 데이터 변경은 다음 순서로 진행합니다.

1. `app/gamedata/dataset.py`의 stable ID와 값을 변경합니다.
2. 기존 DB에도 적용할 새 Alembic migration을 추가합니다.
3. Migration upgrade test를 추가합니다.
4. `uv run alembic upgrade head`를 실행합니다.
5. 서버를 재시작합니다.
6. Recipe/Enemy Chat을 확인합니다.

이미 배포된 migration 파일을 수정하지 않습니다. 새 revision을 추가합니다.

### 4.2 Admin API 변경

운영자가 한 DB 인스턴스만 수정할 때는 `/api/v1/admin/items`, `/recipes`,
`/smelting-recipes`, `/enemies`, `/locations`를 사용할 수 있습니다.

Admin token 설정:

```dotenv
ADMIN_API_TOKEN=replace-with-admin-token
```

Admin 변경은 해당 DB에만 적용되며 새 서버의 빈 DB에는 자동 복제되지 않습니다. 여러 환경에
같은 기준 데이터가 필요하면 migration으로 승격합니다.

## 5. Stable ID 규칙

- Protocol과 DB에서는 display name 대신 stable ID를 사용합니다.
- Item과 Recipe 참조는 현재 seed ID와 정확히 일치해야 합니다.
- 나무 재료는 `PlantStem` 하나를 사용하며 legacy `Branch`는 migration 0015에서 병합합니다.
- UE UObject 이름, Actor name, 배열 index를 서버 ID로 사용하지 않습니다.
- ID를 변경하면 기존 Offline Task와 Recipe foreign key에 영향을 줄 수 있습니다.

## 6. Game State Snapshot (AX-I09 local Review)

AX-I09 로컬 구현은 UE가 검증·저장한 마지막 Game State Snapshot을 인증된
`(profile_id, save_slot_id, companion_id)` scope에 보관합니다. `PUT /api/v1/game-state`는
GameClient 전용이고, 같은 경로의 GET은 GameClient와 WebClient가 읽을 수 있습니다. 서버
Snapshot은 gameplay mutation을 실행하거나 UE의 현재 상태를 대신하는 권위가 아닙니다.

Snapshot은 `schema_version=1`, `content_version=1`, 단조 증가 `state_version`,
`operation_id`, `world_session_id`, offset 포함 `captured_at`과 다음 bounded 값만 저장합니다.

- Player: 일반 Inventory 30칸, Quick Slot 100~109, revision, Weapon Equipment 1칸
- MAKO: `AIRE.Inventory.MAKO`, 20칸, revision, Weapon Equipment 1칸
- Shared Storage: `AIRE.Inventory.SharedStorage`, 50칸, revision, Equipment object의 장착 ID는 null
- Stack: 서버 Item master의 stable `item_id`, slot index, count 1~99. Weapon count는 1

Player는 일반 30칸과 Quick Slot을 합쳐 Stack 최대 40개이고, MAKO와 Shared Storage는 각각
capacity보다 많은 Stack을 가질 수 없습니다. 장착 ID는 서버 Item master의 Weapon이어야
합니다. 전체 UObject/Actor 경로, 임의 JSON World summary, Command와 LLM payload는 저장하지
않습니다.

같은 operation과 HTTP 원문 JSON의 정확한 UTF-8 bytes SHA-256이 같으면 최초 결과를 그대로
반환합니다. 같은 operation에 bytes가 다르면 `409 DuplicateRequest`, 새 operation의 version이
최신 값보다 크지 않으면 `409 GameStateVersionConflict`로 상태를 보존합니다. 자세한 header,
wire shape와 오류는 [API 사용법](api-endpoints.md)을 따릅니다.

이 계약은 **로컬 사전 배포 Review**입니다. 공개 배포 `/openapi.json`에는 아직 Game State
경로가 없으므로 배포 지원이나 runtime 성공을 주장하지 않습니다.

## 7. 현재 제한

- `locations`는 0행입니다.
- `game_context.location_id`가 알려진 lore ID일 때만 현재 지역 사실을 직접 사용합니다.
- World의 나무·적·작업대 instance를 식별하는 Entity registry는 없습니다.
- Game State API는 로컬 Review 계약과 구현만 있으며 공개 배포 OpenAPI에는 아직 없습니다.
- Admin으로 추가한 새 Item이 Mock regex 분류 어휘에 자동 추가되는 것은 아닙니다.

게임 데이터와 UE World 상태는 다른 것입니다. Item/Recipe/Enemy master data가 DB에 있어도 현재
주변에 무엇이 있는지는 UE가 별도 Context로 보내야 합니다.

## 8. 검증

```powershell
uv run pytest tests/test_game_data_migration.py
uv run pytest tests/test_admin_crud.py
uv run ruff check .
uv run mypy
```

Admin test 파일명이나 개별 test 구성이 변경되었으면 `uv run pytest` 전체 검증을 사용합니다.
