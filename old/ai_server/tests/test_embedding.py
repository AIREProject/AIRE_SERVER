from app.brain.memory import normalize_embedding
from app.embedding import MockEmbeddingProvider, build_embedding_provider
from tests.conftest import make_settings


def test_mock_embedding_provider_is_the_default() -> None:
    selected = build_embedding_provider(make_settings())

    assert selected.name == "mock"
    assert selected.model_version == "mock-v1"


async def test_mock_embedding_provider_returns_keyword_fallback_values() -> None:
    vectors = await MockEmbeddingProvider().embed(("밤을 싫어한다", "돌을 좋아한다"))

    assert vectors == (None, None)


def test_embedding_is_normalized_to_unit_length() -> None:
    vector = normalize_embedding((3.0, 4.0))

    assert vector == (0.6, 0.8)
    assert normalize_embedding((0.0, 0.0)) is None
