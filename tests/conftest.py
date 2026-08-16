"""테스트 전역 설정.

**테스트는 개발자의 로컬 환경을 읽지 않는다.** `Settings` 는 평소 `.env` 와 환경변수를
읽는데, 그대로 두면 테스트가 이 저장소의 `.env`(예: `LLM_PROVIDER=local`,
`LOCAL_LLM_TIMEOUT_SECONDS=30`)에 의존하게 된다. 그러면 명시적으로 값을 넘기지 않은
단언이 **이 머신에서만 우연히** 통과하고, 다른 값을 가진 팀원이나 CI 에서 깨진다.

`Settings` 는 `extra="ignore"` 라 잘못된 필드명을 조용히 삼키므로 — frozen dataclass 처럼
`TypeError` 를 내주지 않는다 — 이 격리가 없으면 오타 난 kwarg 가 `.env` 값으로 대체되어
테스트가 통과해 버린다. 그 조합이 가장 위험하다.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.credentials import CredentialProtector
from app.db.base import Base
from app.db.connection import Database
from app.db.models import DeviceModel, ProfileModel
from app.identity import AuthenticatedDevice, DeviceRole
from app.settings import Settings

# 필드명 → 환경변수명. pydantic-settings 의 기본 규칙(대문자)을 그대로 따른다.
_SETTINGS_ENV_NAMES = frozenset(name.upper() for name in Settings.model_fields)


def make_settings(**overrides: Any) -> Settings:
    """테스트용 `Settings`. 모르는 필드명은 조용히 삼키지 않고 `TypeError` 를 낸다.

    `extra="ignore"` 때문에 `Settings(max_request_body=8)` 은 오타를 알려주지 않고
    기본값 그대로 돌아간다. 위 격리와 짝이 되는 나머지 절반이다 — 격리가 `.env` 의
    값이 새어 드는 것을 막는다면, 이 검사는 **넘긴 값이 실제로 적용됐는지**를 보장한다.
    """

    unknown = sorted(set(overrides) - set(Settings.model_fields))
    if unknown:
        raise TypeError(f"Settings 에 없는 필드: {', '.join(unknown)}")
    return Settings(**overrides)


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`Settings` 가 `.env` 와 환경변수 대신 코드의 기본값만 보게 한다.

    두 출처를 모두 막는다. `.env` 만 막으면 셸에 export 된 값이 그대로 새어 든다.

    디스크를 가리키는 두 설정만 예외로 **비워 둔 대신 임시 경로로 돌린다.** 두 값의 코드
    기본값(`data/memories`, `data/transcripts`)은 개발자가 서버를 한 번 띄우면 실제 대화가
    들어 있는 진짜 디렉터리라, 그대로 두면 테스트가 그 사람의 기억과 대화 원문을 읽고 쓴다.
    값을 검사하는 테스트는 여느 설정과 같이 `make_settings` 로 직접 넘긴다.
    """

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in _SETTINGS_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LONG_TERM_MEMORY_DIR", str(tmp_path / "memories"))
    monkeypatch.setenv("TRANSCRIPT_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setenv(
        "LEGACY_MEMORY_QUARANTINE_DIR", str(tmp_path / "memory-quarantine")
    )
    monkeypatch.setenv("ACCESS_LOG_PATH", str(tmp_path / "requests.log"))
    # `CompanionService.from_settings` 는 이제 `CredentialProtector` 를 만들 수 있어야
    # 한다(디바이스 인증, docs/temporary-scaffolds.md §2). 실제 배포 값이 아니라 테스트
    # 전용 문자열이다 — 인증 미설정 자체를 검증하는 테스트는 이 값을 명시적으로 비운다.
    monkeypatch.setenv("DEVICE_CREDENTIAL_PEPPER", "test-only-pepper-not-for-production")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'companion.db'}")


# 테스트가 연 DB 들. 아래 autouse 픽스처가 테스트마다 비운다.
_OPEN_DATABASES: list[Database] = []


async def make_database(settings: Settings) -> Database:
    """`settings.database_url` 을 가리키는 DB 를 만들고 스키마를 스캐폴딩한다.

    `alembic upgrade head` 와 동등한 결과를 `Base.metadata.create_all` 로 즉시 만든다 —
    마이그레이션 파일 자체의 정합성은 실제 `alembic` 실행으로 따로 검증하고, 테스트는
    빠른 스캐폴딩만 필요로 한다.

    만든 DB 는 `dispose_databases` 가 정리하므로 호출자가 닫을 필요가 없다.
    """

    database = Database(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    _OPEN_DATABASES.append(database)
    return database


@pytest.fixture(autouse=True)
async def dispose_databases() -> AsyncIterator[None]:
    """테스트가 연 엔진을 반드시 닫는다.

    닫지 않으면 aiosqlite 의 워커 스레드가 **이미 닫힌 이벤트 루프**에 결과를 돌려주려다
    실패하고, 테스트는 통과하는데 `PytestUnhandledThreadExceptionWarning` 만 쌓인다.
    `-W error` 를 켠 CI 에서는 그대로 실패가 된다. 호출자마다 try/finally 를 두는 대신
    여기서 한 번에 거둔다 — `make_database` 를 쓰는 모든 테스트에 자동으로 적용된다.
    """

    yield
    while _OPEN_DATABASES:
        await _OPEN_DATABASES.pop().dispose()


async def make_authenticated_device(
    database: Database,
    protector: CredentialProtector,
    *,
    profile_id: str | None = None,
    role: DeviceRole = DeviceRole.GAME_CLIENT,
) -> tuple[AuthenticatedDevice, str]:
    """DB 에 프로필+디바이스 행을 직접 만들고 (인증된 신원, 베어러 토큰) 을 돌려준다.

    실제 페어링 플로우(register-game → pairing-codes → pair)를 매 테스트 거치지 않기
    위한 지름길이다. 그 플로우 자체를 검증하는 테스트는 이 헬퍼 대신
    `app/routes/devices.py` 를 직접 호출한다.
    """

    profile_id = profile_id or f"profile-{uuid4()}"
    device_id = f"device-{uuid4()}"
    lookup_id = f"token-{uuid4()}"
    creation_request_id = f"req-{uuid4()}"
    now = datetime.now(UTC)
    token = protector.make_device_token(
        lookup_id=lookup_id,
        device_id=device_id,
        creation_request_id=creation_request_id,
    )
    async with database.session_factory() as session:
        session.add(ProfileModel(profile_id=profile_id, created_at=now))
        # 프로필 삽입을 먼저 플러시한다 — 그러지 않으면 유닛오브워크가 두 INSERT 를 한
        # 트랜잭션에서 등록 순서대로 내보내 devices.profile_id 의 FK 제약이 걸린다.
        await session.flush()
        session.add(
            DeviceModel(
                device_id=device_id,
                profile_id=profile_id,
                role=role.value,
                token_lookup_id=lookup_id,
                token_hash=protector.hash_value("device-token", token),
                creation_request_id=creation_request_id,
                game_registration_key=(
                    profile_id if role is DeviceRole.GAME_CLIENT else None
                ),
                created_at=now,
                last_used_at=None,
                revoked_at=None,
            )
        )
        await session.commit()
    return AuthenticatedDevice(profile_id=profile_id, device_id=device_id, role=role), token
