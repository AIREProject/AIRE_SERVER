from __future__ import annotations

import re

from app.gamedata.dataset import DATASET, Enemy, GameDataSet

from .facts import DialogueFact
from .korean import alias_pattern, subject, topic

_WEAK_ELEMENT_NAMES = {
    "Water": "물",
    "EMP": "전자기 펄스(EMP)",
    "Explosive": "폭발물",
}


class EnemyRepository:
    """게임 데이터셋에서 검증된 적 약점과 공략을 제공한다.

    기본값은 정적 `DATASET`(`app/gamedata/dataset.py`)이지만, `app/main.py` 가 시작 시점에
    DB 를 읽어 만든 `GameDataSet` 을 대신 넘길 수도 있다(`app/service.py`). 인덱스는 여기
    `__init__` 에서 한 번만 만들어진다 — 이전엔 모듈 임포트 시점의 전역이었다.
    """

    def __init__(self, dataset: GameDataSet = DATASET) -> None:
        self._enemies: dict[str, Enemy] = {enemy.enemy_id: enemy for enemy in dataset.enemies}
        aliases_by_enemy: dict[str, tuple[str, ...]] = {
            enemy.enemy_id: tuple(dict.fromkeys((*enemy.aliases, enemy.name_ko, enemy.enemy_id)))
            for enemy in dataset.enemies
        }
        self._enemy_patterns: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
            (enemy_id, pattern)
            for enemy_id, aliases in aliases_by_enemy.items()
            for pattern in (alias_pattern(alias) for alias in aliases)
        )
        self._all_enemy_aliases: tuple[str, ...] = tuple(
            alias for aliases in aliases_by_enemy.values() for alias in aliases
        )

    def _match_single(self, query: str) -> Enemy | None:
        """발화가 정확히 하나의 적을 가리킬 때만 그 적을 반환한다."""

        enemy_ids = {
            enemy_id
            for enemy_id, pattern in self._enemy_patterns
            if pattern.search(query) is not None
        }
        if len(enemy_ids) != 1:
            return None
        return self._enemies[next(iter(enemy_ids))]

    def fact_for(self, query: str) -> DialogueFact | None:
        """현재 발화가 가리키는 적의 약점 사실을 반환한다."""

        enemy = self._match_single(query)
        if enemy is None:
            return None

        element = _WEAK_ELEMENT_NAMES[enemy.weak_element]
        text = (
            f"{topic(enemy.name_ko)} {subject(enemy.weak_part)} 약점이고, {element}에 약해. "
            f"{enemy.ai_advice}"
        )
        return DialogueFact(kind="enemy", text=text)

    def resolve_target(self, query: str) -> str | None:
        """공격 대상으로 지목된 적의 ID. 특정되지 않으면 None(게임의 현재 타깃에 맡긴다)."""

        enemy = self._match_single(query)
        return enemy.enemy_id if enemy is not None else None

    def name_aliases(self) -> tuple[str, ...]:
        """Mock 라우터가 적 공략 의도를 찾을 때 사용할 이름 별칭을 반환한다."""

        return self._all_enemy_aliases

    @classmethod
    def weak_element_names(cls) -> dict[str, str]:
        """데이터셋의 약점 속성에 대응하는 한국어 표시 이름을 반환한다(고정 상수, 테이블 무관)."""

        return dict(_WEAK_ELEMENT_NAMES)
