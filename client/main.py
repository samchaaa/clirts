import argparse
import asyncio
import sys
import time
import urllib.parse

from client.net import NetworkClient
from client.render import Renderer
from client.input import get_key, setup_terminal, restore_terminal
from shared.messages import MAP_WIDTH, MAP_HEIGHT


class GameClient:
    def __init__(self, server: str, name: str):
        self.name = name
        self.server = server
        self.renderer = Renderer()
        self.net = NetworkClient(self._build_url(server, name), on_message=self._on_message)
        self.cursor_x = MAP_WIDTH // 2
        self.cursor_y = MAP_HEIGHT // 2
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
                self.renderer.draw(self.cursor_x, self.cursor_y, self.selected_ids)
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

        if key == 'W':
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
        elif key == ' ':
            await self._toggle_select()
        elif k == 'm':
            if self.selected_ids:
                await self.net.send({
                    "type": "command",
                    "command": "move",
                    "unit_ids": self.selected_ids,
                    "target": [self.cursor_x, self.cursor_y],
                })
                self.renderer.status_message = f"Move {len(self.selected_ids)} units to ({self.cursor_x},{self.cursor_y})"
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
                else:
                    self.renderer.status_message = "No enemy unit at cursor"
        elif k == 'g':
            if self.selected_ids:
                node = self._node_at_cursor()
                if node:
                    await self.net.send({
                        "type": "command",
                        "command": "gather",
                        "unit_ids": self.selected_ids,
                        "node_id": node["id"],
                    })
                    self.renderer.status_message = f"Gathering from node {node['id']}"
                else:
                    self.renderer.status_message = "No resource node at cursor"
        elif k == 'b':
            await self.net.send({
                "type": "command",
                "command": "build",
                "target": [self.cursor_x, self.cursor_y],
            })
            self.renderer.status_message = f"Building unit at ({self.cursor_x},{self.cursor_y})"
        elif k == 'e':
            self.selected_ids.clear()
            self.renderer.status_message = "Selection cleared"
        elif k == 'f':
            self._select_all_nearby()

    async def _toggle_select(self):
        snap = self.renderer.last_snapshot
        if not snap:
            return
        for unit in snap.get("units", []):
            if unit["owner"] != self.player_id:
                continue
            ux, uy = int(round(unit["x"])), int(round(unit["y"]))
            if ux == self.cursor_x and uy == self.cursor_y:
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
            ux, uy = int(round(unit["x"])), int(round(unit["y"]))
            if ux == self.cursor_x and uy == self.cursor_y:
                return unit
        return None

    def _node_at_cursor(self) -> dict | None:
        snap = self.renderer.last_snapshot
        if not snap:
            return None
        for node in snap.get("resource_nodes", []):
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
            if unit["owner"] != self.player_id:
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
