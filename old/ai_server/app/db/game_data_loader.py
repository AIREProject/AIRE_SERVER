"""DB 게임데이터 5개 테이블 → `app.gamedata.dataset.GameDataSet`.

마이그레이션 0002(`migrations/versions/0002_game_data.py`)가 `DATASET` 을 DB 로 실어 나르는
반대 방향이다 — 이 모듈은 DB 를 읽어 같은 모양의 순수 dataclass 로 되돌린다. JSON 컬럼의
키 대소문자(`ItemId`/`Amount`/`X`/`Y`/`Z`)는 그 마이그레이션이 쓴 것과 정확히 같아야 한다.

`app/db/` 에 있는 이유는 `app/gamedata/` 패키지를 SQLAlchemy 로부터 자유롭게 두기
위해서다(`app/brain/CLAUDE.md` 의 import 규칙). 이 함수가 반환하는 `GameDataSet` 은 그 규칙이
허용하는 "순수 데이터"로, `app/main.py` 가 앱 시작 시점에 한 번 호출해 브레인에 넘긴다.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    EnemyModel,
    ItemModel,
    LocationModel,
    RecipeModel,
    SmeltingRecipeModel,
)
from app.gamedata.dataset import (
    Enemy,
    GameDataSet,
    Ingredient,
    Item,
    Location,
    Recipe,
    SmeltingRecipe,
)


def _ingredient(payload: dict[str, object]) -> Ingredient:
    item_id = payload["ItemId"]
    amount = payload["Amount"]
    assert isinstance(item_id, str)
    assert isinstance(amount, int)
    return Ingredient(item_id=item_id, amount=amount)


async def load_game_dataset(session: AsyncSession) -> GameDataSet:
    items = tuple(
        Item(
            item_id=row.item_id,
            item_type=row.item_type,
            name_ko=row.name_ko,
            aliases=tuple(row.aliases),
            description=row.description,
        )
        for row in (await session.execute(select(ItemModel))).scalars()
    )
    recipes = tuple(
        Recipe(
            recipe_id=row.recipe_id,
            result_item_id=row.result_item_id,
            result_amount=row.result_amount,
            required_workbench=row.required_workbench,
            duration_seconds=row.duration_seconds,
            ingredients=tuple(_ingredient(entry) for entry in row.ingredients),
        )
        for row in (await session.execute(select(RecipeModel))).scalars()
    )
    smelting_recipes = tuple(
        SmeltingRecipe(
            smelt_id=row.smelt_id,
            result_item_id=row.result_item_id,
            result_amount=row.result_amount,
            required_workbench=row.required_workbench,
            duration_seconds=row.duration_seconds,
            input=_ingredient(row.input_item),
            fuel=_ingredient(row.fuel),
        )
        for row in (await session.execute(select(SmeltingRecipeModel))).scalars()
    )
    enemies = tuple(
        Enemy(
            enemy_id=row.enemy_id,
            name_ko=row.name_ko,
            aliases=tuple(row.aliases),
            description=row.description,
            weak_element=row.weakness["weak_element"],
            weak_part=row.weakness["weak_part"],
            ai_advice=row.weakness["ai_advice"],
        )
        for row in (await session.execute(select(EnemyModel))).scalars()
    )
    locations = tuple(
        Location(
            location_id=row.location_id,
            coordinates=(row.coordinates["X"], row.coordinates["Y"], row.coordinates["Z"]),
        )
        for row in (await session.execute(select(LocationModel))).scalars()
    )
    return GameDataSet(
        items=items,
        recipes=recipes,
        smelting_recipes=smelting_recipes,
        enemies=enemies,
        locations=locations,
    )
