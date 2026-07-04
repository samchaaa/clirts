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

> The public server runs whatever was last deployed from `main` — client and
> server must be on matching versions for new unit types to work.

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
500 resources. The 120x40 map is generated per room with random terrain
(spawn corners are always clear).

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
| `B` / `T` / `R` | Build worker / tank / range at cursor |
| `C` / `V` | Order the selected worker to build a fort / wall at cursor |
| `Q` | Quit |

A sidebar right of the map shows details for whatever is under the cursor
(unit HP, node amount) and a breakdown of your current selection.

**Map legend:** `o` worker · `T` tank · `r` range · `#` fort · `=` wall ·
`$` resource node · `~` lake · `^` mountain · `@`/bold = selected ·
`* x` projectile tracer + impact · colors identify players
(blue/red/green/yellow by join order).

## Terrain

- **Lakes** (`~`) are impassable: move/build orders onto them are rejected
  and units path around the shore.
- **Mountains** (`^`) slow units to half speed, but double their sight and
  shot range and double their rate of fire while standing on them. You can
  build on mountains — a fort on a peak shoots at range 12, every 2.5 ticks.

## Units and buildings

| Type | Glyph | Cost | HP | Damage | Range | Speed | Notes |
|---|---|---|---|---|---|---|---|
| Worker | `o` | 100 | 100 | 10 | 2 | 1.0 | The only unit that can gather and build |
| Tank | `T` | 250 | 300 | 30 | 1.5 | 0.4 | Slow, hits hard |
| Range | `r` | 150 | 80 | 20 | 5 | 1.0 | Auto-fires in radius, every 3rd tick |
| Fort | `#` | 400 | 500 | 25 | 6 | — | Building; auto-fires in radius, every 5th tick |
| Wall | `=` | 35 | 200 | — | — | — | Building; blocks all unit movement |

Buildings (fort, wall) are constructed on site: select a worker, put the
cursor where you want the building, and press `C`/`V` — the worker walks
there and builds it (forts take 6 s of construction, walls 1 s; the site
shows as a dim glyph with progress in the sidebar). Cost is refunded if the
order is cancelled or the worker dies on the way. Tanks and ranges must be built within 4 tiles of one of
your forts.

Fort and range shots draw projectile tracers. Auto-fire never chases and
skips walls; shots pass over walls (no line-of-sight), so walls stop melee
but not ranged fire. Break through enemy walls by attacking them with `X`.
Last player with units alive wins.

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
