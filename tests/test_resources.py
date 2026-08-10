"""자원 저장소의 별칭 매칭과 수량 정책 검증.

허용 목록 방식이 거부 목록 없이도 미지원 자원을 걸러 내는지, 그리고 조사 경계
매칭이 부분 문자열 오탐(`부싯돌` → 돌)을 막는지를 회귀로 고정한다.
"""

import pytest

from app.brain.intent import ResourceSlot
from app.brain.resources import (
    MAX_GATHER_QUANTITY,
    GatherParameters,
    ResourceId,
    ResourceRepository,
)


@pytest.fixture
def resources() -> ResourceRepository:
    return ResourceRepository()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("나무", ResourceId.WOOD),
        ("나무를 모아 줘", ResourceId.WOOD),
        ("장작 좀 모아줘", ResourceId.WOOD),
        ("통나무 가져와", ResourceId.WOOD),
        ("나뭇가지랑 모아 줘", ResourceId.WOOD),
        ("땔감이 필요해", ResourceId.WOOD),
        ("돌 캐줘", ResourceId.STONE),
        ("돌을 캐 줘", ResourceId.STONE),
        ("바위 좀 캐 줘", ResourceId.STONE),
        ("자갈 모아 줘", ResourceId.STONE),
    ],
)
def test_resolves_supported_aliases(
    resources: ResourceRepository, text: str, expected: ResourceId
) -> None:
    assert resources.find_all(text) == (expected,)


@pytest.mark.parametrize(
    "text",
    [
        # 조사 경계 매칭이 없으면 "돌"이 부분 문자열로 잡히는 표현들이다.
        "부싯돌 캐 줘",
        "조약돌 모아 줘",
        "흑요석 캐 줘",
        # 허용 목록에 없으므로 거부 목록 없이도 미지원으로 떨어진다.
        "철광석을 캐 줘",
        "석탄 좀 캐 줘",
        "풀을 캐 줘",
    ],
)
def test_rejects_unsupported_resources(resources: ResourceRepository, text: str) -> None:
    assert resources.find_all(text) == ()


def test_resolve_ignores_punctuation(resources: ResourceRepository) -> None:
    assert resources.find_all("나무, 좀 모아 줘!") == (ResourceId.WOOD,)


@pytest.mark.parametrize(
    "text",
    ["돌이랑 나무를 모아 줘", "나무랑 돌 캐 줘", "장작이랑 바위 가져와"],
)
def test_finds_every_resource_mentioned(resources: ResourceRepository, text: str) -> None:
    """하나만 돌려주면 표의 정의 순서가 플레이어의 말을 덮어쓴다."""

    assert set(resources.find_all(text)) == {ResourceId.WOOD, ResourceId.STONE}


@pytest.mark.parametrize(
    ("slot", "expected"),
    [
        (ResourceSlot.WOOD, ResourceId.WOOD),
        (ResourceSlot.STONE, ResourceId.STONE),
        (ResourceSlot.OTHER, None),
        (ResourceSlot.UNSPECIFIED, None),
    ],
)
def test_resolve_slot_covers_every_slot(
    resources: ResourceRepository, slot: ResourceSlot, expected: ResourceId | None
) -> None:
    assert resources.resolve_slot(slot) is expected


@pytest.mark.parametrize(
    ("quantity", "allowed"),
    [
        (None, True),  # 미명시는 정상 경로다. 게임이 기본량을 정한다.
        (1, True),
        (MAX_GATHER_QUANTITY, True),
        (0, False),
        (-1, False),
        (MAX_GATHER_QUANTITY + 1, False),
    ],
)
def test_quantity_policy(
    resources: ResourceRepository, quantity: int | None, allowed: bool
) -> None:
    assert resources.allows_quantity(quantity) is allowed


def test_supported_names_backs_dialogue_facts(resources: ResourceRepository) -> None:
    assert resources.supported_names() == ("나무", "돌")


def test_gather_parameters_omit_absent_quantity() -> None:
    parameters = GatherParameters(resource=ResourceId.WOOD, quantity=None)

    assert parameters.model_dump(mode="json", exclude_none=True) == {"resource": "wood"}


def test_gather_parameters_reject_quantity_over_limit() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        GatherParameters(resource=ResourceId.WOOD, quantity=MAX_GATHER_QUANTITY + 1)
