import math
import random
from dataclasses import dataclass, field

from shared.messages import (
    MAP_WIDTH, MAP_HEIGHT,
    GATHER_RANGE, GATHER_RATE, STARTING_RESOURCES,
    STARTING_UNITS, UNIT_STATS, FORT_BUILD_RADIUS,
    BUILDINGS, BUILD_RANGE, WALL_RADIUS, Command,
    LAKE_COUNT, LAKE_SIZE, MOUNTAIN_COUNT, MOUNTAIN_SIZE,
    SPAWN_CLEAR_RADIUS, MOUNTAIN_SPEED_FACTOR,
    MOUNTAIN_RANGE_FACTOR, MOUNTAIN_ROF_FACTOR,
)

# 1.5 guarantees distinct rendered cells even when a pair separates
# diagonally (axis component 1.5/sqrt(2) > 1 cell)
MIN_SEPARATION = 1.5
# a builder ignores blocking/separation from structures this close to its
# site, so it can finish walls adjacent to existing buildings
BUILDER_CLEARANCE = 3.0


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
    # FIFO of {"type", "x", "y", "cost", "progress"}; [0] is in progress
    build_tasks: list[dict] = field(default_factory=list)

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
                "mountains": sorted(self.mountains)}

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

    # --- terrain effects ---------------------------------------------------

    def _is_lake(self, x: float, y: float) -> bool:
        return (int(round(x)), int(round(y))) in self.lakes

    def _on_mountain(self, unit: Unit) -> bool:
        return (int(round(unit.x)), int(round(unit.y))) in self.mountains

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
        return False

    def _cmd_move(self, player_id: int, cmd: dict) -> bool:
        unit_ids = cmd.get("unit_ids", [])
        target = cmd.get("target")
        if not target or len(target) != 2:
            return False
        tx, ty = float(target[0]), float(target[1])
        if not (0 <= tx < MAP_WIDTH and 0 <= ty < MAP_HEIGHT):
            return False
        if self._is_lake(tx, ty):
            return False
        movable = [uid for uid in unit_ids
                   if (u := self.units.get(uid))
                   and u.stats["speed"] > 0]
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
                if self._is_lake(fx, fy):
                    fx, fy = tx, ty
                unit.target = (fx, fy)
                unit.attack_target = None
                unit.gathering = False
                unit.gather_target = None
                self._cancel_build(unit)
                i += 1
        return True

    def _site_occupied(self, player_id: int, tx: float, ty: float) -> bool:
        for u in self.units.values():
            if u.hp <= 0:
                continue
            if (u.type in BUILDINGS
                    and _dist(u.x, u.y, tx, ty) < WALL_RADIUS):
                return True
            if u.owner == player_id:
                for task in u.build_tasks:
                    if _dist(task["x"], task["y"], tx, ty) < WALL_RADIUS:
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
            if unit and unit.owner == player_id and unit.hp > 0:
                unit.attack_target = target_id
                unit.target = None
                unit.gathering = False
                unit.gather_target = None
                self._cancel_build(unit)
        return True

    def _cmd_gather(self, player_id: int, cmd: dict) -> bool:
        unit_ids = cmd.get("unit_ids", [])
        node_id = cmd.get("node_id")
        if node_id is None or node_id not in self.resource_nodes:
            return False
        for uid in unit_ids:
            unit = self.units.get(uid)
            if (unit and unit.owner == player_id and unit.hp > 0
                    and unit.type == "worker"):
                unit.gather_target = node_id
                unit.gathering = True
                unit.target = None
                unit.attack_target = None
                self._cancel_build(unit)
        return True

    def _cmd_build(self, player_id: int, cmd: dict) -> bool:
        target = cmd.get("target")
        if not target or len(target) != 2:
            return False
        unit_type = cmd.get("unit_type", "worker")
        if unit_type not in UNIT_STATS:
            return False
        tx, ty = float(target[0]), float(target[1])
        if not (0 <= tx < MAP_WIDTH and 0 <= ty < MAP_HEIGHT):
            return False
        if self._is_lake(tx, ty):
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
                 and u.type == "worker"),
                None,
            )
            if worker is None:
                return False
            if self._site_occupied(player_id, tx, ty):
                return False
            self.resources[player_id] -= cost
            # queue behind any in-progress builds
            worker.build_tasks.append({"type": unit_type, "x": tx, "y": ty,
                                       "cost": cost, "progress": 0})
            worker.target = None
            worker.attack_target = None
            worker.gathering = False
            worker.gather_target = None
            return True
        if unit_type in ("tank", "range"):
            near_fort = any(
                u.type == "fort" and u.owner == player_id and u.hp > 0
                and _dist(u.x, u.y, tx, ty) <= FORT_BUILD_RADIUS
                for u in self.units.values()
            )
            if not near_fort:
                return False
        self.resources[player_id] -= cost
        uid = self.next_unit_id
        self.next_unit_id += 1
        self.units[uid] = Unit(id=uid, owner=player_id, x=tx, y=ty,
                               type=unit_type)
        return True

    def tick_update(self):
        self.tick += 1
        self.shots = []
        self._move_units()
        self._separate_units()
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
                if target_unit and target_unit.hp > 0:
                    dist = _dist(unit.x, unit.y, target_unit.x, target_unit.y)
                    if dist > self._eff_range(unit):
                        move_target = (target_unit.x, target_unit.y)
                else:
                    unit.attack_target = None
            elif unit.build_task:
                site = (unit.build_task["x"], unit.build_task["y"])
                if _dist(unit.x, unit.y, *site) > BUILD_RANGE:
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
        # lakes are impassable (unless already in one, so units can escape)
        if self._is_lake(nx, ny) and not self._is_lake(unit.x, unit.y):
            return True
        return self._wall_blocked(unit, nx, ny, walls)

    def _wall_blocked(self, unit: Unit, nx: float, ny: float,
                      walls: list[Unit]) -> bool:
        for wall in walls:
            if wall is unit or self._builder_exempt(unit, wall):
                continue
            d_new = _dist(wall.x, wall.y, nx, ny)
            # blocked only when moving deeper into the wall's radius,
            # so a unit that ends up inside can still walk out
            if d_new < WALL_RADIUS and d_new < _dist(wall.x, wall.y,
                                                     unit.x, unit.y):
                return True
        return False

    def _resolve_building(self):
        for unit in list(self.units.values()):
            task = unit.build_task
            if unit.hp <= 0 or not task:
                continue
            if _dist(unit.x, unit.y, task["x"], task["y"]) <= BUILD_RANGE:
                task["progress"] += 1
                if task["progress"] >= UNIT_STATS[task["type"]]["build_time"]:
                    uid = self.next_unit_id
                    self.next_unit_id += 1
                    self.units[uid] = Unit(id=uid, owner=unit.owner,
                                           x=task["x"], y=task["y"],
                                           type=task["type"])
                    unit.build_tasks.pop(0)

    def _separate_units(self):
        units = [u for u in self.units.values() if u.hp > 0]
        for i, a in enumerate(units):
            for b in units[i + 1:]:
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
                if not self._is_lake(ax, ay):
                    a.x, a.y = ax, ay
                bx = max(0, min(MAP_WIDTH - 1, b.x + nx * b_push))
                by = max(0, min(MAP_HEIGHT - 1, b.y + ny * b_push))
                if not self._is_lake(bx, by):
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
            if not target or target.hp <= 0:
                unit.attack_target = None
                continue
            dist = _dist(unit.x, unit.y, target.x, target.y)
            if dist <= self._eff_range(unit):
                target.hp -= unit.stats["damage"]
                # accumulate the fractional remainder so a 1.5-tick reload
                # really fires 2 times every 3 ticks
                unit.cooldown += self._eff_reload(unit) - 1
                self._record_shot(unit, target)

    def _record_shot(self, unit: Unit, target: Unit):
        if unit.type in ("fort", "range"):
            self.shots.append({
                "owner": unit.owner,
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
                    or other.type == "wall":
                continue
            d = _dist(unit.x, unit.y, other.x, other.y)
            if d <= best_dist:
                best, best_dist = other, d
        if best:
            best.hp -= unit.stats["damage"]
            unit.cooldown += self._eff_reload(unit) - 1
            self._record_shot(unit, best)

    def _cleanup_dead(self):
        dead = [uid for uid, u in self.units.items() if u.hp <= 0]
        for uid in dead:
            self._cancel_build(self.units[uid])
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
                 "x": round(u.x, 1), "y": round(u.y, 1), "hp": u.hp}
                for u in self.units.values()
            ],
            "resources": {str(pid): amt for pid, amt in self.resources.items()},
            "resource_nodes": [
                {"id": n.id, "x": round(n.x, 1), "y": round(n.y, 1), "amount": n.amount}
                for n in self.resource_nodes.values()
            ],
            "shots": self.shots,
            "sites": [
                {"owner": u.owner, "type": t["type"],
                 "x": t["x"], "y": t["y"], "progress": t["progress"],
                 "total": UNIT_STATS[t["type"]]["build_time"]}
                for u in self.units.values() if u.hp > 0
                for t in u.build_tasks
            ],
            "winner": self.winner,
        }


def _dist(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
