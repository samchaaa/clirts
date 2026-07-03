import asyncio
import os
import logging
import time

import websockets
from websockets.datastructures import Headers

from shared.messages import HEARTBEAT_TIMEOUT, MsgType, encode
from server.rooms import RoomManager
from server.protocol import handle_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("server")

room_manager = RoomManager()


async def handle_client(websocket):
    path = websocket.request.path if hasattr(websocket, 'request') else ""
    name_param = ""
    if "?" in path:
        qs = path.split("?", 1)[1]
        for part in qs.split("&"):
            if part.startswith("name="):
                name_param = part[5:][:16]
    player_name = name_param or "anon"

    log.info("Client connected: %s", player_name)

    player = None
    room = None

    try:
        async for raw in websocket:
            player, room = await handle_message(
                raw, player, room, room_manager, websocket, player_name,
            )
    except websockets.ConnectionClosed:
        pass
    finally:
        if room and player:
            room.remove_player(player.id)
            await room.broadcast({
                "type": MsgType.PLAYER_LEFT,
                "player_id": player.id,
            })
            if room.is_empty():
                room_manager.cleanup_empty()
        log.info("Client disconnected: %s", player_name)


async def health_check(path, headers):
    if path == "/health":
        return (200, Headers({"Content-Type": "text/plain"}), b"ok\n")


async def heartbeat_checker():
    while True:
        await asyncio.sleep(10)
        now = time.time()
        for room in list(room_manager.rooms.values()):
            stale = [
                p for p in room.players.values()
                if now - p.last_heartbeat > HEARTBEAT_TIMEOUT
            ]
            for p in stale:
                log.info("Heartbeat timeout: player %s in room %s", p.id, room.name)
                try:
                    await p.ws.close()
                except Exception:
                    pass
                room.remove_player(p.id)
            if room.is_empty():
                room_manager.cleanup_empty()


async def main():
    port = int(os.environ.get("PORT", 8080))
    log.info("Starting server on 0.0.0.0:%d", port)

    asyncio.create_task(heartbeat_checker())

    async with websockets.serve(
        handle_client,
        "0.0.0.0",
        port,
        process_request=health_check,
        ping_interval=20,
        ping_timeout=10,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
