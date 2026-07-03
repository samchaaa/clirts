# MVP Deployment Instructions: CLI RTS Multiplayer Game on Fly.io

## Goal

Deploy a simple real-time multiplayer strategy game that runs in the terminal. Each player runs a CLI client on their own machine. All clients connect over the internet to one authoritative game server hosted on Fly.io.

```text
Player A terminal ─┐
Player B terminal ─┼── wss://your-game.fly.dev ── Game server on Fly.io
Player C terminal ─┘
```

The server owns the real game state. Clients only send commands.

---

## MVP Architecture

```text
CLI client
  ↓ WebSocket
Fly.io public HTTPS/WSS endpoint
  ↓
Authoritative game server
  ↓
In-memory game rooms
```

For the MVP:

```text
- One Fly.io app
- One always-running Machine
- One region
- WebSocket connection
- JSON messages
- No database
- No accounts
- No matchmaking beyond simple rooms
- Game state stored in memory
```

---

## What the Server Does

The server should:

```text
- Listen on 0.0.0.0:$PORT
- Accept WebSocket connections
- Let players create or join rooms
- Assign each player an ID
- Own the authoritative game state
- Run a fixed game loop
- Validate every command
- Broadcast game snapshots to clients
- Handle disconnects
- Expose a simple health check endpoint
```

Example server URL:

```text
wss://your-game.fly.dev/play
```

---

## What the Client Does

Each player runs:

```bash
./rts-client --server wss://your-game.fly.dev/play --name sam
```

The CLI client should:

```text
- Connect to the server via WebSocket
- Render the map in the terminal
- Read keyboard input
- Send player commands to the server
- Receive snapshots from the server
- Redraw the game state
```

The client should not decide where units really are. It should only send intentions.

Good:

```json
{
  "type": "command",
  "command": "move",
  "unit_ids": [1, 2, 3],
  "target": [20, 8]
}
```

Bad:

```json
{
  "unit_id": 1,
  "x": 20,
  "y": 8
}
```

---

## Suggested Repo Layout

```text
cli-rts/
  server/
    main.py
    game.py
    rooms.py
    protocol.py
  client/
    main.py
    render.py
    input.py
    net.py
  shared/
    messages.py
  Dockerfile
  fly.toml
```

---

## Step 1: Build the Local MVP

Before deploying, make it work locally.

Target:

```text
- Start server on localhost:8080
- Open two terminal windows
- Run two clients
- Both clients see the same map
- Each player can move units
- Server broadcasts updated state
```

Example:

```bash
python server/main.py
```

Then in two terminals:

```bash
python client/main.py --server ws://localhost:8080/play --name sam
python client/main.py --server ws://localhost:8080/play --name bob
```

---

## Step 2: Add a Fixed Game Loop

The server should run at a fixed tick rate.

For a CLI RTS, start with:

```text
10 ticks per second
```

Server loop:

```text
while running:
    receive commands
    validate commands
    apply legal commands
    move units
    resolve combat
    update resources
    check victory conditions
    broadcast snapshot
    sleep until next tick
```

The server should broadcast snapshots like:

```json
{
  "type": "snapshot",
  "tick": 120,
  "units": [
    {
      "id": 1,
      "owner": 1,
      "x": 10,
      "y": 8,
      "hp": 100
    }
  ],
  "resources": {
    "1": 500,
    "2": 500
  }
}
```

---

## Step 3: Dockerize the Server

Create a `Dockerfile`.

Example for Python:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY server/ ./server/
COPY shared/ ./shared/
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8080

CMD ["python", "-m", "server.main"]
```

Your server must listen on:

```text
0.0.0.0:$PORT
```

Not:

```text
localhost:8080
```

That matters because Fly.io needs to reach the process inside the container.

---

## Step 4: Create the Fly.io App

From the project directory:

```bash
fly launch
```

Use a nearby region. For Seattle, a good default is:

```text
sea
```

When prompted, you can skip adding a database.

---

## Step 5: Configure `fly.toml`

A simple MVP config:

```toml
app = "cli-rts"
primary_region = "sea"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1

[[vm]]
  size = "shared-cpu-1x"
  memory = "256mb"
```

Important settings:

```text
auto_stop_machines = false
min_machines_running = 1
```

For a real-time game server, you want the server to stay alive while testing. Autostop is annoying for WebSocket games.

---

## Step 6: Deploy

Run:

```bash
fly deploy
```

Then check logs:

```bash
fly logs
```

Check status:

```bash
fly status
```

Your server should now be reachable at:

```text
https://cli-rts.fly.dev
```

Your WebSocket endpoint should be:

```text
wss://cli-rts.fly.dev/play
```

---

## Step 7: Connect Clients

Run clients from separate machines:

```bash
python client/main.py --server wss://cli-rts.fly.dev/play --name sam
```

Another player:

```bash
python client/main.py --server wss://cli-rts.fly.dev/play --name bob
```

At this point, both players should see the same game state.

---

## Step 8: Add Basic Room Support

Minimum commands:

```json
{"type": "create_room", "room": "test"}
{"type": "join_room", "room": "test"}
{"type": "list_rooms"}
```

Server responses:

```json
{"type": "room_created", "room": "test"}
{"type": "room_joined", "room": "test", "player_id": 1}
{"type": "rooms", "rooms": ["test"]}
```

For the MVP, rooms can just be stored in a dictionary:

```text
rooms = {
  "test": GameRoom(...)
}
```

No database needed.

---

## Step 9: Add Heartbeats

WebSocket connections can silently die, so add ping/pong or heartbeat messages.

Client sends:

```json
{"type": "ping", "time": 123456}
```

Server responds:

```json
{"type": "pong", "time": 123456}
```

If the server does not hear from a client for some timeout, disconnect them.

---

## Step 10: Add Basic Safety Limits

Even for a toy public server, add simple limits:

```text
- Max players per room
- Max active rooms
- Max commands per second per player
- Max message size
- Validate all commands
- Reject unknown message types
- Disconnect spammy clients
```

The server should never trust the client.

---

## MVP Definition of Done

The MVP is done when:

```text
- Server is deployed on Fly.io
- Two or more people can connect from different networks
- Players can create or join a room
- Each player sees the same terminal map
- Players can move units
- The server validates commands
- The server broadcasts snapshots
- Disconnects do not crash the server
```

---

## What Not to Add Yet

Do not add these at the beginning:

```text
- Accounts
- Login
- Payment
- Matchmaking
- Ranking
- Database
- Redis
- Kubernetes
- Multi-region
- UDP
- Complex graphics
- Anti-cheat beyond server validation
```

The first goal is simple:

```text
Can two people play the same terminal RTS over the internet?
```

Once that works, improve the game.
