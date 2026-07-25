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
    TERRAIN = "terrain"
    PONG = "pong"
    ERROR = "error"
    PLAYER_LEFT = "player_left"
    GAME_START = "game_start"


class Command(str, Enum):
    MOVE = "move"
    ATTACK = "attack"
    GATHER = "gather"
    BUILD = "build"
    DIG = "dig"
    DIG_DOWN = "dig_down"
    DIG_UP = "dig_up"
    FARM = "farm"
    LASER = "laser"


MAP_WIDTH = 120
MAP_HEIGHT = 40
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
               "cost": 150, "auto_range": 5.0, "reload": 3.75},
    "fort":   {"hp": 500, "damage": 25, "range": 6.0, "speed": 0.0,
               "cost": 400, "auto_range": 6.0, "reload": 5,
               "build_time": 60},
    "wall":   {"hp": 200, "damage": 0, "range": 0.0, "speed": 0.0,
               "cost": 35, "auto_range": 0.0, "reload": 1,
               "build_time": 10},
    "farm":   {"hp": 100, "damage": 0, "range": 0.0, "speed": 0.0,
               "cost": 100, "auto_range": 0.0, "reload": 1,
               "build_time": 50},
    "laser":  {"hp": 300, "damage": 0, "range": 0.0, "speed": 0.0,
               "cost": 500, "auto_range": 0.0, "reload": 1,
               "build_time": 300},
}
# placed as a site; a worker walks there to build
BUILDINGS = ("fort", "wall", "farm", "laser")
# tank/range must be built within this distance of a friendly fort
FORT_BUILD_RADIUS = 4.0
# how close a worker must be to a site to finish construction
BUILD_RANGE = 1.5
# units cannot move closer than this to a wall
WALL_RADIUS = 0.9

# --- farming ---------------------------------------------------------------
# farms yield a slow but infinite income, unlike depletable nodes — but only
# while a worker stands on the farm working it. Any other order (move,
# build, dig, …) or death stops the work. Crops need sunlight: surface only.
FARM_YIELD = 5        # resources paid per completed work period
FARM_PERIOD = 100     # ticks of work per payout (5 per 10 s at 10 Hz)
FARM_WORK_RANGE = 1.5 # max distance from the nearest field tile to work it
# a farm is a 2x2 field; offsets from its anchor (the build cursor tile)
FARM_FOOTPRINT = ((0, 0), (1, 0), (0, 1), (1, 1))

# --- space laser -----------------------------------------------------------
# the endgame superweapon. Building one unlocks per player only once they
# have (a) built every other building type, (b) had a unit reach the lowest
# z-level, and (c) every surface resource node is exhausted (global). Each
# building holds ONE charge: firing spends it for good.
LASER_RADIUS = 5.0      # circular kill radius around the beam center
LASER_SPEED = 1 / 30    # beam chase speed: 1 tile per 3 s (cursor is faster)
LASER_DURATION = 600    # ticks the beam burns after triggering (60 s)
LASER_DRILL_TIME = 100  # ticks dwelling on one tile to burn down a z-level

# --- terrain ---------------------------------------------------------------
# random blobs generated per room; spawn corners are kept clear
LAKE_COUNT = 6
LAKE_SIZE = (20, 60)        # cells per lake (min, max)
MOUNTAIN_COUNT = 5
MOUNTAIN_SIZE = (30, 80)    # cells per range (min, max)
SPAWN_CLEAR_RADIUS = 12.0   # no terrain this close to a spawn corner
# mountains: 1/3 speed, but double sight/shot range and double fire rate
MOUNTAIN_SPEED_FACTOR = 1 / 3
MOUNTAIN_RANGE_FACTOR = 2.0
MOUNTAIN_ROF_FACTOR = 2.0

# --- z-levels ----------------------------------------------------------------
# the surface is z0; levels z-1..MIN_Z are solid rock until workers dig them
# out. digging down or up leaves a tunnel connecting the two levels, passable
# in both directions. tunnels are neutral — any player's units may use them.
MIN_Z = -3
DIG_TIME = 15         # ticks for a worker to mine out one solid tile
DIG_DOWN_TIME = 30    # ticks to dig a tunnel down to the next level
DIG_UP_TIME = 30      # ticks to dig a tunnel up to the next level
DIG_RANGE = 1.5       # how close a worker must be to mine a tile
TRANSIT_RANGE = 0.75  # how close a unit must be to a tunnel to use it
# richer resources deeper down: z -> (node count, amount per node);
# underground nodes stay hidden until the tile holding them is mined out
UNDERGROUND_NODES = {-1: (10, 800), -2: (14, 1500), -3: (18, 2500)}
# water-filled caverns per underground level, generated after the nodes so
# some nodes end up submerged; drain a lake (dig up into it from the level
# below) to uncover them
UNDERGROUND_LAKE_COUNT = 3
UNDERGROUND_LAKE_SIZE = (12, 35)  # cells per lake (min, max)


def encode(msg: dict) -> str:
    return json.dumps(msg)


def decode(data: str) -> dict | None:
    try:
        if len(data) > MAX_MESSAGE_SIZE:
            return None
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None
