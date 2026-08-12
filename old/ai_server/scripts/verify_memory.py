"""장기기억이 실제 대화에서 증류되어 다음 세션에 회수되는지 확인한다."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import uuid

import httpx
from dotenv import dotenv_values

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

BASE = "http://127.0.0.1:8013"
DB = "data/spec-memory/companion.db"
SLOT = "slot-mem"


async def main() -> None:
    env = dotenv_values(".env")
    boot = env.get("DEV_GAME_DEVICE_TOKEN") or os.environ.get("DEV_GAME_DEVICE_TOKEN")
    if not boot:
        print("DEV_GAME_DEVICE_TOKEN 없음")
        return

    async with httpx.AsyncClient(base_url=BASE, timeout=120.0) as client:
        r = await client.post(
            "/api/v1/devices/pairing-codes",
            headers={"Authorization": f"Bearer {boot}"},
            json={"request_id": str(uuid.uuid4())},
        )
        if r.status_code != 200:
            # 부트스트랩 토큰은 register-game 전용이므로 기존 게임 디바이스를 다시 만든다.
            r = await client.post(
                "/api/v1/devices/register-game",
                headers={"Authorization": f"Bearer {boot}"},
                json={"request_id": str(uuid.uuid4())},
            )
            print("register-game:", r.status_code)
        payload = r.json()
        token = payload.get("device_token")
        profile_id = payload.get("profile_id")
        if token is None:
            print("게임 디바이스가 이미 있어 새 토큰을 못 받음:", r.text[:160])
            return
        hdr = {"Authorization": f"Bearer {token}"}

        def body(msg: str, session: str) -> dict[str, object]:
            return {
                "schema_version": 1,
                "request_id": str(uuid.uuid4()),
                "session_id": session,
                "save_slot_id": SLOT,
                "companion_id": "mako",
                "profile_id": profile_id,
                "user_message": msg,
                "surface": "game",
                "allowed_commands": ["Command.Follow"],
            }

        # 취향이 드러나는 대화
        for line in (
            "난 밤에 돌아다니는 게 정말 싫어.",
            "어두운 데서는 손이 떨려서 아무것도 못 하겠어.",
            "그래서 해 지기 전에 항상 야영지로 돌아가려고 해.",
        ):
            r = await client.post("/api/v1/chat", headers=hdr, json=body(line, "sess-a"))
            print(f"[턴] {line}\n  -> {r.json()['display_text']}")

    # 배경 증류 루프가 돌 시간을 준다 (LONG_TERM_QUIET_SECONDS=90, TICK=15)
    print("\n증류 대기 중 (최대 150초)...")
    for _ in range(30):
        await asyncio.sleep(5)
        rows = sqlite3.connect(DB).execute(
            "select kind, importance, embedding_model, length(coalesce(embedding,'')), text"
            " from episodic_memories"
        ).fetchall()
        if rows:
            break

    print(f"\n장기기억 행 {len(rows)}개")
    for kind, importance, model, vlen, text in rows:
        print(f"  kind={kind} importance={importance} model={model} vec_len={vlen}")
        print(f"    {text}")

    if not rows:
        print("기억이 아직 없다 — 대기 시간이 부족했거나 추출이 비었다.")
        return

    # 새 세션에서 단어가 겹치지 않는 질문
    async with httpx.AsyncClient(base_url=BASE, timeout=120.0) as client:
        r = await client.post(
            "/api/v1/chat",
            headers=hdr,
            json=body("나 지금 좀 무서운데 같이 있어 줄래?", "sess-b"),
        )
        print(f"\n[새 세션] -> {r.json()['display_text']}")

    recalled = sqlite3.connect(DB).execute(
        "select recall_count from episodic_memories where recall_count > 0"
    ).fetchall()
    print(f"회수된 기억 수: {len(recalled)}")


asyncio.run(main())
