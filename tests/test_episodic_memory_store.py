from datetime import UTC, datetime, timedelta

from app.brain.memory import build_memory
from app.episodic_memory_store import EpisodicMemoryStore
from tests.conftest import make_database, make_settings


async def test_memory_and_embedding_survive_a_new_store() -> None:
    database = await make_database(make_settings())
    memory = build_memory(
        "profile",
        "플레이어는 밤을 무서워한다",
        importance=6,
        created_at=datetime.now(UTC),
        embedding=(1.0, 0.0),
        embedding_model="test-model",
    )
    assert memory is not None

    await EpisodicMemoryStore(database, embedding_model="test-model").remember(
        "player-a", (memory,)
    )
    recalled = await EpisodicMemoryStore(database, embedding_model="test-model").recall(
        "player-a",
        query="어두운 시간이 무서워",
        query_embedding=(1.0, 0.0),
        embedding_model="test-model",
        limit=1,
    )

    assert len(recalled) == 1
    assert recalled[0].embedding == (1.0, 0.0)
    assert recalled[0].recall_count == 1


async def test_players_are_isolated_and_replace_all_can_clear() -> None:
    database = await make_database(make_settings())
    memory = build_memory("episode", "플레이어는 돌을 좋아한다", importance=4)
    assert memory is not None
    store = EpisodicMemoryStore(database)

    await store.remember("player-a", (memory,))

    assert await store.recall("player-b", query="돌", limit=3) == ()
    await store.replace_all("player-a", ())
    assert await store.recall("player-a", query="돌", limit=3) == ()


async def test_memory_cap_is_preserved_in_sqlite() -> None:
    database = await make_database(make_settings())
    now = datetime.now(UTC)
    memories = tuple(
        build_memory(
            "episode",
            f"플레이어가 장소를 기억한다 {chr(0xAC00 + index)}",
            importance=1,
            created_at=now - timedelta(days=index),
        )
        for index in range(33)
    )
    valid = tuple(memory for memory in memories if memory is not None)
    await EpisodicMemoryStore(database).remember("player-a", valid)

    recalled = await EpisodicMemoryStore(database).recall("player-a", query="", limit=64)

    assert len(recalled) == 32
