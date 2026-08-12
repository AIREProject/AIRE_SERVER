"""디바이스/프로필/세이브슬롯 영속화용 비동기 DB 엔진과 세션.

`docs/temporary-scaffolds.md` §2 가 예고한 대로 `cd0be55` 의 구현을 되살렸다. SQLite 를
쓰는 동안은 파일 락 경합을 줄이기 위해 WAL 저널링과 외래키 강제를 켠다.
"""

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    def __init__(self, database_url: str) -> None:
        connect_args: dict[str, object] = {}
        if database_url.startswith("sqlite"):
            connect_args["timeout"] = 5

        self.engine: AsyncEngine = create_async_engine(
            database_url,
            connect_args=connect_args,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )
        if database_url.startswith("sqlite"):
            event.listen(self.engine.sync_engine, "connect", _configure_sqlite)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()


def _configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
