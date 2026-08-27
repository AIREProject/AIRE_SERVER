"""Classify one configured-provider memory fixture without touching the database."""

from __future__ import annotations

import asyncio
import json

from app.brain.llm import build_llm_provider
from app.settings import Settings


async def main() -> int:
    selected = build_llm_provider(Settings())
    try:
        if selected.name == "mock":
            print(json.dumps({"status": "blocked", "reason": "real_provider_not_selected"}))
            return 2
        result = await selected.provider.classify_memory("나는 비 오는 날을 좋아해")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "provider": selected.name,
                    "model": selected.model_version,
                    "decision": result.decision,
                    "importance": result.importance,
                    "confidence": result.confidence,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "provider": selected.name, "error": type(error).__name__}
            )
        )
        return 1
    finally:
        await selected.provider.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
