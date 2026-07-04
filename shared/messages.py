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

# hp/damage/attack range/speed/cost per unit type; auto_range > 0 means the
# unit fires at the nearest enemy in radius without needing an attack order;
# reload is the number of ticks between shots
UNIT_STATS = {
    "worker": {"hp": 100, "damage": 10, "range": 2.0, "speed": 1.0,
               "cost": 100, "auto_range": 0.0, "reload": 1},
    "tank":   {"hp": 300, "damage": 30, "range": 1.5, "speed": 0.4,
               "cost": 250, "auto_range": 0.0, "reload": 1},
    "range":  {"hp": 80, "damage": 20, "range": 5.0, "speed": 1.0,
               "cost": 150, "auto_range": 5.0, "reload": 3},
    "fort":   {"hp": 500, "damage": 25, "range": 6.0, "speed": 0.0,
               "cost": 400, "auto_range": 6.0, "reload": 5,
               "build_time": 60},
    "wall":   {"hp": 200, "damage": 0, "range": 0.0, "speed": 0.0,
               "cost": 35, "auto_range": 0.0, "reload": 1,
               "build_time": 10},
}
BUILDINGS = ("fort", "wall")  # placed as a site; a worker walks there to build
# tank/range must be built within this distance of a friendly fort
FORT_BUILD_RADIUS = 4.0
# how close a worker must be to a site to finish construction
BUILD_RANGE = 1.5
# units cannot move closer than this to a wall
WALL_RADIUS = 0.9


def encode(msg: dict) -> str:
    return json.dumps(msg)


def decode(data: str) -> dict | None:
    try:
        if len(data) > MAX_MESSAGE_SIZE:
            return None
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None
