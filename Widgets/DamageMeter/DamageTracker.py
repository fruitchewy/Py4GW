import time
from collections import deque
from typing import Dict

from Py4GWCoreLib import (
    CombatEvents,
    Agent,
    Player,
    ConsoleLog,
    AgentArray,
    ThrottledTimer,
)
from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.enums import Allegiance

MODULE_NAME = "DamageTracker"
DEBUG = False  # Enable debug logging

class AgentStats:
    def __init__(self, agent_id: int, name: str):
        self.agent_id = agent_id
        self.name = name
        self.primary_profession = 0
        self.primary_texture = ""
        self.total_damage = 0.0
        self.total_healing = 0.0
        self.is_human_player = False  # True for real players (not heroes/NPCs)

    def add_damage(self, amount: float):
        self.total_damage += amount

    def add_healing(self, amount: float):
        self.total_healing += amount

class DamageTracker:
    def __init__(self):
        self.overall_stats: Dict[int, AgentStats] = {}
        self.current_stats: Dict[int, AgentStats] = {}
        
        self.is_running = True
        
        self.max_damage_overall = 1.0
        self.max_healing_overall = 1.0
        self.max_damage_current = 1.0
        self.max_healing_current = 1.0
        
        self.last_event_time = 0.0
        self.encounter_timeout = 8.0
        
        # Queue and Cache for batched processing
        self._damage_queue = deque()
        self._max_healths: Dict[int, int] = {}

        # Display Cache
        self.cache_overall_damage = []
        self.cache_overall_healing = []
        self.cache_current_damage = []
        self.cache_current_healing = []
        self._stats_dirty = False  # Track when stats need re-sorting
        
        # DPS Graph Data (1s sliding window)
        self._recent_damage_events = deque() # (timestamp, amount)
        self.current_window_damage = 0.0
        self.dps_history = deque(maxlen=600) # 60 seconds history at 100ms sample rate
        self.dps_sample_timer = ThrottledTimer(100)
        self.dps_window_duration = 1.0
        
        # Map change detection
        self.current_map_id = 0
        self._last_player_agent_id = 0 # Track player agent ID for map change detection
        
        # Timers
        self.sort_timer = ThrottledTimer(500)
        self.update_timer = ThrottledTimer(33)  # ~30 FPS update rate

        self._register_callbacks()

    def reset(self):
        self.is_running = True # Keep running
        self.max_damage_overall = 1.0
        self.max_healing_overall = 1.0
        self.max_damage_current = 1.0
        self.max_healing_current = 1.0
        self.last_event_time = 0.0
        
        self._damage_queue.clear()
        self._max_healths.clear()

        self.overall_stats.clear()
        self.current_stats.clear()
        self._clear_caches()
        self._stats_dirty = False
        
        self._recent_damage_events.clear()
        self.current_window_damage = 0.0
        self.dps_history.clear()
        self.dps_sample_timer.Reset()

    def _clear_caches(self):
        self.cache_overall_damage.clear()
        self.cache_overall_healing.clear()
        self.cache_current_damage.clear()
        self.cache_current_healing.clear()
        
    def reset_current_encounter(self):
        if DEBUG: ConsoleLog(MODULE_NAME, "New encounter started. Resetting current stats.")
        self.current_stats.clear()
        self.max_damage_current = 1.0
        self.max_healing_current = 1.0
        self.cache_current_damage.clear()
        self.cache_current_healing.clear()

    def _register_callbacks(self):
        CombatEvents.on_damage(self.on_damage)
        ConsoleLog(MODULE_NAME, "Callbacks registered.")

    def on_damage(self, target_id: int, source_id: int, damage_fraction: float, skill_id: int):
        if not self.is_running:
            return
        
        # Safety cap to prevent memory issues or processing lag
        if len(self._damage_queue) > 500:
            ConsoleLog(MODULE_NAME, "Damage queue full, dropping oldest event.")
            self._damage_queue.popleft()
            
        # Lightweight append only
        self._damage_queue.append((time.time(), target_id, source_id, damage_fraction))
    
    def get_caches(self):
        self._refresh_sort()
        return (self.cache_overall_damage, self.cache_overall_healing, 
                self.cache_current_damage, self.cache_current_healing)

    def get_dps_history(self):
        return list(self.dps_history)

    def _refresh_sort(self):
        # Only sort if data has changed and timer expired
        if not self._stats_dirty:
            return
        if self.sort_timer.IsExpired():
            self.sort_timer.Reset()
            self.cache_overall_damage = sorted(self.overall_stats.values(), key=lambda s: s.total_damage, reverse=True)
            self.cache_overall_healing = sorted(self.overall_stats.values(), key=lambda s: s.total_healing, reverse=True)
            self.cache_current_damage = sorted(self.current_stats.values(), key=lambda s: s.total_damage, reverse=True)
            self.cache_current_healing = sorted(self.current_stats.values(), key=lambda s: s.total_healing, reverse=True)
            self._stats_dirty = False

    def _update_stat_entry(self, stats_dict, source_id, amount, is_healing, is_damage):
        source_stat = stats_dict.get(source_id)

        if source_stat is None:
            if Agent.IsValid(source_id):
                if not Agent.CanBeViewedInPartyWindow(source_id):
                    return None

                name = Agent.GetNameByID(source_id)
                source_stat = AgentStats(source_id, name)
                stats_dict[source_id] = source_stat

                primary_prof, _ = Agent.GetProfessions(source_id)
                source_stat.primary_profession = primary_prof

                primary_texture, _ = Agent.GetProfessionsTexturePaths(source_id)
                source_stat.primary_texture = primary_texture

                # Detect if this is a human player (not hero/NPC)
                # Heroes and NPCs have login_number == 0
                # Real players have login_number != 0
                login_number = Agent.GetLoginNumber(source_id)
                source_stat.is_human_player = (login_number != 0)
            else:
                return None

        if is_healing:
            source_stat.add_healing(amount)
        else:
            source_stat.add_damage(amount)

        return source_stat

    def update(self):
        """Process queued damage events. Call this periodically (e.g. every frame or 33ms)."""
        if not self.update_timer.IsExpired():
            return
        self.update_timer.Reset()

        if not self._damage_queue:
            return

        # Optimization: Process limited batch with time budget
        start_time = time.perf_counter()

        while self._damage_queue:
            # Time budget check (8ms for 30 FPS = ~24% frame time)
            if (time.perf_counter() - start_time) > 0.008:
                break
            timestamp, target_id, source_id, damage_fraction = self._damage_queue.popleft()

            # Health Change: > 0 is Healing, < 0 is Damage
            is_damage = damage_fraction < 0
            is_healing = damage_fraction > 0

            if not is_healing and not is_damage:
                continue

            if is_damage:
                # Ignore self-damage
                if source_id == target_id:
                    if DEBUG: ConsoleLog(MODULE_NAME, f"Skipping: Self damage (ID: {source_id})")
                    continue

            # Encounter logic - check on every damage event for accurate encounter detection
            if is_damage:
                if self.last_event_time > 0 and (timestamp - self.last_event_time > self.encounter_timeout):
                    self.reset_current_encounter()
                self.last_event_time = timestamp

            # Resolve Max Health (Cached)
            max_hp = self._max_healths.get(target_id, 0)
            if max_hp == 0:
                max_hp = Agent.GetMaxHealth(target_id)
                if max_hp > 0:
                    self._max_healths[target_id] = max_hp
                else:
                    max_hp = 500 # Fallback default
                    self._max_healths[target_id] = max_hp # Cache fallback to avoid retry spam

            actual_amount = abs(damage_fraction) * max_hp

            if actual_amount <= 0:
                continue

            # Track for DPS Graph (Party total damage)
            if is_damage:
                self._recent_damage_events.append((timestamp, actual_amount))
                self.current_window_damage += actual_amount

            # Update Overall
            stat_overall = self._update_stat_entry(self.overall_stats, source_id, actual_amount, is_healing, is_damage)
            if stat_overall:
                self._stats_dirty = True
                if is_healing:
                    if stat_overall.total_healing > self.max_healing_overall: self.max_healing_overall = stat_overall.total_healing
                else:
                    if stat_overall.total_damage > self.max_damage_overall: self.max_damage_overall = stat_overall.total_damage

            # Update Current
            stat_current = self._update_stat_entry(self.current_stats, source_id, actual_amount, is_healing, is_damage)
            if stat_current:
                if is_healing:
                    if stat_current.total_healing > self.max_healing_current: self.max_healing_current = stat_current.total_healing
                else:
                    if stat_current.total_damage > self.max_damage_current: self.max_damage_current = stat_current.total_damage

        # Update DPS Graph Data
        if self.dps_sample_timer.IsExpired():
            self.dps_sample_timer.Reset()
            now = time.time()
            window_start = now - self.dps_window_duration

            # Prune old events - only if we have events to check
            if self._recent_damage_events:
                while self._recent_damage_events and self._recent_damage_events[0][0] < window_start:
                    removed_event = self._recent_damage_events.popleft()
                    self.current_window_damage -= removed_event[1]

                if not self._recent_damage_events:
                    self.current_window_damage = 0.0

            # Calculate current 1s DPS
            duration = max(0.1, self.dps_window_duration)
            current_dps = max(0.0, self.current_window_damage) / duration
            self.dps_history.append(current_dps)
