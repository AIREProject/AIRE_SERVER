"""서버가 사용하는 게임 마스터 데이터.

이 모듈은 SQLAlchemy나 FastAPI를 모르는 순수 데이터 leaf다. Alembic 시드와 마코의
검증된 제작법 저장소가 같은 상수를 읽으므로, DB와 대사 지식이 서로 다른 값을 갖지 않는다.
안정 ID, 한국어 이름과 별칭은 `docs/game-data.md`의 절차에 따라 함께 검수한다.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Item:
    """제작법에 등장하는 아이템 한 행."""

    item_id: str
    item_type: str
    name_ko: str
    aliases: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class Ingredient:
    """제작 또는 제련에 필요한 아이템과 수량."""

    item_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class Recipe:
    """일반 제작법 한 행."""

    recipe_id: str
    result_item_id: str
    result_amount: int
    required_workbench: str
    duration_seconds: float
    ingredients: tuple[Ingredient, ...]


@dataclass(frozen=True, slots=True)
class SmeltingRecipe:
    """연료를 따로 요구하는 제련법 한 행."""

    smelt_id: str
    result_item_id: str
    result_amount: int
    required_workbench: str
    duration_seconds: float
    input: Ingredient
    fuel: Ingredient


@dataclass(frozen=True, slots=True)
class Enemy:
    """적 한 행과 전투 조언."""

    enemy_id: str
    name_ko: str
    aliases: tuple[str, ...]
    description: str
    weak_element: str
    weak_part: str
    ai_advice: str


@dataclass(frozen=True, slots=True)
class Location:
    """위치 좌표 한 행. 현재 ERD에는 실제 위치 행이 없다."""

    location_id: str
    coordinates: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class GameDataSet:
    """마이그레이션과 런타임 저장소가 공유하는 전체 정적 데이터."""

    items: tuple[Item, ...]
    recipes: tuple[Recipe, ...]
    smelting_recipes: tuple[SmeltingRecipe, ...]
    enemies: tuple[Enemy, ...]
    locations: tuple[Location, ...]


ITEMS: tuple[Item, ...] = (
    Item(
        "PlantStem",
        "Material",
        "나무",
        ("나무", "목재", "나뭇가지", "나무 가지", "가지", "식물 줄기", "풀 줄기", "줄기"),
        "필드에서 구할 수 있는 기본 나무 재료. 도구, 붕대와 밧줄 제작에 쓰임.",
    ),
    Item(
        "ShoddyBandage",
        "Consumable",
        "엉성한 붕대",
        ("엉성한 붕대", "붕대"),
        "식물 줄기로 엉성하게 엮은 붕대. 미미한 출혈을 막고 약간의 체력을 회복함.",
    ),
    Item(
        "Rope",
        "Material",
        "밧줄",
        ("밧줄", "로프"),
        "식물 줄기를 꼬아 만든 질긴 밧줄. 갑옷이나 작업대를 고정할 때 필수적임.",
    ),
    Item(
        "Stone",
        "Material",
        "돌",
        ("돌멩이", "돌"),
        "필드에서 주울 수 있는 단단한 돌멩이. 초반 도구 제작의 핵심 재료.",
    ),
    Item(
        "Axe_Stone",
        "Tool",
        "돌도끼",
        ("돌도끼", "돌 도끼", "석재 도끼"),
        "돌과 나뭇가지를 엮어 만든 기초 도끼. 나무 채집 효율을 높여줌.",
    ),
    Item(
        "Pickaxe_Stone",
        "Tool",
        "돌곡괭이",
        ("돌곡괭이", "돌 곡괭이", "석재 곡괭이"),
        "돌과 나뭇가지를 엮어 만든 기초 곡괭이. 광석 채집 효율을 높여줌.",
    ),
    Item(
        "Campfire",
        "Structure",
        "모닥불",
        ("모닥불", "캠프파이어", "불"),
        "나뭇가지와 돌로 엮은 모닥불 키트. 체온을 유지하고 고기를 굽는 데 사용.",
    ),
    Item(
        "IronOre",
        "Material",
        "철광석",
        ("철광석", "철 광석", "철 원석"),
        "바위에서 캐낸 철광석 원석. 그대로는 쓸 수 없고 제련이 필요함.",
    ),
    Item(
        "IronIngot",
        "Material",
        "철괴",
        ("철괴", "철 주괴", "철 잉곳"),
        "용광로에서 철광석을 제련해 만든 철괴. 고급 도구와 무기의 핵심 재료.",
    ),
    Item(
        "Nail_Iron",
        "Material",
        "철못",
        ("철못", "철 못", "못"),
        "철괴를 가공해 만든 못. 가구나 작업대를 조립할 때 필수적임.",
    ),
    Item(
        "WoodPlank",
        "Material",
        "나무 널빤지",
        ("나무 널빤지", "널빤지", "목재 판자"),
        "원목을 가공해 만든 널빤지. 건축과 작업대 제작에 쓰임.",
    ),
    Item(
        "Workbench",
        "Structure",
        "작업대",
        ("작업대", "제작대"),
        "기초적인 장비와 도구를 만들 수 있는 작업대.",
    ),
    Item(
        "Leather",
        "Material",
        "가죽",
        ("가죽", "동물 가죽"),
        "동물을 사냥해 얻은 가죽. 방어구나 무기 손잡이의 재료로 쓰임.",
    ),
    Item(
        "Armor_Leather",
        "Armor",
        "가죽 갑옷",
        ("가죽 갑옷", "가죽 방어구", "가죽옷"),
        "가죽과 밧줄을 엮어 만든 초반용 방어구. 가벼운 물리 공격을 막아줌.",
    ),
    Item(
        "Coal",
        "Material",
        "석탄",
        ("석탄", "탄"),
        "광맥에서 캐낸 석탄. 온도를 높여 강철을 제련할 때 쓰임.",
    ),
    Item(
        "SteelIngot",
        "Material",
        "강철괴",
        ("강철괴", "강철 주괴", "강철 잉곳"),
        "철괴와 석탄을 섞어 용광로에서 제련한 강철괴. 철보다 훨씬 단단함.",
    ),
    Item(
        "WoodHandle",
        "Material",
        "나무 손잡이",
        ("나무 손잡이", "목재 손잡이", "손잡이"),
        "무기를 쥐기 좋게 가공한 나무 손잡이.",
    ),
    Item(
        "Sword_Iron",
        "Weapon",
        "철검",
        ("철검", "철 검", "쇠검"),
        "철괴로 벼려낸 기본 철검. 본격적인 전투를 위한 첫걸음.",
    ),
    Item(
        "SteelGreatsword",
        "Weapon",
        "강철 대검",
        ("강철 대검", "강철대검", "대검"),
        "강철로 만들어진 거대한 양손검. 강력한 파괴력을 지녔지만 무거움.",
    ),
    Item(
        "Herb",
        "Material",
        "허브",
        ("허브", "약초"),
        "약효가 있는 허브. 포션의 핵심 재료.",
    ),
    Item(
        "ClearWater",
        "Material",
        "깨끗한 물",
        ("깨끗한 물", "정수된 물", "맑은 물"),
        "정수된 깨끗한 물. 연금술의 베이스로 쓰임.",
    ),
    Item(
        "Potion_High",
        "Consumable",
        "고급 포션",
        ("고급 포션", "상급 포션", "포션"),
        "연금술로 정제한 고급 포션. 대량의 체력을 즉시 회복시킴.",
    ),
    Item(
        "CopperOre",
        "Material",
        "구리 광석",
        ("구리 광석", "구리광석", "구리 원석"),
        "바위에서 캐낸 구리 광석. 전도율이 높아 기계 부품에 쓰이나 제련이 필요함.",
    ),
    Item(
        "CopperIngot",
        "Material",
        "구리괴",
        ("구리괴", "구리 주괴", "구리 잉곳"),
        "용광로에서 구리 광석을 제련해 만든 구리괴.",
    ),
    Item(
        "Sand",
        "Material",
        "모래",
        ("모래", "모래알"),
        "강가나 해변에서 얻을 수 있는 모래. 유리의 주원료.",
    ),
    Item(
        "Glass",
        "Material",
        "유리",
        ("유리", "유리병 재료"),
        "모래를 고온에 녹여 만든 유리. 각종 병이나 정밀 부품에 쓰임.",
    ),
)

RECIPES: tuple[Recipe, ...] = (
    Recipe("recipe-1", "ShoddyBandage", 1, "None (Handcraft)", 0.0, (Ingredient("PlantStem", 2),)),
    Recipe("recipe-2", "Rope", 1, "None (Handcraft)", 0.0, (Ingredient("PlantStem", 3),)),
    Recipe(
        "recipe-3",
        "Axe_Stone",
        1,
        "None (Handcraft)",
        0.0,
        (Ingredient("PlantStem", 2), Ingredient("Stone", 1)),
    ),
    Recipe(
        "recipe-4",
        "Pickaxe_Stone",
        1,
        "None (Handcraft)",
        0.0,
        (Ingredient("PlantStem", 2), Ingredient("Stone", 2)),
    ),
    Recipe(
        "recipe-5",
        "Campfire",
        1,
        "None (Handcraft)",
        3.0,
        (Ingredient("PlantStem", 5), Ingredient("Stone", 3)),
    ),
    Recipe("recipe-6", "Nail_Iron", 5, "Basic Workbench", 1.0, (Ingredient("IronIngot", 1),)),
    Recipe(
        "recipe-7",
        "Workbench",
        1,
        "Basic Workbench",
        5.0,
        (Ingredient("WoodPlank", 10), Ingredient("Rope", 2)),
    ),
    Recipe(
        "recipe-8",
        "Armor_Leather",
        1,
        "Basic Workbench",
        2.0,
        (Ingredient("Leather", 5), Ingredient("Rope", 3)),
    ),
    Recipe(
        "recipe-9",
        "IronIngot",
        1,
        "Workbench.Smelter",
        2.0,
        (Ingredient("IronOre", 2),),
    ),
    Recipe(
        "recipe-10",
        "SteelIngot",
        1,
        "Blacksmith Anvil/Furnace",
        3.0,
        (Ingredient("IronIngot", 2), Ingredient("Coal", 1)),
    ),
    Recipe(
        "recipe-11",
        "Sword_Iron",
        1,
        "Blacksmith Anvil/Furnace",
        3.0,
        (Ingredient("IronIngot", 3), Ingredient("WoodHandle", 1)),
    ),
    Recipe(
        "recipe-12",
        "SteelGreatsword",
        1,
        "Blacksmith Anvil/Furnace",
        5.0,
        (Ingredient("SteelIngot", 5), Ingredient("Leather", 2)),
    ),
    Recipe(
        "recipe-13",
        "Potion_High",
        1,
        "Alchemy Table",
        2.0,
        (Ingredient("Herb", 3), Ingredient("ClearWater", 1)),
    ),
    Recipe(
        "recipe-14",
        "WoodHandle",
        1,
        "Basic Workbench",
        1.0,
        (Ingredient("PlantStem", 2),),
    ),
)

SMELTING_RECIPES: tuple[SmeltingRecipe, ...] = (
    SmeltingRecipe(
        "smelt-1",
        "IronIngot",
        1,
        "Workbench.BlastFurnace",
        5.0,
        Ingredient("IronOre", 2),
        Ingredient("Coal", 1),
    ),
    SmeltingRecipe(
        "smelt-2",
        "SteelIngot",
        1,
        "Workbench.BlastFurnace",
        10.0,
        Ingredient("IronIngot", 2),
        Ingredient("Coal", 2),
    ),
    SmeltingRecipe(
        "smelt-3",
        "CopperIngot",
        1,
        "Workbench.BlastFurnace",
        3.0,
        Ingredient("CopperOre", 2),
        Ingredient("Wood", 1),
    ),
    SmeltingRecipe(
        "smelt-4",
        "Glass",
        1,
        "Workbench.BlastFurnace",
        3.0,
        Ingredient("Sand", 4),
        Ingredient("Wood", 1),
    ),
)

ENEMIES: tuple[Enemy, ...] = (
    Enemy(
        "TrenchCrawler",
        "녹슨 참호병",
        ("녹슨 참호병", "참호병", "참호 로봇"),
        (
            "과거 전쟁에서 참호를 파고 방어하던 4족 보행 로봇. 전쟁이 끝난 줄도 모르고 "
            "접근하는 모든 생명체를 적군으로 간주하여 기관총을 난사함. 총열이 휘어 오발이 "
            "잦으나 죽기 직전 자폭 시퀀스를 가동함."
        ),
        "Water",
        "다리 관절",
        "다리 관절이 심하게 녹슬어 있어! 그곳을 집중 사격하면 기동력을 잃을 거야.",
    ),
    Enemy(
        "SirenDrone",
        "절규하는 사이렌 드론",
        ("절규하는 사이렌 드론", "사이렌 드론", "사이렌", "드론"),
        (
            "공습 경보를 알리던 정찰 드론. 스피커가 망가져 사람의 비명소리와 기계음이 섞인 "
            "기괴한 소음을 영원히 재생하며 맵을 배회함. 유저를 발견하면 주변의 묻혀 있던 "
            "지뢰 로봇들을 깨움."
        ),
        "EMP",
        "상단 확성기 센서",
        "저 소음 때문에 집중할 수가 없어! 먼저 상단의 확성기를 파괴해 줘!",
    ),
    Enemy(
        "Goliath",
        "외상성 골리앗",
        ("외상성 골리앗", "골리앗", "의료 로봇"),
        (
            "원래는 아군을 치료하고 보급품을 나르던 대형 의료 로봇이었으나 포탄에 머리를 맞아 "
            "논리 회로가 파괴됨. 모두를 치료해야 한다는 강박에 빠져 유저를 강제로 잡아 "
            "해부하려 드는 거대한 보스급 적."
        ),
        "Explosive",
        "가슴의 깨진 코어",
        (
            "의료 로봇이라 장갑이 두꺼워. 하지만 가슴 쪽에 포탄에 맞아 깨진 틈이 보이니 "
            "그쪽으로 폭발물을 던져!"
        ),
    ),
)

# ERD는 좌표 JSON 형태만 정의했고 실제 Location 행은 전달하지 않았다.
LOCATIONS: tuple[Location, ...] = ()

DATASET = GameDataSet(
    items=ITEMS,
    recipes=RECIPES,
    smelting_recipes=SMELTING_RECIPES,
    enemies=ENEMIES,
    locations=LOCATIONS,
)
