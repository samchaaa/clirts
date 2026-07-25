import argparse
import asyncio
import sys
import time
import urllib.parse

from client.net import NetworkClient
from client.render import Renderer, unit_tiles
from client.input import get_key, setup_terminal, restore_terminal
from shared.messages import MAP_WIDTH, MAP_HEIGHT, UNIT_STATS, MIN_Z


class GameClient:
    def __init__(self, server: str, name: str):
        self.name = name
        self.server = server
        self.renderer = Renderer()
        self.net = NetworkClient(self._build_url(server, name), on_message=self._on_message)
        self.cursor_x = MAP_WIDTH // 2
        self.cursor_y = MAP_HEIGHT // 2
        self.view_z = 0
        self.selected_ids: list[int] = []
        self.player_id = 0
        self.in_lobby = True
        self.lobby_input = ""
        self.running = True
        self._ping_sent_at = 0

    def _build_url(self, server: str, name: str) -> str:
        sep = "&" if "?" in server else "?"
        return f"{server}{sep}name={urllib.parse.quote(name)}"

    def _on_message(self, msg: dict):
        t = msg.get("type")
        if t == "room_joined":
            self.player_id = msg["player_id"]
            self.renderer.player_id = self.player_id
            self.renderer.room_name = msg.get("room", "")
            self.renderer.set_terrain(msg.get("terrain") or {})
            self.renderer.status_message = ""
            self.in_lobby = False
        elif t == "room_created":
            pass
        elif t == "rooms":
            rooms = msg.get("rooms", [])
            if not rooms:
                self.renderer.status_message = "No rooms. Create one with /create <name>"
            else:
                lines = [f"  {r['name']} ({r['players']} players)" for r in rooms]
                self.renderer.status_message = "Rooms:\n" + "\n".join(lines)
        elif t == "snapshot":
            self.renderer.set_snapshot(msg)
        elif t == "terrain":
            self.renderer.set_terrain(msg.get("terrain") or {})
        elif t == "pong":
            sent = msg.get("time", 0)
            now = int(time.time() * 1000)
            self.renderer.latency_ms = now - sent
        elif t == "error":
            self.renderer.status_message = f"Error: {msg.get('message', '?')}"
        elif t == "player_left":
            self.renderer.status_message = f"Player {msg.get('player_id')} disconnected"

    async def run(self):
        await self.net.connect()
        setup_terminal()
        try:
            net_task = asyncio.create_task(self.net.run())
            input_task = asyncio.create_task(self._input_loop())
            render_task = asyncio.create_task(self._render_loop())
            await asyncio.wait(
                [net_task, input_task, render_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            restore_terminal()
            await self.net.close()
            print("\033[H\033[2J")
            print("Disconnected.")

    async def _render_loop(self):
        while self.running:
            if self.in_lobby:
                self.renderer._draw_lobby()
                sys.stdout.write(self.lobby_input)
                sys.stdout.flush()
            else:
                self.renderer.draw(self.cursor_x, self.cursor_y,
                                   self.selected_ids, self.view_z)
            await asyncio.sleep(0.1)

    async def _input_loop(self):
        while self.running:
            key = get_key()
            if key is None:
                await asyncio.sleep(0.02)
                continue

            if self.in_lobby:
                await self._lobby_key(key)
            else:
                await self._game_key(key)

    async def _lobby_key(self, key: str):
        if key == '\r' or key == '\n':
            await self._process_lobby_command(self.lobby_input.strip())
            self.lobby_input = ""
        elif key == '\x7f' or key == '\x08':
            self.lobby_input = self.lobby_input[:-1]
        elif key == '\x1b' or key.lower() == 'q':
            self.running = False
        elif len(key) == 1 and key.isprintable():
            self.lobby_input += key

    async def _process_lobby_command(self, cmd: str):
        if cmd.startswith("/create "):
            room_name = cmd[8:].strip()
            await self.net.send({"type": "create_room", "room": room_name})
        elif cmd.startswith("/join "):
            room_name = cmd[6:].strip()
            await self.net.send({"type": "join_room", "room": room_name})
        elif cmd == "/list":
            await self.net.send({"type": "list_rooms"})
        elif cmd == "/quit":
            self.running = False

    async def _game_key(self, key: str):
        k = key.lower()
        if k == 'q':
            self.running = False
            return

        if key in ('C-w', 'C-s', 'C-a', 'C-d'):
            # ctrl+arrow: jump 10 tiles, stopping at the map edge
            dx, dy = {'C-w': (0, -1), 'C-s': (0, 1),
                      'C-a': (-1, 0), 'C-d': (1, 0)}[key]
            self.cursor_x = max(0, min(MAP_WIDTH - 1, self.cursor_x + dx * 10))
            self.cursor_y = max(0, min(MAP_HEIGHT - 1, self.cursor_y + dy * 10))
        elif key == 'W':
            self.cursor_y = 0
        elif key == 'S':
            self.cursor_y = MAP_HEIGHT - 1
        elif key == 'A':
            self.cursor_x = 0
        elif key == 'D':
            self.cursor_x = MAP_WIDTH - 1
        elif k == 'w':
            self.cursor_y = max(0, self.cursor_y - 1)
        elif k == 's':
            self.cursor_y = min(MAP_HEIGHT - 1, self.cursor_y + 1)
        elif k == 'a':
            self.cursor_x = max(0, self.cursor_x - 1)
        elif k == 'd':
            self.cursor_x = min(MAP_WIDTH - 1, self.cursor_x + 1)
        elif key == '[':
            self.view_z = max(MIN_Z, self.view_z - 1)
            self.renderer.status_message = f"Viewing level z{self.view_z}"
        elif key == ']':
            self.view_z = min(0, self.view_z + 1)
            self.renderer.status_message = f"Viewing level z{self.view_z}"
        elif key == ' ':
            await self._toggle_select()
        elif k == 'm':
            if self.selected_ids:
                await self.net.send({
                    "type": "command",
                    "command": "move",
                    "unit_ids": self.selected_ids,
                    "target": [self.cursor_x, self.cursor_y],
                    "z": self.view_z,
                })
                self.renderer.status_message = (
                    f"Move {len(self.selected_ids)} units to "
                    f"({self.cursor_x},{self.cursor_y}) z{self.view_z}")
        elif k == 'x':
            if self.selected_ids:
                target = self._unit_at_cursor()
                if target and target["owner"] != self.player_id:
                    await self.net.send({
                        "type": "command",
                        "command": "attack",
                        "unit_ids": self.selected_ids,
                        "target_id": target["id"],
                    })
                    self.renderer.status_message = f"Attacking unit {target['id']}"
                    self.renderer.add_log(
                        f"Attacking enemy {target.get('type', 'unit')}"
                        f" #{target['id']}")
                else:
                    self.renderer.status_message = "No enemy unit at cursor"
        elif k == 'g':
            if self.selected_ids:
                node = self._node_at_cursor()
                unit = self._unit_at_cursor()
                if node:
                    await self.net.send({
                        "type": "command",
                        "command": "gather",
                        "unit_ids": self.selected_ids,
                        "node_id": node["id"],
                    })
                    self.renderer.status_message = f"Gathering from node {node['id']}"
                elif unit and unit.get("type") == "farm":
                    await self.net.send({
                        "type": "command",
                        "command": "farm",
                        "unit_ids": self.selected_ids,
                        "farm_id": unit["id"],
                    })
                    self.renderer.status_message = (
                        f"Workers heading to farm #{unit['id']}")
                else:
                    self.renderer.status_message = (
                        "No resource node or friendly farm at cursor")
        elif k in ('n', 'z', 'u'):
            await self._dig_key(k)
        elif k in ('b', 't', 'r'):
            unit_type = {'b': 'worker', 't': 'tank', 'r': 'range'}[k]
            await self.net.send({
                "type": "command",
                "command": "build",
                "unit_type": unit_type,
                "target": [self.cursor_x, self.cursor_y],
                "z": self.view_z,
            })
            cost = UNIT_STATS[unit_type]["cost"]
            self.renderer.status_message = (
                f"Building {unit_type} ({cost}) at ({self.cursor_x},{self.cursor_y})")
        elif k == 'l':
            await self._laser_key()
        elif k in ('c', 'v', 'p', 'o'):
            unit_type = {'c': 'fort', 'v': 'wall', 'p': 'farm',
                         'o': 'laser'}[k]
            if unit_type == "laser":
                snap = self.renderer.last_snapshot or {}
                info = (snap.get("laser") or {}).get(str(self.player_id)) or {}
                if not info.get("unlocked"):
                    self.renderer.status_message = (
                        "Space laser locked: build every other building type,"
                        " reach z-3, and exhaust all surface nodes")
                    return
            if not self.selected_ids:
                self.renderer.status_message = (
                    f"Select a worker to build the {unit_type}")
            else:
                await self.net.send({
                    "type": "command",
                    "command": "build",
                    "unit_type": unit_type,
                    "unit_ids": self.selected_ids,
                    "target": [self.cursor_x, self.cursor_y],
                    "z": self.view_z,
                })
                cost = UNIT_STATS[unit_type]["cost"]
                self.renderer.status_message = (
                    f"Worker heading to build {unit_type} ({cost})"
                    f" at ({self.cursor_x},{self.cursor_y})")
        elif k == 'e':
            self.selected_ids.clear()
            self.renderer.status_message = "Selection cleared"
        elif k == 'f':
            self._select_all_nearby()

    async def _laser_key(self):
        snap = self.renderer.last_snapshot or {}
        info = (snap.get("laser") or {}).get(str(self.player_id)) or {}
        if info.get("active"):
            msg = f"Steering laser to ({self.cursor_x},{self.cursor_y})"
        elif info.get("charges"):
            msg = f"SPACE LASER FIRED at ({self.cursor_x},{self.cursor_y})"
        elif info.get("unlocked"):
            self.renderer.status_message = (
                "No charged space laser — build one with [O] (500)")
            return
        else:
            self.renderer.status_message = (
                "Space laser locked: build every other building type, reach"
                " z-3, and exhaust all surface nodes")
            return
        await self.net.send({
            "type": "command",
            "command": "laser",
            "target": [self.cursor_x, self.cursor_y],
        })
        self.renderer.status_message = msg

    async def _dig_key(self, k: str):
        if not self.selected_ids:
            self.renderer.status_message = "Select units first"
            return
        command = {'n': 'dig', 'z': 'dig_down', 'u': 'dig_up'}[k]
        label = {'n': "Mining", 'z': "Digging down",
                 'u': "Digging up"}[k]
        await self.net.send({
            "type": "command",
            "command": command,
            "unit_ids": self.selected_ids,
            "target": [self.cursor_x, self.cursor_y],
            "z": self.view_z,
        })
        self.renderer.status_message = (
            f"{label} at ({self.cursor_x},{self.cursor_y}) z{self.view_z}")

    async def _toggle_select(self):
        snap = self.renderer.last_snapshot
        if not snap:
            return
        for unit in snap.get("units", []):
            if (unit["owner"] != self.player_id
                    or unit.get("z", 0) != self.view_z):
                continue
            if (self.cursor_x, self.cursor_y) in unit_tiles(unit):
                uid = unit["id"]
                if uid in self.selected_ids:
                    self.selected_ids.remove(uid)
                else:
                    self.selected_ids.append(uid)
                return
        self.renderer.status_message = "No friendly unit here"

    def _unit_at_cursor(self) -> dict | None:
        snap = self.renderer.last_snapshot
        if not snap:
            return None
        for unit in snap.get("units", []):
            if unit.get("z", 0) != self.view_z:
                continue
            if (self.cursor_x, self.cursor_y) in unit_tiles(unit):
                return unit
        return None

    def _node_at_cursor(self) -> dict | None:
        snap = self.renderer.last_snapshot
        if not snap:
            return None
        for node in snap.get("resource_nodes", []):
            if node.get("z", 0) != self.view_z:
                continue
            nx, ny = int(round(node["x"])), int(round(node["y"]))
            if nx == self.cursor_x and ny == self.cursor_y:
                return node
        return None

    def _select_all_nearby(self):
        snap = self.renderer.last_snapshot
        if not snap:
            return
        self.selected_ids.clear()
        for unit in snap.get("units", []):
            if (unit["owner"] != self.player_id
                    or unit.get("z", 0) != self.view_z):
                continue
            ux, uy = int(round(unit["x"])), int(round(unit["y"]))
            if abs(ux - self.cursor_x) <= 3 and abs(uy - self.cursor_y) <= 3:
                self.selected_ids.append(unit["id"])
        self.renderer.status_message = f"Selected {len(self.selected_ids)} nearby units"


def main():
    parser = argparse.ArgumentParser(description="CLI RTS Client")
    parser.add_argument("--server", default="ws://localhost:8080/play",
                        help="Server WebSocket URL")
    parser.add_argument("--name", default="player",
                        help="Player name")
    args = parser.parse_args()

    client = GameClient(args.server, args.name)
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    main()
