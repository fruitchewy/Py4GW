# Option 2A Deep Dive: Double-Buffered Slots vs. Incremental Alternatives

## What Actually Happens Today

### The Write Path (Process A — the slot owner)

`SetPlayerData(email)` is called every frame by the Environment Upkeeper widget.

1. `GetStruct()` calls `AllAccounts.from_buffer(self.shm.buf)` — this returns a **live
   ctypes view directly into the shared memory buffer**. There is no staging area.

2. `player = self.GetStruct().AccountData[index]` gives a pointer into that buffer.

3. Thirteen `_set_*_data(index)` helpers run sequentially, each writing fields one-by-one
   as direct memory stores into the shared buffer:

```
_set_account_data       → CharacterName, AccountName, flags
_set_player_data        → PlayerPosX, PlayerPosY, PlayerHP, PlayerEnergy, ...
_set_map_data           → MapID, MapRegion, MapDistrict, MapLanguage
_set_buff_data          → 240 entries × (SkillId, Duration, Remaining, TargetAgentID, Type)
_set_attribute_data     → 11 entries × (Id, Value, BaseValue)
_set_skill_data         → 8 entries × (Id, Recharge, Adrenaline) + CastingSkillID
_set_rank_data          → Rank, Rating, QualifierPoints, Wins, Losses, TournamentRewardPoints
_set_factions_data      → 4 factions × (Current, TotalEarned, Max)
_set_titles_data        → 48 titles × (TitleID, CurrentPoints)
_set_quests_data        → 100 quests × (QuestID, IsCompleted) + ActiveQuestID
_set_experience_data    → Level, Experience, ProgressPct, SkillPoints
_set_agent_data         → 50+ fields (UUID, position, health, conditions, animation, ...)
_set_available_characters_data → 20 entries × (Name, Level, IsPvP, MapID, Professions, Campaign)
```

Every individual field assignment (e.g., `player.PlayerPosX = playerx`) is an immediate
store into the shared memory buffer. **Readers on other processes can see each write as it
happens.**

### The Read Path (Process B — a different game client)

`GetAllActiveSlotsData()`, `GetAccountDataFromEmail()`, etc. call the same
`self.GetStruct().AccountData[i]` — returning the **same live pointer** into the same
shared buffer that Process A is writing to.

HeroAI's `PartyCache.update()` (`party_cache.py:47`) stores these live pointers:

```python
shmem_accounts = GLOBAL_CACHE.ShMem.GetAllActiveSlotsData()
for acc in shmem_accounts:
    if acc.IsSlotActive and SameMapOrPartyAsAccount(acc):
        self.accounts[acc.PlayerID] = acc  # acc IS a pointer into shared memory
```

Later, HeroAI's UI reads fields from these stored pointers:

```python
account_data.PlayerHP           # live read from shmem at this instant
account_data.PlayerPosX         # live read from shmem at this instant
account_data.PlayerEnergyRegen  # live read from shmem at this instant
```

Each field access is a **live read from shared memory at the moment of access**, not from
any snapshot.

---

## The Concrete Problem 2A Prevents

Process A is mid-write of its slot. It has written `PlayerPosX = 100.0` (new position from
the current frame) but hasn't yet reached `PlayerPosY` (still holds `200.0` from the
previous frame — will be overwritten to `500.0` shortly).

Process B reads `PlayerPosX` and `PlayerPosY` from the same slot. It sees position
`(100.0, 200.0)` — **a coordinate that never actually existed**. X is from the current
frame, Y is from the previous frame.

This generalizes across all 13 helpers. While `_set_buff_data` is iterating through 240
buff entries, a reader can see:
- Buffs 0–119: current frame (newly written)
- Buffs 120–239: previous frame (not yet overwritten)
- PlayerHP: current frame (written earlier by `_set_player_data`)
- Skills: previous frame (`_set_skill_data` hasn't run yet)

The result is an **internally inconsistent snapshot** — correlated fields don't agree on
which frame they're from.

### How Common Is This?

Each slot has exactly **one writer** (the owning client) and **multiple readers** (all other
clients). This is a textbook single-writer-multiple-reader (SWMR) scenario.

- Process A writes ~60+ fields. Each is a single memory store (nanoseconds on x86).
- Process B reads a few fields per access. Each is a single memory load.
- The probability of Process B reading in the exact nanosecond window between two
  correlated writes is low per-access, but **accumulates across thousands of frames per
  second across 8 clients**.
- In practice this manifests as occasional one-frame glitches: HeroAI following to a
  ghost position, health bars flickering, buff indicators temporarily wrong.

---

## How 2A Works

Each player slot gets two copies of its data (A and B). The writer always writes to the
**inactive** copy. Readers always read from the **active** copy.

```python
class SlotPair(Structure):
    _pack_ = 1
    _fields_ = [
        ("DataA", AccountData),
        ("DataB", AccountData),
        ("ActiveBuffer", c_uint),  # 0 = A is active, 1 = B is active
    ]
```

**Writer (Process A):**
```python
pair = self.GetStruct().Slots[slot_index]
inactive = pair.DataB if pair.ActiveBuffer == 0 else pair.DataA
# Write ALL fields to `inactive` — readers cannot see this buffer
_write_all_data(inactive, snapshot)
# Single 4-byte write flips which buffer readers see
pair.ActiveBuffer = 1 - pair.ActiveBuffer
```

**Reader (Process B):**
```python
pair = self.GetStruct().Slots[slot_index]
active = pair.DataA if pair.ActiveBuffer == 0 else pair.DataB
return active.clone()  # complete, consistent frame
```

The flip is a single `uint32` write — **atomic on x86**. After the flip, readers
immediately see the new complete frame. Before the flip, they see the old complete frame.
There is no intermediate state visible to any reader.

---

## How 2A Differs From Each Related Incremental Option

### vs. 1A (Snapshot-Before-Write)

**What 1A does:** Collects all native data into a Python dict first, then bulk-writes to
shmem. Eliminates native API calls between shmem writes.

**What 1A does NOT do:** The bulk-write still writes 60+ fields sequentially to the **same
buffer readers are looking at**. The write sequence is faster (pure ctypes stores, no
interleaved native calls), so the torn-read window is shorter — but it still exists.

```
1A write path:
  [snapshot native data]  →  [write field 1 to shmem] [write field 2] ... [write field 60]
                               ↑ readers can see each write as it lands ↑
```

```
2A write path:
  [write field 1 to INACTIVE buffer] ... [write field 60 to INACTIVE buffer] → [flip]
                                                                                 ↑ only this is visible to readers
```

**Key difference:** 1A reduces the *duration* of the torn-read window. 2A *eliminates* it.

**Complementary, not competing:** 1A solves the stale-pointer crash problem on the write
side (by isolating native reads from shmem writes). 2A solves the torn-read consistency
problem on the read side (by isolating the write target from the read target). A robust
system uses both: 1A to avoid writing garbage, 2A to avoid readers seeing half-updates.

### vs. 1B (Seqlock / Generation Counter)

**What 1B does:** Adds a `WriteGeneration` counter to each slot. Writer increments it
before and after writing (odd = write-in-progress, even = stable). Reader checks: if
generation is odd or changed during the read, the data is potentially torn — retry.

**What 1B does NOT do:** It does not prevent torn reads from occurring. It **detects**
them after the fact and forces the reader to spin/retry until it gets a clean read.

```
1B read path:
  gen1 = slot.WriteGeneration     # read generation
  data = read_all_fields(slot)    # read data (might be torn)
  gen2 = slot.WriteGeneration     # re-read generation
  if gen1 != gen2 or gen1 % 2:    # torn? retry
      goto start
```

**Key difference:** 1B is reactive (detect and retry). 2A is preventive (torn reads
cannot happen). 1B adds latency on every retry and requires reader-side code changes at
every callsite. 2A requires no reader-side retry logic.

**1B's advantage:** Does not double the memory footprint. Each slot stays at one copy.

### vs. 1D (Return Clones)

**What 1D does:** Accessor methods call `memmove(clone, src, sizeof(AccountData))` to
copy the entire struct before returning, so the caller gets a detached snapshot that
won't change underneath them.

**What 1D does NOT do:** The `memmove` itself is not atomic. If the writer is mid-update
when `memmove` executes, the clone captures a mix of old and new bytes — it's a **frozen
torn read**. The clone doesn't keep changing (which prevents one class of bug: the "data
mutates while I'm using it" problem), but the initial copy can still be inconsistent.

```
1D: reader gets a copy that might be internally inconsistent, but at least it's stable
2A: reader gets a copy that is both internally consistent AND stable
```

**Key difference:** 1D prevents the "live pointer keeps changing" problem. 2A prevents
both that AND the "snapshot was torn at capture time" problem. They solve overlapping but
different issues.

---

## Summary Table

| Aspect | Current | 1A | 1B | 1D | 2A |
|--------|---------|----|----|----|----|
| Native reads interleaved with shmem writes | Yes | **No** | Yes | Yes | Depends (combine with 1A) |
| Readers see partially-written frames | Yes | Yes (shorter window) | Detected + retried | Frozen but still torn | **No** |
| Reader holds live mutable pointer | Yes | Yes | Yes | **No** (clone) | **No** (clone from stable buffer) |
| Requires reader-side code changes | — | No | **Yes** (retry loop) | No | Minimal (index through ActiveBuffer) |
| Memory overhead | 1x | 1x | 1x (+4 bytes/slot) | 1x (+alloc per read) | **2x** per slot |
| Prevents stale-pointer crashes | No | **Yes** | No | No | No (needs 1A) |

### What Each Actually Solves

- **1A** solves: writer crashes from stale native pointers (I-1, I-6)
- **1B** solves: reader seeing torn data, via detection and retry (I-2)
- **1D** solves: reader holding a reference that mutates under them (I-3)
- **2A** solves: reader seeing torn data, via structural prevention (I-2), and subsumes
  1D's benefit (clone from a stable buffer is always consistent)

### The Practical Priority Question

The most severe current symptom is **native client crashes** — these come from stale
pointer dereferences on the **write side** (I-1, I-6). 1A addresses this directly. Torn
reads (I-2) cause subtler symptoms: one-frame behavioral glitches in HeroAI, occasional
UI flicker. These are annoying but not crashes.

This means **1A is higher priority than 2A** for stability. But 2A is the only option that
fully eliminates torn reads without reader-side retry logic, making it the right structural
choice if the system is being rearchitected anyway.
