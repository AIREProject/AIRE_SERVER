"""Run the release-gate memory classifier matrix against the configured real LLM."""

from __future__ import annotations

import asyncio
import json

from app.brain.llm import build_llm_provider
from app.settings import Settings

_CASES = (
    ("preference", "나는 비 오는 날을 좋아해", frozenset({"Preference"})),
    ("recipe", "돌도끼 레시피 알려줘", frozenset({"Reject"})),
    ("command", "나무 열 개 캐줘", frozenset({"Reject"})),
    ("current_state", "지금 동굴 안에 있어", frozenset({"Reject"})),
    ("greeting", "안녕", frozenset({"Reject"})),
)


async def main() -> int:
    selected = build_llm_provider(Settings())
    if selected.name == "mock":
        print(json.dumps({"status": "blocked", "reason": "real_provider_not_selected"}))
        await selected.provider.aclose()
        return 2
    failures: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    try:
        try:
            for case_name, text, allowed in _CASES:
                decisions: list[str] = []
                for _ in range(3):
                    classification = await selected.provider.classify_memory(text)
                    decisions.append(classification.decision)
                passed = all(decision in allowed for decision in decisions)
                results.append({"case": case_name, "decisions": decisions, "passed": passed})
                if not passed:
                    failures.append({"case": case_name, "decisions": decisions})
        except Exception as error:
            print(
                json.dumps(
                    {
                        "status": "blocked",
                        "provider": selected.name,
                        "model": selected.model_version,
                        "reason": type(error).__name__,
                    }
                )
            )
            return 2
    finally:
        await selected.provider.aclose()
    print(
        json.dumps(
            {
                "status": "passed" if not failures else "failed",
                "provider": selected.name,
                "model": selected.model_version,
                "runs_per_case": 3,
                "results": results,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
