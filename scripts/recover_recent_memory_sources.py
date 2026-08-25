"""Requeue recent player messages that were completed without memory output."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from app.db.connection import Database
from app.db.source_repository import SourceRepository
from app.settings import Settings


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--days", type=int, default=7, choices=range(1, 8))
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings.database_url)
    now = datetime.now(UTC)
    try:
        async with database.session_factory() as session:
            sequences = await SourceRepository(session).recover_unprocessed_messages(
                since=now - timedelta(days=args.days),
                now=now,
                apply=args.apply,
            )
            if args.apply:
                await session.commit()
            else:
                await session.rollback()
        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry-run",
                    "days": args.days,
                    "eligible_count": len(sequences),
                    "source_sequences": sequences,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        await database.dispose()


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
