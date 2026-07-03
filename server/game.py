import math
import random
from dataclasses import dataclass, field

from shared.messages import (
    MAP_WIDTH, MAP_HEIGHT,
    GATHER_RANGE, GATHER_RATE, STARTING_RESOURCES,
    STARTING_UNITS, UNIT_STATS, FORT_BUILD_RADIUS, Command,
)

# 1.5 guarantees distinct rendered cells even when a pair separates
# diagonally (axis component 1.5/sqrt(2) > 1 cell)
MIN_SEPARATION = 1.5


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
    started: bool = False
    winner: int | None = None

    def add_player(self, player_id: int):
        self.players.add(player_id)
        self.resources[player_id] = STARTING_RESOURCES

        corners = [
            (5, 5),
            (MAP_WIDTH - 6, MAP_HEIGHT - 6),
            (5, MAP_HEIGHT - 6),
            (MAP_WIDTH - 6, 5),
        ]
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

    def spawn_resource_nodes(self):
        positions = [
            (MAP_WIDTH // 2, MAP_HEIGHT // 2),
            (MAP_WIDTH // 4, MAP_HEIGHT // 4),
            (3 * MAP_WIDTH // 4, MAP_HEIGHT // 4),
            (MAP_WIDTH // 4, 3 * MAP_HEIGHT // 4),
            (3 * MAP_WIDTH // 4, 3 * MAP_HEIGHT // 4),
        ]
        for x, y in positions:
            nid = self.next_node_id
            self.next_node_id += 1
            self.resource_nodes[nid] = ResourceNode(id=nid, x=x, y=y)

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
                unit.target = (
                    max(0, min(MAP_WIDTH - 1, tx + ox)),
                    max(0, min(MAP_HEIGHT - 1, ty + oy)),
                )
                unit.attack_target = None
                unit.gathering = False
                unit.gather_target = None
                i += 1
        return True

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
        cost = UNIT_STATS[unit_type]["cost"]
        if self.resources.get(player_id, 0) < cost:
            return False
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
        self._move_units()
        self._separate_units()
        self._resolve_gathering()
        self._resolve_combat()
        self._cleanup_dead()
        self._check_victory()

    def _move_units(self):
        for unit in self.units.values():
            if unit.hp <= 0 or unit.stats["speed"] <= 0:
                continue

            move_target = None
            if unit.attack_target is not None:
                target_unit = self.units.get(unit.attack_target)
                if target_unit and target_unit.hp > 0:
                    dist = _dist(unit.x, unit.y, target_unit.x, target_unit.y)
                    if dist > unit.stats["range"]:
                        move_target = (target_unit.x, target_unit.y)
                else:
                    unit.attack_target = None
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
                    step = min(unit.stats["speed"], dist)
                    unit.x += (dx / dist) * step
                    unit.y += (dy / dist) * step
                    unit.x = max(0, min(MAP_WIDTH - 1, unit.x))
                    unit.y = max(0, min(MAP_HEIGHT - 1, unit.y))
                else:
                    if unit.target == move_target:
                        unit.target = None

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
                a_push = 0 if a_fixed else (push * 2 if b_fixed else push)
                b_push = 0 if b_fixed else (push * 2 if a_fixed else push)
                a.x = max(0, min(MAP_WIDTH - 1, a.x - nx * a_push))
                a.y = max(0, min(MAP_HEIGHT - 1, a.y - ny * a_push))
                b.x = max(0, min(MAP_WIDTH - 1, b.x + nx * b_push))
                b.y = max(0, min(MAP_HEIGHT - 1, b.y + ny * b_push))

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
            if dist <= unit.stats["range"]:
                target.hp -= unit.stats["damage"]

    def _auto_fire(self, unit: Unit):
        auto_range = unit.stats["auto_range"]
        if auto_range <= 0:
            return
        best, best_dist = None, auto_range
        for other in self.units.values():
            if other.owner == unit.owner or other.hp <= 0:
                continue
            d = _dist(unit.x, unit.y, other.x, other.y)
            if d <= best_dist:
                best, best_dist = other, d
        if best:
            best.hp -= unit.stats["damage"]

    def _cleanup_dead(self):
        dead = [uid for uid, u in self.units.items() if u.hp <= 0]
        for uid in dead:
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
            "winner": self.winner,
        }


def _dist(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
