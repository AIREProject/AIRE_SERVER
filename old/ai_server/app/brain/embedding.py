"""장기기억 두뇌가 사용하는 임베딩 공급자 인터페이스."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence


class EmbeddingProvider(ABC):
    """문장들을 벡터로 바꾸는 공급자. 구현과 설정은 HTTP 경계에서 조립한다."""

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...] | None, ...]:
        """각 문장의 단위 벡터를 반환한다. 공급자 오류는 `None`으로 반환한다."""

        raise NotImplementedError

    async def aclose(self) -> None:
        """공급자가 보유한 자원을 정리한다."""

        return None
