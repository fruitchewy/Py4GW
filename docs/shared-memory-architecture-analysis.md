# Shared Memory Architecture: Deep Dive & Improvement Options

## Table of Contents

1. [Current Architecture Summary](#current-architecture-summary)
2. [Identified Instability Sources](#identified-instability-sources)
3. [Options Overview](#options-overview)
4. [Option 1: Incremental Improvements (Same API)](#option-1-incremental-improvements-same-api)
5. [Option 2: Rearchitecture (Compatible Concepts, New API)](#option-2-rearchitecture-compatible-concepts-new-api)
6. [Option 3: Alternative Approaches](#option-3-alternative-approaches)
7. [Comparison Matrix](#comparison-matrix)

---

## Current Architecture Summary

### What It Does

The shared memory system (`Py4GWSharedMemoryManager`) is the backbone of multi-client
coordination for Py4GW. It provides three services over a single POSIX shared memory segment
named `"Py4GW_Shared_Mem"`:

1. **State Replication** — Each game client publishes its own player/hero/pet state (position,
   health, buffs, skills, map, party, etc.) into a slot so other clients can read it.
2. **Message Passing** — A 64-slot message queue lets any client send typed commands
   (`SharedCommandType`) to any other client by email address.
3. **HeroAI Options** — A per-slot config block controls hero AI behavior flags (following,
   avoidance, skill toggles, flag positions).

### Memory Layout

```
AllAccounts (single ctypes Structure, _pack_=1)
├── AccountData[64]         ~  large per-slot blob
│   ├── identity fields     (email, name, character, slot metadata)
│   ├── legacy flat fields  (PlayerHP, PlayerPosX, etc.)
│   ├── PlayerData          (nested PlayerStruct)
│   │   ├── RankData, FactionsData, TitlesData, QuestsData
│   │   ├── ExperienceData, SkillbarData, AttributesData[11]
│   │   ├── BuffData[240], MissionData, UnlockedSkills[108]
│   │   ├── AgentData       (50+ fields of agent state)
│   │   └── AvailableCharacters[20]
│   ├── PlayerBuffs[240]    (legacy duplicate of BuffData)
│   └── LastUpdated         (uint ms timestamp for liveness)
│
├── SharedMessage[64]       ~  one queue slot per message
│   ├── SenderEmail, ReceiverEmail
│   ├── Command, Params[4], ExtraData[4]
│   └── Active, Running, Timestamp
│
└── HeroAIOptions[64]
    ├── Following, Avoidance, Looting, Targeting, Combat
    ├── Skills[8]
    └── IsFlagged, FlagPos, FlagFacingAngle
```

Total size is governed by `sizeof(AllAccounts)` and is created once, then attached by all
subsequent clients. Singleton pattern ensures one manager per Python process.

### Update Pump

The `Environment Upkeeper` widget (`Widgets/System/Environment Upkeeper.py`) calls these
every frame:

```python
GLOBAL_CACHE.ShMem.SetPlayerData(account_email)
GLOBAL_CACHE.ShMem.SetHeroesData()
GLOBAL_CACHE.ShMem.SetPetData()
```

Inside `SetPlayerData`, the manager:
1. Calls `_updatecache()` which refreshes cached native object references (`agent_instance`,
   `party_instance`, `effects_instance`, `_title_instances`, `quest_instance`) via throttled
   timers (63ms and 150ms).
2. Writes identity/map/party fields.
3. Calls a dozen `_set_*_data(index)` helpers that each pull data from the native game API
   (`Agent.GetXYZ()`, `Agent.GetHealth()`, `Effects.GetEffects()`, etc.) and write it
   field-by-field into the ctypes structure.

### Synchronization Model

- **No locks or mutexes.** The only synchronization primitive is a timestamp-based liveness
  check: `(now - LastUpdated) < 500ms` determines whether a slot is "active."
- Readers see whatever bytes are currently in the buffer. There is no read barrier, version
  counter, or double-buffering.
- The `SharedLockManager` (used by the CustomBehaviors subsystem) provides cooperative
  application-level locks with TTL, but these are opt-in and not used by the core system.

### Consumers

| Consumer | How it uses shared memory |
|----------|--------------------------|
| HeroAI (`HeroAI/`) | Reads all party member data, reads/writes HeroAI options |
| MultiBoxing (`Sources/frenkeyLib/MultiBoxing/`) | Sends `SharedCommandType` messages for window layout, settings reload |
| CustomBehaviors (`Sources/oazix/CustomBehaviors/`) | Separate shared memory segment for party config + SharedLockManager |
| Bot scripts (VoltaicSpear, Halloween, YAVB, etc.) | Send messages for skill use, looting, interaction |
| Messaging widget (`Widgets/System/Messaging.py`) | Preview/finish message lifecycle |
| SharedMem Monitor (`Widgets/Coding/Debug/`) | Read-only debug display |
| aC_Scripts (`Sources/aC_Scripts/`) | Separate `SharedState` implementation with file-based locking |

### Arbitrary User-Defined Shared Memory Segments

Beyond the core `Py4GW_Shared_Mem` segment, **widgets and bots can reserve their own
independent shared memory blocks**. This is an established pattern in the codebase — not an
edge case. Known examples:

| Segment Name | Creator | Size | Purpose | Sync Strategy |
|---|---|---|---|---|
| `CustomBehaviorWidgetMemoryManager` | `Sources/oazix/CustomBehaviors/` | ~4.3KB | Party follow/flag/teambuild config + cooperative locks | Application-level locks with TTL |
| `mywidgets_sync` (default, overridable) | `Sources/aC_Scripts/aC_api/shared_state_ctypes.py` | ~296B | Dialog sync, multi-client widget state | `fcntl.flock()` file-based locking |
| `GW_NEXUS_SMA` | Legacy (`Legacy code and tests/`) | Variable | Agent/player data sync | None (deprecated) |

Each follows the same singleton + create-or-attach pattern as the core system:

```python
try:
    self.shm = shared_memory.SharedMemory(name=custom_name)
except FileNotFoundError:
    self.shm = shared_memory.SharedMemory(name=custom_name, create=True, size=size)
```

Key characteristics of this pattern:
- **No central registry.** There is no system-level catalog of which segments exist, who
  owns them, or what schema they use. Each segment is discovered by name convention.
- **No shared lifecycle management.** None of the custom segments call `shm.unlink()`.
  Segments persist until the OS cleans them up on system restart.
- **Inconsistent synchronization.** The core system uses timestamp-based liveness. Custom-
  Behaviors uses application-level TTL locks in shared memory. aC_Scripts uses OS file
  locks. Each segment has its own (or no) approach.
- **Independent schemas.** Each segment defines its own ctypes structures. There is no
  versioning or compatibility checking — if a widget is updated to change its struct layout,
  a stale process attached to the old layout will read garbage.
- **Any widget or bot can create new ones.** The pattern is simple enough that new scripts
  naturally create their own segments when they need cross-client coordination beyond what
  the core message system provides.

---

## Identified Instability Sources

This section catalogs the architectural patterns that contribute to actual instability —
meaning crashes, lost messages, stale slot accumulation, or behavioral errors that persist
beyond a single frame.

**Note on torn reads:** Cross-process readers can see a mix of fields from two consecutive
frames (e.g., position from frame N, health from frame N+1) because writes are not atomic.
This is a known property of the architecture and is **not considered an instability source**.
Sub-frame data inconsistency is acceptable given the update rate (~60 FPS) and the tolerance
of downstream consumers (HeroAI, bots). The live-view behavior of reader accessors — where
each field access reads the latest value from shared memory — is by design and is actually
desirable: readers always get the freshest data.

### I-1. Half-Valid Frames From Mid-Batch Agent Invalidation

The Agent API already handles stale pointers — `Agent.GetHealth(agent_id)` returns `0.0` if
the agent is invalid, not a crash. **The shared memory struct never holds native pointers.**
It holds materialized Python values (`c_float`, `c_uint`, `c_bool`).

The actual problem is **batch atomicity**: `_set_agent_data` makes 60+ individual API calls
in sequence, and if the agent becomes invalid partway through, some calls return real data
and some return safe defaults. The batch is committed anyway, producing a half-valid frame:

```
agent_data.Health = Agent.GetHealth(agent_id)     # → 0.75 (agent still valid)
agent_data.MaxHealth = Agent.GetMaxHealth(agent_id) # → 480
# ── agent becomes invalid here (map transition, despawn) ──
agent_data.XYZ[0] = Agent.GetXYZ(agent_id)[0]    # → 0.0 (returns default)
agent_data.Is_Alive = Agent.IsAlive(agent_id)     # → False (returns default)
```

Result: slot contains Health=0.75, MaxHealth=480, Position=(0,0,0), Is_Alive=False — a
character that appears to have health but is at the origin and flagged dead. HeroAI reads
this and may act on the contradictory state.

Additionally, each of the 60+ calls independently re-validates the agent through the full
`IsValid() → GetAgentByID() → GetLivingAgentByID()` chain — 60+ redundant native lookups
per frame for the same agent_id.

See [Option 1A Developer Commentary](option-1a-developer-commentary.md) for the full trace.

### I-2. Cached Native Context Objects

`SharedMemory.py` caches `self.agent_instance`, `self.effects_instance`,
`self.party_instance`, and `self._title_instances` — these are live ctypes structs pointing
into native game memory, held across frames. `self.agent_instance` is refreshed on a 63ms
throttle timer; the others on 150ms timers or never.

The validation at lines 1015-1018 (`self.agent_instance.GetAsAgentLiving()`) itself
dereferences native memory — if the game freed that agent between frames, this check can
crash before it gets a chance to null-out the reference.

The hero/pet write paths (`SetHeroData`, `SetPetData`) also fetch fresh `AgentStruct`
references via `Agent.GetAgentByID()` and then read fields like `.pos.x`,
`.rotation_angle` directly from native memory without re-checking validity between reads.

### I-3. Disabled Timeout Cleanup

`UpdateTimeouts()` (line 2017-2028) has an immediate `return` at line 2018, making the
entire timeout cleanup dead code. Stale slots from crashed clients accumulate and are only
reclaimed opportunistically by `FindEmptySlot()` when it runs out of clean slots.

### I-4. Message Queue Races

The message system uses a scan-for-first-inactive-slot pattern with no CAS (compare-and-swap)
or lock. Two senders targeting different receivers can race on the same message slot. The
`Active`/`Running` flags are separate `c_bool` fields — not an atomic state word — so a
transient inconsistency during `MarkMessageAsFinished` could cause a message to be picked
up twice or lost.

### I-7. Unmanaged Segment Proliferation

Widgets and bots create arbitrary shared memory segments with no central registry, no schema
versioning, and no lifecycle management. This creates several compounding issues:

- **Orphaned segments.** No segment calls `shm.unlink()`, so crashed or updated processes
  leave stale segments in the OS. A new process attaches to the old segment and interprets
  bytes under a potentially different struct layout.
- **Schema drift.** If `CustomBehaviorWidgetStruct` adds a field, any client still running
  the old code reads past the end of the old layout into whatever follows in memory. There
  is no version header or size check.
- **Invisible coupling.** Two subsystems that independently create segments with the same
  name will silently collide. There is no namespace or prefix convention enforced at the
  framework level.
- **No observability.** The SharedMem Monitor widget only knows about the core
  `Py4GW_Shared_Mem` segment. Custom segments are invisible to debugging tools unless
  each one builds its own monitor.

---

## Options Overview

Before diving into the details, here is a brief summary of each option:

**Option 1 — Incremental improvements (same API):**

| Option | Approach |
|--------|----------|
| **1A** | All-or-nothing batch writes: collect all native data first, skip the entire write if the agent goes invalid mid-batch (prevents half-valid frames) |
| **1C** | Re-enable the disabled `UpdateTimeouts()` to clean up stale slots from crashed clients |
| **1E** | Replace separate Active/Running bools with a single atomic state word for messages |
| **1F** | Add a lightweight segment registry so custom segments are discoverable and version-checked |

*Removed from consideration:* **1B** (seqlock generation counter) and **1D** (return clones)
addressed torn reads, which are acceptable sub-frame noise — not an instability source.

**Option 2 — Rearchitecture (compatible concepts, new API):**

| Option | Approach |
|--------|----------|
| **2B** | Replace the flat message array with per-receiver ring buffers and sequence-number cursors |
| **2C** | Split the monolithic segment into three independent segments (state, commands, config) with per-segment sync strategies |
| **2D** | Wrap shared memory behind a typed accessor layer that returns immutable Python dataclass snapshots |
| **2E** | Provide a managed segment factory so widgets/bots get lifecycle, versioning, and registry for free |

*Removed from consideration:* **2A** (double-buffered slots) existed solely to eliminate torn
reads. Since sub-frame inconsistency is acceptable, the 2x memory cost has no justification.

**Option 3 — Alternative approaches:**

| Option | Approach |
|--------|----------|
| **3A** | Use SQLite in WAL mode as the shared state store — ACID transactions, crash recovery, SQL queries |
| **3B** | Use a plain mmap'd file with OS advisory locking — crash-persistent, inspectable with standard tools |
| **3C** | Keep shmem for high-frequency state but move commands to localhost UDP multicast for natural pub/sub |
| **3D** | Use named pipes / Unix domain sockets for reliable ordered per-receiver command delivery |

---

## Option 1: Incremental Improvements (Same API)

These changes preserve the existing `Py4GWSharedMemoryManager` API surface. Callers would
not need to change their code.

### 1A. All-or-Nothing Batch Writes

**Problem addressed:** I-1 (half-valid frames from mid-batch agent invalidation)

The Agent API already materializes values and returns safe defaults when agents are invalid.
The shared memory struct never holds native pointers. The real problem is that 60+ individual
API calls run in sequence with no all-or-nothing boundary — if the agent goes invalid
midway, the batch is committed anyway with a mix of real values and zeroed defaults.

The fix is batch discipline: **gate the entire batch on a single validity check, and discard
the batch if any read fails.** Additionally, collapse the 60+ redundant per-call agent
lookups into a single lookup.

```python
def _set_agent_data(index):
    agent_id = Player.GetAgentID()
    living = Agent.GetLivingAgentByID(agent_id)
    if living is None:
        return  # skip entirely — keep last good frame in shmem

    try:
        # All reads from the same struct — one lookup, not 60
        health = living.hp
        max_health = living.max_hp
        energy = living.energy
        xyz = (living.pos.x, living.pos.y, living.z)
        is_bleeding = living.is_bleeding
        # ... etc ...
    except Exception:
        return  # agent went invalid mid-batch — discard

    # Only commit if ALL reads succeeded
    agent_data = self.GetStruct().AccountData[index].PlayerData.AgentData
    agent_data.Health = health
    agent_data.MaxHealth = max_health
    agent_data.XYZ[0] = xyz[0]
    # ... etc ...
```

**Tradeoffs:**
- On failure, the previous frame's data stays in shmem. Stale by one frame (~16ms) but
  internally consistent — strictly better than a half-valid frame.
- Collapses 60+ native lookups into 1, reducing per-frame native API overhead significantly.
- The single `living` reference is still a native pointer held briefly within a single
  function call — not cached across frames.
- No API change — `SetPlayerData(email)` works identically.

See [Option 1A Developer Commentary](option-1a-developer-commentary.md) for the full trace
of the current data flow and why "snapshot before write" was a misleading framing.

### 1C. Enable and Fix UpdateTimeouts

**Problem addressed:** I-4 (stale slot accumulation)

Remove the `return` at line 2018, and add a guard so that the timeout cleanup only resets
slots that are *not* owned by the current process. This prevents a client from accidentally
resetting its own slot during a slow frame.

```python
def UpdateTimeouts(self):
    current_time = self.GetBaseTimestamp()
    my_email = self._get_account_email()
    for index in range(self.max_num_players):
        player = self.GetStruct().AccountData[index]
        if player.IsSlotActive:
            delta = current_time - player.LastUpdated
            if delta > SHMEM_SUBSCRIBE_TIMEOUT_MILLISECONDS:
                if player.AccountEmail != my_email:
                    self.ResetPlayerData(index)
```

**Tradeoffs:**
- Simple fix.
- Need to ensure all clients run `UpdateTimeouts` at similar rates so they agree on which
  slots are stale.

### 1E. Atomic Message Slot State

**Problem addressed:** I-5 (message queue races)

Replace the separate `Active` + `Running` bools with a single `c_uint` state field using
defined constants (`EMPTY=0`, `QUEUED=1`, `RUNNING=2`). Use a compare-before-write pattern
on the single word instead of setting two separate fields.

**Tradeoffs:**
- Small struct layout change.
- Messages become slightly easier to reason about.
- Does not provide true CAS on platforms without `lock cmpxchg`, but reduces the race window
  to a single word write which is effectively atomic on x86.

### 1F. Lightweight Segment Registry

**Problem addressed:** I-7 (unmanaged segment proliferation)

Add a small "registry" shared memory segment (or a reserved section at the end of
`Py4GW_Shared_Mem`) that tracks all active custom segments by name, owning process,
struct size, and a schema version number. When a widget creates a custom segment, it
registers it. When another process attaches, it checks the registry for size/version
mismatches before overlaying its struct.

```python
class SegmentRegistryEntry(Structure):
    _pack_ = 1
    _fields_ = [
        ("Name", c_wchar * 64),
        ("SchemaVersion", c_uint),
        ("Size", c_uint),
        ("OwnerPID", c_uint),
        ("CreatedAt", c_uint),
    ]

# In the core manager:
def register_segment(self, name: str, version: int, size: int):
    ...

def attach_segment(self, name: str, expected_version: int, expected_size: int):
    entry = self._find_registry_entry(name)
    if entry and (entry.SchemaVersion != expected_version or entry.Size != expected_size):
        raise SchemaMismatchError(f"Segment '{name}' v{entry.SchemaVersion} "
                                   f"(size {entry.Size}) != expected v{expected_version}")
    ...
```

**Tradeoffs:**
- Small addition — a fixed-size registry array (e.g., 32 entries) in a known location.
- Gives the SharedMem Monitor widget visibility into all custom segments.
- Does not force any particular sync strategy on custom segments — just tracks them.
- Requires all segment creators to opt in (call `register_segment`). Existing code that
  doesn't register still works, just isn't visible in the registry.

---

## Option 2: Rearchitecture (Compatible Concepts, New API)

These options redesign the shared memory system while preserving the underlying concepts
(state replication, message passing, cross-client options) so that existing use cases can be
migrated mechanically.

### 2B. Topic-Based Pub/Sub Message Bus

**Core idea:** Replace the flat 64-slot message array with a ring buffer per topic (or per
receiver). Each message has a sequence number. Senders append to the ring; receivers track
their own read cursor.

```python
RING_SIZE = 32  # Per receiver

class MessageRing(Structure):
    _pack_ = 1
    _fields_ = [
        ("Messages", SharedMessage * RING_SIZE),
        ("WriteHead", c_uint),   # Monotonically increasing, mod RING_SIZE for index
    ]

class MessageBus(Structure):
    _pack_ = 1
    _fields_ = [
        ("Rings", MessageRing * MAX_PLAYERS),  # One ring per receiver
    ]
```

**Sender:**
```python
def send(self, receiver_slot, command, params, extra):
    ring = self.GetStruct().Bus.Rings[receiver_slot]
    idx = ring.WriteHead % RING_SIZE
    ring.Messages[idx] = build_message(...)
    ring.WriteHead += 1  # Single atomic increment
```

**Receiver:**
```python
class MessageCursor:
    def __init__(self):
        self.read_head = 0

    def poll(self, ring) -> list[SharedMessage]:
        messages = []
        while self.read_head < ring.WriteHead:
            idx = self.read_head % RING_SIZE
            messages.append(ring.Messages[idx].clone())
            self.read_head += 1
        return messages
```

**Migration path:** `SendMessage(sender, receiver, cmd, params, extra)` becomes
`bus.send(receiver_slot, cmd, params, extra)`. `GetNextMessage(email)` becomes
`cursor.poll(ring)`. The command types and params structure stay the same. A simple wrapper
could preserve the old API during migration.

**Tradeoffs:**
- Messages can be lost if sender wraps around the ring before receiver reads (bounded buffer).
  For GW bot commands, this is acceptable — a command that's 32 messages stale is irrelevant
  anyway.
- No more "mark as running/finished" lifecycle. Messages are consumed by advancing the read
  cursor. If a receiver needs to ACK, they send a reply message.
- Eliminates the race where two senders claim the same message slot.
- Each receiver gets `RING_SIZE` message capacity instead of competing for 64 global slots.

### 2C. Separated Concerns: State Bus + Command Bus + Config Store

**Core idea:** Split `AllAccounts` into three independent shared memory segments, each with
its own lifecycle, size, and synchronization strategy.

```
Segment 1: "Py4GW_State"
  - Double-buffered AccountData[64] (pub/sub state replication)
  - Read-heavy, write-once-per-frame-per-client
  - Seqlock or double-buffer synchronization

Segment 2: "Py4GW_Messages"
  - Per-receiver ring buffers (command bus)
  - Write-occasionally, read-occasionally
  - Sequence numbers for ordering

Segment 3: "Py4GW_Config"
  - HeroAI options, CustomBehavior configs
  - Write-rarely, read-frequently
  - Simple version counter sufficient
```

**Migration path:** Each segment maps to one of the three current sections of `AllAccounts`.
`GLOBAL_CACHE.ShMem` becomes `GLOBAL_CACHE.StateBus`, `GLOBAL_CACHE.CommandBus`,
`GLOBAL_CACHE.ConfigStore` — or a facade object that exposes all three behind the same
namespace.

**Tradeoffs:**
- Each segment can be sized, versioned, and upgraded independently.
- A bug in the message system can't corrupt state data (isolation).
- Three shared memory segments to manage instead of one.
- Each segment can use the synchronization strategy best suited to its access pattern.
- Allows future scaling (e.g., larger message rings without bloating state segments).

### 2D. Copy-Out Data Model with Typed Accessors

**Core idea:** Instead of exposing raw ctypes structures, the API returns plain Python
dataclasses. All shared memory access goes through a thin accessor layer that handles
snapshot reads, generation checking, and cloning internally.

```python
@dataclass(frozen=True)
class PlayerSnapshot:
    email: str
    character_name: str
    map_id: int
    position: tuple[float, float, float]
    health: float
    max_health: float
    energy: float
    max_energy: float
    buffs: tuple[BuffInfo, ...]
    skills: tuple[SkillInfo, ...]
    # ... etc

class StateBus:
    def get_player(self, email: str) -> PlayerSnapshot | None:
        """Returns an immutable snapshot, never a live pointer."""
        index = self._find_account(email)
        if index == -1:
            return None
        raw = self._read_consistent(index)  # seqlock/double-buffer read
        return PlayerSnapshot(
            email=raw.AccountEmail,
            character_name=raw.CharacterName,
            # ... map all fields
        )

    def get_all_players(self) -> list[PlayerSnapshot]:
        return [self.get_player_by_index(i) for i in self._active_indices()]
```

**Migration path:** Code that currently does `player = ShMem.GetAccountDataFromEmail(email)`
and then `player.PlayerHP` would change to `snap = StateBus.get_player(email)` and
`snap.health`. The field names change but the concepts map 1:1. An agent could do this
rename mechanically.

**Tradeoffs:**
- Clean typed API that enforces batch discipline on the write side (the accessor layer
  naturally implements 1A's all-or-nothing pattern internally).
- Immutable snapshots are convenient for consumers that want to compare across frames or
  pass data between functions without worrying about mutation.
- Per-read allocation cost (Python dataclass creation). For 64 slots at ~60 FPS this is
  negligible.
- Biggest API surface change of the Option 2 proposals, but the most structured.

### 2E. Managed Segment Factory for Widgets/Bots

**Core idea:** Instead of letting each widget/bot manually create shared memory segments
with `shared_memory.SharedMemory(name=..., create=True, ...)`, provide a framework-level
`SegmentFactory` that handles creation, attachment, schema versioning, lifecycle cleanup,
and registry in a uniform way.

```python
class SegmentFactory:
    """Framework-provided factory for creating managed shared memory segments."""

    def create_or_attach(self,
                         name: str,
                         struct_type: type[Structure],
                         schema_version: int = 1,
                         on_first_init: callable = None) -> ManagedSegment:
        """
        Create or attach to a named shared memory segment.
        - Registers in the central registry (visible to monitor tools).
        - Validates schema version and size on attach.
        - Calls on_first_init(struct) only when creating for the first time.
        - Tracks owning PID for orphan detection.
        """
        size = sizeof(struct_type) + sizeof(SegmentHeader)
        try:
            shm = shared_memory.SharedMemory(name=name)
            header = SegmentHeader.from_buffer(shm.buf)
            if header.SchemaVersion != schema_version:
                raise SchemaMismatchError(...)
            if header.Size != sizeof(struct_type):
                raise SchemaMismatchError(...)
        except FileNotFoundError:
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
            header = SegmentHeader.from_buffer(shm.buf)
            header.SchemaVersion = schema_version
            header.Size = sizeof(struct_type)
            header.CreatorPID = os.getpid()
            if on_first_init:
                data = struct_type.from_buffer(shm.buf, sizeof(SegmentHeader))
                on_first_init(data)

        self._register(name, schema_version, sizeof(struct_type))
        return ManagedSegment(shm, struct_type)

class ManagedSegment:
    """Wrapper providing typed access and cleanup."""
    def get_struct(self) -> Structure:
        return self._struct_type.from_buffer(
            self._shm.buf, sizeof(SegmentHeader))

    def close(self):
        self._shm.close()
        # Optionally unlink if we are the creator
```

**Usage by a widget:**
```python
# Before (manual, unmanaged):
shm = shared_memory.SharedMemory(name="my_widget_sync", create=True, size=sizeof(MyStruct))
data = MyStruct.from_buffer(shm.buf)

# After (managed):
seg = GLOBAL_CACHE.SegmentFactory.create_or_attach(
    name="my_widget_sync",
    struct_type=MyStruct,
    schema_version=2,
    on_first_init=lambda s: reset_defaults(s),
)
data = seg.get_struct()
```

**Migration path:** CustomBehaviorWidgetMemoryManager and SharedState would be refactored
to use `SegmentFactory.create_or_attach()` internally, replacing their bespoke create-or-
attach code. Their public APIs stay the same — the change is in how the segment is obtained.
New widgets/bots would use the factory from the start.

**Tradeoffs:**
- Solves orphan segments (factory tracks creator PID, can detect and clean up).
- Solves schema drift (version + size checked on every attach).
- Solves observability (all segments registered, visible to monitor tools).
- Does NOT dictate synchronization strategy — each segment can still use whatever sync
  approach fits (locks, seqlocks, timestamps, none).
- Adds a small framework dependency — widgets that want raw `shared_memory` access can
  still use it, but lose the management benefits.
- The `SegmentHeader` prefix means existing segment layouts are not wire-compatible (one-time
  migration per segment).

---

## Option 3: Alternative Approaches

These go beyond shared memory and tackle the problem from different angles.

### 3A. SQLite WAL as Shared State Store

**Core idea:** Use a single SQLite database file in WAL (Write-Ahead Logging) mode as the
shared state store. WAL mode allows concurrent reads from multiple processes while one
process writes. SQLite handles all locking, crash recovery, and data integrity.

```python
import sqlite3

class StateStore:
    def __init__(self, db_path="/tmp/py4gw_state.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def publish_state(self, email, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO player_state
            (email, character_name, map_id, pos_x, pos_y, pos_z,
             health, max_health, energy, max_energy, buffs_json,
             updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (email, data['name'], data['map_id'], ...))
        self.conn.commit()

    def get_all_players(self) -> list[dict]:
        cur = self.conn.execute("""
            SELECT * FROM player_state
            WHERE updated_at > ?
        """, (time.time() - 0.5,))
        return [dict(row) for row in cur.fetchall()]

    def send_command(self, sender, receiver, command, params):
        self.conn.execute("""
            INSERT INTO command_queue (sender, receiver, command, params_json, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (sender, receiver, command, json.dumps(params), time.time()))
        self.conn.commit()

    def poll_commands(self, receiver) -> list[dict]:
        cur = self.conn.execute("""
            SELECT rowid, * FROM command_queue
            WHERE receiver = ? AND processed = 0
            ORDER BY created_at
        """, (receiver,))
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            self.conn.executemany(
                "UPDATE command_queue SET processed = 1 WHERE rowid = ?",
                [(r['rowid'],) for r in rows]
            )
            self.conn.commit()
        return rows
```

**Advantages:**
- ACID transactions — no torn reads, no races, no corruption.
- Crash recovery is automatic (WAL journal).
- Schema migrations are trivial (`ALTER TABLE ADD COLUMN`).
- Built-in query capability — "give me all players on map X in party Y" is a WHERE clause
  instead of a Python loop over 64 slots.
- No manual memory layout management, no ctypes, no struct packing.
- Complex data (buff lists, skill arrays) can be stored as JSON columns.

**Multi-process access model:** SQLite in WAL mode **does** support multiple processes
accessing the same database file directly — no broker/server process is required. Each of the
8 interpreter environments would open its own `sqlite3.connect()` to the same file path.
WAL mode allows concurrent readers while one writer holds the write lock. Writers serialize
via SQLite's internal file locking (the WAL and `-shm` files). In practice, with 8 clients
each writing once per frame (~60 FPS) and reads being non-blocking in WAL mode, contention
would be low. The main caveat is that **all 8 processes must see the same filesystem path**
(they do, since they're on the same machine) and that write transactions should be kept
short to minimize lock hold time. If write contention becomes a bottleneck under load, the
alternative is a single writer process that other clients communicate with, but this adds
significant complexity and is unlikely to be necessary at 8 clients.

**Disadvantages:**
- Higher per-operation latency than raw shared memory (~0.1-1ms per transaction vs ~1us for
  a memcpy). At ~60 FPS with 8 clients this needs measurement — 480 write transactions/sec.
- Requires file system access (temp directory or configurable path).
- Less "real-time" feel — inherent write-commit-read pipeline adds latency.
- Write serialization under WAL means only one process can write at a time. At 8 clients
  this is manageable, but write-heavy workloads could see occasional blocking (~ms scale).
- Debugging is easier (can open the DB with any SQLite tool) but monitoring widgets would
  need rewriting.

**Migration path:** The snapshot-before-write pattern is the same — native data is collected
into a dict, then written via SQL. Reader-side code changes from `player.PlayerHP` to
`row['health']` (or a dataclass wrapper). Command types and params translate directly.

### 3B. Memory-Mapped File with Struct Overlay + Advisory Locking

**Core idea:** Replace `multiprocessing.shared_memory` with a plain `mmap`'d file. The file
persists on disk, survives process crashes, and can be inspected with hex editors or custom
tools. Use `fcntl.flock()` (or Windows equivalent) for advisory locking around writes.

```python
import mmap, os, fcntl

class MMapStateStore:
    def __init__(self, path="/tmp/py4gw_state.bin"):
        size = sizeof(AllAccounts)
        fd = os.open(path, os.O_RDWR | os.O_CREAT)
        os.ftruncate(fd, size)
        self.mm = mmap.mmap(fd, size)
        self.fd = fd

    def write_slot(self, index, snapshot: dict):
        offset = index * sizeof(AccountData)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        try:
            buf = serialize_to_bytes(snapshot)
            self.mm[offset:offset+len(buf)] = buf
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_UN)

    def read_slot(self, index) -> AccountData:
        offset = index * sizeof(AccountData)
        fcntl.flock(self.fd, fcntl.LOCK_SH)
        try:
            raw = self.mm[offset:offset+sizeof(AccountData)]
            return deserialize_from_bytes(raw)
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
```

**Advantages:**
- File persists after crash — new process can attach and see last known state.
- Advisory locking prevents torn reads/writes.
- Compatible with the same ctypes struct overlay (`AllAccounts.from_buffer(mm)`).
- Can coexist with the current approach incrementally.
- Inspectable with standard tools (`hexdump`, `xxd`).

**Disadvantages:**
- `fcntl.flock` is per-file, not per-slot — locking the whole file for one slot write is
  coarse. Per-slot locking requires per-slot files or a lock table (added complexity).
- Windows support requires `msvcrt.locking()` or a compat layer.
- Doesn't solve the stale native pointer problem — that requires the snapshot pattern
  regardless.

### 3C. Localhost UDP Multicast for Events + Shared State for Data

**Core idea:** Hybrid architecture: keep shared memory for high-frequency state replication
(positions, health, buffs) but move commands/events to a localhost UDP multicast channel.
Each client joins a multicast group and both broadcasts and listens for event messages.

```python
import socket, struct, json

MCAST_GROUP = '239.255.0.1'
MCAST_PORT = 47001

class EventBus:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)  # localhost only
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', MCAST_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(MCAST_GROUP), socket.INADDR_ANY)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.sock.setblocking(False)

    def send_command(self, command: dict):
        data = json.dumps(command).encode()
        self.sock.sendto(data, (MCAST_GROUP, MCAST_PORT))

    def poll_commands(self) -> list[dict]:
        messages = []
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
                messages.append(json.loads(data))
            except BlockingIOError:
                break
        return messages
```

**Advantages:**
- True pub/sub — any client can subscribe to events without polling shared memory.
- Natural broadcast support — "all clients do X" is a single send, not N sends.
- Commands are ordered by arrival time naturally.
- Decouples command delivery from state storage.
- Can add filtering (receiver checks if message is for them) or topic channels.

**Disadvantages:**
- UDP is unreliable — messages can be dropped under load (rare on localhost, but possible).
  For critical commands (travel, skill use), may need an ACK/retry layer.
- Requires network stack to be available (may be an issue in some sandboxed environments).
- Two systems to maintain (shared memory for state, UDP for events).
- Monitoring/debugging is harder — need packet capture or a dedicated listener.

**Migration path:** State reads stay on shared memory. `SendMessage()` becomes
`EventBus.send_command()`. `GetNextMessage()` becomes `EventBus.poll_commands()` with a
filter on receiver email. The `SharedCommandType` enum and params structure can be serialized
as-is.

### 3D. Named Pipe / Unix Domain Socket Command Channel

**Core idea:** Each client opens a named pipe (or Unix domain socket) for receiving commands.
Senders connect to the receiver's pipe and write command structs directly. This provides
reliable, ordered, process-to-process communication without shared memory.

```python
import os, select, struct

class CommandReceiver:
    def __init__(self, email: str):
        self.pipe_path = f"/tmp/py4gw_cmd_{email.replace('@','_')}"
        if not os.path.exists(self.pipe_path):
            os.mkfifo(self.pipe_path)
        self.fd = os.open(self.pipe_path, os.O_RDONLY | os.O_NONBLOCK)

    def poll(self) -> list[bytes]:
        messages = []
        while select.select([self.fd], [], [], 0)[0]:
            data = os.read(self.fd, 4096)
            if data:
                messages.extend(self._parse_messages(data))
        return messages

class CommandSender:
    def send(self, receiver_email: str, command_bytes: bytes):
        pipe_path = f"/tmp/py4gw_cmd_{receiver_email.replace('@','_')}"
        fd = os.open(pipe_path, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, command_bytes)
        os.close(fd)
```

**Advantages:**
- Reliable delivery (unlike UDP).
- OS handles buffering and ordering.
- Per-receiver channels — no contention.
- Clean cleanup on process exit (pipe file can be unlinked).

**Disadvantages:**
- One pipe per receiver — need to know receiver's identity to connect.
- Broadcast requires sending to each pipe individually.
- Platform differences between Unix (mkfifo) and Windows (named pipes API).
- More complex setup/teardown than shared memory.

---

## Comparison Matrix

| Criterion | 1A,C,E,F (Incremental) | 2B-E (Rearchitecture) | 3A (SQLite) | 3B (mmap+lock) | 3C (UDP hybrid) | 3D (Pipes) |
|-----------|----------------------|-----------------------|-------------|-----------------|-----------------|------------|
| **Prevents half-valid frames** | Yes (1A) | Yes (2D enforces 1A pattern) | Yes (transaction boundary) | Yes (if combined with 1A) | Yes (if combined with 1A) | N/A |
| **Message race safety** | Improved (1E) | Yes (2B) | Yes | Depends on impl | Yes | Yes |
| **Stale slot cleanup** | Yes (1C) | Yes (inherits 1C) | Yes (query by timestamp) | Manual | N/A | N/A |
| **Crash recovery** | No | No | Yes (WAL) | Yes (file persists) | No | No |
| **Custom segment management** | Partial (1F registry) | Yes (2E factory) | N/A (single DB) | Per-file | N/A | N/A |
| **Migration effort** | Low | Medium | High | Medium | Medium | Medium-High |
| **Runtime overhead** | Lower (fewer native lookups) | ~Same (+alloc) | Higher (+IO) | ~Same (+lock) | ~Same (+network) | ~Same (+IO) |
| **Debugging ease** | Better (1F) | Better (typed API + registry) | Best (SQL queries) | Good (hex dump) | Harder | Harder |
| **Broadcast support** | Same (loop) | Better (2B ring) | Same (query) | Same (loop) | Best (multicast) | Worst (N sends) |
| **Schema evolution** | Partial (1F version) | Good (2E versioned) | Easy (ALTER TABLE) | Hard (struct) | Easy (JSON) | Medium |

### Recommended Approach

**Short term (lowest risk, highest impact):** Implement **1A** (all-or-nothing batch writes)
and **1C** (re-enable timeout cleanup). 1A is the single most impactful change — it prevents
half-valid frames from being committed and collapses 60+ redundant native lookups into 1,
directly addressing the most common data quality issue. 1C is a trivial fix (remove one
`return` statement) that prevents stale slot accumulation.

**Medium term:** Implement **1E** (atomic message state) and **1F** (segment registry).
1E tightens the message system against lost/duplicated commands. 1F provides observability
into the custom segment ecosystem without forcing migration.

**Long term (if the system continues to grow):** Move to **2C** (separated segments) with
**2B** (ring buffer messages) and **2E** (managed segment factory). The separated segments
allow independent evolution of state, commands, and config. The ring buffer eliminates
message queue races entirely. 2E standardizes the custom segment lifecycle. If query
capability becomes important, consider **3A** (SQLite) for the state segment specifically —
it naturally solves the custom segment problem too, since any widget can create a table
instead of a segment.

The half-valid frame problem (I-1) is **orthogonal to the IPC mechanism** — it must be
solved at the data collection layer (1A pattern) regardless of which architecture is chosen.
Every option in this document still needs all-or-nothing batch discipline when collecting
native data.
