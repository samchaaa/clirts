import math
import random
from collections import deque
from dataclasses import dataclass, field

from shared.messages import (
    MAP_WIDTH, MAP_HEIGHT,
    GATHER_RANGE, GATHER_RATE, STARTING_RESOURCES,
    STARTING_UNITS, UNIT_STATS, FORT_BUILD_RADIUS,
    BUILDINGS, BUILD_RANGE, WALL_RADIUS, Command,
    LAKE_COUNT, LAKE_SIZE, MOUNTAIN_COUNT, MOUNTAIN_SIZE,
    SPAWN_CLEAR_RADIUS, MOUNTAIN_SPEED_FACTOR,
    MOUNTAIN_RANGE_FACTOR, MOUNTAIN_ROF_FACTOR,
    MIN_Z, DIG_TIME, DIG_DOWN_TIME, DIG_UP_TIME,
    DIG_RANGE, TRANSIT_RANGE, UNDERGROUND_NODES,
    UNDERGROUND_LAKE_COUNT, UNDERGROUND_LAKE_SIZE,
)

# 1.5 guarantees distinct rendered cells even when a pair separates
# diagonally (axis component 1.5/sqrt(2) > 1 cell)
MIN_SEPARATION = 1.5
# a builder ignores blocking/separation from structures this close to its
# site, so it can finish walls adjacent to existing buildings
BUILDER_CLEARANCE = 3.0
# ticks between "under attack" log events per player
UNDER_ATTACK_COOLDOWN = 50


@dataclass
class Unit:
    id: int
    owner: int
    x: float
    y: float
    type: str = "worker"
    hp: int | None = None
    target: tuple[float, float] | None = None
    attack_target: int | None = None
    gathering: bool = False
    gather_target: int | None = None
    # fractional so mountain units can fire at exactly 2x rate
    cooldown: float = 0.0
    # FIFO of {"type", "x", "y", "z", "cost", "progress"}; [0] is in progress
    build_tasks: list[dict] = field(default_factory=list)
    z: int = 0
    # FIFO of {"kind": "dig"|"down"|"up", "x", "y", "z", "progress"}
    dig_tasks: list[dict] = field(default_factory=list)
    # {"x", "y", "dz"}: walk to an existing hole/ladder and change level
    transit: dict | None = None
    # set when something other than combat kills the unit (e.g. "flood")
    death_cause: str | None = None

    @property
    def build_task(self) -> dict | None:
        return self.build_tasks[0] if self.build_tasks else None

    def __post_init__(self):
        if self.hp is None:
            self.hp = UNIT_STATS[self.type]["hp"]

    @property
    def stats(self) -> dict:
        return UNIT_STATS[self.type]


@dataclass
class ResourceNode:
    id: int
    x: float
    y: float
    amount: int = 500
    z: int = 0


@dataclass
class GameState:
    tick: int = 0
    units: dict[int, Unit] = field(default_factory=dict)
    resources: dict[int, int] = field(default_factory=dict)
    resource_nodes: dict[int, ResourceNode] = field(default_factory=dict)
    next_unit_id: int = 1
    next_node_id: int = 1
    players: set[int] = field(default_factory=set)
    shots: list[dict] = field(default_factory=list)
    started: bool = False
    winner: int | None = None
    lakes: set[tuple[int, int]] = field(default_factory=set)
    mountains: set[tuple[int, int]] = field(default_factory=set)
    # underground levels are solid except tiles in dug[z]
    dug: dict[int, set[tuple[int, int]]] = field(
        default_factory=lambda: {z: set() for z in range(MIN_Z, 0)})
    # holes[z] lead down from level z; ladders[z] lead up from level z
    holes: dict[int, set[tuple[int, int]]] = field(
        default_factory=lambda: {z: set() for z in range(MIN_Z + 1, 1)})
    ladders: dict[int, set[tuple[int, int]]] = field(
        default_factory=lambda: {z: set() for z in range(MIN_Z, 0)})
    # flooded tunnel tiles (dug but impassable); filled by lake breaches
    water: dict[int, set[tuple[int, int]]] = field(
        default_factory=lambda: {z: set() for z in range(MIN_Z, 0)})
    # set when digging changes terrain; the room loop broadcasts and clears
    terrain_dirty: bool = False
    # log events for this tick, sent in the snapshot (cleared like shots)
    events: list[dict] = field(default_factory=list)
    # last tick each player got an "under attack" event (throttling)
    hit_alerts: dict[int, int] = field(default_factory=dict)

    @staticmethod
    def _spawn_corners() -> list[tuple[int, int]]:
        return [
            (5, 5),
            (MAP_WIDTH - 6, MAP_HEIGHT - 6),
            (5, MAP_HEIGHT - 6),
            (MAP_WIDTH - 6, 5),
        ]

    def add_player(self, player_id: int):
        self.players.add(player_id)
        self.resources[player_id] = STARTING_RESOURCES

        corners = self._spawn_corners()
        idx = (len(self.players) - 1) % len(corners)
        cx, cy = corners[idx]

        for i in range(STARTING_UNITS):
            uid = self.next_unit_id
            self.next_unit_id += 1
            self.units[uid] = Unit(
                id=uid, owner=player_id,
                x=cx + (i % 3) - 1, y=cy + (i // 3) - 1,
            )

    def remove_player(self, player_id: int):
        self.players.discard(player_id)
        self.resources.pop(player_id, None)
        dead = [uid for uid, u in self.units.items() if u.owner == player_id]
        for uid in dead:
            del self.units[uid]

    def generate_terrain(self, rng: random.Random | None = None):
        """Grow random lake and mountain blobs, keeping spawn corners clear."""
        rng = rng or random
        corners = self._spawn_corners()

        def clear_of_spawns(x: int, y: int) -> bool:
            return all(_dist(x, y, cx, cy) > SPAWN_CLEAR_RADIUS
                       for cx, cy in corners)

        def grow_blobs(count, size_range, occupied) -> set:
            cells: set[tuple[int, int]] = set()
            for _ in range(count):
                target = rng.randint(*size_range)
                for _ in range(100):
                    sx = rng.randrange(MAP_WIDTH)
                    sy = rng.randrange(MAP_HEIGHT)
                    if (clear_of_spawns(sx, sy)
                            and (sx, sy) not in occupied
                            and (sx, sy) not in cells):
                        break
                else:
                    continue
                blob = {(sx, sy)}
                tries = 0
                while len(blob) < target and tries < target * 20:
                    tries += 1
                    x, y = rng.choice(tuple(blob))
                    nx = x + rng.choice((-1, 0, 1))
                    ny = y + rng.choice((-1, 0, 1))
                    if (0 <= nx < MAP_WIDTH and 0 <= ny < MAP_HEIGHT
                            and clear_of_spawns(nx, ny)
                            and (nx, ny) not in occupied):
                        blob.add((nx, ny))
                cells |= blob
            return cells

        self.lakes = grow_blobs(LAKE_COUNT, LAKE_SIZE, set())
        self.mountains = grow_blobs(MOUNTAIN_COUNT, MOUNTAIN_SIZE, self.lakes)

    def terrain_msg(self) -> dict:
        return {"lakes": sorted(self.lakes),
                "mountains": sorted(self.mountains),
                "dug": {str(z): sorted(c) for z, c in self.dug.items()},
                "holes": {str(z): sorted(c) for z, c in self.holes.items()},
                "ladders": {str(z): sorted(c)
                            for z, c in self.ladders.items()},
                "water": {str(z): sorted(c)
                          for z, c in self.water.items()}}

    def _nearest_dry(self, x: int, y: int) -> tuple[int, int]:
        if (x, y) not in self.lakes:
            return x, y
        for r in range(1, max(MAP_WIDTH, MAP_HEIGHT)):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, ny = x + dx, y + dy
                    if (0 <= nx < MAP_WIDTH and 0 <= ny < MAP_HEIGHT
                            and (nx, ny) not in self.lakes):
                        return nx, ny
        return x, y

    def spawn_resource_nodes(self):
        positions = [
            (MAP_WIDTH // 2, MAP_HEIGHT // 2),
            (MAP_WIDTH // 4, MAP_HEIGHT // 4),
            (3 * MAP_WIDTH // 4, MAP_HEIGHT // 4),
            (MAP_WIDTH // 4, 3 * MAP_HEIGHT // 4),
            (3 * MAP_WIDTH // 4, 3 * MAP_HEIGHT // 4),
            (MAP_WIDTH // 2, MAP_HEIGHT // 4),
            (MAP_WIDTH // 2, 3 * MAP_HEIGHT // 4),
            (MAP_WIDTH // 4, MAP_HEIGHT // 2),
            (3 * MAP_WIDTH // 4, MAP_HEIGHT // 2),
        ]
        for x, y in positions:
            x, y = self._nearest_dry(x, y)
            nid = self.next_node_id
            self.next_node_id += 1
            self.resource_nodes[nid] = ResourceNode(id=nid, x=x, y=y)
        self._spawn_underground_nodes()

    def _spawn_underground_nodes(self, rng: random.Random | None = None):
        rng = rng or random
        for z, (count, amount) in UNDERGROUND_NODES.items():
            placed: set[tuple[int, int]] = set()
            while len(placed) < count:
                x = rng.randrange(MAP_WIDTH)
                y = rng.randrange(MAP_HEIGHT)
                if (x, y) in placed:
                    continue
                placed.add((x, y))
                nid = self.next_node_id
                self.next_node_id += 1
                self.resource_nodes[nid] = ResourceNode(
                    id=nid, x=x, y=y, amount=amount, z=z)
        # after the nodes, so lakes can form over them (submerging them)
        self._generate_underground_lakes()

    def _generate_underground_lakes(self, rng: random.Random | None = None):
        """Water-filled caverns: open (dug) but flooded. Drain one by
        tunneling beneath it and digging up into its floor."""
        rng = rng or random
        for z in self.water:
            for _ in range(UNDERGROUND_LAKE_COUNT):
                target = rng.randint(*UNDERGROUND_LAKE_SIZE)
                sx = rng.randrange(MAP_WIDTH)
                sy = rng.randrange(MAP_HEIGHT)
                blob = {(sx, sy)}
                tries = 0
                while len(blob) < target and tries < target * 20:
                    tries += 1
                    x, y = rng.choice(tuple(blob))
                    nx = x + rng.choice((-1, 0, 1))
                    ny = y + rng.choice((-1, 0, 1))
                    if 0 <= nx < MAP_WIDTH and 0 <= ny < MAP_HEIGHT:
                        blob.add((nx, ny))
                self.dug[z] |= blob
                self.water[z] |= blob

    # --- terrain effects ---------------------------------------------------

    def _is_lake(self, x: float, y: float) -> bool:
        return (int(round(x)), int(round(y))) in self.lakes

    def _passable(self, x: float, y: float, z: int) -> bool:
        tile = (int(round(x)), int(round(y)))
        if z == 0:
            return tile not in self.lakes
        return tile in self.dug[z] and tile not in self.water[z]

    def _node_exposed(self, node: ResourceNode) -> bool:
        # dug out AND dry: submerged nodes stay hidden until drained
        return self._passable(node.x, node.y, node.z)

    def _on_mountain(self, unit: Unit) -> bool:
        return (unit.z == 0
                and (int(round(unit.x)), int(round(unit.y))) in self.mountains)

    def _eff_speed(self, unit: Unit) -> float:
        speed = unit.stats["speed"]
        return speed * MOUNTAIN_SPEED_FACTOR if self._on_mountain(unit) else speed

    def _eff_range(self, unit: Unit) -> float:
        rng = unit.stats["range"]
        return rng * MOUNTAIN_RANGE_FACTOR if self._on_mountain(unit) else rng

    def _eff_auto_range(self, unit: Unit) -> float:
        rng = unit.stats["auto_range"]
        return rng * MOUNTAIN_RANGE_FACTOR if self._on_mountain(unit) else rng

    def _eff_reload(self, unit: Unit) -> float:
        reload = unit.stats["reload"]
        if self._on_mountain(unit):
            return max(1.0, reload / MOUNTAIN_ROF_FACTOR)
        return float(reload)

    def apply_command(self, player_id: int, cmd: dict) -> bool:
        action = cmd.get("command")
        if action == Command.MOVE:
            return self._cmd_move(player_id, cmd)
        elif action == Command.ATTACK:
            return self._cmd_attack(player_id, cmd)
        elif action == Command.GATHER:
            return self._cmd_gather(player_id, cmd)
        elif action == Command.BUILD:
            return self._cmd_build(player_id, cmd)
        elif action == Command.DIG:
            return self._cmd_dig(player_id, cmd)
        elif action == Command.DIG_DOWN:
            return self._cmd_dig_down(player_id, cmd)
        elif action == Command.DIG_UP:
            return self._cmd_dig_up(player_id, cmd)
        return False

    @staticmethod
    def _cmd_z(cmd: dict) -> int | None:
        z = cmd.get("z", 0)
        if not isinstance(z, int) or not (MIN_Z <= z <= 0):
            return None
        return z

    def _clear_orders(self, unit: Unit, *, keep_build: bool = False,
                      keep_dig: bool = False):
        unit.target = None
        unit.attack_target = None
        unit.gathering = False
        unit.gather_target = None
        unit.transit = None
        if not keep_build:
            self._cancel_build(unit)
        if not keep_dig:
            unit.dig_tasks.clear()

    def _cmd_move(self, player_id: int, cmd: dict) -> bool:
        unit_ids = cmd.get("unit_ids", [])
        target = cmd.get("target")
        z = self._cmd_z(cmd)
        if z is None or not target or len(target) != 2:
            return False
        tx, ty = float(target[0]), float(target[1])
        if not (0 <= tx < MAP_WIDTH and 0 <= ty < MAP_HEIGHT):
            return False
        if not self._passable(tx, ty, z):
            return False
        movable = [uid for uid in unit_ids
                   if (u := self.units.get(uid))
                   and u.z == z and u.stats["speed"] > 0]
        i = 0
        for uid in movable:
            unit = self.units.get(uid)
            if unit and unit.owner == player_id and unit.hp > 0:
                # fan group moves out into a grid formation so units
                # don't converge onto the same cell
                side = max(1, math.ceil(math.sqrt(len(movable))))
                ox = (i % side - (side - 1) / 2) * MIN_SEPARATION
                oy = (i // side - (side - 1) / 2) * MIN_SEPARATION
                fx = max(0, min(MAP_WIDTH - 1, tx + ox))
                fy = max(0, min(MAP_HEIGHT - 1, ty + oy))
                if not self._passable(fx, fy, z):
                    fx, fy = tx, ty
                self._clear_orders(unit)
                unit.target = (fx, fy)
                i += 1
        return True

    def _site_occupied(self, player_id: int, tx: float, ty: float,
                       z: int) -> bool:
        for u in self.units.values():
            if u.hp <= 0:
                continue
            if (u.type in BUILDINGS and u.z == z
                    and _dist(u.x, u.y, tx, ty) < WALL_RADIUS):
                return True
            if u.owner == player_id:
                for task in u.build_tasks:
                    if (task["z"] == z
                            and _dist(task["x"], task["y"], tx, ty) < WALL_RADIUS):
                        return True
        return False

    def _cancel_build(self, unit: Unit):
        for task in unit.build_tasks:
            self.resources[unit.owner] = (
                self.resources.get(unit.owner, 0) + task["cost"])
        unit.build_tasks.clear()

    def _cmd_attack(self, player_id: int, cmd: dict) -> bool:
        unit_ids = cmd.get("unit_ids", [])
        target_id = cmd.get("target_id")
        if target_id is None or target_id not in self.units:
            return False
        target_unit = self.units[target_id]
        if target_unit.owner == player_id:
            return False
        for uid in unit_ids:
            unit = self.units.get(uid)
            if (unit and unit.owner == player_id and unit.hp > 0
                    and unit.z == target_unit.z):
                self._clear_orders(unit)
                unit.attack_target = target_id
        return True

    def _cmd_gather(self, player_id: int, cmd: dict) -> bool:
        unit_ids = cmd.get("unit_ids", [])
        node_id = cmd.get("node_id")
        if node_id is None or node_id not in self.resource_nodes:
            return False
        node = self.resource_nodes[node_id]
        if not self._node_exposed(node):
            return False
        for uid in unit_ids:
            unit = self.units.get(uid)
            if (unit and unit.owner == player_id and unit.hp > 0
                    and unit.type == "worker" and unit.z == node.z):
                self._clear_orders(unit)
                unit.gather_target = node_id
                unit.gathering = True
        return True

    def _cmd_build(self, player_id: int, cmd: dict) -> bool:
        target = cmd.get("target")
        z = self._cmd_z(cmd)
        if z is None or not target or len(target) != 2:
            return False
        unit_type = cmd.get("unit_type", "worker")
        if unit_type not in UNIT_STATS:
            return False
        tx, ty = float(target[0]), float(target[1])
        if not (0 <= tx < MAP_WIDTH and 0 <= ty < MAP_HEIGHT):
            return False
        if not self._passable(tx, ty, z):
            return False
        cost = UNIT_STATS[unit_type]["cost"]
        if self.resources.get(player_id, 0) < cost:
            return False
        if unit_type in BUILDINGS:
            # buildings are constructed on site by a worker
            worker = next(
                (u for uid in cmd.get("unit_ids", [])
                 if (u := self.units.get(uid))
                 and u.owner == player_id and u.hp > 0
                 and u.type == "worker" and u.z == z),
                None,
            )
            if worker is None:
                return False
            if self._site_occupied(player_id, tx, ty, z):
                return False
            self.resources[player_id] -= cost
            # queue behind any in-progress builds
            self._clear_orders(worker, keep_build=True)
            worker.build_tasks.append({"type": unit_type, "x": tx, "y": ty,
                                       "z": z, "cost": cost, "progress": 0})
            return True
        if unit_type in ("tank", "range"):
            near_fort = any(
                u.type == "fort" and u.owner == player_id and u.hp > 0
                and u.z == z and _dist(u.x, u.y, tx, ty) <= FORT_BUILD_RADIUS
                for u in self.units.values()
            )
            if not near_fort:
                return False
        self.resources[player_id] -= cost
        uid = self.next_unit_id
        self.next_unit_id += 1
        self.units[uid] = Unit(id=uid, owner=player_id, x=tx, y=ty,
                               type=unit_type, z=z)
        return True

    def _pick_digger(self, player_id: int, unit_ids: list,
                     z: int) -> Unit | None:
        """The selected worker on z with the shortest dig queue, so repeated
        dig orders spread across the selection."""
        workers = [u for uid in unit_ids
                   if (u := self.units.get(uid))
                   and u.owner == player_id and u.hp > 0
                   and u.type == "worker" and u.z == z]
        return min(workers, key=lambda u: len(u.dig_tasks), default=None)

    def _queue_dig(self, worker: Unit, kind: str, tx: int, ty: int, z: int):
        self._clear_orders(worker, keep_dig=True)
        worker.dig_tasks.append({"kind": kind, "x": tx, "y": ty, "z": z,
                                 "progress": 0})

    def _dig_target(self, cmd: dict) -> tuple[int, int, int] | None:
        z = self._cmd_z(cmd)
        target = cmd.get("target")
        if z is None or not target or len(target) != 2:
            return None
        tx, ty = int(target[0]), int(target[1])
        if not (0 <= tx < MAP_WIDTH and 0 <= ty < MAP_HEIGHT):
            return None
        return tx, ty, z

    def _cmd_dig(self, player_id: int, cmd: dict) -> bool:
        spot = self._dig_target(cmd)
        if spot is None:
            return False
        tx, ty, z = spot
        if self._already_dug((tx, ty), z):
            return False
        worker = self._pick_digger(player_id, cmd.get("unit_ids", []), z)
        if worker is None:
            return False
        self._queue_dig(worker, "dig", tx, ty, z)
        return True

    def _cmd_dig_down(self, player_id: int, cmd: dict) -> bool:
        spot = self._dig_target(cmd)
        if spot is None or spot[2] <= MIN_Z:
            return False
        tx, ty, z = spot
        unit_ids = cmd.get("unit_ids", [])
        if (tx, ty) in self.holes[z]:
            # existing hole: any selected unit may walk over and descend
            return self._order_transit(player_id, unit_ids, tx, ty, z, -1)
        if not self._passable(tx, ty, z) or (tx, ty) in self.ladders.get(z, ()):
            return False
        worker = self._pick_digger(player_id, unit_ids, z)
        if worker is None:
            return False
        self._queue_dig(worker, "down", tx, ty, z)
        return True

    def _cmd_dig_up(self, player_id: int, cmd: dict) -> bool:
        spot = self._dig_target(cmd)
        if spot is None or spot[2] == 0:
            return False
        tx, ty, z = spot
        unit_ids = cmd.get("unit_ids", [])
        if (tx, ty) in self.ladders[z]:
            # existing ladder: any selected unit may walk over and ascend
            return self._order_transit(player_id, unit_ids, tx, ty, z, +1)
        if not self._passable(tx, ty, z) or (tx, ty) in self.holes.get(z, ()):
            return False
        worker = self._pick_digger(player_id, unit_ids, z)
        if worker is None:
            return False
        self._queue_dig(worker, "up", tx, ty, z)
        return True

    def _order_transit(self, player_id: int, unit_ids: list,
                       tx: int, ty: int, z: int, dz: int) -> bool:
        ok = False
        for uid in unit_ids:
            unit = self.units.get(uid)
            if (unit and unit.owner == player_id and unit.hp > 0
                    and unit.z == z and unit.stats["speed"] > 0):
                self._clear_orders(unit)
                unit.transit = {"x": tx, "y": ty, "dz": dz}
                ok = True
        return ok

    def tick_update(self):
        self.tick += 1
        self.shots = []
        self.events = []
        self._move_units()
        self._separate_units()
        self._resolve_transit()
        self._resolve_digging()
        self._resolve_building()
        self._resolve_gathering()
        self._resolve_combat()
        self._cleanup_dead()
        self._check_victory()

    def _move_units(self):
        walls = [u for u in self.units.values()
                 if u.type == "wall" and u.hp > 0]
        for unit in self.units.values():
            if unit.hp <= 0 or unit.stats["speed"] <= 0:
                continue

            move_target = None
            if unit.attack_target is not None:
                target_unit = self.units.get(unit.attack_target)
                if (target_unit and target_unit.hp > 0
                        and target_unit.z == unit.z):
                    dist = _dist(unit.x, unit.y, target_unit.x, target_unit.y)
                    if dist > self._eff_range(unit):
                        move_target = (target_unit.x, target_unit.y)
                else:
                    # dead, or escaped to another level
                    unit.attack_target = None
            elif unit.build_task:
                site = (unit.build_task["x"], unit.build_task["y"])
                if _dist(unit.x, unit.y, *site) > BUILD_RANGE:
                    move_target = site
            elif unit.dig_tasks:
                task = unit.dig_tasks[0]
                site = (task["x"], task["y"])
                reach = DIG_RANGE if task["kind"] == "dig" else TRANSIT_RANGE
                if (task["z"] == unit.z
                        and _dist(unit.x, unit.y, *site) > reach):
                    move_target = site
            elif unit.transit:
                site = (unit.transit["x"], unit.transit["y"])
                if _dist(unit.x, unit.y, *site) > TRANSIT_RANGE:
                    move_target = site
            elif unit.gathering and unit.gather_target is not None:
                node = self.resource_nodes.get(unit.gather_target)
                if node:
                    dist = _dist(unit.x, unit.y, node.x, node.y)
                    if dist > GATHER_RANGE:
                        move_target = (node.x, node.y)
                else:
                    unit.gathering = False
                    unit.gather_target = None
            elif unit.target:
                move_target = unit.target

            if move_target:
                tx, ty = move_target
                dx, dy = tx - unit.x, ty - unit.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0.1:
                    step = min(self._eff_speed(unit), dist)
                    ux, uy = dx / dist, dy / dist
                    nx = max(0, min(MAP_WIDTH - 1, unit.x + ux * step))
                    ny = max(0, min(MAP_HEIGHT - 1, unit.y + uy * step))
                    if not self._blocked(unit, nx, ny, walls):
                        unit.x, unit.y = nx, ny
                    else:
                        # slide perpendicular along the obstacle, whichever
                        # side ends up closer to the target
                        best = None
                        for px, py in ((-uy, ux), (uy, -ux)):
                            sx = max(0, min(MAP_WIDTH - 1,
                                            unit.x + px * step))
                            sy = max(0, min(MAP_HEIGHT - 1,
                                            unit.y + py * step))
                            if self._blocked(unit, sx, sy, walls):
                                continue
                            d = _dist(sx, sy, tx, ty)
                            if best is None or d < best[0]:
                                best = (d, sx, sy)
                        if best:
                            unit.x, unit.y = best[1], best[2]
                else:
                    if unit.target == move_target:
                        unit.target = None

    def _builder_exempt(self, unit: Unit, structure: Unit) -> bool:
        task = unit.build_task
        return bool(task) and _dist(task["x"], task["y"],
                                    structure.x, structure.y) <= BUILDER_CLEARANCE

    def _blocked(self, unit: Unit, nx: float, ny: float,
                 walls: list[Unit]) -> bool:
        # impassable terrain blocks (unless already inside it, so units
        # can escape): lakes on the surface, solid rock underground
        if (not self._passable(nx, ny, unit.z)
                and self._passable(unit.x, unit.y, unit.z)):
            return True
        return self._wall_blocked(unit, nx, ny, walls)

    def _wall_blocked(self, unit: Unit, nx: float, ny: float,
                      walls: list[Unit]) -> bool:
        for wall in walls:
            if wall is unit or wall.z != unit.z \
                    or self._builder_exempt(unit, wall):
                continue
            d_new = _dist(wall.x, wall.y, nx, ny)
            # blocked only when moving deeper into the wall's radius,
            # so a unit that ends up inside can still walk out
            if d_new < WALL_RADIUS and d_new < _dist(wall.x, wall.y,
                                                     unit.x, unit.y):
                return True
        return False

    def _resolve_transit(self):
        for unit in self.units.values():
            t = unit.transit
            if unit.hp <= 0 or not t:
                continue
            if _dist(unit.x, unit.y, t["x"], t["y"]) > TRANSIT_RANGE:
                continue
            tile = (t["x"], t["y"])
            passage = self.holes if t["dz"] < 0 else self.ladders
            unit.transit = None
            if tile in passage.get(unit.z, ()):
                unit.x, unit.y = float(tile[0]), float(tile[1])
                unit.z += t["dz"]
                self._maybe_drown(unit)

    def _maybe_drown(self, unit: Unit):
        """Arriving on a flooded tile (e.g. dropping through a hole into
        an underground lake) is fatal."""
        if (unit.z < 0 and (int(round(unit.x)), int(round(unit.y)))
                in self.water[unit.z]):
            unit.hp = 0
            unit.death_cause = "flood"

    def _already_dug(self, tile: tuple[int, int], z: int) -> bool:
        """Nothing left to mine: on the surface only mountains are minable
        (mining levels one flat); underground it's any solid tile."""
        if z == 0:
            return tile not in self.mountains
        return tile in self.dug[z]

    def _resolve_digging(self):
        durations = {"dig": DIG_TIME, "down": DIG_DOWN_TIME,
                     "up": DIG_UP_TIME}
        for unit in self.units.values():
            if unit.hp <= 0 or not unit.dig_tasks:
                continue
            task = unit.dig_tasks[0]
            tx, ty, z, kind = task["x"], task["y"], task["z"], task["kind"]
            tile = (tx, ty)
            if z != unit.z:
                unit.dig_tasks.pop(0)  # stale: queued on another level
                continue
            if kind == "dig" and self._already_dug(tile, z):
                unit.dig_tasks.pop(0)  # someone already mined it
                continue
            reach = DIG_RANGE if kind == "dig" else TRANSIT_RANGE
            if _dist(unit.x, unit.y, tx, ty) > reach:
                continue
            # a hole/ladder someone else finished meanwhile skips the digging
            if (kind == "down" and tile in self.holes[z]) \
                    or (kind == "up" and tile in self.ladders[z]):
                task["progress"] = durations[kind]
            task["progress"] += 1
            if task["progress"] < durations[kind]:
                continue
            unit.dig_tasks.pop(0)
            if kind == "dig":
                if z == 0:
                    self.mountains.discard(tile)  # mountain mined flat
                else:
                    self.dug[z].add(tile)
                if not unit.dig_tasks:
                    self.events.append({"kind": "dig_queue_done",
                                        "owner": unit.owner,
                                        "x": tx, "y": ty, "z": z})
            elif kind == "down":
                self.holes[z].add(tile)
                self.dug[z - 1].add(tile)
                unit.x, unit.y = float(tx), float(ty)
                unit.z = z - 1
                self._maybe_drown(unit)
                self.events.append({"kind": "dug_down", "owner": unit.owner,
                                    "x": tx, "y": ty, "z": z - 1})
            else:  # up
                self.ladders[z].add(tile)
                if z + 1 < 0:
                    self.dug[z + 1].add(tile)
                unit.x, unit.y = float(tx), float(ty)
                unit.z = z + 1
                self.events.append({"kind": "dug_up", "owner": unit.owner,
                                    "x": tx, "y": ty, "z": z + 1})
                # breaching a lakebed (or a flooded level) drains the
                # water above into the tunnels; the digger climbs out
                # first, onto the freshly drained tile
                self._flood_down(tx, ty, z)
            self.terrain_dirty = True

    @staticmethod
    def _connected(sx: int, sy: int,
                   cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
        """The 8-connected blob of cells containing (sx, sy)."""
        if (sx, sy) not in cells:
            return set()
        blob = {(sx, sy)}
        queue = deque(blob)
        while queue:
            x, y = queue.popleft()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if n in cells and n not in blob:
                        blob.add(n)
                        queue.append(n)
        return blob

    def _flood_down(self, tx: int, ty: int, z: int):
        """Water above (tx, ty) pours down onto level z: connected tunnel
        tiles flood one-for-one until the source blob above is drained.
        Units caught in the flood drown."""
        if z + 1 == 0:
            above = self.lakes
        else:
            above = self.water[z + 1]
        source = self._connected(tx, ty, above)
        if not source:
            return
        # water spreads through open tunnels (4-connected), breach first
        flooded: list[tuple[int, int]] = []
        seen = {(tx, ty)}
        queue = deque(seen)
        while queue and len(flooded) < len(source):
            x, y = queue.popleft()
            flooded.append((x, y))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, y + dy)
                if (n not in seen and n in self.dug[z]
                        and n not in self.water[z]):
                    seen.add(n)
                    queue.append(n)
        self.water[z] |= set(flooded)
        # the source drains tile-for-tile, nearest the breach first
        drained = sorted(source,
                         key=lambda c: _dist(c[0], c[1], tx, ty))[:len(flooded)]
        above -= set(drained)
        for u in self.units.values():
            if (u.hp > 0 and u.z == z
                    and (int(round(u.x)), int(round(u.y))) in self.water[z]):
                u.hp = 0  # drowned
                u.death_cause = "flood"
        self.events.append({"kind": "flood", "x": tx, "y": ty, "z": z,
                            "count": len(flooded)})
        self.terrain_dirty = True

    def _resolve_building(self):
        for unit in list(self.units.values()):
            task = unit.build_task
            if unit.hp <= 0 or not task or unit.z != task["z"]:
                continue
            if _dist(unit.x, unit.y, task["x"], task["y"]) <= BUILD_RANGE:
                task["progress"] += 1
                if task["progress"] >= UNIT_STATS[task["type"]]["build_time"]:
                    uid = self.next_unit_id
                    self.next_unit_id += 1
                    self.units[uid] = Unit(id=uid, owner=unit.owner,
                                           x=task["x"], y=task["y"],
                                           type=task["type"], z=task["z"])
                    unit.build_tasks.pop(0)

    def _separate_units(self):
        units = [u for u in self.units.values() if u.hp > 0]
        for i, a in enumerate(units):
            for b in units[i + 1:]:
                if a.z != b.z:
                    continue
                dx, dy = b.x - a.x, b.y - a.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist >= MIN_SEPARATION:
                    continue
                push = (MIN_SEPARATION - dist) / 2
                if dist < 1e-6:
                    # perfectly stacked: pick a stable direction per pair
                    angle = ((a.id * 7 + b.id * 13) % 16) * (math.pi / 8)
                    dx, dy = math.cos(angle), math.sin(angle)
                    dist = 1.0
                nx, ny = dx / dist, dy / dist
                # buildings don't budge: the mobile unit takes the full push
                a_fixed = a.stats["speed"] <= 0
                b_fixed = b.stats["speed"] <= 0
                if a_fixed and b_fixed:
                    continue
                # don't shove a builder away from structures at its site
                if (a_fixed and self._builder_exempt(b, a)) or \
                        (b_fixed and self._builder_exempt(a, b)):
                    continue
                a_push = 0 if a_fixed else (push * 2 if b_fixed else push)
                b_push = 0 if b_fixed else (push * 2 if a_fixed else push)
                ax = max(0, min(MAP_WIDTH - 1, a.x - nx * a_push))
                ay = max(0, min(MAP_HEIGHT - 1, a.y - ny * a_push))
                if self._passable(ax, ay, a.z):
                    a.x, a.y = ax, ay
                bx = max(0, min(MAP_WIDTH - 1, b.x + nx * b_push))
                by = max(0, min(MAP_HEIGHT - 1, b.y + ny * b_push))
                if self._passable(bx, by, b.z):
                    b.x, b.y = bx, by

    def _resolve_gathering(self):
        for unit in self.units.values():
            if unit.hp <= 0 or not unit.gathering or unit.gather_target is None:
                continue
            node = self.resource_nodes.get(unit.gather_target)
            if not node:
                unit.gathering = False
                unit.gather_target = None
                continue
            dist = _dist(unit.x, unit.y, node.x, node.y)
            if dist <= GATHER_RANGE and node.amount > 0:
                gathered = min(GATHER_RATE, node.amount)
                node.amount -= gathered
                self.resources[unit.owner] = self.resources.get(unit.owner, 0) + gathered
                if node.amount <= 0:
                    del self.resource_nodes[node.id]
                    unit.gathering = False
                    unit.gather_target = None
                    self.events.append({"kind": "node_depleted",
                                        "x": int(round(node.x)),
                                        "y": int(round(node.y)),
                                        "z": node.z})

    def _resolve_combat(self):
        for unit in self.units.values():
            if unit.hp <= 0:
                continue
            if unit.cooldown >= 1:
                unit.cooldown -= 1
                continue
            if unit.attack_target is None:
                # stateless auto-fire: shoot but never chase or
                # override move orders
                self._auto_fire(unit)
                continue
            target = self.units.get(unit.attack_target)
            if not target or target.hp <= 0 or target.z != unit.z:
                unit.attack_target = None
                continue
            dist = _dist(unit.x, unit.y, target.x, target.y)
            if dist <= self._eff_range(unit):
                target.hp -= unit.stats["damage"]
                # accumulate the fractional remainder so a 1.5-tick reload
                # really fires 2 times every 3 ticks
                unit.cooldown += self._eff_reload(unit) - 1
                self._record_shot(unit, target)
                self._note_hit(target)

    def _record_shot(self, unit: Unit, target: Unit):
        if unit.type in ("fort", "range"):
            self.shots.append({
                "owner": unit.owner, "z": unit.z,
                "fx": round(unit.x, 1), "fy": round(unit.y, 1),
                "tx": round(target.x, 1), "ty": round(target.y, 1),
            })

    def _auto_fire(self, unit: Unit):
        auto_range = self._eff_auto_range(unit)
        if auto_range <= 0:
            return
        best, best_dist = None, auto_range
        for other in self.units.values():
            # never auto-target walls; they must be attacked deliberately
            if other.owner == unit.owner or other.hp <= 0 \
                    or other.z != unit.z or other.type == "wall":
                continue
            d = _dist(unit.x, unit.y, other.x, other.y)
            if d <= best_dist:
                best, best_dist = other, d
        if best:
            best.hp -= unit.stats["damage"]
            unit.cooldown += self._eff_reload(unit) - 1
            self._record_shot(unit, best)
            self._note_hit(best)

    def _note_hit(self, target: Unit):
        """Throttled 'under attack' log event for the victim's owner."""
        last = self.hit_alerts.get(target.owner, -UNDER_ATTACK_COOLDOWN)
        if self.tick - last >= UNDER_ATTACK_COOLDOWN:
            self.hit_alerts[target.owner] = self.tick
            self.events.append({"kind": "under_attack",
                                "owner": target.owner,
                                "x": int(round(target.x)),
                                "y": int(round(target.y)), "z": target.z})

    def _cleanup_dead(self):
        dead = [uid for uid, u in self.units.items() if u.hp <= 0]
        for uid in dead:
            u = self.units[uid]
            self._cancel_build(u)
            self.events.append({"kind": "unit_died", "owner": u.owner,
                                "type": u.type,
                                "x": int(round(u.x)), "y": int(round(u.y)),
                                "z": u.z,
                                "cause": u.death_cause or "combat"})
            del self.units[uid]

    def _check_victory(self):
        if len(self.players) < 2:
            return
        owners_alive = {u.owner for u in self.units.values()}
        alive_players = self.players & owners_alive
        if len(alive_players) == 1:
            self.winner = alive_players.pop()
        elif len(alive_players) == 0:
            self.winner = -1

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "tick": self.tick,
            "units": [
                {"id": u.id, "owner": u.owner, "type": u.type,
                 "x": round(u.x, 1), "y": round(u.y, 1), "z": u.z,
                 "hp": u.hp}
                for u in self.units.values()
            ],
            "resources": {str(pid): amt for pid, amt in self.resources.items()},
            "resource_nodes": [
                {"id": n.id, "x": round(n.x, 1), "y": round(n.y, 1),
                 "z": n.z, "amount": n.amount}
                for n in self.resource_nodes.values()
                # undug underground nodes stay hidden
                if self._node_exposed(n)
            ],
            "shots": self.shots,
            "events": self.events,
            "sites": [
                {"owner": u.owner, "type": t["type"],
                 "x": t["x"], "y": t["y"], "z": t["z"],
                 "progress": t["progress"],
                 "total": UNIT_STATS[t["type"]]["build_time"]}
                for u in self.units.values() if u.hp > 0
                for t in u.build_tasks
            ] + [
                {"owner": u.owner,
                 "type": {"dig": "dig", "down": "dig_down",
                          "up": "dig_up"}[t["kind"]],
                 "x": t["x"], "y": t["y"], "z": t["z"],
                 "progress": t["progress"],
                 "total": {"dig": DIG_TIME, "down": DIG_DOWN_TIME,
                           "up": DIG_UP_TIME}[t["kind"]]}
                for u in self.units.values() if u.hp > 0
                for t in u.dig_tasks
            ],
            "winner": self.winner,
        }


def _dist(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
