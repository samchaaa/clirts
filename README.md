# clirts

A real-time multiplayer strategy game that runs in your terminal. Each player
runs a CLI client; everyone connects over WebSocket to one authoritative game
server (deployed on Fly.io).

```text
Player A terminal ─┐
Player B terminal ─┼── wss://cli-rts.fly.dev/play ── authoritative server
Player C terminal ─┘
```

The server owns the game state and runs a fixed 10 Hz tick loop; clients only
send intentions (move, attack, build, …) and render snapshots.

## Quick start

Requires Python 3.12+ and `pip install -r requirements.txt` (just `websockets`).

**Play on the public server:**

```bash
python -m client.main --server wss://cli-rts.fly.dev/play --name sam
```

**Run everything locally:**

```bash
# terminal 1 — server (listens on ws://localhost:8080/play)
python -m server.main

# terminals 2 & 3 — clients (default server is localhost)
python -m client.main --name sam
python -m client.main --name rival
```

## Lobby commands

| Command | Effect |
|---|---|
| `/create <name>` | Create a room (and join it) |
| `/join <name>` | Join an existing room |
| `/list` | List open rooms |
| `/quit` | Exit |

Rooms hold up to 4 players. You start with 5 workers in a corner and
500 resources.

## In-game controls

| Key | Action |
|---|---|
| `WASD` / arrows | Move cursor |
| `Shift` + arrow/WASD | Jump cursor to that map edge |
| `Space` | Select / deselect the unit under the cursor |
| `F` | Select all your units near the cursor |
| `E` | Clear selection |
| `M` | Move selected units to cursor |
| `X` | Attack the enemy under the cursor |
| `G` | Gather from the resource node (`$`) under the cursor |
| `B` / `T` / `R` / `C` | Build worker / tank / range / fort at cursor |
| `Q` | Quit |

A sidebar right of the map shows details for whatever is under the cursor
(unit HP, node amount) and a breakdown of your current selection.

## Units and buildings

| Type | Glyph | Cost | HP | Damage | Range | Speed | Notes |
|---|---|---|---|---|---|---|---|
| Worker | `o` | 100 | 100 | 10 | 2 | 1.0 | The only unit that can gather |
| Tank | `T` | 250 | 300 | 30 | 1.5 | 0.4 | Slow, hits hard |
| Range | `r` | 150 | 80 | 8 | 5 | 1.0 | Auto-fires at enemies in radius |
| Fort | `#` | 400 | 500 | 15 | 6 | — | Immobile; auto-fires in radius |

Tanks and ranges must be built within 4 tiles of one of your forts. Auto-fire
never chases: forts and ranges shoot the nearest enemy in radius while
otherwise following orders. Last player with units alive wins.

## Repo layout

```text
server/    authoritative game server (websockets, in-memory rooms)
  main.py      connection handling, heartbeats, health check
  rooms.py     room manager + per-room 10 Hz game loop
  game.py      game state, command validation, movement/combat/gathering
  protocol.py  message dispatch
client/    terminal client
  main.py      input handling and command sending
  render.py    ANSI map + sidebar renderer
  input.py     cross-platform (Windows/Unix) raw key reading
  net.py       WebSocket client
shared/
  messages.py  message types, game constants, UNIT_STATS
```

## Deployment (Fly.io)

Rooms live in process memory, so the app must run on **exactly one machine**:

```bash
fly deploy --ha=false
```

If a second machine ever appears (`fly machines list`), remove it with
`fly scale count 1`. Health check endpoint: `https://cli-rts.fly.dev/health`.

See `spec.md` for the original MVP design notes.
