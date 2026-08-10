"""GameClient 제한을 서버당 1개 → 프로필당 1개로 바꾼다.

`game_registration_key` 를 리터럴 "single-game-client" 대신 각 GameClient 의
`profile_id` 로 채워, 기존 단일 컬럼 유니크 제약이 "프로필당 GameClient 1개"를
강제하게 한다. 컬럼은 profile_id(약 44자)를 담도록 32→128 로 넓힌다.

downgrade 는 사실상 일방향이다: 다중 프로필이 이미 존재하면 여러 GameClient 를 같은
sentinel 로 되돌릴 때 유니크 위반이 난다. dev 스캐폴드라 이를 허용한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite 는 alter_column / 제약 변경에 batch 모드가 필요하다(이 레포 최초의 alter).
    with op.batch_alter_table("devices") as batch:
        batch.alter_column(
            "game_registration_key",
            existing_type=sa.String(length=32),
            type_=sa.String(length=128),
            existing_nullable=True,
        )
        batch.drop_constraint("uq_devices_single_game_registration", type_="unique")
        batch.create_unique_constraint(
            "uq_devices_game_registration_per_profile", ["game_registration_key"]
        )
    # 컬럼 확장 후 백필: 기존 GameClient(현재 "single-game-client")를 각자 profile_id 로
    # 재바인딩한다. 오늘 GameClient 는 최대 1개이므로 충돌하지 않는다.
    op.execute(
        "UPDATE devices SET game_registration_key = profile_id WHERE role = 'GameClient'"
    )


def downgrade() -> None:
    # 좁히기 전에 값을 먼저 되돌린다(profile_id 가 32자를 넘기 때문).
    op.execute(
        "UPDATE devices SET game_registration_key = 'single-game-client' "
        "WHERE role = 'GameClient'"
    )
    with op.batch_alter_table("devices") as batch:
        batch.drop_constraint(
            "uq_devices_game_registration_per_profile", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_devices_single_game_registration", ["game_registration_key"]
        )
        batch.alter_column(
            "game_registration_key",
            existing_type=sa.String(length=128),
            type_=sa.String(length=32),
            existing_nullable=True,
        )
