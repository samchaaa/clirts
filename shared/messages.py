import json
from enum import Enum


class MsgType(str, Enum):
    # Client -> Server
    CREATE_ROOM = "create_room"
    JOIN_ROOM = "join_room"
    LIST_ROOMS = "list_rooms"
    COMMAND = "command"
    PING = "ping"

    # Server -> Client
    ROOM_CREATED = "room_created"
    ROOM_JOINED = "room_joined"
    ROOMS = "rooms"
    SNAPSHOT = "snapshot"
    PONG = "pong"
    ERROR = "error"
    PLAYER_LEFT = "player_left"
    GAME_START = "game_start"


class Command(str, Enum):
    MOVE = "move"
    ATTACK = "attack"
    GATHER = "gather"
    BUILD = "build"


MAP_WIDTH = 60
MAP_HEIGHT = 20
TICK_RATE = 10
UNIT_SPEED = 1.0
UNIT_HP = 100
UNIT_DAMAGE = 10
UNIT_ATTACK_RANGE = 2.0
GATHER_RANGE = 2.0
GATHER_RATE = 5
STARTING_RESOURCES = 500
STARTING_UNITS = 5
MAX_PLAYERS_PER_ROOM = 4
MAX_ROOMS = 20
MAX_COMMANDS_PER_TICK = 10
MAX_MESSAGE_SIZE = 4096
HEARTBEAT_TIMEOUT = 30
WORKER_COST = 100


def encode(msg: dict) -> str:
    return json.dumps(msg)


def decode(data: str) -> dict | None:
    try:
        if len(data) > MAX_MESSAGE_SIZE:
            return None
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None
