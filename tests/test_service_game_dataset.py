"""게임데이터 DB-authoritative 전환: `load_game_dataset` 과 주입 가능한 리포지토리(§5).

`test_recipes.py`/`test_enemies.py` 가 인자 없는 `RecipeRepository()`/`EnemyRepository()` 가
여전히 정적 `DATASET` 과 같은 동작을 한다는 걸 이미 검증한다 — 여기서는 이번에 새로 생긴
경로, 즉 DB 를 읽어 만든 `GameDataSet` 이 실제로 기본값과 다른 데이터를 실어 나르는지만
검증한다.
"""

from app.brain.enemies import EnemyRepository
from app.brain.recipes import RecipeRepository
from app.db.game_data_loader import load_game_dataset
from app.db.models import EnemyModel, ItemModel, RecipeModel
from app.gamedata.dataset import DATASET, Enemy, GameDataSet, Ingredient, Item, Recipe
from app.main import _load_startup_game_dataset
from tests.conftest import make_database, make_settings


async def test_startup_dataset_falls_back_to_static_file_when_db_is_unmigrated() -> None:
    """스키마만 있고 시드가 없는 DB(테스트 스캐폴딩 등)는 조용히 정적 DATASET 으로 돌아간다."""

    settings = make_settings()
    database = await make_database(settings)

    assert _load_startup_game_dataset(database) is None


async def test_startup_dataset_ignores_a_lone_fk_helper_item() -> None:
    """아이템 한 행만 있고 recipes/enemies 는 비어 있으면(FK 를 만족시키려는 테스트 삽입 흔한
    패턴) 아직 '진짜 시드'가 아니므로 정적 DATASET 으로 돌아간다 — items 만 보고 판단하면
    test_offline_tasks.py 처럼 아이템 한 개만 넣는 테스트가 DB-authoritative 로 잘못 전환된다.
    """

    settings = make_settings()
    database = await make_database(settings)
    async with database.session_factory() as session:
        session.add(
            ItemModel(
                item_id="LoneItem",
                item_type="Material",
                name_ko="외톨이아이템",
                aliases=[],
                description="FK 만 채우는 아이템.",
            )
        )
        await session.commit()

    assert _load_startup_game_dataset(database) is None


async def test_startup_dataset_uses_db_when_items_recipes_and_enemies_are_all_seeded() -> None:
    settings = make_settings()
    database = await make_database(settings)
    async with database.session_factory() as session:
        session.add(
            ItemModel(
                item_id="SeededOre",
                item_type="Material",
                name_ko="시드광석",
                aliases=[],
                description="설명.",
            )
        )
        session.add(
            RecipeModel(
                recipe_id="seeded-recipe",
                result_item_id="SeededOre",
                result_amount=1,
                required_workbench="Basic Workbench",
                duration_seconds=1.0,
                ingredients=[{"ItemId": "SeededOre", "Amount": 1}],
            )
        )
        session.add(
            EnemyModel(
                enemy_id="SeededEnemy",
                name_ko="시드몹",
                aliases=[],
                description="설명.",
                weakness={"weak_element": "Water", "weak_part": "몸통", "ai_advice": "조심."},
            )
        )
        await session.commit()

    loaded = _load_startup_game_dataset(database)
    assert loaded is not None
    assert any(item.item_id == "SeededOre" for item in loaded.items)


async def test_load_game_dataset_reflects_db_rows_not_the_static_file() -> None:
    settings = make_settings()
    database = await make_database(settings)
    async with database.session_factory() as session:
        session.add(
            ItemModel(
                item_id="DbOnlyOre",
                item_type="Material",
                name_ko="디비전용광석",
                aliases=["디비전용광석"],
                description="DB 에만 있는 아이템.",
            )
        )
        session.add(
            RecipeModel(
                recipe_id="db-only-recipe",
                result_item_id="DbOnlyOre",
                result_amount=1,
                required_workbench="Basic Workbench",
                duration_seconds=2.0,
                ingredients=[{"ItemId": "DbOnlyOre", "Amount": 1}],
            )
        )
        session.add(
            EnemyModel(
                enemy_id="DbOnlyEnemy",
                name_ko="디비몹",
                aliases=["디비몹"],
                description="DB 에만 있는 적.",
                weakness={"weak_element": "Water", "weak_part": "몸통", "ai_advice": "조심해."},
            )
        )
        await session.commit()

    async with database.session_factory() as session:
        loaded = await load_game_dataset(session)

    assert any(item.item_id == "DbOnlyOre" for item in loaded.items)
    assert not any(item.item_id == "DbOnlyOre" for item in DATASET.items)
    assert any(recipe.recipe_id == "db-only-recipe" for recipe in loaded.recipes)
    assert any(enemy.enemy_id == "DbOnlyEnemy" for enemy in loaded.enemies)


def test_recipe_repository_uses_the_injected_dataset_not_the_static_one() -> None:
    custom = GameDataSet(
        items=(
            Item(
                item_id="CustomIngot",
                item_type="Material",
                name_ko="커스텀주괴",
                aliases=("커스텀주괴",),
                description="테스트 전용.",
            ),
        ),
        recipes=(
            Recipe(
                recipe_id="custom-recipe",
                result_item_id="CustomIngot",
                result_amount=1,
                required_workbench="Basic Workbench",
                duration_seconds=0,
                ingredients=(Ingredient("CustomIngot", 1),),
            ),
        ),
        smelting_recipes=(),
        enemies=(),
        locations=(),
    )

    injected = RecipeRepository(custom)
    fact = injected.fact_for("커스텀주괴 만드는 법")
    assert fact is not None
    assert "커스텀주괴" in fact.text

    # 정적 DATASET 기반 기본 인스턴스는 커스텀 데이터셋의 아이템을 전혀 모른다.
    default = RecipeRepository()
    assert default.fact_for("커스텀주괴 만드는 법") is None


def test_enemy_repository_uses_the_injected_dataset_not_the_static_one() -> None:
    custom = GameDataSet(
        items=(),
        recipes=(),
        smelting_recipes=(),
        enemies=(
            Enemy(
                enemy_id="CustomBeast",
                name_ko="커스텀야수",
                aliases=("커스텀야수",),
                description="테스트 전용.",
                weak_element="Water",
                weak_part="머리",
                ai_advice="물을 써라.",
            ),
        ),
        locations=(),
    )

    injected = EnemyRepository(custom)
    fact = injected.fact_for("커스텀야수 약점")
    assert fact is not None
    assert "커스텀야수" in fact.text

    default = EnemyRepository()
    assert default.fact_for("커스텀야수 약점") is None
