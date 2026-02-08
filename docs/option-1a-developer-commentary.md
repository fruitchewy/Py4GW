# Option 1A Developer Commentary: What the Problem Actually Is

## The Misconception

The original 1A description frames the problem as "stale native pointers get written into
shared memory." This is inaccurate. **The shared memory struct never holds native pointers.**
It holds materialized Python values — `c_float`, `c_uint`, `c_bool` — plain numbers.

## What the Agent API Already Does

Every `Agent.Get*()` function already handles stale pointers internally:

```python
# Agent.py — every accessor follows this pattern:
@staticmethod
def GetHealth(agent_id: int) -> float:
    living = Agent.GetLivingAgentByID(agent_id)  # re-lookups agent, validates
    if living is None:
        return 0.0           # safe default, not a crash
    return living.hp          # materialized Python float
```

The call chain for each field is:

```
Agent.GetHealth(agent_id)
  → Agent.GetLivingAgentByID(agent_id)
    → @_require_valid decorator
      → Agent.IsValid(agent_id)
        → AgentArray.GetAgentByID(agent_id)  # native lookup, returns None if invalid
      → if invalid: return None
    → AgentArray.GetAgentByID(agent_id)      # second native lookup
    → agent.GetAsAgentLiving()               # ctypes cast to living struct
  → if None: return 0.0
  → return living.hp                          # plain Python float
```

The returned value is a **Python float**. When SharedMemory does
`agent_data.Health = Agent.GetHealth(agent_id)`, it writes `0.75` or `0.0` into a
`c_float` field. The shmem struct is already a copy of materialized values, completely
disconnected from native memory.

## What Actually Goes Wrong

The problem is not that pointers leak into shmem. The problem is that `_set_agent_data`
makes **60+ individual Agent API calls in sequence**, and the agent can become invalid
between any two of them:

```python
def _set_agent_data(index):
    agent_id = self.agent_instance.agent_id    # cached native pointer → int

    agent_data.Health = Agent.GetHealth(agent_id)        # → 0.75 (agent valid)
    agent_data.MaxHealth = Agent.GetMaxHealth(agent_id)  # → 480  (agent valid)
    agent_data.HealthPips = ...                          # → 3    (agent valid)
    #
    # ── agent becomes invalid here (map transition, despawn, etc.) ──
    #
    agent_data.XYZ[0] = Agent.GetXYZ(agent_id)[0]       # → 0.0  (agent invalid, returns default)
    agent_data.XYZ[1] = Agent.GetXYZ(agent_id)[1]       # → 0.0
    agent_data.XYZ[2] = Agent.GetXYZ(agent_id)[2]       # → 0.0
    agent_data.Is_Bleeding = Agent.IsBleeding(agent_id)  # → False
    agent_data.Is_Moving = Agent.IsMoving(agent_id)      # → False
    agent_data.Is_Alive = Agent.IsAlive(agent_id)        # → False
```

The result is a shmem slot containing:
- Health = 0.75, MaxHealth = 480, HealthPips = 3 (real values from before invalidation)
- XYZ = (0.0, 0.0, 0.0), Is_Alive = False (defaults from after invalidation)

This is a **half-valid frame**: the character appears to have 75% health but is at position
(0,0,0) and flagged as dead. HeroAI reads this and sees a ghost — alive by health, dead by
flag, at the map origin.

The same pattern applies across the 13 `_set_*_data` helpers. If the agent becomes invalid
after `_set_player_data` but before `_set_buff_data`, the slot has real position/health but
zeroed buffs. If it happens after `_set_buff_data` but before `_set_skill_data`, real buffs
but zeroed skills. Any split point produces an internally inconsistent frame.

## What 1A Should Actually Be

The original 1A description ("snapshot all native data into a Python dict first") is solving
a problem that's already solved — values are already materialized by the API layer. The real
improvement is about **batch atomicity**: making the entire 60-call sequence all-or-nothing.

### The All-or-Nothing Gate

```python
def _set_agent_data(index):
    agent_id = self.agent_instance.agent_id if self.agent_instance else 0
    if not Agent.IsValid(agent_id):
        return  # skip entirely — don't write a mix of real and default values

    try:
        # All 60+ calls here
        health = Agent.GetHealth(agent_id)
        max_health = Agent.GetMaxHealth(agent_id)
        xyz = Agent.GetXYZ(agent_id)
        is_bleeding = Agent.IsBleeding(agent_id)
        # ... etc ...
    except Exception:
        return  # agent went invalid mid-batch — discard everything

    # Only commit if ALL reads succeeded with non-default values
    agent_data = self.GetStruct().AccountData[index].PlayerData.AgentData
    agent_data.Health = health
    agent_data.MaxHealth = max_health
    agent_data.XYZ[0] = xyz[0]
    # ... etc ...
```

This does NOT change what data is collected or how it's materialized — the Agent API
already handles that. What it changes is: **if any read returns a default because the agent
went invalid, the entire batch is discarded and the previous frame's data stays in shmem.**

The previous frame's data is stale by one frame (16-66ms) but at least it's internally
consistent. A reader seeing last frame's coherent snapshot is strictly better than seeing
this frame's half-valid snapshot.

### The Redundant Lookup Problem

A secondary issue: each of the 60+ `Agent.Get*()` calls independently does the full
validation chain:

```
Agent.GetHealth(agent_id)     → IsValid() → GetAgentByID() → GetLivingAgentByID() → living.hp
Agent.GetMaxHealth(agent_id)  → IsValid() → GetAgentByID() → GetLivingAgentByID() → living.max_hp
Agent.GetEnergy(agent_id)     → IsValid() → GetAgentByID() → GetLivingAgentByID() → living.energy
```

That's 60+ individual `IsValid()` checks, each doing `AgentArray.GetAgentByID()` which
walks a native array. For fields that all come from the same `AgentLivingStruct`, this is
redundant — one lookup would suffice:

```python
living = Agent.GetLivingAgentByID(agent_id)
if living is None:
    return  # skip batch

# Read all fields from the same struct in one go — no redundant lookups
health = living.hp
max_health = living.max_hp
energy = living.energy
is_bleeding = living.is_bleeding
# ... etc ...
```

This collapses 60 native lookups into 1, and the read window (where the struct must remain
valid) shrinks from "the duration of 60 separate API calls with Python overhead between
each" to "the duration of 60 attribute reads on the same Python object."

The native pointer is still live during those reads, so it's not zero-risk. But the window
is microseconds instead of milliseconds.

## Summary: What 1A Actually Solves

| Aspect | Current Behavior | With Corrected 1A |
|--------|-----------------|-------------------|
| Values materialized before shmem write | Already yes (Agent API returns Python values) | Same |
| Stale pointer causes crash | Unlikely (API returns safe defaults) | Same |
| Half-valid frames committed to shmem | **Yes** — some fields real, some defaults | **No** — entire batch discarded on failure |
| Redundant native lookups per frame | 60+ independent lookups for the same agent | 1 lookup, 60 attribute reads |
| Reader sees internally inconsistent data | Yes — until next successful frame | No — sees last good frame instead |

The core insight: the shared memory layer doesn't need to worry about native pointers
because it never touches them. What it needs is **batch discipline** — don't commit a frame
that you know is partially invalid.
