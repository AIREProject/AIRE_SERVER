"""Explicit, idempotent migration of legacy JSONL player messages.

The importer never creates memories. It creates canonical LegacyUnknown Message
sources; the regular leased worker decides whether each source is important enough.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.db.connection import Database
from app.db.models import ConversationModel, MessageModel, SaveSlotModel
from app.db.source_repository import SOURCE_MESSAGE, SourceRepository
from app.settings import Settings

PROFILE_ID = "AIRE_OPEN"
SAVE_SLOT_ID = "demo-slot-1"
COMPANION_ID = "mako"


@dataclass(slots=True)
class ImportReport:
    filename: str
    sha256: str
    mode: str
    status: str
    queued_messages: int
    skipped_rows: int
    invalid_rows: int
    cursor: int
    quarantine_path: str | None
    quarantine_delete_after: str | None
    error: str | None


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--source-dir", type=Path, default=Path("data/transcripts"))
    parser.add_argument(
        "--quarantine-dir", type=Path, default=Path("data/transcript_quarantine")
    )
    return parser.parse_args()


def _read(path: Path) -> tuple[list[dict[str, object]], int, int]:
    rows: list[dict[str, object]] = []
    skipped = 0
    invalid = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                invalid += 1
                continue
            if not isinstance(item, dict):
                invalid += 1
                continue
            if item.get("speaker") != "player":
                skipped += 1
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                invalid += 1
                continue
            raw_at = item.get("at")
            try:
                occurred_at = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
            except ValueError:
                invalid += 1
                continue
            if occurred_at.tzinfo is None:
                invalid += 1
                continue
            item["_line"] = line_number
            item["_at"] = occurred_at
            rows.append(item)
    return rows, skipped, invalid


async def _apply_file(
    database: Database, path: Path, digest: str, rows: list[dict[str, object]], settings: Settings
) -> int:
    async with database.session_factory() as session:
        slot = await session.scalar(
            select(SaveSlotModel).where(
                SaveSlotModel.profile_id == PROFILE_ID,
                SaveSlotModel.save_slot_id == SAVE_SLOT_ID,
            )
        )
        if slot is None:
            raise RuntimeError("AIRE_OPEN / demo-slot-1 must exist before legacy import")
        conversation_row_id = str(uuid5(NAMESPACE_URL, f"aire:legacy:{digest}:conversation"))
        conversation = await session.get(ConversationModel, conversation_row_id)
        now = datetime.now(UTC)
        if conversation is None:
            conversation = ConversationModel(
                row_id=conversation_row_id,
                conversation_id=f"legacy-{digest[:32]}",
                profile_id=PROFILE_ID,
                save_slot_row_id=slot.row_id,
                companion_id=COMPANION_ID,
                session_id=f"legacy-{digest[:32]}",
                surface="legacy",
                created_at=now,
            )
            session.add(conversation)
            await session.flush()
        queued = 0
        repository = SourceRepository(session)
        for sequence, item in enumerate(rows, start=1):
            line_number = int(item["_line"])
            text = str(item["text"]).strip()
            row_id = str(uuid5(NAMESPACE_URL, f"aire:legacy:{digest}:{line_number}"))
            if await session.get(MessageModel, row_id) is not None:
                continue
            created_at = item["_at"]
            if not isinstance(created_at, datetime):
                raise RuntimeError("validated legacy timestamp is unavailable")
            message = MessageModel(
                row_id=row_id,
                message_id=f"legacy-{digest[:20]}-{line_number}",
                conversation_row_id=conversation_row_id,
                profile_id=PROFILE_ID,
                save_slot_row_id=slot.row_id,
                companion_id=COMPANION_ID,
                request_id=f"legacy-{digest[:20]}-{line_number}",
                sequence=sequence,
                speaker="player",
                source_mode="LegacyUnknown",
                content=text,
                content_digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                time_context=None,
                storage_class="Transient",
                retention_reason="LegacyImportPendingClassification",
                expires_at=now + timedelta(days=settings.user_message_retention_days),
                audit_expires_at=now + timedelta(days=settings.audit_retention_days),
                content_deleted_at=None,
                created_at=created_at,
                delivered_at=created_at,
            )
            session.add(message)
            await session.flush()
            await repository.enqueue(SOURCE_MESSAGE, row_id, now=now, commit=False)
            queued += 1
        await session.commit()
        return queued


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings.database_url)
    mode = "apply" if args.apply else "dry-run"
    reports: list[ImportReport] = []
    failed = False
    try:
        for path in sorted(args.source_dir.glob("*.jsonl")):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows, skipped, invalid = _read(path)
            queued = 0
            quarantine_path: Path | None = None
            delete_after: datetime | None = None
            error_message: str | None = None
            if args.apply:
                try:
                    queued = await _apply_file(database, path, digest, rows, settings)
                    args.quarantine_dir.mkdir(parents=True, exist_ok=True)
                    quarantine_path = args.quarantine_dir / f"{digest}.jsonl"
                    if not quarantine_path.exists():
                        shutil.move(path, quarantine_path)
                    elif path.exists():
                        path.unlink()
                    delete_after = datetime.now(UTC) + timedelta(days=30)
                except Exception as error:
                    failed = True
                    error_message = type(error).__name__
            reports.append(
                ImportReport(
                    filename=path.name,
                    sha256=digest,
                    mode=mode,
                    status=(
                        "error"
                        if error_message is not None
                        else "quarantined"
                        if args.apply
                        else "validated"
                    ),
                    queued_messages=queued if args.apply else len(rows),
                    skipped_rows=skipped,
                    invalid_rows=invalid,
                    cursor=max((int(item["_line"]) for item in rows), default=0),
                    quarantine_path=None if quarantine_path is None else str(quarantine_path),
                    quarantine_delete_after=(
                        None if delete_after is None else delete_after.isoformat()
                    ),
                    error=error_message,
                )
            )
        report_path = args.quarantine_dir / f"import-report-{mode}.json"
        if args.apply:
            args.quarantine_dir.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps([asdict(report) for report in reports], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(json.dumps([asdict(report) for report in reports], ensure_ascii=False, indent=2))
        return 1 if failed else 0
    finally:
        await database.dispose()


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
