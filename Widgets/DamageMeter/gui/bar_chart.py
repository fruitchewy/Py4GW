from Py4GWCoreLib import PyImGui, Color, ImGui

# Profession Colors (normalized RGBA)
PROFESSION_COLORS = {
    0:  (0.6, 0.6, 0.6, 1.0), # None/Unknown - Grey
    1:  (1.0, 0.75, 0.35, 1.0), # Warrior - Yellow/Gold
    2:  (0.5, 0.8, 0.3, 1.0), # Ranger - Green
    3:  (0.4, 0.85, 1.0, 1.0), # Monk - Light Blue
    4:  (0.7, 0.4, 0.85, 1.0), # Necromancer - Purple
    5:  (0.9, 0.45, 0.7, 1.0), # Mesmer - Pink
    6:  (1.0, 0.3, 0.3, 1.0), # Elementalist - Red
    7:  (0.4, 0.4, 0.9, 1.0), # Assassin - Dark Blue
    8:  (0.3, 0.8, 0.8, 1.0), # Ritualist - Teal
    9:  (1.0, 0.6, 0.2, 1.0), # Paragon - Orange
    10: (0.85, 0.85, 0.3, 1.0), # Dervish - Tan/Gold
}

# View State
# 0 = Damage, 1 = Healing
metric_selection = 0 

# Window state tracking for persistence
_last_x = 0
_last_y = 0
_last_w = 0
_last_h = 0
_last_collapsed = False

def render_window(tracker, ini, save_timer, anonymize_players, flags=0):
    global _last_x, _last_y, _last_w, _last_h, _last_collapsed

    if PyImGui.begin("Damage Meter", flags):
        if save_timer.IsExpired():
            pos = PyImGui.get_window_pos()
            size = PyImGui.get_window_size()
            collapsed = PyImGui.is_window_collapsed()

            x, y = int(pos[0]), int(pos[1])
            w, h = int(size[0]), int(size[1])

            if x != _last_x or y != _last_y or w != _last_w or h != _last_h or collapsed != _last_collapsed:
                ini.write_key("Window", "x", str(x))
                ini.write_key("Window", "y", str(y))
                ini.write_key("Window", "width", str(w))
                ini.write_key("Window", "height", str(h))
                ini.write_key("Window", "collapsed", str(collapsed))

                _last_x, _last_y, _last_w, _last_h, _last_collapsed = x, y, w, h, collapsed

            save_timer.Reset()

        render(tracker, anonymize_players, ini)

    PyImGui.end()

def render_bars(stats_list, max_val, is_healing, anonymize_players):
    total_sum = sum((s.total_healing if is_healing else s.total_damage) for s in stats_list)
    max_val = max(1.0, max_val)
    total_sum = max(1.0, total_sum)

    # Counter for anonymized player names
    player_counter = 1

    for stat in stats_list:
        val = stat.total_healing if is_healing else stat.total_damage
        if val <= 0: continue

        primary_texture = getattr(stat, 'primary_texture', "")
        if primary_texture:
            ImGui.image(primary_texture, (18, 18))
            PyImGui.same_line(0, 5)
        else:
            PyImGui.dummy(18, 18)
            PyImGui.same_line(0, 5)

        primary_prof = getattr(stat, 'primary_profession', 0)
        color = PROFESSION_COLORS.get(primary_prof, PROFESSION_COLORS[0])

        # Anonymize player names if enabled
        is_human = getattr(stat, 'is_human_player', False)
        if anonymize_players and is_human:
            display_name = f"Player {player_counter}"
            player_counter += 1
        else:
            display_name = stat.name

        fraction = val / max_val
        percent = (val / total_sum) * 100
        label = f"{display_name}: {int(val):,} | {percent:.1f}%"

        PyImGui.push_style_color(int(PyImGui.ImGuiCol.PlotHistogram), color)
        PyImGui.progress_bar(fraction, -1.0, 0.0, " ")
        PyImGui.pop_style_color(1)
        # Manual text drawing with shadow for high contrast
        rect_min = PyImGui.get_item_rect_min()
        rect_max = PyImGui.get_item_rect_max()
        text_size = PyImGui.calc_text_size(label)

        text_x = rect_min[0] + (rect_max[0] - rect_min[0] - text_size[0]) / 2
        text_y = rect_min[1] + (rect_max[1] - rect_min[1] - text_size[1]) / 2

        PyImGui.draw_list_add_text(text_x + 1, text_y + 1, Color(0, 0, 0, 255).color_int, label)
        PyImGui.draw_list_add_text(text_x, text_y, Color(255, 255, 255, 255).color_int, label)

def render(tracker, anonymize_players, ini):
    global metric_selection

    # Get cached data
    overall_dmg, overall_heal, current_dmg, current_heal = tracker.get_caches()

    # Anonymize players checkbox
    new_value = PyImGui.checkbox("Anonymize Players", anonymize_players)
    if new_value != anonymize_players:
        ini.write_key("Window", "anonymize_players", str(new_value))
    anonymize_players = new_value

    if PyImGui.begin_tab_bar("StatsContextBar"):
        if PyImGui.begin_tab_item("Current"):
            # Dropdown for metric
            PyImGui.set_next_item_width(100)
            metric_selection = PyImGui.combo("##MetricSelectCurr", metric_selection, ["Damage", "Healing"])

            if metric_selection == 0:
                render_bars(current_dmg, tracker.max_damage_current, False, anonymize_players)
            else:
                render_bars(current_heal, tracker.max_healing_current, True, anonymize_players)

            PyImGui.end_tab_item()

        if PyImGui.begin_tab_item("Overall"):
            # Dropdown for metric
            PyImGui.set_next_item_width(100)
            metric_selection = PyImGui.combo("##MetricSelectOver", metric_selection, ["Damage", "Healing"])

            if metric_selection == 0:
                render_bars(overall_dmg, tracker.max_damage_overall, False, anonymize_players)
            else:
                render_bars(overall_heal, tracker.max_healing_overall, True, anonymize_players)

            PyImGui.end_tab_item()

        PyImGui.end_tab_bar()