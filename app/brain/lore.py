from __future__ import annotations

from typing import ClassVar

from .facts import DialogueFact


class LoreRepository:
    """위치 ID에 대응하는 검증된 세계관 정보를 제공한다."""

    # 현재는 작은 고정 데이터지만, 나중에 파일이나 데이터베이스 저장소로 교체할 수 있다.
    _LORE: ClassVar[dict[str, str]] = {
        "forest_camp": (
            "숲 캠프는 탐사와 작업을 준비하는 일반 거점이야. "
            "주변 숲을 살피고 필요한 장비와 자원을 정비하기 좋은 곳이야."
        )
    }

    def fact_for(self, location_id: str | None) -> DialogueFact | None:
        """알려진 위치라면 세계관 사실을, 아니라면 None을 반환한다."""

        if location_id is None or location_id not in self._LORE:
            return None
        return DialogueFact(kind="lore", text=self._LORE[location_id])
