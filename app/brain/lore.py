from __future__ import annotations

from typing import ClassVar

from .facts import DialogueFact


class LoreRepository:
    """위치 ID에 대응하는 검증된 세계관 정보를 제공한다."""

    # 현재는 작은 고정 데이터지만, 나중에 파일이나 데이터베이스 저장소로 교체할 수 있다.
    _LORE: ClassVar[dict[str, str]] = {
        "region_abandoned_mining_village": (
            "버려진 광산 마을은 오래전 광산이 폐쇄된 뒤 사람들이 떠난 곳이야. "
            "남아 있는 건물과 기록은 조심해서 살펴보자."
        )
    }

    def fact_for(self, location_id: str | None) -> DialogueFact | None:
        """알려진 위치라면 세계관 사실을, 아니라면 None을 반환한다."""

        if location_id is None or location_id not in self._LORE:
            return None
        return DialogueFact(kind="lore", text=self._LORE[location_id])
