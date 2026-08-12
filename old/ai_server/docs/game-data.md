# 게임 데이터 마스터

## 출처와 현재 범위

현재 게임 데이터에는 **서로 다른 두 출처**를 구분해야 한다.

- `docs/AI_RE.sql`과 ERD Cloud 캡처: 테이블 구조와 관계의 권위 있는 정의. 이 SQL에는
  `INSERT` 문이 없으므로 행 데이터는 포함하지 않는다.
- 병일님이 별도로 전달한 게임 데이터 명세: 현재 `app/gamedata/dataset.py`에 옮긴
  27개 Item, 13개 Recipe, 4개 제련법, 3개 Enemy의 값. Alembic `0002_game_data`는 이
  파이썬 데이터셋을 SQLite에 적재하고, 마코의 제작법 저장소도 같은 상수를 읽는다.

따라서 현재 데이터셋은 `AI_RE.sql`의 **정확한 행 사본**이라기보다, ERD 스키마를 참고해
서버에 옮긴 애플리케이션용 마스터 데이터다. 두 소비자가 서로 다른 값을 갖지 않는다는
의미에서만 `dataset.py`가 단일 출처다.

현재 서버의 게임 데이터 행 수는 다음과 같다.

| 데이터 | 행 수 | 현재 서버에서의 역할 |
|---|---:|---|
| `Item` | 27 | 재료·결과물 명칭과 설명. 레시피 렌더링에 사용 |
| `Recipe` | 13 | 채팅 명세에서 옮긴 일반 제작법. 마코가 조회 |
| `SmeltingRecipe` | 4 | ERD에 없는 서버 확장. 연료를 쓰는 제련법으로 마코가 조회 |
| `Enemies` | 3 | 약점·공략 질문에 검증된 사실로 사용 |
| `Location` | 0 | ERD의 적/아이템 스폰 위치 테이블이지만 실제 좌표 행은 아직 없음 |

`AI_RE.sql`의 테이블 목록에는 `Chat_Buffer`, `Episodic_Memory`, `Enemies`, `Item`,
`Location`, `Offline_Task`, `Recipe`가 있다. 현재 `Chat_Buffer`는 원문 보존 정책 때문에 JSONL로
유지하고, `Episodic_Memory`는 서버 확장 스코프를 포함한 `episodic_memories` 테이블로
매핑했다. 게임 데이터 CRUD API와 `Location` 기반 위치 조회/RAG는 좌표 행이 들어온 뒤의
별도 작업이다.

## 장기기억 RAG 확장

`Episodic_Memory`에 대응하는 `episodic_memories` 테이블은 ERD에 없는 `player_key`를
서버측 스코프로 추가한다. `player_key`는 인증된 프로필과 세이브 슬롯에서 만든 HMAC라
원문 신원을 DB에 적지 않는다. 중요도는 1~10으로 저장하고, 임베딩은 정규화된 float 배열과
`embedding_model`을 함께 저장한다.

검색은 다음 순서다.

1. 질의와 기억에 같은 모델의 임베딩이 있으면 키워드 점수·시간 감쇠·코사인 유사도를 함께 본다.
2. 임베딩 공급자가 없거나 실패하거나 모델/차원이 다르면 그 기억은 키워드+시간 감쇠 점수만
   사용한다. 기존 기억이 임베딩이 없다는 이유로 회수에서 사라지지 않는다.
3. `mock` 기본 설정은 외부 호출이 없고 모든 기억을 키워드 검색으로 처리한다.

기존 `LONG_TERM_MEMORY_DIR`의 v1/v2 JSON은 `0005` 마이그레이션 때 DB로 복사하며 원본은
삭제하지 않는다. 파일에 저장된 이전 중요도는 새 척도에 맞춰 두 배로 환산한다. 이후
전사 JSONL은 Chat_Buffer의 대체 원문으로 계속 유지한다.

## 아이템 이름·별칭 검수표

ERD의 `아이템` 테이블은 `PK int AUTO_INCREASE`, `이름 varchar(50) NOT NULL`,
`설명 TEXT NULL`을 정의한다. 현재 SQL에는 실제 이름 값이나 영어 문자열 ID가 없다.
아래 문자열 Item ID, 한국어 표시 이름, 별칭은 채팅으로 받은 데이터 명세와 게임 설명을
서버에서 사용할 수 있도록 만든 애플리케이션 확장이다. 게임 안에서 실제로 부르는 이름이
다르면 이 표와 `app/gamedata/dataset.py`를 함께 고친다. 별칭은 한국어 조사 경계를
고려해 매칭한다.

| Item ID | 표시 이름 | 별칭 |
|---|---|---|
| `PlantStem` | 식물 줄기 | 식물 줄기, 풀 줄기, 줄기 |
| `ShoddyBandage` | 엉성한 붕대 | 엉성한 붕대, 붕대 |
| `Rope` | 밧줄 | 밧줄, 로프 |
| `Branch` | 나뭇가지 | 나뭇가지, 나무 가지, 가지 |
| `Stone` | 돌 | 돌멩이, 돌 |
| `Axe_Stone` | 돌도끼 | 돌도끼, 돌 도끼, 석재 도끼 |
| `Pickaxe_Stone` | 돌곡괭이 | 돌곡괭이, 돌 곡괭이, 석재 곡괭이 |
| `Campfire` | 모닥불 | 모닥불, 캠프파이어, 불 |
| `IronOre` | 철광석 | 철광석, 철 광석, 철 원석 |
| `IronIngot` | 철괴 | 철괴, 철 주괴, 철 잉곳 |
| `Nail_Iron` | 철못 | 철못, 철 못, 못 |
| `WoodPlank` | 나무 널빤지 | 나무 널빤지, 널빤지, 목재 판자 |
| `Workbench` | 작업대 | 작업대, 제작대 |
| `Leather` | 가죽 | 가죽, 동물 가죽 |
| `Armor_Leather` | 가죽 갑옷 | 가죽 갑옷, 가죽 방어구, 가죽옷 |
| `Coal` | 석탄 | 석탄, 탄 |
| `SteelIngot` | 강철괴 | 강철괴, 강철 주괴, 강철 잉곳 |
| `WoodHandle` | 나무 손잡이 | 나무 손잡이, 목재 손잡이, 손잡이 |
| `Sword_Iron` | 철검 | 철검, 철 검, 쇠검 |
| `SteelGreatsword` | 강철 대검 | 강철 대검, 강철대검, 대검 |
| `Herb` | 허브 | 허브, 약초 |
| `ClearWater` | 깨끗한 물 | 깨끗한 물, 정수된 물, 맑은 물 |
| `Potion_High` | 고급 포션 | 고급 포션, 상급 포션, 포션 |
| `CopperOre` | 구리 광석 | 구리 광석, 구리광석, 구리 원석 |
| `CopperIngot` | 구리괴 | 구리괴, 구리 주괴, 구리 잉곳 |
| `Sand` | 모래 | 모래, 모래알 |
| `Glass` | 유리 | 유리, 유리병 재료 |

## 적 이름·별칭 검수표

`Enemy`의 이름과 별칭은 채팅으로 받은 데이터 명세에서 왔다. ERD의 `약점 JSON`은
내용의 키나 표시 언어를 정하지 않으므로, 서버는 `weak_element`·`weak_part`·`ai_advice`로
해석하고 약점 속성은 한국어 표시 이름으로 바꾼다.

| Enemy ID | 표시 이름 | 별칭 | 약점 속성 | 약점 부위 |
|---|---|---|---|---|
| `TrenchCrawler` | 녹슨 참호병 | 녹슨 참호병, 참호병, 참호 로봇 | Water → 물 | 다리 관절 |
| `SirenDrone` | 절규하는 사이렌 드론 | 절규하는 사이렌 드론, 사이렌 드론, 사이렌, 드론 | EMP → 전자기 펄스(EMP) | 상단 확성기 센서 |
| `Goliath` | 외상성 골리앗 | 외상성 골리앗, 골리앗, 의료 로봇 | Explosive → 폭발물 | 가슴의 깨진 코어 |

`녹슨 참호병`, `참호병`, `골리앗`, `드론`처럼 현재 발화에 별칭을 넣어 질문한다.
서로 다른 적을 한 발화에서 함께 언급하면 서버는 임의로 하나를 고르지 않는다.

## 서버측 확장과 ERD와의 차이

현재 레시피 저장소는 마코가 검증된 제작법을 말할 수 있도록 ERD보다 풍부한 구조를
사용한다.

- `items`의 `item_type`, `name_ko`, `aliases`와 `enemies`의 `aliases`: ERD에는 없는 검색·대사
  편의용 확장이다. 적 약점 JSON의 `weak_element` 영→한 표시 매핑도 서버가 해석한 값이다.
- 문자열 `item_id`·`enemy_id`: ERD의 정수 PK와 직접 호환되지 않으므로 클라이언트가 정수
  PK를 보내는 시점에는 매핑 전략이 필요하다.
- `recipes`의 `result_amount`, `required_workbench`, `duration_seconds`,
  `ingredients`: ERD의 `Recipe`는 `Recipe_id`, Item FK, nullable JSON 한 컬럼만 정의한다.
  서버는 대사에 필요한 값을 별도 컬럼으로 정규화했다.
- `smelting_recipes`: `AI_RE.sql`과 캡처에는 Smelting 테이블이 없다. 채팅으로 받은 제련
  명세를 서버측 확장 테이블로 유지한다. 현재 결정은 제련을 `Recipe.material_json`으로
  합치지 않고 별도 테이블로 둔다는 것이다.
- 현재 게임 데이터 테이블은 ERD의 정수 AUTO_INCREMENT PK와 `Recipe→Item`,
  `Location→Enemies/Item` FK를 그대로 복제하지 않는다. 이 차이는 의도된 애플리케이션
  스키마 결정이며, ERD 호환 DB가 필요한 경우 별도 매핑·마이그레이션이 필요하다.

## 마코가 답하는 제작법

마코는 결과물 이름을 현재 발화에서 찾고, 해당 결과물의 일반 제작법과 서버 확장 제련법을
모두 확정 사실로 전달한다. 숫자는 DB 사실 문장에 있는 값만 대사에 허용된다.

- `돌도끼`, `돌 곡괭이`, `모닥불`, `붕대`, `철괴`, `강철괴`, `철검`, `고급 포션` 등
  결과물 별칭을 현재 발화에 넣는다.
- 한 발화에서 서로 다른 결과물을 말하면 임의로 하나를 고르지 않고 확인된 제작법 없음으로
  폴백한다.
- 결과물이 1개인 경우 대사에서 결과 수량을 생략하고, 5개인 철못은 5개를 표시한다.
- 소요 시간이 0초인 맨손 제작은 시간을 말하지 않는다.

## 데이터 명세에서 확인이 필요한 불일치

1. `IronIngot`와 `SteelIngot`은 채팅으로 받은 일반 `Recipe`와 서버 확장
   `SmeltingRecipe`에 서로 다른 작업대·시간·연료 값으로 각각 존재한다. 현재 서버는 두
   경로를 모두 답한다. ERD의 `Recipe`만 보면 이 두 경로가 존재한다는 사실은 확인할 수 없다.
2. 제련 3·4번의 연료 `Wood`는 현재 채팅 데이터셋의 Item 27행에 없다. 이 값은 SQL의
   ERD 행에서 검증된 것이 아니라 제련 명세에서 온 것이다. `Wood`를 추가할지 `Branch`나
   `WoodPlank`로 바꿀지 확인이 필요하다.
3. ERD의 `Location`은 lore 지역 테이블이 아니라 적과 아이템의 위치를 연결하는 테이블이다.
   현재 `Location` 구현은 좌표만 담고 두 FK를 아직 표현하지 않는다. 실제 좌표 행을 받으면
   아이템·적 위치 조회/RAG를 별도 배선한다. `LoreRepository`의
   `region_abandoned_mining_village` 고정 데이터와는 다른 개념이다.
4. 기존 시연용 `철 도끼`는 현재 채팅 데이터셋 13종에 없다. 필요하면 데이터 명세에 다시
   추가해야 한다.
5. ERD의 `Location`은 `PK`, `FK`, `FK1`을 모두 복합 PK에 넣으면서 두 FK를 nullable로
   선언한다. 이 정의는 SQL 제약 자체가 모순되므로, 실제 좌표 데이터를 받을 때 원하는
   cardinality와 nullability를 확인해야 한다.

## 테이블과 데이터 적재

`app/db/models.py`의 `ItemModel`, `RecipeModel`, `SmeltingRecipeModel`, `EnemyModel`,
`LocationModel`이 현재 서버 스키마를 표현한다. ERD 원본의 JSON은 `레시피`, `약점`,
`좌표`이고, 서버는 레시피 재료와 제련 input/fuel을 읽기 쉽게 별도 JSON 컬럼으로
확장했다. SQLAlchemy 타입 표기는 의도한 JSON 모양을 설명하지만 데이터베이스가 JSON
키를 자동 검증하지는 않는다.

```powershell
uv run alembic upgrade head
```

현재 기대 행 수는 `items=27`, `recipes=13`, `smelting_recipes=4`, `enemies=3`,
`locations=0`이다. 테이블은 먼저 만들어졌지만, CRUD 엔드포인트와 런타임 로더가 추가되기
전까지 DB 행을 수정해도 마코의 답변은 파이썬 데이터셋을 읽으므로 바뀌지 않는다.
