from Py4GWCoreLib import Botting
from Widgets.CustomBehaviors.primitives.botting.botting_helpers import BottingHelpers
from Widgets.CustomBehaviors.primitives.parties.custom_behavior_party import CustomBehaviorParty

# Map IDs
UMBRAL_GROTTO_MAP_ID = 639  # Outpost
VERDANT_CASCADES_MAP_ID = 566  # Explorable area to reach dungeon
SLAVERS_EXILE_MAP_ID = 577  # Dungeon entrance level
JUSTICIAR_THOMMIS_ROOM_MAP_ID = 620  # Boss room

# Waypoint paths extracted from VoltaicSpearTeamFarm.py
VERDANT_CASCADES_TRAVEL_PATH = [
    (-19887, 6074),
    (-10273, 3251),
    (-6878, -329),
    (-3041, -3446),
    (3571, -9501),
    (4721, -10626),
    (10764, -6448),
    (13063, -4396),
    (18054, -3275),
    (20966, -6476),
    (25298, -9456),
]

ENTER_DUNGEON_PATH = [
    (-16797, 9251),
    (-17835, 12524),
]

# Path 1: Initial clearing route in Justiciar Thommis room
SLAVERS_EXILE_PATH_PRE_PATH_1 = (-12590, -17740)
SLAVERS_EXILE_TRAVEL_PATH_1 = [
    (-13480, -16570),
    (-13500, -15750),
    (-12500, -15000),
    (-10400, -14800),
    (-10837, -13823),
    (-11500, -13300),
    (-12175, -12211),
    (-13400, -11500),
    (-13700, -9550),
    (-14100, -8600),
    (-15000, -7500),
    (-16000, -7112),
    (-17347, -7438),
]

# Path 2: Second clearing route to boss
SLAVERS_EXILE_PATH_PRE_PATH_2 = (-18781, -8064)
SLAVERS_EXILE_TRAVEL_PATH_2 = [
    (-19083, -10150),
    (-18500, -11500),
    (-17700, -12500),
    (-17663, -13497),
]

# Final chest location
FINAL_CHEST_POSITION = (-17461, -14258)


def bot_routine(bot_instance: Botting):
    """
    Main bot routine for farming Slavers Exile - Justiciar Thommis room.

    This script handles:
    - Traveling to Umbral Grotto outpost
    - Navigating through Verdant Cascades to Slavers Exile
    - Entering the Justiciar Thommis boss room
    - Clearing two main paths through the dungeon
    - Looting the final chest
    - Resigning and looping back
    """

    # Disable blessing due to Norn NPC identification issues
    CustomBehaviorParty().set_party_is_blessing_enabled(False)

    # Register error handlers for critical failures
    bot_instance.Templates.Routines.UseCustomBehaviors(
        on_player_critical_death=BottingHelpers.botting_unrecoverable_issue,
        on_party_death=BottingHelpers.botting_unrecoverable_issue,
        on_player_critical_stuck=BottingHelpers.botting_unrecoverable_issue
    )

    # Enable aggressive combat behavior
    bot_instance.Templates.Aggressive()

    # === MAIN LOOP ===
    bot_instance.States.AddHeader("MAIN_LOOP")
    bot_instance.Map.Travel(target_map_id=UMBRAL_GROTTO_MAP_ID)
    bot_instance.Party.SetHardMode(True)

    # === EXIT TO VERDANT CASCADES ===
    bot_instance.States.AddHeader("EXIT_TO_VERDANT_CASCADES")
    bot_instance.Move.XY(-22735, 6339, "exit outpost to Verdant Cascades")
    bot_instance.Wait.ForMapLoad(target_map_id=VERDANT_CASCADES_MAP_ID)
    bot_instance.Wait.ForTime(2_000)

    # === NAVIGATE TO SLAVERS EXILE ===
    bot_instance.States.AddHeader("NAVIGATE_TO_SLAVERS_EXILE")
    for waypoint in VERDANT_CASCADES_TRAVEL_PATH:
        bot_instance.Move.XY(waypoint[0], waypoint[1], "navigate to dungeon")

    # Move to dungeon portal (auto-zones when close enough)
    bot_instance.Move.XY(25729, -9360, "enter Slavers Exile portal", forced_timeout=15)
    bot_instance.config.FSM.AddSelfManagedYieldStep(
        "wait for Slavers Exile map load",
        lambda: BottingHelpers.wrapper(
            action=BottingHelpers.wait_until_on_map(SLAVERS_EXILE_MAP_ID, timeout_ms=15_000),
            on_failure=BottingHelpers.botting_unrecoverable_issue
        )
    )

    # === ENTER JUSTICIAR THOMMIS ROOM ===
    bot_instance.States.AddHeader("ENTER_JUSTICIAR_THOMMIS_ROOM")
    for waypoint in ENTER_DUNGEON_PATH:
        bot_instance.Move.XY(waypoint[0], waypoint[1], "navigate to boss room entrance")

    # Move to boss room portal (auto-zones when close enough)
    bot_instance.Move.XY(-18656, 13136, "enter Justiciar Thommis room portal", forced_timeout=15)
    bot_instance.config.FSM.AddSelfManagedYieldStep(
        "wait for Justiciar Thommis room map load",
        lambda: BottingHelpers.wrapper(
            action=BottingHelpers.wait_until_on_map(JUSTICIAR_THOMMIS_ROOM_MAP_ID, timeout_ms=15_000),
            on_failure=BottingHelpers.botting_unrecoverable_issue
        )
    )

    # === CLEAR PATH 1 ===
    bot_instance.States.AddHeader("CLEAR_PATH_1")
    bot_instance.Move.XY(
        SLAVERS_EXILE_PATH_PRE_PATH_1[0],
        SLAVERS_EXILE_PATH_PRE_PATH_1[1],
        "move to path 1 starting position"
    )

    for waypoint in SLAVERS_EXILE_TRAVEL_PATH_1:
        bot_instance.Move.XY(waypoint[0], waypoint[1], "clear path 1")

    # === CLEAR PATH 2 ===
    bot_instance.States.AddHeader("CLEAR_PATH_2")
    bot_instance.Move.XY(
        SLAVERS_EXILE_PATH_PRE_PATH_2[0],
        SLAVERS_EXILE_PATH_PRE_PATH_2[1],
        "move to path 2 starting position"
    )

    for waypoint in SLAVERS_EXILE_TRAVEL_PATH_2:
        bot_instance.Move.XY(waypoint[0], waypoint[1], "clear path 2")

    # === FINAL CHEST & LOOT ===
    bot_instance.States.AddHeader("LOOT_FINAL_CHEST")
    bot_instance.Move.XY(FINAL_CHEST_POSITION[0], FINAL_CHEST_POSITION[1], "move to final chest")

    # Wait for automatic looting to complete (CustomBehaviors auto-loots chest)
    bot_instance.Wait.ForTime(120_000)

    # === RESIGN & RETURN ===
    bot_instance.States.AddHeader("RESIGN_PARTY")
    bot_instance.config.FSM.AddSelfManagedYieldStep(
        "wait for party resign",
        lambda: BottingHelpers.wrapper(
            action=BottingHelpers.wait_until_party_resign(timeout_ms=50_000),
            on_failure=BottingHelpers.botting_unrecoverable_issue
        )
    )
    bot_instance.Wait.ForMapLoad(target_map_id=UMBRAL_GROTTO_MAP_ID)

    # === REFRESH INSTANCE ===
    bot_instance.States.AddHeader("REFRESH_INSTANCE")
    # Exit to Verdant Cascades to refresh the instance
    bot_instance.Move.XY(-22735, 6339, "exit to refresh instance")
    bot_instance.Wait.ForMapLoad(target_map_id=VERDANT_CASCADES_MAP_ID)

    # Re-enter outpost
    bot_instance.Move.XY(-23139, 8233, "re-enter Umbral Grotto")
    bot_instance.Wait.ForMapLoad(target_map_id=UMBRAL_GROTTO_MAP_ID)

    # === LOOP BACK ===
    bot_instance.States.JumpToStepName("[H]MAIN_LOOP_1")

    bot_instance.States.AddHeader("END")


# Initialize bot instance with descriptive name
bot = Botting("[DUNGEON] Slavers Exile - Justiciar Thommis")
bot.SetMainRoutine(bot_routine)


def main():
    """Main update loop for the bot UI."""
    bot.Update()
    bot.UI.draw_window()


if __name__ == "__main__":
    main()