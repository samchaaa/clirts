import os
import sys

from shared.messages import MAP_WIDTH, MAP_HEIGHT, UNIT_STATS

UNIT_CHARS = {"worker": "o", "tank": "T", "range": "r", "fort": "#"}

PLAYER_COLORS = {
    1: "\033[94m",   # blue
    2: "\033[91m",   # red
    3: "\033[92m",   # green
    4: "\033[93m",   # yellow
}
RESOURCE_COLOR = "\033[33m"
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

    def set_snapshot(self, snapshot: dict):
        self.last_snapshot = snapshot

    def draw(self, cursor_x: int, cursor_y: int, selected_ids: list[int]):
        if self.last_snapshot is None:
            self._draw_lobby()
            return

        snap = self.last_snapshot
        units = snap.get("units", [])
        resources = snap.get("resources", {})
        nodes = snap.get("resource_nodes", [])
        tick = snap.get("tick", 0)
        winner = snap.get("winner")

        grid = [[" " for _ in range(MAP_WIDTH)] for _ in range(MAP_HEIGHT)]
        color_grid = [["" for _ in range(MAP_WIDTH)] for _ in range(MAP_HEIGHT)]

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
                if is_selected:
                    grid[uy][ux] = "@" if char == "o" else char
                    color_grid[uy][ux] = c + BOLD
                else:
                    grid[uy][ux] = char
                    color_grid[uy][ux] = c

        sidebar = self._build_sidebar(units, nodes, cursor_x, cursor_y, selected_ids)

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
        out.append(f" {DIM}build: [B]worker [T]tank [R]range [C]fort (tank/range need a fort nearby){RESET}\n")

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

    def _build_sidebar(self, units, nodes, cursor_x, cursor_y,
                       selected_ids) -> list[str]:
        lines = [f"{BOLD}── CURSOR ──{RESET}"]

        unit = next((u for u in units
                     if int(round(u["x"])) == cursor_x
                     and int(round(u["y"])) == cursor_y), None)
        node = next((n for n in nodes
                     if int(round(n["x"])) == cursor_x
                     and int(round(n["y"])) == cursor_y), None)

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
        else:
            lines.append(f"{DIM}(nothing here){RESET}")

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
