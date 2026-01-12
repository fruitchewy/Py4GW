from Py4GWCoreLib import PyImGui

def render_config(show_bar_chart: bool, show_dps_graph: bool, dps_window: float):
    changed = False
    if PyImGui.begin("Damage Meter Configuration", PyImGui.WindowFlags.AlwaysAutoResize):
        
        new_bar = PyImGui.checkbox("Show Bar Chart", show_bar_chart)
        if new_bar != show_bar_chart:
            show_bar_chart = new_bar
            changed = True
            
        new_graph = PyImGui.checkbox("Show DPS Graph", show_dps_graph)
        if new_graph != show_dps_graph:
            show_dps_graph = new_graph
            changed = True

        new_window = PyImGui.slider_float("DPS Window (s)", dps_window, 0.5, 10.0)
        if new_window != dps_window:
            dps_window = new_window
            changed = True
            
    PyImGui.end()
    return changed, show_bar_chart, show_dps_graph, dps_window
