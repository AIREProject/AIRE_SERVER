import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.db.connection import Database
from app.db.game_data_loader import load_game_dataset
from app.errors_http import register_error_handlers
from app.gamedata.dataset import GameDataSet
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware
from app.routes.admin import router as admin_router
from app.routes.chat import router as chat_router
from app.routes.devices import router as devices_router
from app.routes.game_state import router as game_state_router
from app.routes.offline_tasks import router as offline_tasks_router
from app.routes.situations import router as situations_router
from app.routes.system import router as system_router
from app.routes.ws_chat import router as ws_chat_router
from app.service import CompanionService
from app.settings import Settings, get_settings

logger = logging.getLogger("aire.backend")


def _run_to_completion[T](coro: Coroutine[Any, Any, T]) -> T:
    """이미 실행 중인 루프 안에서도 코루틴 하나를 동기적으로 끝까지 돌린다.

    실제 진입점(`app = create_app()`)은 모듈 임포트 시점이라 루프가 없어 `asyncio.run` 이면
    충분하다. 테스트는 `create_app(settings)` 를 pytest-asyncio 가 이미 돌리는 루프 **안에서**
    부르므로 `asyncio.run` 이 "cannot be called from a running event loop" 로 바로 죽는다 —
    그때만 별도 스레드에 새 루프를 띄운다.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    results: list[T] = []
    errors: list[Exception] = []

    def _runner() -> None:
        try:
            results.append(asyncio.run(coro))
        except Exception as exc:  # 스레드 경계를 넘겨 호출자 쪽에서 다시 던진다
            errors.append(exc)

    thread = threading.Thread(target=_runner)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return results[0]


async def _bootstrap_game_dataset(database: Database) -> GameDataSet:
    """`asyncio.run()` 이 도는 짧은 루프 안에서만 커넥션을 연다.

    `database` 는 그 뒤 uvicorn 의 진짜 이벤트 루프에서 다시 쓰인다 — aiosqlite 커넥션은
    자신을 만든 루프에 묶이므로, 여기서 연 커넥션을 반납만 하고 `dispose()` 하지 않으면
    나중에 "다른 루프에 붙은 Future" 류의 오류가 난다. 세션을 정상 종료한 뒤 풀을 비워서,
    실제 서버 루프가 시작되면 그 루프에 묶인 새 커넥션을 다시 연다.
    """

    async with database.session_factory() as session:
        dataset = await load_game_dataset(session)
    await database.engine.dispose()
    return dataset


def _load_startup_game_dataset(database: Database) -> GameDataSet | None:
    """게임데이터는 다음 재시작에만 반영된다(핫 리로드 아님) — 앱 임포트 시점에, 이벤트
    루프가 아직 없는 상태로 딱 한 번 읽는다.

    `alembic upgrade head` 를 아직 안 돌린 DB(items 테이블 자체가 없거나 비어 있는 경우 —
    테스트 스캐폴딩 포함)는 정적 `DATASET` 으로 조용히 되돌아간다. `DEVICE_CREDENTIAL_PEPPER`
    미설정이 인증 라우트만 503 으로 좁히듯, 여기 실패도 앱 부팅 자체를 막으면 안 된다.
    """

    try:
        loaded = _run_to_completion(_bootstrap_game_dataset(database))
    except SQLAlchemyError:
        logger.warning(
            "game_dataset_bootstrap_failed",
            extra={"event": "game_dataset_bootstrap_failed"},
        )
        return None
    # 세 카테고리를 동시에 요구하는 이유: 실제 마이그레이션 0002 시드는 이 셋을 항상 함께
    # 채운다. items 만 있고 recipes/enemies 가 비어 있는 DB는 대개 FK 를 만족시키려고 테스트가
    # 아이템 한 행만 심어 둔 경우라, 그런 부분 데이터를 "권위 있는 게임데이터"로 취급하면
    # 안 된다. locations 는 현재 시드 대상이 아니므로 조건에서 뺀다
    # (`docs/game-data.md`의 현재 제한 사항 참고).
    if loaded.items and loaded.recipes and loaded.enemies:
        return loaded
    return None


def create_app(settings: Settings | None = None) -> FastAPI:
    selected_settings = settings or get_settings()
    configure_logging(
        selected_settings.log_level,
        access_log_enabled=selected_settings.access_log_enabled,
        access_log_path=selected_settings.access_log_path,
        access_log_max_bytes=selected_settings.access_log_max_bytes,
        access_log_backup_count=selected_settings.access_log_backup_count,
    )
    database = Database(selected_settings.database_url)
    game_dataset = _load_startup_game_dataset(database)
    companion = CompanionService.from_settings(
        selected_settings, database, game_dataset=game_dataset
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await companion.aclose()
        await database.dispose()

    app = FastAPI(
        title="Mako Companion API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.companion = companion
    app.state.database = database
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: selected_settings

    register_error_handlers(app)
    app.add_middleware(
        RequestContextMiddleware,
        max_body_bytes=selected_settings.max_request_body_bytes,
        timeout_seconds=selected_settings.request_timeout_seconds,
    )
    app.include_router(system_router)
    app.include_router(chat_router)
    app.include_router(ws_chat_router)
    app.include_router(devices_router)
    app.include_router(game_state_router)
    app.include_router(offline_tasks_router)
    app.include_router(situations_router)
    app.include_router(admin_router)
    return app


app = create_app()
