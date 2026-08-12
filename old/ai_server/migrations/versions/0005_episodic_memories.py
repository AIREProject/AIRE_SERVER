"""장기기억 테이블을 만들고 기존 JSON 기억을 가져온다."""

from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

from app.brain.memory import MAX_IMPORTANCE, read_memory_file
from app.settings import Settings

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    episodic_memories = op.create_table(
        "episodic_memories",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("player_key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recalled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recall_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("player_key", "text", name="uq_episodic_memories_player_text"),
    )
    op.create_index(
        "ix_episodic_memories_player_key", "episodic_memories", ["player_key"]
    )

    memory_directory = Settings().long_term_memory_dir
    if not memory_directory.exists():
        return
    connection = op.get_bind()
    for path in sorted(memory_directory.glob("*.json")):
        _import_file(connection, episodic_memories, path)


def _import_file(connection: sa.Connection, table: sa.Table, path: Path) -> None:
    """파일 하나가 깨져도 스키마 마이그레이션은 완료한다. 원본은 지우지 않는다."""

    try:
        memories = read_memory_file(path)
        rows = [
            {
                "row_id": str(uuid4()),
                "player_key": path.stem,
                "kind": memory.kind,
                "text": memory.text,
                # 예전 1~3 척도를 새 1~10 척도로 옮긴다.
                "importance": min(memory.importance * 2, MAX_IMPORTANCE),
                "source_key": memory.source_key,
                "created_at": memory.created_at,
                "recalled_at": memory.recalled_at,
                "recall_count": memory.recall_count,
                "embedding": None
                if memory.embedding is None
                else list(memory.embedding),
                "embedding_model": memory.embedding_model,
            }
            for memory in memories
        ]
        if rows:
            connection.execute(table.insert().prefix_with("OR IGNORE"), rows)
    except Exception:
        return


def downgrade() -> None:
    op.drop_index("ix_episodic_memories_player_key", table_name="episodic_memories")
    op.drop_table("episodic_memories")
