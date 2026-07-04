import os
import sys

from shared.messages import MAP_WIDTH, MAP_HEIGHT, UNIT_STATS

UNIT_CHARS = {"worker": "o", "tank": "T", "range": "r",
              "fort": "#", "wall": "="}

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
LAKE_COLOR = "\033[34m"
MOUNTAIN_COLOR = "\033[90m"
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

    def set_snapshot(self, snapshot: dict):
        self.last_snapshot = snapshot

    def set_terrain(self, terrain: dict):
        self.lakes = {tuple(c) for c in terrain.get("lakes", [])}
        self.mountains = {tuple(c) for c in terrain.get("mountains", [])}

    def draw(self, cursor_x: int, cursor_y: int, selected_ids: list[int]):
        if self.last_snapshot is None:
            self._draw_lobby()
            return

        snap = self.last_snapshot
        units = snap.get("units", [])
        resources = snap.get("resources", {})
        nodes = snap.get("resource_nodes", [])
        sites = snap.get("sites", [])
        tick = snap.get("tick", 0)
        winner = snap.get("winner")

        grid = [[" " for _ in range(MAP_WIDTH)] for _ in range(MAP_HEIGHT)]
        color_grid = [["" for _ in range(MAP_WIDTH)] for _ in range(MAP_HEIGHT)]

        # terrain first: everything else draws over it
        for x, y in self.lakes:
            if 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT:
                grid[y][x] = "~"
                color_grid[y][x] = LAKE_COLOR
        for x, y in self.mountains:
            if 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT:
                grid[y][x] = "^"
                color_grid[y][x] = MOUNTAIN_COLOR

        # projectile tracers, so units/nodes draw over them
        for shot in snap.get("shots", []):
            self._draw_shot(grid, color_grid, shot)

        for site in sites:
            sx, sy = int(round(site["x"])), int(round(site["y"]))
            if 0 <= sx < MAP_WIDTH and 0 <= sy < MAP_HEIGHT:
                grid[sy][sx] = UNIT_CHARS.get(site["type"], "?")
                color_grid[sy][sx] = (
                    PLAYER_COLORS.get(site["owner"], RESET) + DIM)

        for node in nodes:
            nx, ny = int(round(node["x"])), int(round(node["y"]))
            if 0 <= nx < MAP_WIDTH and 0 <= ny < MAP_HEIGHT:
                grid[ny][nx] = "$"
                color_grid[ny][nx] = RESOURCE_COLOR

        for unit in units:
            ux, uy = int(round(unit["x"])), int(round(unit["y"]))
            if 0 <= ux < MAP_WIDTH and 0 <= uy < MAP_HEIGHT:
                owner = unit["owner"]
                is_selected = unit["id"] in selected_ids
                c = PLAYER_COLORS.get(owner, RESET)
                char = UNIT_CHARS.get(unit.get("type", "worker"), "o")
                grid[uy][ux] = char
                if is_selected:
                    color_grid[uy][ux] = PLAYER_HIGHLIGHT.get(owner, "\033[7m")
                else:
                    color_grid[uy][ux] = c

        sidebar = self._build_sidebar(units, nodes, sites,
                                      cursor_x, cursor_y, selected_ids)

        out = []
        out.append("\033[H\033[2J")

        out.append(f"{DIM}{'─' * (MAP_WIDTH + 2)}{RESET}\n")
        for y in range(MAP_HEIGHT):
            out.append(f"{DIM}│{RESET}")
            for x in range(MAP_WIDTH):
                if x == cursor_x and y == cursor_y:
                    if grid[y][x] != " ":
                        out.append(f"\033[7m{color_grid[y][x]}{grid[y][x]}{RESET}")
                    else:
                        out.append(f"\033[7m+{RESET}")
                else:
                    c = color_grid[y][x]
                    if c:
                        out.append(f"{c}{grid[y][x]}{RESET}")
                    else:
                        out.append(grid[y][x])
            out.append(f"{DIM}│{RESET}")
            if y < len(sidebar):
                out.append(f"  {sidebar[y]}")
            out.append("\n")
        out.append(f"{DIM}{'─' * (MAP_WIDTH + 2)}{RESET}\n")

        my_res = resources.get(str(self.player_id), 0)
        my_units = sum(1 for u in units if u["owner"] == self.player_id)
        out.append(f" Tick: {tick}  Resources: {my_res}  Units: {my_units}")
        out.append(f"  Room: {self.room_name}  Ping: {self.latency_ms}ms\n")

        sel_count = len(selected_ids)
        out.append(f" Selected: {sel_count}  Cursor: ({cursor_x},{cursor_y})\n")

        out.append(f" {DIM}[WASD]cursor [Space]select [F]area [E]clear [M]move [X]attack [G]gather [Q]quit{RESET}\n")
        out.append(f" {DIM}build: [B]worker [T]tank [R]range [C]fort [V]wall"
                   f" (fort/wall: selected worker builds; tank/range need a fort near){RESET}\n")

        if winner is not None:
            if winner == self.player_id:
                out.append(f"\n {BOLD}\033[92m*** YOU WIN! ***{RESET}\n")
            elif winner == -1:
                out.append(f"\n {BOLD}\033[93m*** DRAW ***{RESET}\n")
            else:
                out.append(f"\n {BOLD}\033[91m*** YOU LOSE ***{RESET}\n")

        if self.status_message:
            out.append(f" {self.status_message}\n")

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

    def _build_sidebar(self, units, nodes, sites, cursor_x, cursor_y,
                       selected_ids) -> list[str]:
        lines = [f"{BOLD}── CURSOR ──{RESET}"]

        unit = next((u for u in units
                     if int(round(u["x"])) == cursor_x
                     and int(round(u["y"])) == cursor_y), None)
        node = next((n for n in nodes
                     if int(round(n["x"])) == cursor_x
                     and int(round(n["y"])) == cursor_y), None)

        terrain = None
        if (cursor_x, cursor_y) in self.lakes:
            terrain = f"{LAKE_COLOR}Lake — impassable{RESET}"
        elif (cursor_x, cursor_y) in self.mountains:
            terrain = (f"{MOUNTAIN_COLOR}Mountain — 2x sight & fire rate,"
                       f" 1/3 speed{RESET}")

        if unit:
            owner = unit["owner"]
            c = PLAYER_COLORS.get(owner, RESET)
            who = "you" if owner == self.player_id else f"enemy P{owner}"
            utype = unit.get("type", "worker")
            lines.append(f"{c}{utype.capitalize()} #{unit['id']} ({who}){RESET}")
            lines.append(f"HP: {unit['hp']}/{UNIT_STATS[utype]['hp']}")
            lines.append(f"Pos: ({int(round(unit['x']))},{int(round(unit['y']))})")
        elif node:
            lines.append(f"{RESOURCE_COLOR}Resource node #{node['id']}{RESET}")
            lines.append(f"Amount: {node['amount']}")
        elif site := next((s for s in sites
                           if int(round(s["x"])) == cursor_x
                           and int(round(s["y"])) == cursor_y), None):
            owner = site["owner"]
            c = PLAYER_COLORS.get(owner, RESET)
            who = "you" if owner == self.player_id else f"enemy P{owner}"
            pct = 100 * site["progress"] // site["total"]
            lines.append(f"{c}{site['type'].capitalize()} site ({who}){RESET}")
            lines.append(f"Progress: {pct}%")
        elif terrain:
            lines.append(terrain)
        else:
            lines.append(f"{DIM}(nothing here){RESET}")
        if (unit or node) and terrain:
            lines.append(terrain)

        selected = [u for u in units if u["id"] in selected_ids]
        lines.append("")
        lines.append(f"{BOLD}── SELECTED ({len(selected)}) ──{RESET}")
        room = MAP_HEIGHT - len(lines) - 1
        for u in selected[:room]:
            utype = u.get("type", "worker")
            lines.append(f"#{u['id']} {utype}: "
                         f"{u['hp']}/{UNIT_STATS[utype]['hp']} hp"
                         f"  ({int(round(u['x']))},{int(round(u['y']))})")
        if len(selected) > room:
            lines.append(f"{DIM}…+{len(selected) - room} more{RESET}")
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
