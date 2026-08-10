"""HTTP 경계에서 조립하는 장기기억 임베딩 공급자."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.brain.embedding import EmbeddingProvider
from app.brain.memory import normalize_embedding
from app.settings import Settings


class MockEmbeddingProvider(EmbeddingProvider):
    """외부 호출 없이 키워드 검색 폴백을 사용하게 하는 공급자."""

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...] | None, ...]:
        return tuple(None for _ in texts)


class _OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI Embeddings API와 OpenAI 호환 로컬 엔드포인트를 공통 처리한다."""

    def __init__(self, *, client: object, model: str, dimensions: int | None = None) -> None:
        self._client = client
        self._model = model
        # 차원 축소를 지원하지 않는 서버(bge-m3 등)가 있으므로 선택 인자로 둔다.
        self._dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...] | None, ...]:
        if not texts:
            return ()
        try:
            embeddings = self._client.embeddings  # type: ignore[attr-defined]
            extra = {} if self._dimensions is None else {"dimensions": self._dimensions}
            response = await embeddings.create(
                model=self._model,
                input=list(texts),
                **extra,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            if len(ordered) != len(texts):
                return tuple(None for _ in texts)
            return tuple(normalize_embedding(item.embedding) for item in ordered)
        except Exception:
            return tuple(None for _ in texts)

    async def aclose(self) -> None:
        await self._client.close()  # type: ignore[attr-defined]


class OpenAIEmbeddingProvider(_OpenAIEmbeddingProvider):
    """OpenAI의 `text-embedding-3-small` 계열을 호출한다."""

    def __init__(self, config: Settings) -> None:
        from openai import AsyncOpenAI

        if not config.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai")
        super().__init__(
            client=AsyncOpenAI(
                api_key=config.openai_api_key,
                timeout=config.embedding_timeout_seconds,
            ),
            model=config.openai_embedding_model,
            dimensions=config.openai_embedding_dimensions,
        )


class LocalEmbeddingProvider(_OpenAIEmbeddingProvider):
    """OpenAI 호환 자체 호스팅 임베딩 엔드포인트를 호출한다.

    대사 LLM과 **다른 서버일 수 있다.** 임베딩 서버는 base_url·자격증명·헤더가 따로라,
    대사용 설정을 그대로 쓰면 남의 서버에 다른 서버의 키를 보내게 된다.
    """

    def __init__(self, config: Settings) -> None:
        from openai import AsyncOpenAI

        if not config.local_embedding_model:
            raise ValueError("LOCAL_EMBEDDING_MODEL is required when EMBEDDING_PROVIDER=local")
        base_url = config.local_embedding_base_url or config.local_llm_base_url
        # 임베딩 전용 키가 없으면 대사 서버와 같은 호스트일 때만 그 키를 물려받는다.
        api_key = config.local_embedding_api_key
        if api_key is None and base_url == config.local_llm_base_url:
            api_key = config.local_llm_api_key
        headers = (
            {"User-Agent": config.local_embedding_user_agent}
            if config.local_embedding_user_agent
            else None
        )
        super().__init__(
            client=AsyncOpenAI(
                base_url=base_url,
                # 인증이 필요 없는 서버도 있어 SDK 가 요구하는 자리만 채운다.
                api_key=api_key or "not-required",
                timeout=config.embedding_timeout_seconds,
                default_headers=headers,
            ),
            model=config.local_embedding_model,
            dimensions=config.local_embedding_dimensions,
        )


@dataclass(frozen=True, slots=True)
class SelectedEmbeddingProvider:
    """선택된 임베딩 공급자와 모델 식별자."""

    provider: EmbeddingProvider
    name: str
    model_version: str


def build_embedding_provider(config: Settings) -> SelectedEmbeddingProvider:
    """설정·자격증명이 맞을 때만 외부 임베딩을 선택하고 아니면 mock을 쓴다."""

    provider = config.embedding_provider.casefold()
    if provider == "local" and config.local_embedding_model:
        inner: EmbeddingProvider = LocalEmbeddingProvider(config)
        name, model_version = "local", config.local_embedding_model
    elif provider == "openai" and config.openai_api_key:
        inner = OpenAIEmbeddingProvider(config)
        name, model_version = "openai", config.openai_embedding_model
    else:
        inner = MockEmbeddingProvider()
        name, model_version = "mock", "mock-v1"
    return SelectedEmbeddingProvider(provider=inner, name=name, model_version=model_version)
