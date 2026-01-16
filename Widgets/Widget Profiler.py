import os
import traceback
import PyImGui
import Py4GW
from Py4GWCoreLib import *

MODULE_NAME = "Widget Profiler"
OPTIONAL = False  # This widget should always run when enabled

# Get the widget handler
from Py4GW_widget_manager import get_widget_handler, WidgetProfilingData

# Window state
first_run = True
BASE_PATH = Py4GW.Console.get_projects_path()
CONFIG_DIR = os.path.join(BASE_PATH, "Widgets", "Config")
INI_PATH = os.path.join(CONFIG_DIR, "Widget_Profiler.ini")
os.makedirs(CONFIG_DIR, exist_ok=True)

ini_window = IniHandler(INI_PATH)
save_timer = Timer()
save_timer.Start()

# Window position/state
window_x = ini_window.read_int(MODULE_NAME, "x", 100)
window_y = ini_window.read_int(MODULE_NAME, "y", 100)
window_collapsed = ini_window.read_bool(MODULE_NAME, "collapsed", False)

# Sort state
SORT_BY_NAME = 0
SORT_BY_AVG = 1
SORT_BY_MAX = 2
SORT_BY_PERCENT = 3
sort_column = SORT_BY_AVG
sort_ascending = False

# UI state
show_disabled_widgets = False
buffer_size_input = 100  # Local copy for input field


def format_time_ms(ms: float) -> str:
    """Format milliseconds for display."""
    if ms < 0.001:
        return "<0.001"
    elif ms < 1.0:
        return f"{ms:.3f}"
    elif ms < 10.0:
        return f"{ms:.2f}"
    else:
        return f"{ms:.1f}"


def get_sorted_profiling_data(handler) -> list[tuple[str, WidgetProfilingData, float, float, float, int]]:
    """Get profiling data sorted by current criteria.
    Returns list of (name, data, avg, max, percent, sample_count)
    """
    profiling_data = handler.get_profiling_data()

    # Calculate totals for percentage
    total_avg = 0.0
    entries = []

    for name, data in profiling_data.items():
        avg, min_t, max_t, sample_count = data.get_combined_stats()
        total_avg += avg
        entries.append((name, data, avg, max_t, 0.0, sample_count))

    # Calculate percentages
    if total_avg > 0:
        entries = [(name, data, avg, max_t, (avg / total_avg) * 100.0, sc)
                   for name, data, avg, max_t, _, sc in entries]

    # Sort based on current criteria
    if sort_column == SORT_BY_NAME:
        entries.sort(key=lambda x: x[0].lower(), reverse=not sort_ascending)
    elif sort_column == SORT_BY_AVG:
        entries.sort(key=lambda x: x[2], reverse=not sort_ascending)
    elif sort_column == SORT_BY_MAX:
        entries.sort(key=lambda x: x[3], reverse=not sort_ascending)
    elif sort_column == SORT_BY_PERCENT:
        entries.sort(key=lambda x: x[4], reverse=not sort_ascending)

    return entries


def draw_profiler_controls(handler):
    """Draw the profiler control buttons."""
    global buffer_size_input

    profiling_enabled = handler.profiling_enabled

    # Toggle profiling button
    if profiling_enabled:
        PyImGui.push_style_color(PyImGui.ImGuiCol.Button, (0.2, 0.6, 0.2, 1.0))
        if PyImGui.button("Stop Profiling"):
            handler.disable_profiling()
        PyImGui.pop_style_color(1)
    else:
        PyImGui.push_style_color(PyImGui.ImGuiCol.Button, (0.6, 0.2, 0.2, 1.0))
        if PyImGui.button("Start Profiling"):
            handler.enable_profiling()
        PyImGui.pop_style_color(1)

    PyImGui.same_line(0, 10)

    # Clear data button
    if PyImGui.button("Clear Data"):
        handler.clear_profiling_data()

    PyImGui.same_line(0, 10)

    # Status indicator
    if profiling_enabled:
        PyImGui.text_colored("PROFILING ACTIVE", (0.2, 1.0, 0.2, 1.0))
    else:
        PyImGui.text_colored("PROFILING STOPPED", (0.6, 0.6, 0.6, 1.0))

    # Buffer size control
    current_size = handler.get_profiling_buffer_size()
    PyImGui.text("Buffer Size:")
    PyImGui.same_line(0, 5)
    PyImGui.set_next_item_width(80)
    buffer_size_input = PyImGui.input_int("##buffer_size", current_size)
    if buffer_size_input != current_size:
        handler.set_profiling_buffer_size(buffer_size_input)
    PyImGui.same_line(0, 10)
    PyImGui.text_colored(f"(10-10000, current: {current_size})", (0.6, 0.6, 0.6, 1.0))


def draw_loop_stats(handler):
    """Draw overall loop statistics."""
    avg, min_t, max_t = handler.get_loop_stats()
    loop_times = handler.get_loop_times()
    sample_count = len(loop_times)

    PyImGui.separator()
    PyImGui.text("Loop Statistics:")

    if sample_count > 0:
        fps_estimate = 1000.0 / avg if avg > 0 else 0.0
        PyImGui.text(f"  Avg: {format_time_ms(avg)} ms  |  Min: {format_time_ms(min_t)} ms  |  Max: {format_time_ms(max_t)} ms  |  ~{fps_estimate:.1f} loops/sec")
        PyImGui.text(f"  Samples: {sample_count}")
    else:
        PyImGui.text("  No data collected yet")

    PyImGui.separator()


def draw_profiling_table(handler):
    """Draw the main profiling data table."""
    global sort_column, sort_ascending, show_disabled_widgets

    entries = get_sorted_profiling_data(handler)

    if not entries:
        PyImGui.text("No profiling data available.")
        PyImGui.text("Enable profiling and wait for widgets to execute.")
        return

    # Calculate totals
    total_avg = sum(e[2] for e in entries)

    # Table flags
    table_flags = (
        PyImGui.TableFlags.Borders |
        PyImGui.TableFlags.RowBg |
        PyImGui.TableFlags.Resizable |
        PyImGui.TableFlags.Sortable |
        PyImGui.TableFlags.ScrollY
    )

    # Begin table with 6 columns
    if not PyImGui.begin_table("ProfilingTable", 6, table_flags, 0, 300):
        return

    # Setup columns
    PyImGui.table_setup_column("Widget", PyImGui.TableColumnFlags.DefaultSort, init_width_or_weight=200.0)
    PyImGui.table_setup_column("Avg (ms)", PyImGui.TableColumnFlags.NoFlag, init_width_or_weight=70.0)
    PyImGui.table_setup_column("Min (ms)", PyImGui.TableColumnFlags.NoFlag, init_width_or_weight=70.0)
    PyImGui.table_setup_column("Max (ms)", PyImGui.TableColumnFlags.NoFlag, init_width_or_weight=70.0)
    PyImGui.table_setup_column("% Total", PyImGui.TableColumnFlags.NoFlag, init_width_or_weight=60.0)
    PyImGui.table_setup_column("Samples", PyImGui.TableColumnFlags.NoFlag, init_width_or_weight=60.0)
    PyImGui.table_headers_row()

    # Draw rows
    for name, data, avg, max_t, percent, sample_count in entries:
        main_avg, main_min, main_max, _ = data.get_main_stats()
        minimal_avg, minimal_min, minimal_max, _ = data.get_minimal_stats()
        combined_min = main_min + minimal_min

        PyImGui.table_next_row()

        # Widget name
        PyImGui.table_set_column_index(0)

        # Color code by performance impact
        if avg > 5.0:
            PyImGui.text_colored(name, (1.0, 0.3, 0.3, 1.0))  # Red for slow
        elif avg > 1.0:
            PyImGui.text_colored(name, (1.0, 0.8, 0.3, 1.0))  # Yellow for moderate
        else:
            PyImGui.text(name)

        # Show tooltip with detailed breakdown
        if PyImGui.is_item_hovered():
            PyImGui.begin_tooltip()
            PyImGui.text(f"{name}")
            PyImGui.separator()
            PyImGui.text(f"main():    avg={format_time_ms(main_avg)}ms  min={format_time_ms(main_min)}ms  max={format_time_ms(main_max)}ms")
            PyImGui.text(f"minimal(): avg={format_time_ms(minimal_avg)}ms  min={format_time_ms(minimal_min)}ms  max={format_time_ms(minimal_max)}ms")
            PyImGui.end_tooltip()

        # Avg time
        PyImGui.table_set_column_index(1)
        PyImGui.text(format_time_ms(avg))

        # Min time
        PyImGui.table_set_column_index(2)
        PyImGui.text(format_time_ms(combined_min))

        # Max time
        PyImGui.table_set_column_index(3)
        PyImGui.text(format_time_ms(max_t))

        # Percentage
        PyImGui.table_set_column_index(4)
        PyImGui.text(f"{percent:.1f}%")

        # Sample count
        PyImGui.table_set_column_index(5)
        PyImGui.text(str(sample_count))

    PyImGui.end_table()

    # Summary
    PyImGui.text(f"Total widgets: {len(entries)}  |  Total avg time: {format_time_ms(total_avg)} ms")


def draw_sort_controls():
    """Draw sorting control buttons."""
    global sort_column, sort_ascending

    PyImGui.text("Sort by:")
    PyImGui.same_line(0, 5)

    if PyImGui.button("Name"):
        if sort_column == SORT_BY_NAME:
            sort_ascending = not sort_ascending
        else:
            sort_column = SORT_BY_NAME
            sort_ascending = True

    PyImGui.same_line(0, 5)

    if PyImGui.button("Avg Time"):
        if sort_column == SORT_BY_AVG:
            sort_ascending = not sort_ascending
        else:
            sort_column = SORT_BY_AVG
            sort_ascending = False

    PyImGui.same_line(0, 5)

    if PyImGui.button("Max Time"):
        if sort_column == SORT_BY_MAX:
            sort_ascending = not sort_ascending
        else:
            sort_column = SORT_BY_MAX
            sort_ascending = False

    PyImGui.same_line(0, 5)

    if PyImGui.button("% Total"):
        if sort_column == SORT_BY_PERCENT:
            sort_ascending = not sort_ascending
        else:
            sort_column = SORT_BY_PERCENT
            sort_ascending = False


def draw_widget():
    """Draw the profiler widget."""
    global first_run, window_x, window_y, window_collapsed

    handler = get_widget_handler()

    if first_run:
        PyImGui.set_next_window_pos(window_x, window_y)
        PyImGui.set_next_window_collapsed(window_collapsed, 0)
        PyImGui.set_next_window_size((500, 400))
        first_run = False

    is_open = PyImGui.begin(MODULE_NAME)
    new_collapsed = PyImGui.is_window_collapsed()
    end_pos = PyImGui.get_window_pos()

    if is_open:
        draw_profiler_controls(handler)
        draw_loop_stats(handler)
        draw_sort_controls()
        PyImGui.spacing()
        draw_profiling_table(handler)

    PyImGui.end()

    # Save window state periodically
    if save_timer.HasElapsed(1000):
        if (end_pos[0], end_pos[1]) != (window_x, window_y):
            window_x, window_y = int(end_pos[0]), int(end_pos[1])
            ini_window.write_key(MODULE_NAME, "x", str(window_x))
            ini_window.write_key(MODULE_NAME, "y", str(window_y))
        if new_collapsed != window_collapsed:
            window_collapsed = new_collapsed
            ini_window.write_key(MODULE_NAME, "collapsed", str(window_collapsed))
        save_timer.Reset()


def configure():
    """Configuration window for the profiler."""
    pass


def main():
    """Main entry point called every frame."""
    try:
        draw_widget()
    except ImportError as e:
        Py4GW.Console.Log(MODULE_NAME, f"ImportError: {str(e)}", Py4GW.Console.MessageType.Error)
        Py4GW.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", Py4GW.Console.MessageType.Error)
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Error: {str(e)}", Py4GW.Console.MessageType.Error)
        Py4GW.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", Py4GW.Console.MessageType.Error)


if __name__ == "__main__":
    main()
