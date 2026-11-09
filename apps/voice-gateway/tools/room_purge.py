"""Purge LiveKit rooms — Day 17 test tooling (not product code).

Browser acceptance tests need each scenario to start with a *fresh* room: the
agent worker auto-dispatches one job per ROOM CREATION, so a stale empty room
would make a new browser session join without any agent. Deleting the room
via the LiveKit RoomService API (server-assigned auth, no browser involvement)
forces room recreation — and therefore agent dispatch — on the next join.

Usage (from `apps/voice-gateway`):

    uv run python -m tools.room_purge --room emma-voice

Exits 0 when the room no longer exists.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

from app.core.config import get_settings


async def _purge(room_name: str) -> None:
    settings = get_settings()
    if not settings.LIVEKIT_URL or not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        print("purge: LIVEKIT_* credentials missing", file=sys.stderr)
        raise SystemExit(2)

    import livekit.api as livekit_api

    client = livekit_api.LiveKitAPI(
        url=settings.LIVEKIT_URL,
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )
    try:
        rooms = await client.room.list_rooms(
            livekit_api.room_service.ListRoomsRequest(names=[room_name])
        )
        if not rooms.rooms:
            print(f"purge: room {room_name!r} does not exist", flush=True)
            return
        await client.room.delete_room(livekit_api.room_service.DeleteRoomRequest(room=room_name))
        # Confirm disappearance (deletion is asynchronous on the server).
        for _ in range(20):
            rooms = await client.room.list_rooms(
                livekit_api.room_service.ListRoomsRequest(names=[room_name])
            )
            if not rooms.rooms:
                print(f"purge: room {room_name!r} deleted", flush=True)
                return
            await asyncio.sleep(0.25)
        print(f"purge: room {room_name!r} still listed after deletion", file=sys.stderr)
        raise SystemExit(1)
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete LiveKit rooms for test isolation")
    parser.add_argument("--room", required=True)
    args = parser.parse_args()
    asyncio.run(_purge(args.room))


if __name__ == "__main__":
    main()
