from typing import Literal

from pydantic import BaseModel


class DialogueFact(BaseModel):
    """저장소에서 찾은 제작법·세계관·적 약점 사실."""

    kind: Literal["recipe", "lore", "enemy"]
    text: str
