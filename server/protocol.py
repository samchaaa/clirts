import time

from shared.messages import (
    MsgType, MAX_COMMANDS_PER_TICK, encode, decode,
)
from server.rooms import RoomManager, Player, GameRoom


async def handle_message(
    raw: str,
    player: Player | None,
    room: GameRoom | None,
    room_manager: RoomManager,
    websocket,
    player_name: str,
) -> tuple[Player | None, GameRoom | None]:
    msg = decode(raw)
    if msg is None:
        await websocket.send(encode({"type": MsgType.ERROR, "message": "invalid message"}))
        return player, room

    msg_type = msg.get("type")

    if msg_type == MsgType.PING:
        if player:
            player.last_heartbeat = time.time()
        await websocket.send(encode({
            "type": MsgType.PONG,
            "time": msg.get("time", 0),
        }))
        return player, room

    if msg_type == MsgType.LIST_ROOMS:
        rooms = room_manager.list_rooms()
        await websocket.send(encode({
            "type": MsgType.ROOMS,
            "rooms": rooms,
        }))
        return player, room

    if msg_type == MsgType.CREATE_ROOM:
        room_name = msg.get("room", "").strip()
        if not room_name or len(room_name) > 32:
            await websocket.send(encode({
                "type": MsgType.ERROR,
                "message": "invalid room name",
            }))
            return player, room

        new_room = room_manager.create_room(room_name)
        if new_room is None:
            await websocket.send(encode({
                "type": MsgType.ERROR,
                "message": "room already exists or max rooms reached",
            }))
            return player, room

        new_player = new_room.add_player(player_name, websocket)
        new_room.start_loop()
        await websocket.send(encode({
            "type": MsgType.ROOM_CREATED,
            "room": room_name,
        }))
        await websocket.send(encode({
            "type": MsgType.ROOM_JOINED,
            "room": room_name,
            "player_id": new_player.id,
            "terrain": new_room.state.terrain_msg(),
        }))
        return new_player, new_room

    if msg_type == MsgType.JOIN_ROOM:
        room_name = msg.get("room", "").strip()
        target_room = room_manager.get_room(room_name)
        if target_room is None:
            await websocket.send(encode({
                "type": MsgType.ERROR,
                "message": "room not found",
            }))
            return player, room

        new_player = target_room.add_player(player_name, websocket)
        if new_player is None:
            await websocket.send(encode({
                "type": MsgType.ERROR,
                "message": "room is full",
            }))
            return player, room

        await websocket.send(encode({
            "type": MsgType.ROOM_JOINED,
            "room": room_name,
            "player_id": new_player.id,
            "terrain": target_room.state.terrain_msg(),
        }))
        return new_player, target_room

    if msg_type == MsgType.COMMAND:
        if player is None or room is None:
            await websocket.send(encode({
                "type": MsgType.ERROR,
                "message": "join a room first",
            }))
            return player, room

        player.commands_this_tick += 1
        if player.commands_this_tick > MAX_COMMANDS_PER_TICK:
            await websocket.send(encode({
                "type": MsgType.ERROR,
                "message": "too many commands",
            }))
            return player, room

        player.last_heartbeat = time.time()
        ok = room.state.apply_command(player.id, msg)
        if not ok:
            await websocket.send(encode({
                "type": MsgType.ERROR,
                "message": f"{msg.get('command', 'command')} failed"
                           " (cost? fort nearby? valid target?)",
            }))
        return player, room

    await websocket.send(encode({
        "type": MsgType.ERROR,
        "message": f"unknown message type: {msg_type}",
    }))
    return player, room
