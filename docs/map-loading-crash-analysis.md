# Map Loading Crash Analysis

## The Symptom

The most commonly observed crash occurs **just after finishing loading a new map**. Not
during loading — immediately after. The game process doesn't tick the Python interpreter
for part of the loading process, creating a blind window where native memory is torn down
and rebuilt while our cached Python objects still reference the old memory.

---

## The Execution Timeline

Every interpreter tick, Environment Upkeeper's `main()` runs this sequence:

```
Line 90:  GLOBAL_CACHE._update_cache()              ← refreshes PartyCache, Agent (dead), etc.
Line 91:  account_email = Player.GetAccountEmail()
Line 92:  GLOBAL_CACHE.ShMem.SetPlayerData(email)    ← calls _updatechache() internally
Line 93:  GLOBAL_CACHE.ShMem.SetHeroesData()         ← reads party_instance.heroes
Line 94:  GLOBAL_CACHE.ShMem.SetPetData()            ← reads party_instance.GetPetInfo()
```

Note that **lines 90–94 run unconditionally** — there is no map-loading guard around them.
The loading guards are inside each function, at varying depths.

---

## What Gets Cached (and What Doesn't Get Cleared)

`Py4GWSharedMemoryManager` holds these cached native objects (`SharedMemory.py:558–570`):

| Cached Object | Created | Nulled on Map Load? | Notes |
|---------------|---------|---------------------|-------|
| `party_instance` | `_updatechache:1013` | **No** — GetContext() called instead | Live PyParty wrapping native PartyContext |
| `agent_instance` | `_updatechache:1023` | **Yes** — nulled at line 1006 | Live AgentStruct in native agent array |
| `effects_instance` | `_updatechache:1026` | **Yes** — nulled at line 1007 | PyEffects wrapper (lightweight) |
| `_title_instances` | `_updatechache:1042` | **Never** | Dict of live `TitleStruct` pointers into native title array |
| `quest_instance` | `_updatechache:1029` | **Never** | `PyQuest.PyQuest()` — holds native refs |

There are also two separate `PyParty` instances in play — a common source of confusion:
- `GLOBAL_CACHE.Party._party_instance` — owned by `PartyCache`, refreshed by `GLOBAL_CACHE._update_cache()`
- `GLOBAL_CACHE.ShMem.party_instance` — owned by `SharedMemory`, refreshed only inside `_updatechache()`

These are **independent objects**. Refreshing one does not refresh the other.

---

## The Three Phases of a Map Transition

### Phase 1: Loading Detected (interpreter is ticking, `Map.IsMapLoading() = True`)

Each frame:

1. **`GLOBAL_CACHE._update_cache()`** (GlobalCache.py:60):
   - Detects loading → calls `self.Party._update_cache()` → `_party_instance.GetContext()`
   - This refreshes **PartyCache's** instance (not SharedMemory's)
   - Also calls `Agent._update_cache()` — **dead code** (immediate `return` at Agent.py:19)

2. **`SetPlayerData(email)`** → calls `_updatechache()` (SharedMemory.py:999–1009):
   ```python
   if (Map.IsMapLoading() or Map.IsInCinematic()):
       if self.party_instance is not None:
           self.party_instance.GetContext()    # refresh SharedMemory's party instance
       self.agent_instance = None              # nulled ✓
       self.effects_instance = None            # nulled ✓
       return                                  # _title_instances NOT cleared ✗
                                               # quest_instance NOT cleared ✗
   ```
   Then writes identity fields (IsSlotActive, AccountEmail), hits `Map.IsMapLoading()` guard
   at line 1377 → returns early. The 13 `_set_*_data` helpers do NOT run.

3. **`SetHeroesData()`** (SharedMemory.py:1775–1782):
   ```python
   def SetHeroesData(self):
       owner_id = Player.GetAgentID()
       for hero_data in self.party_instance.heroes if self.party_instance else []:
           # ...
           self.SetHeroData(hero_data)
   ```
   Reads `self.party_instance.heroes` — **accesses native memory** (no loading guard at this
   level). Individual `SetHeroData()` calls have loading guards inside and return early.

4. **`SetPetData()`** (SharedMemory.py:1596):
   Accesses `self.party_instance.GetPetInfo()` — **accesses native memory** before any
   loading guard.

### Phase 2: Interpreter Frozen (game loading, no ticks)

The game stops ticking the Python interpreter for a portion of the loading process. During
this window:

- **All native memory from the old map is torn down** — agent arrays freed, PartyContext
  deallocated, effects contexts destroyed, title arrays reallocated, quest data rebuilt
- **New map structures are allocated** — potentially at different memory addresses
- **Our Python objects are NOT updated** — they still hold pointers/references to the old
  native memory
- **No Python code runs** — there is no opportunity to clean up or refresh

### Phase 3: Interpreter Resumes (first tick after freeze)

This is where the crash happens. Two sub-scenarios depending on timing:

#### 3A: Interpreter resumes while `Map.IsMapLoading()` is still True

The loading code path runs again. The critical call is:

```python
# _updatechache() line 1004
if self.party_instance is not None:
    self.party_instance.GetContext()    # dereferences native PartyContext
```

**Risk**: If `GetContext()` internally uses a cached pointer to the old PartyContext (which
was freed during the freeze), this dereferences freed memory. Whether this is safe depends
entirely on how the C++ binding implements `GetContext()` — does it start from a known-good
root pointer (safe) or from a cached pointer (crash)?

Then `SetHeroesData()` reads `self.party_instance.heroes` — which goes through the same
native PartyContext that may have just been refreshed (or not, if GetContext() crashed first).

#### 3B: Interpreter resumes after loading is complete (`Map.IsMapLoading() = False`)

This is the scenario described by the user — "just after finishing loading."

1. **`GLOBAL_CACHE._update_cache()`**: Not loading → runs throttled path. All throttle
   timers expired (frozen for seconds). Calls `self.Party._update_cache()` → refreshes
   **PartyCache's** party_instance to the new map. Does NOT touch SharedMemory's.

2. **`SetPlayerData(email)`**: Calls `_updatechache()`:
   - Not loading → enters non-loading branch
   - `self.party_instance` is not None → **does not re-create it** (line 1012 skipped)
   - `self.agent_instance` is None → creates fresh via `Agent.GetAgentByID(Player.GetAgentID())`
   - `self.effects_instance` = fresh `Effects.get_instance(Player.GetAgentID())`
   - 150ms throttle expired → calls `self.party_instance.GetContext()` → **refreshes SharedMemory's party instance** to new map

   **BUT**: What if `Player.GetAccountEmail()` returns `""` on this first frame?
   ```python
   # SetPlayerData line 1365
   if not account_email:
       return    # _updatechache() NEVER CALLED
   ```
   If the account email isn't available yet (player not fully loaded), `SetPlayerData`
   returns **before calling `_updatechache()`**. The SharedMemory's `party_instance` still
   holds a native pointer from the old map.

3. **`SetHeroesData()`**: Reads `self.party_instance.heroes`:
   - If `_updatechache()` ran: party_instance was refreshed → probably safe
   - If `_updatechache()` was skipped: party_instance points to freed memory → **CRASH**

---

## Crash Vectors

### CV-1: SetPlayerData skips _updatechache(), heroes/pets read stale party_instance

**Severity: HIGH — Most likely cause of the observed crash**

If `Player.GetAccountEmail()` returns `""` on the first post-load frame, `SetPlayerData()`
returns at line 1365 without ever calling `_updatechache()`. The SharedMemory's
`party_instance` was last refreshed during Phase 1 (old map). Its internal native pointer
now points to freed memory.

Then `SetHeroesData()` (line 1778) unconditionally reads:
```python
for hero_data in self.party_instance.heroes if self.party_instance else []:
```

`.heroes` accesses the native PartyContext through the stale pointer → **access violation**.

Same for `SetPetData()` (line 1666):
```python
pet_info = self.party_instance.GetPetInfo(owner_agent_id) if self.party_instance else None
```

**The gap**: `SetHeroesData()` and `SetPetData()` have no top-level loading/validity guard.
They rely on `SetPlayerData()` having run `_updatechache()` first, but that's not guaranteed.

### CV-2: GetContext() during loading dereferences freed context

**Severity: MEDIUM — depends on C++ binding implementation**

During Phase 1 and Phase 3A, `_updatechache()` calls `self.party_instance.GetContext()`.
If the C++ binding caches the PartyContext pointer and `GetContext()` dereferences it
before looking up the new one, this crashes when the old context was freed during the
interpreter freeze.

This applies to both:
- `SharedMemory._updatechache():1004` — during loading branch
- `PartyCache._update_cache():15` → called by `GlobalCache._update_cache():61`

### CV-3: Title instances never cleared — read from freed native title array

**Severity: MEDIUM — triggers when _set_titles_data runs on first valid post-load frame**

`self._title_instances` (SharedMemory.py:566) stores `TitleStruct` objects returned by
`Player.GetTitle()`. These are **live ctypes structures pointing directly into the native
title array** (WorldContext.py:622–636). The struct has fields like:

```python
("current_points", c_uint32),            # +h0004  — direct native memory read
("points_desc_ptr", POINTER(c_wchar)),   # +h0024  — pointer INTO native memory
```

These instances are **never cleared on map transition**. When `_set_titles_data` eventually
runs (after all loading guards pass on a subsequent frame), it reads:

```python
titles_data.Titles[title_id].CurrentPoints = title_instance.current_points  # line 1181
```

`title_instance.current_points` reads `c_uint32` at offset +4 from the base of the old
title struct — which is freed native memory. This could crash or silently read garbage.

### CV-4: Quest instance never cleared

**Severity: LOW-MEDIUM — protected by multiple loading guards**

`self.quest_instance` is a `PyQuest.PyQuest()` created once (line 1029) and never nulled
on map transition. When `_set_quests_data` runs, it calls:

```python
active_quest = self.quest_instance.get_active_quest_id()     # line 1190
quest_log = self.quest_instance.get_quest_log_ids()          # line 1193
```

Whether these access stale native memory depends on how `PyQuest` is implemented. If it
re-queries the current context on each call (like most Py4GW wrappers), it's safe. If it
cached a pointer at construction time, it's a crash vector.

### CV-5: Direct native struct reads in hero/pet paths

**Severity: LOW — protected by IsValid + null checks, but window exists**

Hero path (SharedMemory.py:1505–1509):
```python
hero_agent_instance = Agent.GetAgentByID(agent_id)
if hero_agent_instance is None:
    return
playerx, playery, playerz = hero_agent_instance.pos.x, hero_agent_instance.pos.y, hero_agent_instance.z
```

Pet path (SharedMemory.py:1699–1703):
```python
agent_instance = Agent.GetAgentByID(agent_id)
if agent_instance is None:
    return
playerx, playery, playerz = agent_instance.pos.x, agent_instance.pos.y, agent_instance.z
```

`Agent.GetAgentByID()` returns a **live ctypes pointer** to the native agent struct. The
subsequent `.pos.x` reads are direct memory accesses. If the agent is valid at the moment
of `GetAgentByID()` but becomes invalid (freed/relocated) between that call and the `.pos`
read, this crashes.

The window is nanoseconds under normal conditions — essentially zero risk during normal
gameplay. But during map transitions, if the agent array is being reconstructed, the
`IsValid()` check could pass on a partially-initialized slot, and the subsequent read could
hit uninitialized memory.

---

## Why the Crash Is Specifically "Just After Loading Finishes"

The timing matches CV-1 precisely:

1. During loading, the interpreter gets frozen for part of the process
2. When it resumes, the map may have just finished loading (`Map.IsMapLoading()` = False)
3. `Player.GetAccountEmail()` may return `""` if the player context isn't fully initialized
4. `SetPlayerData` exits early without refreshing the party_instance
5. `SetHeroesData` reads `party_instance.heroes` from the stale (freed) native pointer
6. **Crash**

The reason it's not 100% reproducible is that the timing varies:
- Sometimes the email IS available on the first post-load frame → `_updatechache()` runs →
  party_instance gets refreshed → no crash
- Sometimes it isn't → crash
- The probability depends on how fast the game initializes the player context after marking
  the map as loaded

---

## The Fix Priorities

### Fix 1: Guard SetHeroesData and SetPetData at the top level

The iteration over `self.party_instance.heroes` and `self.party_instance.GetPetInfo()`
happens BEFORE any loading check. Add top-level guards:

```python
def SetHeroesData(self):
    if Map.IsMapLoading() or Map.IsInCinematic():
        return
    if self.party_instance is None or not Player.IsPlayerLoaded():
        return
    if not Map.IsMapReady():
        return
    # ... existing code ...
```

Same for `SetPetData()`.

### Fix 2: Decouple _updatechache() from SetPlayerData

`_updatechache()` refreshes critical cached objects (party_instance, agent_instance,
effects_instance). Currently it only runs if `SetPlayerData` gets past the email check.
It should run unconditionally at the start of every frame, regardless of whether we're
writing player data:

```python
# Environment Upkeeper main():
GLOBAL_CACHE._update_cache()
GLOBAL_CACHE.ShMem._updatechache()            # ← always refresh, regardless of email
account_email = Player.GetAccountEmail()
GLOBAL_CACHE.ShMem.SetPlayerData(account_email)
GLOBAL_CACHE.ShMem.SetHeroesData()
GLOBAL_CACHE.ShMem.SetPetData()
```

Or move the `_updatechache()` call out of `SetPlayerData` entirely and into the upkeeper.

### Fix 3: Clear _title_instances and quest_instance on map transition

In `_updatechache()`, during the loading branch (line 999–1009), clear the stale caches:

```python
if (Map.IsMapLoading() or Map.IsInCinematic()):
    if self.party_instance is not None:
        self.party_instance.GetContext()
    self.agent_instance = None
    self.effects_instance = None
    self._title_instances.clear()      # ← ADD: clear stale title pointers
    self.quest_instance = None          # ← ADD: clear stale quest instance
    return
```

### Fix 4: Null party_instance during loading (instead of just calling GetContext)

Currently `party_instance` is preserved across map transitions, with `GetContext()` called
to "refresh" it. This is dangerous if `GetContext()` itself dereferences a stale pointer
internally. Safer to null it and re-create:

```python
if (Map.IsMapLoading() or Map.IsInCinematic()):
    self.party_instance = None          # ← null instead of GetContext()
    self.agent_instance = None
    self.effects_instance = None
    self._title_instances.clear()
    self.quest_instance = None
    return
```

Then on the first non-loading frame, line 1012–1013 will create a fresh instance:
```python
if self.party_instance is None:
    self.party_instance = Party.party_instance()  # fresh PyParty with new map's context
```

### Fix 5: Guard Environment Upkeeper's shmem calls

Add a unified loading guard in the upkeeper itself, so shmem writes don't even attempt to
run until the map is ready:

```python
def main():
    GLOBAL_CACHE._update_cache()
    GLOBAL_CACHE.ShMem._updatechache()  # always refresh cached objects

    if Map.IsMapLoading() or Map.IsInCinematic():
        # Don't touch shmem data during transitions
        widget_config.action_queue_manager.ResetNonTransitionQueues()
        if widget_config.throttle_transition_queue.IsExpired():
            widget_config.action_queue_manager.ProcessQueue("TRANSITION")
            widget_config.throttle_transition_queue.Reset()
        return

    account_email = Player.GetAccountEmail()
    GLOBAL_CACHE.ShMem.SetPlayerData(account_email)
    GLOBAL_CACHE.ShMem.SetHeroesData()
    GLOBAL_CACHE.ShMem.SetPetData()
    # ... rest of main ...
```

---

## Summary

| Vector | What | Likelihood | Severity | Fix |
|--------|------|------------|----------|-----|
| CV-1 | `SetHeroesData` reads stale `party_instance.heroes` when `SetPlayerData` skipped `_updatechache()` | **High** — matches observed crash pattern | **Crash** | Fix 1 + Fix 2 |
| CV-2 | `GetContext()` dereferences freed PartyContext during loading | Medium — depends on C++ impl | **Crash** | Fix 4 |
| CV-3 | `_title_instances` holds freed native `TitleStruct` pointers | Medium — delayed until guards pass | **Crash or garbage** | Fix 3 |
| CV-4 | `quest_instance` holds stale native refs | Low-Medium | **Crash or garbage** | Fix 3 |
| CV-5 | Direct `.pos.x` reads on just-spawned agents | Low | **Crash** (rare) | Already guarded (IsValid check) |

The minimum fix for the observed crash is **Fix 1 + Fix 2**: ensure `_updatechache()` runs
every frame regardless of email availability, and guard the hero/pet iteration at the top
level.

The robust fix is all five: null all cached native objects during loading (Fix 3 + Fix 4),
decouple cache refresh from data writing (Fix 2), guard all native memory access at the
entry point (Fix 1 + Fix 5).
