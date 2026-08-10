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
- UE UObject 이름, Actor name, 배열 index를 서버 ID로 사용하지 않습니다.
- ID를 변경하면 기존 Offline Task와 Recipe foreign key에 영향을 줄 수 있습니다.

## 6. 현재 제한

- `locations`는 0행입니다.
- `game_context.location_id`가 알려진 lore ID일 때만 현재 지역 사실을 직접 사용합니다.
- World의 나무·적·작업대 instance를 식별하는 Entity registry는 없습니다.
- UE Inventory snapshot을 저장하는 Game State API는 아직 없습니다.
- Admin으로 추가한 새 Item이 Mock regex 분류 어휘에 자동 추가되는 것은 아닙니다.

게임 데이터와 UE World 상태는 다른 것입니다. Item/Recipe/Enemy master data가 DB에 있어도 현재
주변에 무엇이 있는지는 UE가 별도 Context로 보내야 합니다.

## 7. 검증

```powershell
uv run pytest tests/test_game_data_migration.py
uv run pytest tests/test_admin_crud.py
uv run ruff check .
uv run mypy
```

Admin test 파일명이나 개별 test 구성이 변경되었으면 `uv run pytest` 전체 검증을 사용합니다.
