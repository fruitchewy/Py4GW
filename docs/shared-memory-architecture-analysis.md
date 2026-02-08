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

The primary instability manifests as **native client crashes** caused by accessing stale game
memory pointers. This section catalogs the architectural patterns that contribute to this.

### I-1. Stale Native Pointer Cache

`SharedMemory.py` caches `self.agent_instance` (an `AgentStruct` pointing into native game
memory) across frames. The validation/refresh logic at lines 1015-1023 has a gap: between
checking `agent_instance` validity and actually using it in the `_set_*_data()` helpers, the
game can free or reallocate the underlying memory. The agent pointer is also refreshed on a
63ms throttle timer, meaning it can be up to 63ms stale.

Similarly, `self.effects_instance`, `self.party_instance`, and `self._title_instances` are
all cached native objects that may point to freed game memory if the game state changes
(map transition, party change, cinematic).

### I-2. No Atomic Multi-Field Writes

When `SetPlayerData` writes 50+ fields to AgentData, each field is a separate memory store.
A reader on another process can observe a half-written record — e.g., position from frame N
but health from frame N+1. While this is unlikely to crash directly, it creates inconsistent
snapshots that downstream logic (HeroAI targeting, following) may act on incorrectly.

### I-3. Readers Get Live Pointers Into Shared Memory

Methods like `GetAllActivePlayers()`, `GetAccountDataFromEmail()`, etc. return references
directly into the shared memory buffer — not copies. The caller holds a live ctypes pointer
to the shared segment. If the owning process resets that slot (via `ResetPlayerData`) or the
slot times out and gets reused, the reader is now looking at data from a different entity.

`AccountData.clone()` exists (using `memmove`) but is not called by default in any of the
accessor methods.

### I-4. Disabled Timeout Cleanup

`UpdateTimeouts()` (line 2017-2028) has an immediate `return` at line 2018, making the
entire timeout cleanup dead code. Stale slots from crashed clients accumulate and are only
reclaimed opportunistically by `FindEmptySlot()` when it runs out of clean slots.

### I-5. Message Queue Races

The message system uses a scan-for-first-inactive-slot pattern with no CAS (compare-and-swap)
or lock. Two senders targeting different receivers can race on the same message slot. The
`Active`/`Running` flags are separate `c_bool` fields — not an atomic state word — so a
reader could see `Active=True, Running=False` momentarily during `MarkMessageAsFinished`.

### I-6. Large Per-Frame Native API Surface

Each `SetPlayerData` call makes 60+ individual native API calls (`Agent.GetXYZ()`,
`Agent.GetHealth()`, `Agent.IsBleeding()`, etc.), each of which independently dereferences
native pointers. If the agent becomes invalid partway through, some fields get written with
valid data and some with garbage or trigger access violations. This is the most likely direct
cause of the native crashes — the sheer number of individual native reads per frame creates
a large window for the game to invalidate the underlying memory.

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
| **1A** | Collect all native game data into a Python snapshot before writing to shmem, reducing the stale-pointer crash window |
| **1B** | Add a seqlock generation counter to each slot so readers can detect torn/in-progress writes |
| **1C** | Re-enable the disabled `UpdateTimeouts()` to clean up stale slots from crashed clients |
| **1D** | Return cloned copies from all read accessors instead of live pointers into the shared buffer |
| **1E** | Replace separate Active/Running bools with a single atomic state word for messages |
| **1F** | Add a lightweight segment registry so custom segments are discoverable and version-checked |

**Option 2 — Rearchitecture (compatible concepts, new API):**

| Option | Approach |
|--------|----------|
| **2A** | Double-buffer each player slot (A/B copies with atomic flip) to eliminate torn reads entirely |
| **2B** | Replace the flat message array with per-receiver ring buffers and sequence-number cursors |
| **2C** | Split the monolithic segment into three independent segments (state, commands, config) with per-segment sync strategies |
| **2D** | Wrap shared memory behind a typed accessor layer that returns immutable Python dataclass snapshots |
| **2E** | Provide a managed segment factory so widgets/bots get lifecycle, versioning, and registry for free |

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

### 1A. Snapshot-Before-Write Pattern

**Problem addressed:** I-1, I-6 (stale pointers, large native API surface)

Instead of making 60+ individual native API calls that each dereference the agent pointer,
collect all native data into a local Python dict/dataclass *first*, then bulk-write it to
shared memory. This collapses the "window of vulnerability" from the entire write sequence
down to the single native snapshot call.

```python
# Conceptual change inside SetPlayerData:
def _snapshot_native_data(agent_id) -> dict | None:
    """One try/except boundary around ALL native reads."""
    if not Agent.IsValid(agent_id):
        return None
    try:
        snapshot = {}
        snapshot['xyz'] = Agent.GetXYZ(agent_id)
        snapshot['health'] = Agent.GetHealth(agent_id)
        # ... all other reads ...
        return snapshot
    except Exception:
        return None  # Agent became invalid mid-read

def _write_snapshot_to_shmem(index, snapshot):
    """Pure ctypes writes, no native calls."""
    player = self.GetStruct().AccountData[index]
    player.PlayerPosX = snapshot['xyz'][0]
    # ...
```

**Tradeoffs:**
- Does not eliminate the race window entirely, but makes it much smaller.
- Adds one frame of latency (snapshot then write).
- If the snapshot fails partway, you skip the entire write rather than leaving a half-written
  record.
- No API change — `SetPlayerData(email)` still works identically.

### 1B. Generation Counter for Readers

**Problem addressed:** I-2, I-3 (torn reads, stale references)

Add a `WriteGeneration` (uint32) field to `AccountData`. The writer increments it before
starting a write and again after finishing. Readers check: if the generation is odd, a write
is in progress and the data is potentially torn — retry or skip. If the generation changed
between the start and end of a read, the data was modified during the read.

```
# In AccountData:
("WriteGeneration", c_uint),  # Even = stable, odd = write-in-progress
```

This is a standard seqlock pattern adapted for single-writer-multiple-reader shared memory.

**Tradeoffs:**
- Requires adding one field to the structure (breaking existing shared memory layout — but
  this is a one-time migration).
- Readers need a small wrapper: `with shmem.consistent_read(index) as data:` that retries
  on torn reads.
- No change to the write-side API.

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

### 1D. Return Clones by Default

**Problem addressed:** I-3 (readers holding live pointers)

Change `GetAllActivePlayers()`, `GetAllAccountData()`, `GetAccountDataFromEmail()`, etc.
to call `.clone()` before returning, so callers get a detached copy that won't change
underneath them.

**Tradeoffs:**
- Memory allocation per read (one `sizeof(AccountData)` memcpy per slot).
- Callers that intentionally want a live reference (e.g., the monitor widget) would need a
  separate `_raw` accessor.
- This prevents the scenario where a reader holds a reference that gets overwritten by a
  different entity when the slot is reused.

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

### 2A. Double-Buffered State Slots with Copy-on-Read

**Core idea:** Each player slot has two copies of its data (A and B). The writer always writes
to the *inactive* copy, then atomically flips a flag to make it the *active* copy. Readers
always read the *active* copy, which is guaranteed to be a complete, consistent snapshot.

```python
class SlotPair(Structure):
    _pack_ = 1
    _fields_ = [
        ("DataA", AccountData),
        ("DataB", AccountData),
        ("ActiveBuffer", c_uint),  # 0 = A is active, 1 = B is active
    ]
```

**Writer side:**
```python
def publish(self, slot_index, account_email):
    pair = self.GetStruct().Slots[slot_index]
    inactive = pair.DataB if pair.ActiveBuffer == 0 else pair.DataA
    # Write all fields to `inactive` (native snapshot pattern from 1A)
    _write_snapshot_to_slot(inactive, snapshot)
    # Flip
    pair.ActiveBuffer = 1 - pair.ActiveBuffer
```

**Reader side:**
```python
def read(self, slot_index) -> AccountData:
    pair = self.GetStruct().Slots[slot_index]
    active = pair.DataA if pair.ActiveBuffer == 0 else pair.DataB
    return active.clone()  # Return detached copy
```

**Migration path:** Replace `GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(email)` with
`GLOBAL_CACHE.ShMem.read_account(email)` — same concept, slightly different name. An
adapter layer could preserve the old method names during transition.

**Tradeoffs:**
- Doubles the memory footprint for state data (~2x AccountData * 64 slots).
- Eliminates torn reads entirely.
- Writer still needs the snapshot-before-write pattern to avoid stale pointer crashes.
- The flip is a single uint32 write, effectively atomic on x86.

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
- Eliminates the entire class of stale-pointer-into-shmem bugs on the reader side.
- Immutable snapshots are safe to pass around, store, compare across frames.
- Per-read allocation cost (Python dataclass creation). For 64 slots at 15-60 FPS this is
  negligible.
- Writers still need the snapshot-before-write pattern for native data.
- Biggest API surface change of the Option 2 proposals, but the most robust.

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
each writing once per frame (~15 FPS) and reads being non-blocking in WAL mode, contention
would be low. The main caveat is that **all 8 processes must see the same filesystem path**
(they do, since they're on the same machine) and that write transactions should be kept
short to minimize lock hold time. If write contention becomes a bottleneck under load, the
alternative is a single writer process that other clients communicate with, but this adds
significant complexity and is unlikely to be necessary at 8 clients.

**Disadvantages:**
- Higher per-operation latency than raw shared memory (~0.1-1ms per transaction vs ~1us for
  a memcpy). At 15 FPS with 8 clients this is likely fine, but needs measurement.
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

| Criterion | 1A-F (Incremental) | 2A-E (Rearchitecture) | 3A (SQLite) | 3B (mmap+lock) | 3C (UDP hybrid) | 3D (Pipes) |
|-----------|--------------------|-----------------------|-------------|-----------------|-----------------|------------|
| **Eliminates torn reads** | Partial (1B) | Yes (2A, 2D) | Yes | Yes | N/A (state stays in shmem) | N/A |
| **Eliminates stale pointer crashes** | Partial (1A) | Partial (still need 1A pattern) | Same (need snapshot) | Same (need snapshot) | Same (need snapshot) | Same (need snapshot) |
| **Message race safety** | Improved (1E) | Yes (2B) | Yes | Depends on impl | Yes | Yes |
| **Crash recovery** | No | No | Yes (WAL) | Yes (file persists) | No | No |
| **Custom segment management** | Partial (1F registry) | Yes (2E factory) | N/A (single DB) | Per-file | N/A | N/A |
| **Migration effort** | Low | Medium | High | Medium | Medium | Medium-High |
| **Runtime overhead** | ~Same | ~Same (+alloc) | Higher (+IO) | ~Same (+lock) | ~Same (+network) | ~Same (+IO) |
| **Debugging ease** | Better (1F) | Better (typed API + registry) | Best (SQL queries) | Good (hex dump) | Harder | Harder |
| **Broadcast support** | Same (loop) | Better (2B ring) | Same (query) | Same (loop) | Best (multicast) | Worst (N sends) |
| **Schema evolution** | Partial (1F version) | Good (2E versioned) | Easy (ALTER TABLE) | Hard (struct) | Easy (JSON) | Medium |
| **Windows compat** | Same | Same | Same | Needs compat | Same | Needs compat |

### Recommended Approach

**Short term (lowest risk, highest impact):** Implement **1A** (snapshot-before-write),
**1D** (return clones), and **1F** (segment registry). The first two address the most
dangerous instability (stale native pointers causing crashes) and the most common reader-side
surprise (live pointers into shmem), without changing any API signatures. 1F provides
immediate observability into the custom segment ecosystem with minimal effort.

**Medium term:** Implement **2D** (typed accessor layer with dataclass snapshots) and **2E**
(managed segment factory) together. 2D can be a new module that wraps the existing
`Py4GWSharedMemoryManager`, providing a clean API while the underlying storage stays the same.
2E gives widgets/bots a standardized way to create custom segments with versioning, lifecycle
management, and registry — replacing the current ad-hoc pattern. Migrate consumers one at a
time.

**Long term (if the system continues to grow):** Move to **2C** (separated segments) with
**2B** (ring buffer messages). The separated segments allow independent evolution of state,
commands, and config. The ring buffer eliminates the message queue races entirely. If query
capability becomes important, consider **3A** (SQLite) for the state segment specifically —
it naturally solves the custom segment problem too, since any widget can create a table
instead of a segment.

The stale native pointer problem (the primary crash source) is **orthogonal to the IPC
mechanism** — it must be solved at the data collection layer (1A pattern) regardless of which
architecture is chosen. Every option in this document still needs the "snapshot native data
into Python objects first, then write to shared storage" pattern to avoid crashes.
