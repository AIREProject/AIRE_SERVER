"""명세 항목이 실제 서버에서 동작하는지 확인하는 일회성 점검 스크립트.

토큰과 페어링 코드는 절대 출력하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

import httpx
import websockets
from dotenv import dotenv_values

BASE = "http://127.0.0.1:8012"
PROFILE = f"spec-{uuid.uuid4().hex[:8]}"
SLOT = "slot-1"


sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def ok(label: str, passed: bool, detail: str = "") -> bool:
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {label}{(' | ' + detail) if detail else ''}")
    return passed


async def main() -> None:
    env = dotenv_values(".env")
    boot = env.get("DEV_GAME_DEVICE_TOKEN") or os.environ.get("DEV_GAME_DEVICE_TOKEN")
    if not boot:
        print("DEV_GAME_DEVICE_TOKEN 이 없어 인증 흐름을 건너뛴다.")
        return

    results: list[bool] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as client:
        # 1) 게임 클라이언트 등록
        r = await client.post(
            "/api/v1/devices/register-game",
            headers={"Authorization": f"Bearer {boot}"},
            json={"request_id": str(uuid.uuid4())},
        )
        results.append(ok("게임 디바이스 등록", r.status_code == 200, r.text[:200]))
        payload = r.json()
        profile_id = payload["profile_id"]
        game_token = payload["device_token"]
        game_device_id = payload["device"]["device_id"]
        results.append(ok("등록 역할 GameClient", payload["device"]["role"] == "GameClient"))
        game_hdr = {"Authorization": f"Bearer {game_token}"}

        # 2) 페어링 코드 발급 -> 모바일 페어링
        r = await client.post(
            "/api/v1/devices/pairing-codes",
            headers=game_hdr,
            json={"request_id": str(uuid.uuid4())},
        )
        results.append(ok("페어링 코드 발급", r.status_code == 200, f"{r.status_code}"))
        code = r.json()["pairing_code"]

        r = await client.post(
            "/api/v1/devices/pair",
            json={"request_id": str(uuid.uuid4()), "pairing_code": code},
        )
        results.append(ok("모바일 페어링", r.status_code == 200, r.text[:200]))
        results.append(
            ok("페어링 결과가 같은 프로필", r.json()["profile_id"] == profile_id)
        )
        results.append(ok("페어링 역할 WebClient", r.json()["device"]["role"] == "WebClient"))
        web_token = r.json()["device_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}"}

        # 3) 인증 없이 chat -> 401
        r = await client.post("/api/v1/chat", json={})
        results.append(ok("무인증 chat 거부", r.status_code == 401, f"{r.status_code}"))

        def body(msg: str, surface: str, **extra: object) -> dict[str, object]:
            payload: dict[str, object] = {
                "schema_version": 1,
                "request_id": str(uuid.uuid4()),
                "session_id": "sess-1",
                "save_slot_id": SLOT,
                "companion_id": "mako",
                "profile_id": profile_id,
                "message_id": f"msg-{uuid.uuid4().hex[:6]}",
                "user_message": msg,
                "surface": surface,
                "time_context": {
                    "source": "GameWorld",
                    "day": 3,
                    "hour": 23,
                    "period": "Night",
                },
                "recent_event_ids": [],
                "game_context": (
                    {
                        "schema_version": 1,
                        "location_id": "forest_camp",
                        "threat": {
                            "present": False,
                            "count": 0,
                            "nearest_kind": None,
                        },
                        "nearby_resources": [],
                        "available_workstations": [],
                        "current_work": None,
                        "inventories": [],
                    }
                    if surface == "game"
                    else None
                ),
                "allowed_commands": ["Command.Follow", "Command.HoldPosition"],
            }
            payload.update(extra)
            return payload

        # 4) 명세 Request JSON 전체 필드 수용
        r = await client.post(
            "/api/v1/chat", headers=game_hdr, json=body("따라와", "game")
        )
        results.append(ok("명세 Request JSON 수용", r.status_code == 200, f"{r.status_code}"))
        data = r.json()
        results.append(
            ok(
                "명령 후보 생성(Command.Follow)",
                any(c["type"] == "Command.Follow" for c in data["command_candidates"]),
                json.dumps(
                    [c["type"] for c in data["command_candidates"]], ensure_ascii=False
                ),
            )
        )
        game_line = data["display_text"]

        # 5) surface 별 톤 차이
        r = await client.post(
            "/api/v1/chat", headers=web_hdr, json=body("따라와", "mobile")
        )
        mobile_line = r.json()["display_text"]
        results.append(
            ok(
                "surface 별 응답 구분",
                r.status_code == 200 and game_line != mobile_line,
                f"game={game_line!r} / mobile={mobile_line!r}",
            )
        )

        # 6) 레시피 사실
        r = await client.post(
            "/api/v1/chat", headers=game_hdr, json=body("강철괴 어떻게 만들어?", "game")
        )
        text = r.json()["display_text"]
        results.append(
            ok("레시피 응답", "석탄" in text or "철괴" in text or "용광로" in text, text)
        )

        # 7) 적 약점 사실
        r = await client.post(
            "/api/v1/chat", headers=game_hdr, json=body("외상성 골리앗 약점이 뭐야?", "game")
        )
        text = r.json()["display_text"]
        results.append(ok("적 약점 응답", "코어" in text or "폭발" in text, text))

        # 8) 알 수 없는 필드 거부 (400 + 균일 오류 봉투)
        r = await client.post(
            "/api/v1/chat", headers=game_hdr, json=body("안녕", "game", unknown_field="x")
        )
        results.append(
            ok(
                "미지 필드 거부(400 InvalidRequest)",
                r.status_code == 400 and r.json()["error"]["code"] == "InvalidRequest",
                f"{r.status_code} {r.text[:120]}",
            )
        )

        # 8-b) 신원 위조 시도 거부
        r = await client.post(
            "/api/v1/chat",
            headers=game_hdr,
            json={**body("안녕", "game"), "profile_id": "profile-someone-else"},
        )
        results.append(ok("타인 프로필 주장 거부(403)", r.status_code == 403, f"{r.status_code}"))

        # 8-c) WS 채널 — HTTP 와 같은 봉투 스키마, 인증은 프레임마다, 오류가 나도 연결 유지
        ws_uri = BASE.replace("http://", "ws://", 1) + "/api/v1/chat"
        async with websockets.connect(ws_uri) as ws:
            await ws.send(
                json.dumps({"type": "chat", "token": game_token, "payload": body("따라와", "game")})
            )
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            results.append(
                ok(
                    "WS 명령 후보 생성(Command.Follow)",
                    msg.get("type") == "chat_response"
                    and any(
                        c["type"] == "Command.Follow"
                        for c in msg["payload"]["command_candidates"]
                    ),
                    json.dumps(msg, ensure_ascii=False)[:200],
                )
            )

            await ws.send(
                json.dumps(
                    {
                        "type": "chat",
                        "token": "token-does-not-exist.invalid-secret",
                        "payload": body("안녕", "game"),
                    }
                )
            )
            err = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            results.append(
                ok(
                    "WS 무효 토큰 거부(UnauthorizedDevice)",
                    err.get("type") == "error"
                    and err["payload"]["error"]["code"] == "UnauthorizedDevice",
                    json.dumps(err, ensure_ascii=False)[:200],
                )
            )

            await ws.send(json.dumps({"type": "not-chat", "payload": {}}))
            malformed = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            results.append(
                ok(
                    "WS 알 수 없는 type 거부(InvalidRequest)",
                    malformed.get("type") == "error"
                    and malformed["payload"]["error"]["code"] == "InvalidRequest",
                    json.dumps(malformed, ensure_ascii=False)[:200],
                )
            )

            await ws.send(
                json.dumps({"type": "chat", "token": game_token, "payload": body("고마워", "game")})
            )
            recovered = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            results.append(
                ok("WS 오류 후에도 연결 유지", recovered.get("type") == "chat_response")
            )

            await ws.send(
                json.dumps(
                    {
                        "type": "situation",
                        "token": game_token,
                        "payload": {
                            "request_id": str(uuid.uuid4()),
                            "session_id": "sess-1",
                            "save_slot_id": SLOT,
                            "companion_id": "mako",
                            "surface": "game",
                            "situation": ["적이 나타났다"],
                        },
                    }
                )
            )
            situation_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            results.append(
                ok(
                    "WS 상황 이벤트 응답",
                    situation_msg.get("type") == "situation_response"
                    and bool(situation_msg["payload"].get("display_text")),
                    json.dumps(situation_msg, ensure_ascii=False)[:200],
                )
            )

        # 8-d) 상황 이벤트 — 라우팅 없이 대사만
        r = await client.post(
            "/api/v1/situations",
            headers=game_hdr,
            json={
                "request_id": str(uuid.uuid4()),
                "session_id": "sess-1",
                "save_slot_id": SLOT,
                "companion_id": "mako",
                "surface": "game",
                "situation": ["플레이어 체력이 20% 남았다", "주변에 적 2마리가 있다"],
            },
        )
        results.append(ok("상황 이벤트 200", r.status_code == 200, f"{r.status_code}"))
        results.append(
            ok("상황 이벤트 대사 비어있지 않음", bool(r.json().get("display_text")), r.text[:200])
        )

        # 9) Offline_Task — 모바일이 생성
        task_req = str(uuid.uuid4())
        r = await client.post(
            "/api/v1/tasks",
            headers={**web_hdr, "X-Request-ID": task_req},
            json={
                "request_id": task_req,
                "save_slot_id": SLOT,
                "task_type": "Gathering",
                "item_id": "IronOre",
            },
        )
        results.append(ok("모바일 작업 생성", r.status_code in (200, 201), f"{r.status_code}"))
        task_id = r.json()["task"]["task_id"]
        results.append(ok("생성 직후 Pending", r.json()["task"]["status"] == "Pending"))

        # 10) 게임 클라이언트가 상태를 전이
        transitions = (
            ("start", "InProgress"),
            ("complete", "Completed"),
            ("claim", "Claimed"),
        )
        for step, expect in transitions:
            r = await client.post(f"/api/v1/tasks/{task_id}/{step}", headers=game_hdr)
            actual = r.json().get("task", {}).get("status")
            results.append(ok(f"게임 전이 {step} -> {expect}", actual == expect))

        # 11) 순서 위반 거부
        r = await client.post(f"/api/v1/tasks/{task_id}/start", headers=game_hdr)
        results.append(ok("잘못된 전이 거부(409)", r.status_code == 409, f"{r.status_code}"))

        # 12) 모바일이 전이 시도 -> 403
        r = await client.post(f"/api/v1/tasks/{task_id}/start", headers=web_hdr)
        results.append(ok("모바일의 전이 거부(403)", r.status_code == 403, f"{r.status_code}"))

        # 13) 목록 조회
        r = await client.get(
            "/api/v1/tasks", headers=game_hdr, params={"save_slot_id": SLOT}
        )
        results.append(
            ok("작업 목록 조회", r.status_code == 200 and len(r.json()["tasks"]) >= 1)
        )

        # 14) 디바이스 목록 / 상한
        r = await client.get("/api/v1/devices", headers=game_hdr)
        results.append(
            ok("디바이스 목록", r.status_code == 200 and len(r.json()["devices"]) == 2)
        )

        # 15) 게임이 모바일 디바이스를 해지 (게임 디바이스 자신은 해지 대상이 아니다)
        r = await client.delete(f"/api/v1/devices/{game_device_id}", headers=game_hdr)
        results.append(
            ok("게임 디바이스 해지는 거부(403)", r.status_code == 403, f"{r.status_code}")
        )
        r = await client.get("/api/v1/devices/me", headers=web_hdr)
        web_device_id = r.json()["device_id"]
        r = await client.delete(f"/api/v1/devices/{web_device_id}", headers=game_hdr)
        results.append(ok("모바일 디바이스 해지", r.status_code == 200, f"{r.status_code}"))
        r = await client.post("/api/v1/chat", headers=web_hdr, json=body("안녕", "mobile"))
        results.append(ok("해지된 토큰 거부(401)", r.status_code == 401, f"{r.status_code}"))

    print(f"\n{sum(results)}/{len(results)} 통과")


asyncio.run(main())
