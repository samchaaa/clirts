import os
import sys

from shared.messages import (MAP_WIDTH, MAP_HEIGHT, UNIT_STATS,
                             FARM_YIELD, FARM_PERIOD, TICK_RATE,
                             FARM_FOOTPRINT)

UNIT_CHARS = {"worker": "o", "tank": "T", "range": "r",
              "fort": "#", "wall": "=", "farm": '"', "laser": "Ψ"}


def unit_tiles(unit: dict) -> list[tuple[int, int]]:
    """Grid cells a unit covers: one, except farms (a 2x2 field)."""
    ux, uy = int(round(unit["x"])), int(round(unit["y"]))
    if unit.get("type") == "farm":
        return [(ux + dx, uy + dy) for dx, dy in FARM_FOOTPRINT]
    return [(ux, uy)]
# dig sites render as marked (denser) rock / a faint passage-to-be
SITE_CHARS = {"dig": "▒", "dig_down": "↓", "dig_up": "↑"}
ROCK = "^"  # same glyph as surface mountains, but dim
# one tunnel connects two levels and is passable both ways; the glyph
# shows where it leads from the level you are viewing
TUNNEL_DOWN = "↓"
TUNNEL_UP = "↑"
TUNNEL_BOTH = "↕"
# laser bore hole: step on it and you fall down the shaft
HOLE = "●"
# dark red + dim, so it never reads as the red team's bright beam
HOLE_COLOR = "\033[2;31m"

PLAYER_COLORS = {
    1: "\033[94m",   # blue
    2: "\033[91m",   # red
    3: "\033[92m",   # green
    4: "\033[93m",   # yellow
}
# selected units: same glyph, black on the player's color
PLAYER_HIGHLIGHT = {
    1: "\033[30;104m",
    2: "\033[30;101m",
    3: "\033[30;102m",
    4: "\033[30;103m",
}
RESOURCE_COLOR = "\033[33m"
# farms are neutral: always green, whoever built them
FARM_COLOR = "\033[92m"
FARM_HIGHLIGHT = "\033[30;102m"
BEAM_COLOR = "\033[91m"  # space laser burn area
LAKE_COLOR = "\033[34m"
MOUNTAIN_COLOR = "\033[90m"
# dim gray: rock fades toward the terminal background
ROCK_COLOR = "\033[2;90m"
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"


class Renderer:
    def __init__(self, player_id: int = 0):
        self.player_id = player_id
        self.last_snapshot = None
        self.status_message = ""
        self.room_name = ""
        self.latency_ms = 0
        self.lakes: set[tuple[int, int]] = set()
        self.mountains: set[tuple[int, int]] = set()
        self.dug: dict[int, set[tuple[int, int]]] = {}
        # tunnels[z] connect level z with level z - 1 (both ways)
        self.tunnels: dict[int, set[tuple[int, int]]] = {}
        self.holes: dict[int, set[tuple[int, int]]] = {}
        self.water: dict[int, set[tuple[int, int]]] = {}
        self.event_log: list[str] = []

    def set_snapshot(self, snapshot: dict):
        self.last_snapshot = snapshot
        for ev in snapshot.get("events", []):
            msg = self._format_event(ev)
            if msg:
                self.add_log(msg)

    def add_log(self, msg: str):
        """Timestamped rolling log; newest entries shown at the bottom."""
        tick = (self.last_snapshot or {}).get("tick", 0)
        self.event_log.append(f"{DIM}{tick // 10:>4}s{RESET} {msg}")
        del self.event_log[:-100]

    def _format_event(self, ev: dict) -> str | None:
        kind = ev.get("kind")
        mine = ev.get("owner") == self.player_id
        pos = f"({ev.get('x')},{ev.get('y')}) z{ev.get('z', 0)}"
        if kind == "node_depleted":
            return f"{RESOURCE_COLOR}Node {pos} exhausted{RESET}"
        if kind == "flood":
            return (f"{LAKE_COLOR}Flood! z{ev.get('z')}"
                    f" ({ev.get('count')} tiles) {pos}{RESET}")
        if kind == "tunnel_destroyed":
            return f"{LAKE_COLOR}Tunnel {pos} destroyed by flood{RESET}"
        laser_color = PLAYER_COLORS.get(ev.get("owner"), BEAM_COLOR)
        if kind == "laser_fired":
            return (f"{laser_color}{BOLD}SPACE LASER FIRED"
                    f" {pos}{RESET}")
        if kind == "laser_through":
            return (f"{laser_color}Laser burns through to"
                    f" z{ev.get('z')} ({ev.get('x')},{ev.get('y')}){RESET}")
        if kind == "laser_expired":
            return f"{laser_color}Laser beam expired {pos}{RESET}"
        if kind == "unit_died":
            utype = ev.get("type", "unit")
            cause = ev.get("cause")
            verb = ("drowned" if cause == "flood"
                    else "vaporized" if cause == "laser"
                    else "fell into abyss" if cause == "abyss"
                    else "fell down a shaft" if cause == "fell"
                    else "destroyed" if utype in ("fort", "wall", "farm",
                                                  "laser")
                    else "killed")
            if mine:
                return f"\033[91mYour {utype} {verb} {pos}{RESET}"
            return f"\033[92mEnemy {utype} {verb} {pos}{RESET}"
        # the rest only concern the acting player
        if not mine:
            return None
        if kind == "under_attack":
            return f"\033[91m{BOLD}Under attack! {pos}{RESET}"
        if kind == "dig_queue_done":
            return f"Mining queue done {pos}"
        if kind == "dug_down":
            return (f"Tunnel down to z{ev.get('z')}"
                    f" ({ev.get('x')},{ev.get('y')})")
        if kind == "dug_up":
            return f"Tunnel up to z{ev.get('z')} ({ev.get('x')},{ev.get('y')})"
        return None

    def set_terrain(self, terrain: dict):
        self.lakes = {tuple(c) for c in terrain.get("lakes", [])}
        self.mountains = {tuple(c) for c in terrain.get("mountains", [])}
        self.dug = {int(z): {tuple(c) for c in cells}
                    for z, cells in terrain.get("dug", {}).items()}
        self.tunnels = {int(z): {tuple(c) for c in cells}
                        for z, cells in terrain.get("tunnels", {}).items()}
        self.holes = {int(z): {tuple(c) for c in cells}
                      for z, cells in terrain.get("holes", {}).items()}
        self.water = {int(z): {tuple(c) for c in cells}
                      for z, cells in terrain.get("water", {}).items()}

    def draw(self, cursor_x: int, cursor_y: int, selected_ids: list[int],
             view_z: int = 0):
        if self.last_snapshot is None:
            self._draw_lobby()
            return

        snap = self.last_snapshot
        all_units = snap.get("units", [])
        resources = snap.get("resources", {})
        tick = snap.get("tick", 0)
        winner = snap.get("winner")

        # only what lives on the viewed level is drawn
        units = [u for u in all_units if u.get("z", 0) == view_z]
        nodes = [n for n in snap.get("resource_nodes", [])
                 if n.get("z", 0) == view_z]
        sites = [s for s in snap.get("sites", [])
                 if s.get("z", 0) == view_z]

        grid = [[" " for _ in range(MAP_WIDTH)] for _ in range(MAP_HEIGHT)]
        color_grid = [["" for _ in range(MAP_WIDTH)] for _ in range(MAP_HEIGHT)]

        # terrain first: everything else draws over it
        if view_z == 0:
            for x, y in self.lakes:
                if 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT:
                    grid[y][x] = "~"
                    color_grid[y][x] = LAKE_COLOR
            for x, y in self.mountains:
                if 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT:
                    grid[y][x] = "^"
                    color_grid[y][x] = MOUNTAIN_COLOR
        else:
            # underground: solid rock except what has been dug out
            for y in range(MAP_HEIGHT):
                for x in range(MAP_WIDTH):
                    grid[y][x] = ROCK
                    color_grid[y][x] = ROCK_COLOR
            for x, y in self.dug.get(view_z, ()):
                if 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT:
                    grid[y][x] = " "
                    color_grid[y][x] = ""
        down = self.tunnels.get(view_z, set())
        up = self.tunnels.get(view_z + 1, set())
        for x, y in down | up:
            if 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT:
                if (x, y) in down and (x, y) in up:
                    grid[y][x] = TUNNEL_BOTH
                else:
                    grid[y][x] = TUNNEL_DOWN if (x, y) in down else TUNNEL_UP
                color_grid[y][x] = BOLD
        for x, y in self.holes.get(view_z, ()):
            if 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT:
                grid[y][x] = HOLE
                color_grid[y][x] = HOLE_COLOR
        # flood water covers whatever it swallowed (incl. tunnels)
        for x, y in self.water.get(view_z, ()):
            if 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT:
                grid[y][x] = "~"
                color_grid[y][x] = LAKE_COLOR

        # projectile tracers, so units/nodes draw over them
        for shot in snap.get("shots", []):
            if shot.get("z", 0) == view_z:
                self._draw_shot(grid, color_grid, shot)

        for site in sites:
            site_color = (FARM_COLOR if site["type"] == "farm"
                          else PLAYER_COLORS.get(site["owner"], RESET))
            for sx, sy in unit_tiles(site):
                if 0 <= sx < MAP_WIDTH and 0 <= sy < MAP_HEIGHT:
                    grid[sy][sx] = SITE_CHARS.get(
                        site["type"], UNIT_CHARS.get(site["type"], "?"))
                    color_grid[sy][sx] = site_color + DIM

        for node in nodes:
            nx, ny = int(round(node["x"])), int(round(node["y"]))
            if 0 <= nx < MAP_WIDTH and 0 <= ny < MAP_HEIGHT:
                grid[ny][nx] = "$"
                color_grid[ny][nx] = RESOURCE_COLOR

        # farms first, so mobile units (e.g. farmers) draw on top of the field
        for unit in sorted(units, key=lambda u: u.get("type") != "farm"):
            owner = unit["owner"]
            is_selected = unit["id"] in selected_ids
            if unit.get("type") == "farm":
                c, hl = FARM_COLOR, FARM_HIGHLIGHT
            else:
                c = PLAYER_COLORS.get(owner, RESET)
                hl = PLAYER_HIGHLIGHT.get(owner, "\033[7m")
            char = UNIT_CHARS.get(unit.get("type", "worker"), "o")
            for ux, uy in unit_tiles(unit):
                if 0 <= ux < MAP_WIDTH and 0 <= uy < MAP_HEIGHT:
                    grid[uy][ux] = char
                    color_grid[uy][ux] = hl if is_selected else c

        # the beam draws over everything — whatever is inside is being
        # vaporized anyway. It shows on every level it has burned through
        # (dim) down to the one it is burning now (bright), never below
        for beam in snap.get("beams", []):
            beam_z = beam.get("z", 0)
            if view_z < beam_z:
                continue
            owner_color = PLAYER_COLORS.get(beam.get("owner"), BEAM_COLOR)
            burning_here = view_z == beam_z
            fill = owner_color if burning_here else owner_color + DIM
            bx, by, r = beam["x"], beam["y"], beam.get("r", 5)
            for y in range(int(by - r), int(by + r) + 2):
                for x in range(int(bx - r), int(bx + r) + 2):
                    if not (0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT):
                        continue
                    if (x - bx) ** 2 + (y - by) ** 2 <= r * r:
                        grid[y][x] = "░"
                        color_grid[y][x] = fill
            cx, cy = int(round(bx)), int(round(by))
            if 0 <= cx < MAP_WIDTH and 0 <= cy < MAP_HEIGHT:
                grid[cy][cx] = "X"
                color_grid[cy][cx] = owner_color + BOLD

        sidebar = self._build_sidebar(all_units, nodes, sites,
                                      cursor_x, cursor_y, selected_ids,
                                      view_z)

        out = []
        # repaint in place (no full-screen clear — that causes flicker);
        # every row ends with erase-to-eol and the frame with erase-below
        out.append("\033[H")

        out.append(f"{DIM}{'─' * (MAP_WIDTH + 2)}{RESET}\033[K\n")
        for y in range(MAP_HEIGHT):
            out.append(f"{DIM}│{RESET}")
            active = ""  # emit color codes only when the color changes
            for x in range(MAP_WIDTH):
                if x == cursor_x and y == cursor_y:
                    if active:
                        out.append(RESET)
                        active = ""
                    if grid[y][x] != " ":
                        out.append(f"\033[7m{color_grid[y][x]}{grid[y][x]}{RESET}")
                    else:
                        out.append(f"\033[7m+{RESET}")
                    continue
                c = color_grid[y][x]
                if c != active:
                    if active:
                        out.append(RESET)
                    if c:
                        out.append(c)
                    active = c
                out.append(grid[y][x])
            if active:
                out.append(RESET)
            out.append(f"{DIM}│{RESET}")
            if y < len(sidebar):
                out.append(f"  {sidebar[y]}")
            out.append("\033[K\n")
        out.append(f"{DIM}{'─' * (MAP_WIDTH + 2)}{RESET}\033[K\n")

        my_res = resources.get(str(self.player_id), 0)
        my_units = sum(1 for u in all_units if u["owner"] == self.player_id)
        out.append(f" Tick: {tick}  Resources: {my_res}  Units: {my_units}")
        out.append(f"  Room: {self.room_name}  Ping: {self.latency_ms}ms")
        laser = (snap.get("laser") or {}).get(str(self.player_id)) or {}
        if laser.get("active"):
            out.append(f"  {BEAM_COLOR}{BOLD}LASER ACTIVE{RESET}"
                       f" {DIM}[L] steers it to the cursor{RESET}")
        elif laser.get("charges"):
            out.append(f"  {BEAM_COLOR}{BOLD}LASER READY{RESET}"
                       f" {DIM}[L] fires at cursor (60s, one shot){RESET}")
        elif laser.get("unlocked"):
            out.append(f"  {BEAM_COLOR}space laser unlocked{RESET}"
                       f" {DIM}[O] builds one (500){RESET}")
        out.append("\033[K\n")

        sel_count = len(selected_ids)
        out.append(f" {BOLD}Level: z{view_z}{RESET}  Selected: {sel_count}"
                   f"  Cursor: ({cursor_x},{cursor_y})\033[K\n")

        out.append(f" {DIM}[WASD]cursor [Space]select [F]area [E]clear [M]move [X]attack [G]gather [Q]quit{RESET}\033[K\n")
        out.append(f" {DIM}build: [B]worker [T]tank [R]range [C]fort [V]wall"
                   f" [P]farm [O]space laser{RESET}\033[K\n")
        out.append(f" {DIM}depth: [ down / ] up view  [N]mine rock"
                   f"  [Z]tunnel down/descend {TUNNEL_DOWN}"
                   f"  [U]tunnel up/ascend {TUNNEL_UP}{RESET}\033[K\n")

        if winner is not None:
            if winner == self.player_id:
                out.append(f"\033[K\n {BOLD}\033[92m*** YOU WIN! ***{RESET}\033[K\n")
            elif winner == -1:
                out.append(f"\033[K\n {BOLD}\033[93m*** DRAW ***{RESET}\033[K\n")
            else:
                out.append(f"\033[K\n {BOLD}\033[91m*** YOU LOSE ***{RESET}\033[K\n")

        if self.status_message:
            out.append(f" {self.status_message}\033[K\n")

        out.append("\033[J")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _draw_shot(self, grid, color_grid, shot):
        fx, fy = shot["fx"], shot["fy"]
        tx, ty = shot["tx"], shot["ty"]
        dx, dy = tx - fx, ty - fy
        steps = max(1, int(round(max(abs(dx), abs(dy)))))
        c = PLAYER_COLORS.get(shot.get("owner"), RESET)
        for i in range(1, steps, 2):
            x = int(round(fx + dx * i / steps))
            y = int(round(fy + dy * i / steps))
            if 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT:
                grid[y][x] = "*"
                color_grid[y][x] = c + DIM
        # impact marker on the target cell (units drawn later cover it,
        # except the frame where the target just died)
        ix, iy = int(round(tx)), int(round(ty))
        if 0 <= ix < MAP_WIDTH and 0 <= iy < MAP_HEIGHT:
            grid[iy][ix] = "x"
            color_grid[iy][ix] = c

    def _build_sidebar(self, all_units, nodes, sites, cursor_x, cursor_y,
                       selected_ids, view_z) -> list[str]:
        lines = [f"{BOLD}── CURSOR (z{view_z}) ──{RESET}"]

        unit = next((u for u in all_units
                     if u.get("z", 0) == view_z
                     and (cursor_x, cursor_y) in unit_tiles(u)), None)
        node = next((n for n in nodes
                     if int(round(n["x"])) == cursor_x
                     and int(round(n["y"])) == cursor_y), None)

        cur = (cursor_x, cursor_y)
        terrain = None
        if cur in self.holes.get(view_z, ()):
            terrain = (f"{HOLE_COLOR}Laser bore hole — units that step in"
                       f" fall to their death{RESET}")
        elif cur in self.water.get(view_z, ()):
            terrain = (f"{LAKE_COLOR}Water — impassable; drain it from"
                       f" the level below{RESET}")
        elif (cur in self.tunnels.get(view_z, ())
                and cur in self.tunnels.get(view_z + 1, ())):
            terrain = f"{BOLD}Tunnel shaft — [Z] descends, [U] ascends{RESET}"
        elif cur in self.tunnels.get(view_z, ()):
            terrain = f"{BOLD}Tunnel down — [Z] descends{RESET}"
        elif cur in self.tunnels.get(view_z + 1, ()):
            terrain = f"{BOLD}Tunnel up — [U] ascends{RESET}"
        elif view_z < 0 and cur not in self.dug.get(view_z, ()):
            terrain = (f"{MOUNTAIN_COLOR}Solid rock —"
                       f" [N] sends a worker to mine{RESET}")
        elif view_z == 0 and cur in self.lakes:
            terrain = f"{LAKE_COLOR}Lake — impassable{RESET}"
        elif view_z == 0 and cur in self.mountains:
            terrain = (f"{MOUNTAIN_COLOR}Mountain — 2x sight & fire rate,"
                       f" 1/3 speed; [N] mines it flat{RESET}")

        if unit:
            owner = unit["owner"]
            c = PLAYER_COLORS.get(owner, RESET)
            who = "you" if owner == self.player_id else f"enemy P{owner}"
            utype = unit.get("type", "worker")
            if utype == "farm":
                lines.append(f"{FARM_COLOR}Farm #{unit['id']} (neutral){RESET}")
            else:
                lines.append(f"{c}{utype.capitalize()} #{unit['id']} ({who}){RESET}")
            lines.append(f"HP: {unit['hp']}/{UNIT_STATS[utype]['hp']}")
            lines.append(f"Pos: ({int(round(unit['x']))},{int(round(unit['y']))})")
            if activity := unit.get("activity"):
                lines.append(f"Status: {activity}")
            if unit.get("under_attack"):
                lines.append(f"\033[91m{BOLD}UNDER ATTACK{RESET}")
            if utype == "farm":
                n = unit.get("farmers", 0)
                per = FARM_PERIOD // TICK_RATE
                lines.append(
                    f"Farmers: {n} (each +{FARM_YIELD}/{per}s;"
                    f" [G] sends workers)")
            elif utype == "laser":
                if unit.get("spent"):
                    lines.append(f"{DIM}Charge spent — build another{RESET}")
                else:
                    lines.append(f"{BEAM_COLOR}{BOLD}CHARGED{RESET}"
                                 f" — [L] fires at cursor")
        elif node:
            lines.append(f"{RESOURCE_COLOR}Resource node #{node['id']}{RESET}")
            lines.append(f"Amount: {node['amount']}")
        elif site := next((s for s in sites
                           if (cursor_x, cursor_y) in unit_tiles(s)), None):
            owner = site["owner"]
            c = PLAYER_COLORS.get(owner, RESET)
            who = "you" if owner == self.player_id else f"enemy P{owner}"
            pct = 100 * site["progress"] // site["total"]
            name = site['type'].replace('_', ' ').capitalize()
            lines.append(f"{c}{name} site ({who}){RESET}")
            lines.append(f"Progress: {pct}%")
        elif terrain:
            lines.append(terrain)
        else:
            lines.append(f"{DIM}(nothing here){RESET}")
        if (unit or node) and terrain:
            lines.append(terrain)

        selected = [u for u in all_units if u["id"] in selected_ids]
        lines.append("")
        lines.append(f"{BOLD}── SELECTED ({len(selected)}) ──{RESET}")
        # cap the selection list so the log below keeps some space
        room = min(8, MAP_HEIGHT - len(lines) - 1)
        for u in selected[:room]:
            utype = u.get("type", "worker")
            uz = u.get("z", 0)
            depth = f" z{uz}" if uz != view_z else ""
            lines.append(f"#{u['id']} {utype}: "
                         f"{u['hp']}/{UNIT_STATS[utype]['hp']} hp"
                         f"  ({int(round(u['x']))},{int(round(u['y']))})"
                         f"{depth}")
        if len(selected) > room:
            lines.append(f"{DIM}…+{len(selected) - room} more{RESET}")

        lines.append("")
        lines.append(f"{BOLD}── LOG ──{RESET}")
        room = MAP_HEIGHT - len(lines)
        lines.extend(self.event_log[-room:])
        return lines[:MAP_HEIGHT]

    def _draw_lobby(self):
        out = []
        out.append("\033[H\033[2J")
        out.append(f"{BOLD}=== CLI RTS ==={RESET}\n\n")
        out.append(" Commands:\n")
        out.append("   /create <name>  - Create a room\n")
        out.append("   /join <name>    - Join a room\n")
        out.append("   /list           - List rooms\n")
        out.append("   /quit           - Quit\n")
        if self.status_message:
            # persistent (not cleared): the lobby redraws every frame,
            # so the message must survive until the next command
            out.append(f"\n {self.status_message}\n")
        out.append(f"\n > ")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
