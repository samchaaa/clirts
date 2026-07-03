import asyncio
import json
import time

import websockets


class NetworkClient:
    def __init__(self, server_url: str, on_message=None):
        self.server_url = server_url
        self.ws = None
        self.on_message = on_message
        self.connected = False
        self._send_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        self.ws = await websockets.connect(self.server_url)
        self.connected = True

    async def send(self, msg: dict):
        await self._send_queue.put(json.dumps(msg))

    async def run(self):
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._reader())
                tg.create_task(self._writer())
                tg.create_task(self._heartbeat())
        except* websockets.ConnectionClosed:
            self.connected = False
        except* Exception:
            self.connected = False

    async def _reader(self):
        async for raw in self.ws:
            if self.on_message:
                try:
                    msg = json.loads(raw)
                    self.on_message(msg)
                except json.JSONDecodeError:
                    pass

    async def _writer(self):
        while self.connected:
            data = await self._send_queue.get()
            await self.ws.send(data)

    async def _heartbeat(self):
        while self.connected:
            await asyncio.sleep(5)
            await self._send_queue.put(json.dumps({
                "type": "ping",
                "time": int(time.time() * 1000),
            }))

    async def close(self):
        self.connected = False
        if self.ws:
            await self.ws.close()
