import sys
import Py4GW
from Py4GWCoreLib import Routines, PyImGui, IniHandler, Timer, ThrottledTimer, ConsoleLog
from Widgets.DamageMeter.DamageTracker import DamageTracker
from Widgets.DamageMeter.gui.bar_chart import render_window
from Widgets.DamageMeter.gui.dps_graph import render_dps_graph_window
from Widgets.DamageMeter.gui.config import render_config

# Constants
INI_FILENAME = "damage_meter.ini"
WINDOW_FLAGS = 0

# State
ini = IniHandler(INI_FILENAME)
window_x = ini.read_int("Window", "x", 100)
window_y = ini.read_int("Window", "y", 100)
window_width = ini.read_int("Window", "width", 300)
window_height = ini.read_int("Window", "height", 200)
collapsed = ini.read_bool("Window", "collapsed", False)
show_dps_graph = ini.read_bool("Window", "show_dps_graph", False)
show_bar_chart = ini.read_bool("Window", "show_bar_chart", True)
dps_window = ini.read_float("Window", "dps_window", 1.0)
anonymize_players = ini.read_bool("Window", "anonymize_players", False)
first_run = True

save_timer = ThrottledTimer(60000)
save_timer.Start()
graph_save_timer = ThrottledTimer(60000)
graph_save_timer.Start()
update_timer = ThrottledTimer(500)
damage_tracker = DamageTracker()
damage_tracker.dps_window_duration = dps_window

def reset_on_load():
    global damage_tracker, sorted_stats_cache, sorted_healing_stats_cache
    damage_tracker.reset()

def gui():
    global damage_tracker, dps_window, anonymize_players
    global window_x, window_y, window_width, window_height, collapsed, first_run, show_dps_graph, show_bar_chart

    # Window setup
    io = PyImGui.get_io()
    display_width = io.display_size_x
    display_height = io.display_size_y

    # Clamp window position to screen (ensure entire window is visible)
    # Ensure window_width and window_height are at least some minimal value to avoid issues if they are 0
    effective_window_width = max(window_width, 100) # Assuming a min width of 100 for proper clamping
    effective_window_height = max(window_height, 100) # Assuming a min height of 100

    # Ensure left edge is not off screen
    window_x = max(0, window_x)
    # Ensure top edge is not off screen
    window_y = max(0, window_y)

    # Ensure right edge is not off screen
    window_x = min(window_x, display_width - effective_window_width)
    # Ensure bottom edge is not off screen
    window_y = min(window_y, display_height - effective_window_height)

    if show_bar_chart:
        if first_run:
            PyImGui.set_next_window_pos([float(window_x), float(window_y)], int(PyImGui.ImGuiCond.FirstUseEver))
            PyImGui.set_next_window_size([float(window_width), float(window_height)], int(PyImGui.ImGuiCond.FirstUseEver))
            PyImGui.set_next_window_collapsed(collapsed, int(PyImGui.ImGuiCond.FirstUseEver))
            first_run = False

        render_window(damage_tracker, ini, save_timer, anonymize_players, WINDOW_FLAGS)
        # Read back in case it was changed by the checkbox
        anonymize_players = ini.read_bool("Window", "anonymize_players", False)
    
    if show_dps_graph:
        render_dps_graph_window(damage_tracker, ini, graph_save_timer, WINDOW_FLAGS)

def main():
    global damage_tracker
    if hasattr(damage_tracker, 'update'):
        damage_tracker.update()

    if not Routines.Checks.Map.MapValid():
        damage_tracker.reset()
        return
        
    gui()

def configure():
    global show_bar_chart, show_dps_graph, dps_window
    changed, show_bar_chart, show_dps_graph, dps_window = render_config(show_bar_chart, show_dps_graph, dps_window)
    if changed:
        ini.write_key("Window", "show_bar_chart", str(show_bar_chart))
        ini.write_key("Window", "show_dps_graph", str(show_dps_graph))
        ini.write_key("Window", "dps_window", str(dps_window))
        damage_tracker.dps_window_duration = dps_window

__all__ = ["main", "configure"]