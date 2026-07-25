# clirts

A real-time multiplayer strategy game that runs in your terminal. Each player
runs a CLI client; everyone connects over WebSocket to one authoritative game
server (deployed on Fly.io). Fight on the surface — or dig Dwarf
Fortress-style z-levels beneath it, mine buried riches, and drain lakes onto
your enemies.

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
| `Ctrl` + arrow | Jump cursor 10 tiles that way (stops at the edge) |
| `Space` | Select / deselect the unit under the cursor |
| `F` | Select all your units near the cursor |
| `E` | Clear selection |
| `M` | Move selected units to cursor (auto-routes through tunnels if the cursor is on another z-level) |
| `X` | Attack the enemy under the cursor |
| `G` | Gather from the resource node (`$`) under the cursor |
| `B` / `T` / `R` | Build worker / tank / range at cursor |
| `C` / `V` | Order the selected worker to build a fort / wall at cursor |
| `[` / `]` | View one z-level down / up (z0 to z-3) |
| `N` | Order a selected worker to mine out the solid tile at cursor |
| `Z` | Tunnel down at cursor (or descend an existing tunnel `↓`) |
| `U` | Tunnel up at cursor (or ascend an existing tunnel `↑`) |
| `Q` | Quit |

A sidebar right of the map shows details for whatever is under the cursor
(unit HP, node amount), a breakdown of your current selection, and a rolling
event log (kills, attacks, floods, finished digs, depleted nodes, …).

**Map legend:** `o` worker · `T` tank · `r` range · `#` fort · `=` wall ·
`$` resource node · `~` lake / flood water · `^` mountain (surface) or solid
rock (underground, dim) · `↓` tunnel down · `↑` tunnel up · `↕` both ·
highlighted (black on your color) = selected ·
`* x` projectile tracer + impact · colors identify players
(blue/red/green/yellow by join order).

## Terrain

- **Lakes** (`~`) are impassable: move/build orders onto them are rejected
  and units path around the shore.
- **Mountains** (`^`) slow units to one-third speed, but double their sight
  and shot range and double their rate of fire while standing on them. You
  can build on mountains — a fort on a peak shoots at range 12, every
  2.5 ticks. (Mountain bonuses apply on the surface only.) Workers can
  **mine mountains flat** with `N`, turning the tile into open ground.

## Z-levels (digging)

Below the surface (z0) are three underground levels — z-1, z-2, z-3 — made
of solid rock (dim `^`; tiles marked for mining show `▒` in the
digger's color). Press `[` / `]` to view a level down / up; commands
apply to the level you are viewing, and only units on that level respond.

- **Tunnel down** (`Z`): a selected worker walks to the cursor tile and digs
  a tunnel to the level below, then drops through it.
- **Mine** (`N`): a selected worker digs out the solid tile at the cursor
  (on the surface, this levels a mountain tile instead). Press it over
  several tiles to queue jobs; with multiple workers selected, jobs spread
  across the ones with the shortest queues.
- **Tunnel up** (`U`): a worker underground digs a tunnel to the level above
  and climbs through it.
- A tunnel connects two levels and is passable **both ways**, no matter
  which direction it was dug from. It renders as `↓` on its upper level and
  `↑` on its lower one (`↕` where two shafts share a tile).
- **Flooding**: digging up into a lakebed breaches it — the lake drains
  tile-for-tile into the tunnels. Water (`~`) flood-fills outward from the
  breach and is permanently impassable; **units caught in it drown**, and
  **tunnels on flooded tiles are destroyed** (including the breach tunnel
  itself — the event log reports each loss). The breaching worker climbs
  out first, onto the freshly drained lakebed. A
  small lake over a big tunnel network drains completely (leaving walkable
  lakebed on the surface); a big lake over a small network floods it
  entirely and keeps the rest of its water. Breaching an already-flooded
  level from below cascades the same way. Handle with care — or tunnel
  under a lake next to the enemy's mine and let it loose.
- **Underground lakes**: each underground level also has water-filled
  caverns (`~`). Some resource nodes lie submerged in them and stay hidden
  until drained. Drain a lake by tunneling on the level *below* it and
  digging up (`U`) into its floor — the water falls through, flooding the
  level below tile-for-tile, and the drained cavern (and any `$` in it)
  becomes walkable. Dropping through a tunnel into water drowns the unit,
  and z-3 lakes can't be drained (there's nothing below).
- Tunnels are **neutral**: any unit — including the enemy's — can press `Z`
  or `U` on one to use it.
- **Cross-level move orders auto-route**: select units, view another level
  with `[` / `]`, and press `M` — units not already on that level find the
  cheapest chain of tunnels to it and walk there on their own, hugging the
  dug passages. If no walkable chain of tunnels connects the two levels the
  order is rejected. A tunnel that floods away mid-route triggers a replan.
- Underground resource nodes are richer the deeper you go (z-1: 800 each,
  z-2: 1500, z-3: 2500) but stay **hidden until you mine the tile** holding
  them.
- Combat, auto-fire, and building are per-level; units never see or shoot
  across levels. Forts and walls work underground too.

## Units and buildings

| Type | Glyph | Cost | HP | Damage | Range | Speed | Notes |
|---|---|---|---|---|---|---|---|
| Worker | `o` | 100 | 100 | 10 | 2 | 1.0 | The only unit that can gather and build |
| Tank | `T` | 250 | 300 | 30 | 1.5 | 0.4 | Slow, hits hard |
| Range | `r` | 150 | 80 | 20 | 5 | 1.0 | Auto-fires in radius, every 3.75 ticks |
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
  game.py      game state, command validation, movement/combat/digging
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

### Self-hosting your own server

The `app` name in `fly.toml` (`cli-rts`) belongs to the public instance, so
to run your own you need your own app name:

```bash
# 1. Pick a name and create the app under your Fly account
fly apps create my-clirts

# 2. Point fly.toml at it: change the app line to
#      app = 'my-clirts'
#    (or skip editing and pass --app to every fly command instead)

# 3. Deploy — one machine only, rooms live in process memory
fly deploy --ha=false
```

Then connect clients to your instance:

```bash
python -m client.main --server wss://my-clirts.fly.dev/play --name sam
```

No secrets or extra configuration are required — the server reads only
`PORT` (set in the Dockerfile) and keeps all state in memory, so the free
`shared-cpu-1x` / 256 MB VM in `fly.toml` is enough. Verify it's up at
`https://my-clirts.fly.dev/health`.

See `spec.md` for the original MVP design notes.
