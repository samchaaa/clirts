import asyncio
import time

from shared.messages import (
    TICK_RATE, MAX_PLAYERS_PER_ROOM, MAX_ROOMS, MsgType, encode,
)
from server.game import GameState


class Player:
    def __init__(self, player_id: int, name: str, websocket):
        self.id = player_id
        self.name = name
        self.ws = websocket
        self.last_heartbeat = time.time()
        self.commands_this_tick: int = 0


class GameRoom:
    def __init__(self, name: str):
        self.name = name
        self.players: dict[int, Player] = {}
        self.state = GameState()
        self.next_player_id = 1
        self._task: asyncio.Task | None = None
        self.state.generate_terrain()
        self.state.spawn_resource_nodes()

    def add_player(self, name: str, websocket) -> Player | None:
        if len(self.players) >= MAX_PLAYERS_PER_ROOM:
            return None
        pid = self.next_player_id
        self.next_player_id += 1
        player = Player(pid, name, websocket)
        self.players[pid] = player
        self.state.add_player(pid)
        return player

    def remove_player(self, player_id: int):
        self.players.pop(player_id, None)
        self.state.remove_player(player_id)

    def is_empty(self) -> bool:
        return len(self.players) == 0

    async def broadcast(self, msg: dict):
        data = encode(msg)
        disconnected = []
        for player in self.players.values():
            try:
                await player.ws.send(data)
            except Exception:
                disconnected.append(player.id)
        for pid in disconnected:
            self.remove_player(pid)

    def start_loop(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._game_loop())

    def stop_loop(self):
        if self._task and not self._task.done():
            self._task.cancel()

    async def _game_loop(self):
        interval = 1.0 / TICK_RATE
        try:
            while True:
                self.state.tick_update()
                snapshot = self.state.snapshot()
                await self.broadcast(snapshot)
                for p in self.players.values():
                    p.commands_this_tick = 0
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass


class RoomManager:
    def __init__(self):
        self.rooms: dict[str, GameRoom] = {}

    def create_room(self, name: str) -> GameRoom | None:
        if name in self.rooms or len(self.rooms) >= MAX_ROOMS:
            return None
        room = GameRoom(name)
        self.rooms[name] = room
        return room

    def get_room(self, name: str) -> GameRoom | None:
        return self.rooms.get(name)

    def list_rooms(self) -> list[dict]:
        return [
            {"name": r.name, "players": len(r.players)}
            for r in self.rooms.values()
        ]

    def cleanup_empty(self):
        empty = [name for name, room in self.rooms.items() if room.is_empty()]
        for name in empty:
            room = self.rooms.pop(name)
            room.stop_loop()
