from Py4GWCoreLib import PyImGui, Color

# Window state tracking for persistence
_last_x = 0
_last_y = 0
_last_w = 0
_last_h = 0
_last_collapsed = False
_first_run = True

def render_dps_graph_window(tracker, ini, save_timer, flags=0):
    global _last_x, _last_y, _last_w, _last_h, _last_collapsed, _first_run
    
    if _first_run:
        x = ini.read_int("GraphWindow", "x", 400)
        y = ini.read_int("GraphWindow", "y", 100)
        w = ini.read_int("GraphWindow", "width", 300)
        h = ini.read_int("GraphWindow", "height", 150)
        collapsed = ini.read_bool("GraphWindow", "collapsed", False)
        
        PyImGui.set_next_window_pos([float(x), float(y)], int(PyImGui.ImGuiCond.FirstUseEver))
        PyImGui.set_next_window_size([float(w), float(h)], int(PyImGui.ImGuiCond.FirstUseEver))
        PyImGui.set_next_window_collapsed(collapsed, int(PyImGui.ImGuiCond.FirstUseEver))
        _first_run = False

    title = f"Party DPS ({tracker.dps_window_duration:.1f}s)###PartyDPSGraph"
    if PyImGui.begin(title, flags | int(PyImGui.WindowFlags.NoScrollbar)):
        if save_timer.IsExpired():
            pos = PyImGui.get_window_pos()
            size = PyImGui.get_window_size()
            collapsed = PyImGui.is_window_collapsed()
            
            x, y = int(pos[0]), int(pos[1])
            w, h = int(size[0]), int(size[1])
            
            if x != _last_x or y != _last_y or w != _last_w or h != _last_h or collapsed != _last_collapsed:
                ini.write_key("GraphWindow", "x", str(x))
                ini.write_key("GraphWindow", "y", str(y))
                ini.write_key("GraphWindow", "width", str(w))
                ini.write_key("GraphWindow", "height", str(h))
                ini.write_key("GraphWindow", "collapsed", str(collapsed))
                
                _last_x, _last_y, _last_w, _last_h, _last_collapsed = x, y, w, h, collapsed
            
            save_timer.Reset()

        history = tracker.get_dps_history()
        
        avail = PyImGui.get_content_region_avail()
        w, h = avail[0], avail[1]
        
        start_pos = PyImGui.get_cursor_screen_pos()
        
        if history:
            max_val = max(history)
            if max_val == 0: max_val = 1
            
            line_col = Color(0, 255, 255, 255).color_int # Cyan
            
            points = list(history)
            if len(points) > 1:
                step = w / (len(points) - 1)
                for i in range(len(points) - 1):
                    v1 = points[i]
                    v2 = points[i+1]
                    
                    x1 = start_pos[0] + i * step
                    y1 = start_pos[1] + h - (v1 / max_val * h)
                    
                    x2 = start_pos[0] + (i + 1) * step
                    y2 = start_pos[1] + h - (v2 / max_val * h)
                    
                    PyImGui.draw_list_add_line(x1, y1, x2, y2, line_col, 2.0)
            
            PyImGui.set_cursor_pos(5.0, 5.0)
            PyImGui.text(f"Cur: {int(history[-1]):,}")
            PyImGui.text(f"Max: {int(max_val):,}")
        else:
            PyImGui.text("Waiting for data...")
            
    PyImGui.end()