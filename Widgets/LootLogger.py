"""
Loot Logger Widget
Logs all yellow (gold) and green (unique) rarity loot seen on the ground,
tracking item name, requirement, and assigned player. Persists data to disk.
"""

import json
import os
import re
import traceback
from datetime import datetime
from typing import Dict, List, Set

import Py4GW
from Py4GWCoreLib import (
    Color,
    GLOBAL_CACHE,
    Agent,
    AgentArray,
    Attribute,
    AttributeNames,
    IconsFontAwesome5,
    IniHandler,
    Item,
    Map,
    Party,
    PyImGui,
    Routines,
    ThrottledTimer,
    Timer,
)

# =============================================================================
# Constants
# =============================================================================

MODULE_NAME = "Loot Logger"
module_name = "Loot Logger"  # Required for Widget Manager registration

# Guild Wars item colors (RGBA) - using normalized values for PyImGui
# Gold: RGB(255, 204, 50) -> normalized (1.0, 0.8, 0.196, 1.0)
# Green: RGB(0, 255, 0) -> normalized (0.0, 1.0, 0.0, 1.0)
COLOR_GOLD = (1.0, 0.8, 0.196, 1.0)  # Normalized RGBA for gold/rare loot
COLOR_GREEN = (0.0, 1.0, 0.0, 1.0)    # Normalized RGBA for green/unique loot

# Modifier identifier for attribute requirements
MODIFIER_REQUIREMENT = 10136

# Minimized window size
MINIMIZED_SIZE = 48.0

# =============================================================================
# Paths
# =============================================================================

_project_root = Py4GW.Console.get_projects_path()
_config_dir = os.path.join(_project_root, "Widgets", "Config")
os.makedirs(_config_dir, exist_ok=True)

PATH_WINDOW_INI = os.path.join(_config_dir, "loot_logger_window.ini")
PATH_LOOT_DATA = os.path.join(_config_dir, "loot_logger_data.json")

# =============================================================================
# State
# =============================================================================


class WindowState:
    """Manages window position, collapse state, and minimized mode."""

    def __init__(self):
        self.ini = IniHandler(PATH_WINDOW_INI)
        self.save_timer = Timer()
        self.save_timer.Start()
        self.first_run = True
        self.needs_reposition = False  # Set when switching between modes
        self.x = self.ini.read_int(MODULE_NAME, "x", 100)
        self.y = self.ini.read_int(MODULE_NAME, "y", 100)
        self.collapsed = self.ini.read_bool(MODULE_NAME, "collapsed", False)
        self.minimized = self.ini.read_bool(MODULE_NAME, "minimized", False)

    def save_if_changed(self, new_x: int, new_y: int, new_collapsed: bool):
        """Save window state if changed (throttled to 1s intervals)."""
        if not self.save_timer.HasElapsed(1000):
            return

        if (new_x, new_y) != (self.x, self.y):
            self.x, self.y = new_x, new_y
            self.ini.write_key(MODULE_NAME, "x", str(self.x))
            self.ini.write_key(MODULE_NAME, "y", str(self.y))

        if new_collapsed != self.collapsed:
            self.collapsed = new_collapsed
            self.ini.write_key(MODULE_NAME, "collapsed", str(self.collapsed))

        self.save_timer.Reset()

    def save_minimized(self):
        """Save minimized state immediately."""
        self.ini.write_key(MODULE_NAME, "minimized", str(self.minimized))


class LootState:
    """Manages loot scanning and logging state."""

    def __init__(self):
        self.scan_timer = ThrottledTimer(500)
        self.save_timer = ThrottledTimer(5000)
        self.seen_agents: Set[int] = set()
        self.pending_names: Dict[int, int] = {}  # entry_index -> item_id
        self.entries: List[dict] = []
        self.pending_save = False
        self.last_map_id = 0
        self.new_drops_count = 0  # Drops since window was last expanded
        self.last_seen_count = 0  # Entry count when window was minimized

    def reset_for_new_map(self):
        """Clear tracking state when map changes."""
        self.seen_agents.clear()
        self.pending_names.clear()


# Global state instances
_window = WindowState()
_loot = LootState()

# =============================================================================
# Utility Functions
# =============================================================================


def strip_formatting_tags(text: str) -> str:
    """Remove GW formatting tags like <c=@ItemRare>...</c> from text."""
    if not text:
        return text
    # Keep stripping tags until none remain (handles nested tags)
    prev = ""
    while prev != text:
        prev = text
        text = re.sub(r"<[^>]+>", "", text)
    return text


def get_item_requirement(item_id: int) -> str:
    """Extract attribute requirement from item modifiers."""
    try:
        modifiers = Item.Customization.Modifiers.GetModifiers(item_id)
        for mod in modifiers:
            if mod.GetIdentifier() == MODIFIER_REQUIREMENT:
                attr_id = mod.GetArg1()
                level = mod.GetArg2()

                try:
                    attr_enum = Attribute(attr_id)
                    attr_name = AttributeNames.get(attr_enum, attr_enum.name)
                except ValueError:
                    attr_name = f"Attr{attr_id}"
                return f"{level} {attr_name}"
    except Exception:
        pass
    return ""


def get_owner_name(owner_agent_id: int) -> str:
    """Resolve the name of the player assigned to loot."""
    if owner_agent_id == 0:
        return "Unassigned"

    try:
        login_number = Agent.GetLoginNumber(owner_agent_id)
        if login_number > 0:
            name = Party.Players.GetPlayerNameByLoginNumber(login_number)
            if name:
                return name
    except Exception:
        pass

    return f"Agent {owner_agent_id}"


def get_item_rarity(item_id: int) -> str:
    """Get the rarity of an item as a string."""
    try:
        _, rarity_name = Item.Rarity.GetRarity(item_id)
        return rarity_name
    except Exception:
        return ""


# =============================================================================
# Persistence
# =============================================================================


def migrate_legacy_entries():
    """Migrate old entries that are missing the rarity field."""
    migrated = False
    for entry in _loot.entries:
        # Add missing rarity field (default to Gold for old entries)
        if "rarity" not in entry:
            entry["rarity"] = "Gold"
            migrated = True
        # Clean up any remaining formatting tags in item names
        if "item_name" in entry and "<" in entry["item_name"]:
            entry["item_name"] = strip_formatting_tags(entry["item_name"])
            migrated = True
    
    if migrated:
        _loot.pending_save = True
        Py4GW.Console.Log(
            MODULE_NAME,
            "Migrated legacy loot entries",
            Py4GW.Console.MessageType.Info,
        )


def load_loot_log():
    """Load loot entries from disk."""
    if not os.path.exists(PATH_LOOT_DATA):
        return

    try:
        with open(PATH_LOOT_DATA, "r", encoding="utf-8") as f:
            data = json.load(f)
            _loot.entries = data.get("entries", [])
            _loot.last_seen_count = len(_loot.entries)
            # Migrate old entries that might be missing fields
            migrate_legacy_entries()
    except Exception as e:
        Py4GW.Console.Log(
            MODULE_NAME,
            f"Failed to load loot log: {e}",
            Py4GW.Console.MessageType.Warning,
        )
        _loot.entries = []


def save_loot_log():
    """Save loot entries to disk."""
    try:
        with open(PATH_LOOT_DATA, "w", encoding="utf-8") as f:
            json.dump({"entries": _loot.entries}, f, indent=2, ensure_ascii=False)
        _loot.pending_save = False
    except Exception as e:
        Py4GW.Console.Log(
            MODULE_NAME,
            f"Failed to save loot log: {e}",
            Py4GW.Console.MessageType.Warning,
        )


# =============================================================================
# Loot Scanning
# =============================================================================


def resolve_pending_names():
    """Attempt to resolve item names that weren't ready on first scan."""
    resolved = []
    for entry_idx, item_id in _loot.pending_names.items():
        try:
            name = GLOBAL_CACHE.Item.GetName(item_id)
            if name and not name.startswith("Item "):
                if entry_idx < len(_loot.entries):
                    _loot.entries[entry_idx]["item_name"] = strip_formatting_tags(name)
                    _loot.pending_save = True
                resolved.append(entry_idx)
        except Exception:
            pass

    for idx in resolved:
        _loot.pending_names.pop(idx, None)


def scan_for_loot():
    """Scan for gold and green rarity items on the ground and log them."""
    # Handle map changes
    current_map = Map.GetMapID()
    if current_map != _loot.last_map_id:
        _loot.reset_for_new_map()
        _loot.last_map_id = current_map

    # Try to resolve any pending item names
    resolve_pending_names()

    # Scan ground items
    for agent_id in AgentArray.GetItemArray():
        if agent_id in _loot.seen_agents:
            continue

        _loot.seen_agents.add(agent_id)

        # Get item data
        item_agent = Agent.GetItemAgentByID(agent_id)
        if item_agent is None:
            continue

        item_id = item_agent.item_id
        if item_id == 0:
            continue

        # Filter to gold or green rarity only
        try:
            is_gold = Item.Rarity.IsGold(item_id)
            is_green = Item.Rarity.IsGreen(item_id)
            if not (is_gold or is_green):
                continue
            rarity = "Gold" if is_gold else "Green"
        except Exception:
            continue

        # Get item name (async - may not be ready yet)
        needs_name_resolution = False
        try:
            item_name = GLOBAL_CACHE.Item.GetName(item_id)
            if item_name:
                item_name = strip_formatting_tags(item_name)
            else:
                item_name = f"Item {item_id}"
                needs_name_resolution = True
        except Exception:
            item_name = f"Item {item_id}"
            needs_name_resolution = True

        # Get model ID
        try:
            model_id = Item.GetModelID(item_id)
        except Exception:
            model_id = 0

        # Build entry
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "item_name": item_name,
            "model_id": model_id,
            "requirement": get_item_requirement(item_id),
            "assigned_to": get_owner_name(Agent.GetItemAgentOwnerID(agent_id)),
            "rarity": rarity,
            "item_id": item_id,
        }

        _loot.entries.append(entry)
        _loot.pending_save = True

        # Track new drops for minimized badge
        if _window.minimized:
            _loot.new_drops_count += 1

        if needs_name_resolution:
            _loot.pending_names[len(_loot.entries) - 1] = item_id


# =============================================================================
# UI
# =============================================================================


def draw_minimized_widget():
    """Draw the minimized loot logger as a small icon with badge."""
    # Set position only when switching modes or first run
    if _window.needs_reposition or _window.first_run:
        PyImGui.set_next_window_pos(_window.x, _window.y)
        _window.needs_reposition = False
    PyImGui.set_next_window_size(MINIMIZED_SIZE, MINIMIZED_SIZE)

    # Window flags for minimized mode
    flags = (
        PyImGui.WindowFlags.NoTitleBar
        | PyImGui.WindowFlags.NoResize
        | PyImGui.WindowFlags.NoScrollbar
        | PyImGui.WindowFlags.AlwaysAutoResize
    )

    if PyImGui.begin(f"{MODULE_NAME}##minimized", flags):
        pos = PyImGui.get_window_pos()

        # Add padding to prevent button clipping
        PyImGui.set_cursor_pos(4, 4)
        
        # Draw coins icon as button
        icon = IconsFontAwesome5.ICON_COINS
        button_size = MINIMIZED_SIZE - 8
        if PyImGui.button(f"{icon}##expand", button_size, button_size):
            # Save current position before switching
            _window.x, _window.y = int(pos[0]), int(pos[1])
            _window.minimized = False
            _window.needs_reposition = True  # Tell expanded window to reposition
            _window.save_minimized()
            _loot.new_drops_count = 0
            _loot.last_seen_count = len(_loot.entries)

        # Draw badge with new drop count if > 0
        if _loot.new_drops_count > 0:
            badge_text = str(_loot.new_drops_count) if _loot.new_drops_count < 100 else "99+"
            PyImGui.set_cursor_pos(MINIMIZED_SIZE - 20, 2)
            PyImGui.text_colored(badge_text, COLOR_GOLD)

        _window.save_if_changed(int(pos[0]), int(pos[1]), False)

    PyImGui.end()


def draw_expanded_widget():
    """Draw the full loot logger window."""
    if _window.first_run or _window.needs_reposition:
        PyImGui.set_next_window_size(500.0, 300.0)
        PyImGui.set_next_window_pos(_window.x, _window.y)
        PyImGui.set_next_window_collapsed(_window.collapsed, 0)
        _window.first_run = False
        _window.needs_reposition = False

    is_open = PyImGui.begin(MODULE_NAME, 0)
    new_collapsed = PyImGui.is_window_collapsed()
    pos = PyImGui.get_window_pos()

    if is_open:
        # Header row with minimize button
        if PyImGui.button(f"{IconsFontAwesome5.ICON_MINUS_SQUARE}##minimize"):
            # Save current position before switching
            _window.x, _window.y = int(pos[0]), int(pos[1])
            _window.minimized = True
            _window.needs_reposition = True  # Tell minimized window to reposition
            _window.save_minimized()
            _loot.new_drops_count = 0
            _loot.last_seen_count = len(_loot.entries)

        PyImGui.same_line(0, 10)
        PyImGui.text(f"Rare Loot Logged: {len(_loot.entries)}")
        PyImGui.same_line(0, 20)
        if PyImGui.button("Clear Log"):
            _loot.entries.clear()
            _loot.pending_save = True

        PyImGui.separator()

        # Loot table
        table_flags = (
            PyImGui.TableFlags.Borders
            | PyImGui.TableFlags.RowBg
            | PyImGui.TableFlags.ScrollY
            | PyImGui.TableFlags.Resizable
        )

        if PyImGui.begin_table("LootLogTable", 4, table_flags):
            PyImGui.table_setup_column("Time", 0, 70.0)
            PyImGui.table_setup_column("Item Name", 0, 200.0)
            PyImGui.table_setup_column("Req", 0, 100.0)
            PyImGui.table_setup_column("Assigned To", 0, 110.0)
            PyImGui.table_headers_row()

            for entry in reversed(_loot.entries):
                PyImGui.table_next_row()

                # Time (show only HH:MM:SS)
                PyImGui.table_next_column()
                timestamp = entry.get("timestamp", "")
                time_only = timestamp.split(" ")[1] if " " in timestamp else timestamp
                PyImGui.text(time_only)

                # Item Name (colored by rarity)
                PyImGui.table_next_column()
                rarity = entry.get("rarity", "Gold")
                # Case-insensitive rarity check
                if rarity and rarity.lower() == "green":
                    color = COLOR_GREEN
                else:
                    # Gold, Rare, Yellow, or any other rarity
                    color = COLOR_GOLD
                PyImGui.text_colored(entry.get("item_name", "Unknown"), color)

                # Requirement
                PyImGui.table_next_column()
                PyImGui.text(entry.get("requirement", ""))

                # Assigned To
                PyImGui.table_next_column()
                PyImGui.text(entry.get("assigned_to", ""))

            PyImGui.end_table()

    PyImGui.end()

    # Save window state
    _window.save_if_changed(int(pos[0]), int(pos[1]), new_collapsed)


def draw_widget():
    """Draw the appropriate widget based on minimized state."""
    if _window.minimized:
        draw_minimized_widget()
    else:
        draw_expanded_widget()


# =============================================================================
# Entry Points
# =============================================================================

# Load saved data on module import
load_loot_log()


def configure():
    """Configuration entry point (required by widget system)."""
    pass


def main():
    """Main entry point called every frame."""
    try:
        # Validate game state
        if not Routines.Checks.Map.MapValid():
            return
        if not Routines.Checks.Map.IsMapReady():
            return
        if not Routines.Checks.Party.IsPartyLoaded():
            return

        # Throttled loot scanning
        if _loot.scan_timer.IsExpired():
            scan_for_loot()
            _loot.scan_timer.Reset()

        # Throttled persistence
        if _loot.pending_save and _loot.save_timer.IsExpired():
            save_loot_log()
            _loot.save_timer.Reset()

        # Draw UI
        draw_widget()

    except Exception as e:
        Py4GW.Console.Log(
            MODULE_NAME, f"Error: {e}", Py4GW.Console.MessageType.Error
        )
        Py4GW.Console.Log(
            MODULE_NAME, traceback.format_exc(), Py4GW.Console.MessageType.Error
        )


if __name__ == "__main__":
    main()
