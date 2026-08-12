from __future__ import annotations

import re
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from .intent import ResourceSlot

# 한 번에 채집을 요청할 수 있는 최대 수량. 게임 정책이므로 설정이 아니라 이 모듈이 소유한다.
MAX_GATHER_QUANTITY = 50


class ResourceId(StrEnum):
    """서버가 채집을 지원하는 자원의 정식 식별자."""

    WOOD = "wood"
    STONE = "stone"


# 정식 식별자 → 플레이어가 실제로 쓰는 표현. 허용 목록이므로 여기에 없으면 미지원이다.
# 미지원 자원을 거부 목록으로 나열하면 끝없이 늘어나므로 반대 방향으로 관리한다.
_ALIASES: dict[ResourceId, tuple[str, ...]] = {
    ResourceId.WOOD: ("나무", "목재", "장작", "통나무", "나뭇가지", "땔감"),
    ResourceId.STONE: ("돌", "바위", "석재", "자갈"),
}

# 대사에서 자원을 부를 때 쓰는 이름. 되묻기 대사의 확정 사실을 만드는 데 쓴다.
_DISPLAY_NAMES: dict[ResourceId, str] = {
    ResourceId.WOOD: "나무",
    ResourceId.STONE: "돌",
}

# 한국어는 교착어라 "나무를"처럼 조사가 붙는다. 단순 부분 문자열 검색은
# "부싯돌"을 돌로 잘못 잡으므로 앞은 어절 경계, 뒤는 조사까지만 허용한다.
_PARTICLES = "을|를|은|는|이|가|도|만|와|과|랑|이랑|하고"
_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]", flags=re.UNICODE)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?:^|\s){re.escape(alias)}(?:{_PARTICLES})?(?=\s|$)")


class GatherParameters(BaseModel):
    """채집 명령 후보의 `parameters` 에 실리는 값. 상한 검증이 여기 한곳에 모인다."""

    model_config = ConfigDict(extra="forbid")

    resource: ResourceId
    quantity: int | None = Field(default=None, ge=1, le=MAX_GATHER_QUANTITY)


class ResourceRepository:
    """채집 가능한 자원의 정식 식별자, 별칭, 수량 정책을 소유한다."""

    _PATTERNS: ClassVar[dict[ResourceId, tuple[re.Pattern[str], ...]]] = {
        resource: tuple(_alias_pattern(alias) for alias in aliases)
        for resource, aliases in _ALIASES.items()
    }

    max_quantity: int = MAX_GATHER_QUANTITY

    def find_all(self, text: str) -> tuple[ResourceId, ...]:
        """발화에 등장하는 지원 자원을 모두 찾는다. Mock 공급자와 실패 폴백이 쓴다.

        하나만 반환하면 "돌이랑 나무"처럼 여럿을 말했을 때 표의 정의 순서가
        플레이어의 말을 덮어쓴다. 호출자가 개수를 보고 판단하도록 전부 돌려준다.
        """

        normalized = _PUNCTUATION_PATTERN.sub(" ", text.casefold())
        normalized = " ".join(normalized.split())
        return tuple(
            resource
            for resource, patterns in self._PATTERNS.items()
            if any(pattern.search(normalized) for pattern in patterns)
        )

    def resolve_slot(self, slot: ResourceSlot) -> ResourceId | None:
        """LLM이 고른 슬롯을 정식 식별자로 옮긴다. 지원하지 않으면 None이다."""

        if slot is ResourceSlot.WOOD:
            return ResourceId.WOOD
        if slot is ResourceSlot.STONE:
            return ResourceId.STONE
        return None

    def allows_quantity(self, quantity: int | None) -> bool:
        """수량이 정책 범위 안인지 판정한다. 미명시(None)는 정상으로 취급한다."""

        return quantity is None or 1 <= quantity <= self.max_quantity

    def supported_names(self) -> tuple[str, ...]:
        """되묻기 대사에서 안내할 자원 이름을 반환한다."""

        return tuple(_DISPLAY_NAMES[resource] for resource in _ALIASES)

    def display_name(self, resource: ResourceId) -> str:
        """자원의 대사용 이름을 반환한다."""

        return _DISPLAY_NAMES[resource]
