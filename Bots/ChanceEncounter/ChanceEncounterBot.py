import Py4GW
from Py4GWCoreLib import *
from Py4GWCoreLib.Botting import BottingClass
from time import sleep
from typing import Generator, List, Tuple

MODULE_NAME = "Chance Encounter Bot"

#region Constants
MAP_ID_KAINENG_CENTER = 194
MAP_ID_A_CHANCE_ENCOUNTER = 861

# Hero IDs (AutoIt Constants - Static IDs)
HERO_ACOLYTE_SOUSUKE = 8   # Fire Ele
HERO_VEKK = 26             # Earth Ele
HERO_PYRE_FIERCESHOT = 19  # Trapper
HERO_GWEN = 24             # Prot Mesmer
HERO_MERCENARY_1 = 28      # SoS Resto
HERO_OLIAS = 14            # BiP Resto
HERO_XANDRA = 25           # ST Rit

# Build Templates
BUILD_PLAYER_WARRIOR = "OQojQhV6KT4k9F8E7gUiEY5iwF"
BUILD_PLAYER_RANGER = "OgEUcDqWV8S4k9F8E7gUi+G5iMH"
BUILD_PLAYER_MONK = "OwUTMy3FSdkNE7A3gzh3gDTHgCA"
BUILD_PLAYER_NECRO = "OAljQwGMIOSdkNE7A3gzh3gDjrA"
BUILD_PLAYER_MESMER = "OQNDAy3FSdkNE7A3gzh3gDTDgCA"
BUILD_PLAYER_ELEMENTALIST = "OgljkwGMpOSdkNE7A3gzh3gDTDA"
BUILD_PLAYER_ASSASSIN = "OwZjQy3FSdkNE7A3gzh3gDTHgCA"
BUILD_PLAYER_RITUALIST = "OASjUyiMpOSdkNE7A3gzh3gDTDA"
BUILD_PLAYER_PARAGON = "OQGjAy3FSdkNE7A3gzh3gDTHgCA"
BUILD_PLAYER_DERVISH = "OgGlwWrJlWpqFhT2XwTsDSJSglL29B"

BUILD_FIRE_ELE = "OgBDgqyMSlVHR3C8CLg4CKDADA"
BUILD_EARTH_ELE = "OgljkwMopOdVm22oHuK2x14UBA"
BUILD_TRAPPER = "OggjclYsYSNHLHJHKHchYOIHCAA"
BUILD_PROT_MESMER = "OQNDAowvOqkcw0z0NEEcaRBA"
BUILD_SOS_RESTO = "OAejEyiM5QXTYMdOTMSTdiVPciA"
BUILD_BIP_RESTO = "OAhjQoGYIP3BqdVV4JNncDzxJA"
BUILD_ST_RIT = "OAmjAyk85QYTWPPOhTOTkTQTfiA"
#endregion

class ChanceEncounterBot(BottingClass):
    def __init__(self):
        super().__init__(
            bot_name=MODULE_NAME,
            config_log_actions=True,
            config_movement_tolerance=150,
            config_movement_timeout=15000,
            upkeep_hero_ai_active=False
        )
        self.run_count = 0
        self.foe_agents = []
        self.foe_count = 0

        self.SetMainRoutine(self.BotRoutine)

    def GetProfessionBuildTemplate(self) -> str:
        primary, _ = Agent.GetProfessionNames(GLOBAL_CACHE.Player.GetAgentID())
        mapping = {
            "Warrior": BUILD_PLAYER_WARRIOR, "Ranger": BUILD_PLAYER_RANGER,
            "Monk": BUILD_PLAYER_MONK, "Necromancer": BUILD_PLAYER_NECRO,
            "Mesmer": BUILD_PLAYER_MESMER, "Elementalist": BUILD_PLAYER_ELEMENTALIST,
            "Assassin": BUILD_PLAYER_ASSASSIN, "Ritualist": BUILD_PLAYER_RITUALIST,
            "Paragon": BUILD_PLAYER_PARAGON, "Dervish": BUILD_PLAYER_DERVISH
        }
        return mapping.get(primary, BUILD_PLAYER_WARRIOR)

    def BotRoutine(self):
        """Builder for the FSM sequence."""
        # --- Outpost Preparation ---
        self.Map.Travel(MAP_ID_KAINENG_CENTER)
        self.Party.LeaveParty()
        self.Wait.ForTime(500)

        # Add Heroes in order matching build positions
        heroes_to_add = [
            HERO_ACOLYTE_SOUSUKE, HERO_VEKK, HERO_PYRE_FIERCESHOT,
            HERO_GWEN, HERO_MERCENARY_1, HERO_OLIAS, HERO_XANDRA
        ]
        for hero_id in heroes_to_add:
            self.Party.AddHero(hero_id)

        # Load Skillbars
        self.SkillBar.LoadSkillBar(self.GetProfessionBuildTemplate())

        hero_builds = [
            (1, BUILD_FIRE_ELE), (2, BUILD_EARTH_ELE), (3, BUILD_TRAPPER),
            (4, BUILD_PROT_MESMER), (5, BUILD_SOS_RESTO), (6, BUILD_BIP_RESTO), (7, BUILD_ST_RIT)
        ]
        for idx, template in hero_builds:
            self.SkillBar.LoadHeroSkillBar(idx, template)

        # Start Quest Interaction - Move to Miku and enter quest
        self.config.FSM.AddState("Set Hero Behavior", self.SetHeroBehaviorAction)
        self.Move.XY(2230, -1237) # Move to Miku
        self.Dialogs.AtXY(2230, -1237, 0x84) # Miku interaction - accept quest
        self.Wait.ForMapLoad(MAP_ID_A_CHANCE_ENCOUNTER)
        self.Wait.ForTime(1000) # Wait for map to fully load before flagging heroes

        # --- Instance Setup Phase ---
        # Initial flagging positions (AutoIt CommandStartingPositions)
        self.Party.FlagHero(1, -6362, -4967) # Hero 1 (Fire Ele)
        self.Party.FlagHero(2, -6060, -5168) # Hero 2 (Earth Ele)
        self.Party.FlagHero(3, -6245, -5232) # Hero 3 (Trapper)
        self.Party.FlagHero(4, -6362, -4967) # Hero 4 (Mesmer)
        self.Party.FlagHero(5, -5691, -5195) # Hero 5 (SoS)
        self.Party.FlagHero(6, -5606, -4747) # Hero 6 (BiP)
        self.Party.FlagHero(7, -5452, -4380) # Hero 7 (ST)

        # Start background coroutines AFTER heroes are flagged
        # self.config.FSM.AddState("Managed Routines Start", self.StartManagedRoutinesAction)

        self.Move.XY(-6232, -5392)

        # Trapper stages
        self.config.FSM.AddYieldRoutineStep("First Trap Setup", self.FirstTrapSetupCoro)
        self.Party.FlagHero(1, -6517, -5129)  # Fire Ele
        self.Party.FlagHero(3, -6311, -5635)  # Trapper
        self.Wait.ForTime(1500)
        self.config.FSM.AddYieldRoutineStep("Second Trap Setup", self.SecondTrapSetupCoro)
        self.Party.FlagHero(1, -6480, -5258)  # Fire Ele
        self.Party.FlagHero(3, -6503, -5937)  # Trapper
        self.Wait.ForTime(1500)
        self.config.FSM.AddYieldRoutineStep("Third Trap Setup", self.ThirdTrapSetupCoro)
        self.Party.FlagHero(3, -6311, -5635)  # Trapper
        self.Party.FlagHero(6, -5795, -4942)  # BiP
        self.Wait.ForTime(1500)

        # --- Combat Phase ---
        self.Move.XY(-6306, -5260)
        self.config.FSM.AddYieldRoutineStep("Opening Buffs", self.OpeningBuffsCoro)
        self.config.FSM.AddYieldRoutineStep("Combat Sequence", self.CombatRoutine)

        # --- Journey Phase ---
        self.config.FSM.AddYieldRoutineStep("Run to Stairs Sequence", self.RunToStairsSequenceCoro)

        # --- Spike Phase ---
        self.config.FSM.AddYieldRoutineStep("Wait for Foes", self.WaitForFoesCoro)
        self.config.FSM.AddYieldRoutineStep("Spike Execution", self.SpikeExecutionCoro)
        self.config.FSM.AddWaitState("Spike Engagement", lambda: self.GetFoeCountNearby(Range.Earshot.value) <= 10, timeout_ms=10000)

        # --- Cleanup ---
        self.config.FSM.AddYieldRoutineStep("Loot Area", lambda: self.helpers.Items.loot())
        self.config.FSM.AddState("Auto Cleanup", lambda: self.Items.AutoIDAndSalvageAndDepositItems())

        # Reset for next run
        self.Map.Travel(MAP_ID_KAINENG_CENTER)
        # self.config.FSM.AddState("Managed Routines Stop", self.StopManagedRoutinesAction)


    # region Actions & Coroutines
    def SetHeroBehaviorAction(self):
        # Hero behaviors matching AutoIt: [0, 0, 0, 0, 2, 1, 2]
        # 0 = Guard, 1 = Fight, 2 = Avoid Combat
        hero_behaviors = [0, 0, 0, 0, 2, 1, 2]  # Indexed 0-6 for heroes 1-7

        heroes = Party.GetHeroes()
        for i, hero in enumerate(heroes):
            if hero.agent_id and i < len(hero_behaviors):
                behavior = hero_behaviors[i]
                Party.Heroes.SetHeroBehavior(hero.agent_id, behavior)
                hero_name = Agent.GetNameByID(hero.agent_id) if Agent.IsValid(hero.agent_id) else f"Hero {i+1}"
                ConsoleLog(MODULE_NAME, f"Set {hero_name} behavior to {behavior}", Py4GW.Console.MessageType.Info)

    def EndFightStateAction(self):
        pass

    def FirstTrapSetupCoro(self):
        # Get hero agent IDs for targeting
        trapper_agent = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(3)  # Hero 3 = Trapper
        sos_agent = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(5)      # Hero 5 = SoS
        bip_agent = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(6)      # Hero 6 = BiP

        yield from Routines.Yield.Keybinds.HeroSkill(3, 7, log=True)  # Trapper - Serpent's Quickness
        yield from Routines.Yield.wait(500)
        yield from Routines.Yield.Keybinds.HeroSkill(3, 3, log=True)  # Trapper - Dust Trap
        yield from Routines.Yield.Keybinds.HeroSkill(5, 1, log=True)  # SoS - Signet of Spirits

        # BiP on Trapper
        if trapper_agent and bip_agent:
            if self.IsHeroSkillReady(6, 1):
                bip_casting = Agent.IsCasting(bip_agent) if Agent.IsValid(bip_agent) else False
                if not bip_casting:
                    SkillBar.HeroUseSkill(trapper_agent, 1, 6)

        yield from Routines.Yield.wait(2500)

        yield from Routines.Yield.Keybinds.HeroSkill(3, 1, log=True)  # Trapper - Spike Trap
        yield from Routines.Yield.Keybinds.HeroSkill(5, 6, log=True)  # SoS - Agony

        # BiP on SoS
        if sos_agent and bip_agent:
            if self.IsHeroSkillReady(6, 1):
                bip_casting = Agent.IsCasting(bip_agent) if Agent.IsValid(bip_agent) else False
                if not bip_casting:
                    SkillBar.HeroUseSkill(sos_agent, 1, 6)

        yield from Routines.Yield.wait(2500)

    def SecondTrapSetupCoro(self):
        yield from Routines.Yield.Keybinds.HeroSkill(3, 4, log=True)  # Trapper - Barbed Trap
        yield from Routines.Yield.wait(2500)
        yield from Routines.Yield.Keybinds.HeroSkill(3, 5, log=True)  # Trapper - Flame Trap
        yield from Routines.Yield.Keybinds.HeroSkill(7, 5, log=True)  # ST Rit - Disenchantment
        yield from Routines.Yield.wait(2500)

    def ThirdTrapSetupCoro(self):
        yield from Routines.Yield.Keybinds.HeroSkill(3, 2, log=True)  # Trapper - Tripwire
        yield from Routines.Yield.Keybinds.HeroSkill(6, 8, log=True)  # BiP - Flesh of My Flesh
        yield from Routines.Yield.Keybinds.HeroSkill(7, 2, log=True)  # ST Rit - Shelter
        yield from Routines.Yield.wait(2500)

    def OpeningBuffsCoro(self):
        # Get hero agent IDs for targeting
        trapper_agent = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(3)  # Hero 3 = Trapper
        player_agent = GLOBAL_CACHE.Player.GetAgentID()  # Player

        yield from Routines.Yield.Skills.CastSkillSlot(1)  # Player - Dwarven Stability
        yield from Routines.Yield.Keybinds.HeroSkill(1, 7, log=True)  # Fire Ele - Glyph of Sacrifice
        yield from Routines.Yield.Keybinds.HeroSkill(3, 4, log=True)  # Trapper - Barbed Trap
        yield from Routines.Yield.Keybinds.HeroSkill(5, 7, log=True)  # SoS - Recuperation

        # BiP on Trapper
        if trapper_agent and self.IsHeroSkillReady(6, 1):
            SkillBar.HeroUseSkill(trapper_agent, 1, 6)

        yield from Routines.Yield.Keybinds.HeroSkill(7, 7, log=True)  # ST Rit - Armor of Unfeeling
        yield from Routines.Yield.wait(1500)

        # Flag SoS hero to position
        self.Party.FlagHero(5, -5984, -5524)

        yield from Routines.Yield.Skills.CastSkillSlot(7)  # Player - Honor
        # Note: Move happens in FSM between this coro and combat (self.Move.XY(-6306, -5260))

        # Fire Ele - Meteor Shower (ground-targeted)
        # Clear player target so hero uses default AI targeting instead of player's (potentially invalid) target
        GLOBAL_CACHE.Player.ChangeTarget(0)  # Clear target
        yield from Routines.Yield.wait(250)
        yield from Routines.Yield.Keybinds.HeroSkill(1, 8, log=True)  # Meteor Shower

        yield from Routines.Yield.Keybinds.HeroSkill(3, 1, log=True)  # Trapper - Spike Trap

        # SoS - Splinter Weapon on Player
        if player_agent and self.IsHeroSkillReady(5, 2):
            SkillBar.HeroUseSkill(player_agent, 2, 5)

        # ST Rit - Inspirational Speech on Player
        if player_agent and self.IsHeroSkillReady(7, 8):
            SkillBar.HeroUseSkill(player_agent, 8, 7)

        yield from Routines.Yield.wait(500)

    def FlagHeroByIndex(self, index, x, y):
        """Flag a hero by party index (1-7) without requiring agent ID resolution."""
        try:
            # Use self.Party.FlagHero which takes party position directly
            # This works even when heroes are out of range
            self.Party.FlagHero(index, x, y)
        except Exception as e:
            ConsoleLog(MODULE_NAME, f"FlagHero Error for index {index}: {str(e)}", Py4GW.Console.MessageType.Warning)

    def RunToStairsSequenceCoro(self):
        """Multi-stage flagging logic matching AutoIt script."""
        ConsoleLog(MODULE_NAME, "RunToStairs: Start", Py4GW.Console.MessageType.Info)

        # Initial Speedboost
        yield from self.PlayerSpeedboostCoro()

        # Flag most heroes away to safe spot
        ConsoleLog(MODULE_NAME, "RunToStairs: Flagging heroes", Py4GW.Console.MessageType.Info)
        for i in range(1, 8):
            self.FlagHeroByIndex(i, -7047, -2651)
            yield from Routines.Yield.wait(100)
        yield from Routines.Yield.wait(500)

        # BiP on player if needed
        try:
            hero_6_id = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(6)
            player_id = GLOBAL_CACHE.Player.GetAgentID()
            if hero_6_id and Agent.IsValid(hero_6_id) and not Agent.IsDead(hero_6_id):
                if Agent.GetHealth(hero_6_id) > 0.5 and self.IsHeroSkillReady(6, 1):
                    SkillBar.HeroUseSkill(player_id, 1, 6)
        except:
            pass

        # Flag support heroes ahead (SoS and ST)
        ConsoleLog(MODULE_NAME, "RunToStairs: Advance Support - Flag SoS", Py4GW.Console.MessageType.Info)
        self.FlagHeroByIndex(5, -2195, 33) # SoS
        yield from Routines.Yield.wait(1000)

        ConsoleLog(MODULE_NAME, "RunToStairs: Advance Support - Flag ST", Py4GW.Console.MessageType.Info)
        self.FlagHeroByIndex(7, -2195, 33) # ST
        yield from Routines.Yield.wait(1000)

        # Recall (Hero 5 Slot 3) on ST (Hero 7)
        try:
            st_agent_id = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(7)
            if st_agent_id and Agent.IsValid(st_agent_id) and self.IsHeroSkillReady(5, 3):
                SkillBar.HeroUseSkill(st_agent_id, 3, 5)
        except Exception as e:
            ConsoleLog(MODULE_NAME, f"RunToStairs: Recall Error - {str(e)}", Py4GW.Console.MessageType.Error)
        yield from Routines.Yield.wait(500)

        # Journey to Ship
        ConsoleLog(MODULE_NAME, "RunToStairs: Moving to Ship", Py4GW.Console.MessageType.Info)
        yield from Routines.Yield.Movement.FollowPath([(-4800, -3700)])

        # Wait for Miku (Model 58)
        ConsoleLog(MODULE_NAME, "RunToStairs: Waiting for Miku (Name Search)", Py4GW.Console.MessageType.Info)
        wait_timer = Timer()
        wait_timer.Start()
        while wait_timer.GetElapsedTime() < 15000:
            try:
                # Use Name Search as suggested by user
                miku = yield from Routines.Yield.Agents.GetAgentIDByName("Miku")
                if miku != 0:
                    miku_pos = Agent.GetXY(miku)
                    dist = Utils.Distance(miku_pos, (-4800, -3700))
                    ConsoleLog(MODULE_NAME, f"Miku found at {miku_pos}, Dist: {dist}", Py4GW.Console.MessageType.Info)

                    if dist <= 2500:
                        ConsoleLog(MODULE_NAME, "RunToStairs: Miku in range!", Py4GW.Console.MessageType.Info)
                        break
            except:
                pass
            yield from Routines.Yield.wait(250)

        # Martyr at Ship
        if self.IsHeroSkillReady(4, 1):
            yield from Routines.Yield.Keybinds.HeroSkill(4, 1, log=True)  # Prot Mesmer - Martyr

        ConsoleLog(MODULE_NAME, "RunToStairs: Moving to Stairs", Py4GW.Console.MessageType.Info)
        yield from self.PlayerSpeedboostCoro()
        yield from Routines.Yield.Movement.FollowPath([(-4658, -757), (-3135, 628)])

        # Move support heroes to intermediate waypoint
        self.FlagHeroByIndex(5, -766, -3262) # SoS
        self.FlagHeroByIndex(7, -766, -3262) # ST
        yield from Routines.Yield.wait(500)

        yield from Routines.Yield.Movement.FollowPath([(-2127, -1224), (-878, -1854)])

        # Final Position Flags before Spike
        self.FlagHeroByIndex(4, -5606, -2916) # Mesmer
        self.FlagHeroByIndex(6, -5606, -2916) # BiP
        self.FlagHeroByIndex(5, -1119, -4683) # SoS
        self.FlagHeroByIndex(7, -1665, -6015) # ST
        yield from Routines.Yield.wait(500)

        yield from Routines.Yield.Movement.FollowPath([(-766, -3262), (-687, -3780)])

        # Pre-spike buffs
        yield from Routines.Yield.Keybinds.HeroSkill(6, 7, log=True)  # BiP - Kaolai
        yield from Routines.Yield.Keybinds.HeroSkill(7, 8, log=True)  # ST Rit - Inspirational Speech
        yield

    def PlayerSpeedboostCoro(self):
        """Cast speed skills with profession-specific logic (AutoIt Speedboost equivalent).

        AutoIt logic:
        - Assassin: Dark Escape (Slot 5) chained under Dwarven Stability (Slot 1)
        - Others: "To the Limit!" (Slot 3) + Soldier's Speed (Slot 5)

        Simplified implementation: The skill template loads profession-specific skills,
        so we just cast Slots 3 & 5 which will be the correct skills per profession.
        """
        yield from Routines.Yield.Skills.CastSkillSlot(3)  # "To the Limit!" or adrenaline skill
        yield from Routines.Yield.Skills.CastSkillSlot(5)  # Soldier's Speed or Dark Escape
        yield

    def WaitForFoesCoro(self):
        """Wait for enemy ball with sophisticated tracking (AutoIt WaitForFoes equivalent lines 1869-1975).

        Uses multi-zone tracking:
        - Tracks up to 60 unique enemies seen within 1000 range
        - Monitors which enemies have entered 200 range (balled)
        - Counts "dwell ticks" for enemies stuck at 1000 but not 200
        - Exits when 98% of seen enemies are "resolved" (balled, dead, or dwelling)
        - Has 18s "no new arrivals" gate and 60s global timeout
        """
        player_id = GLOBAL_CACHE.Player.GetAgentID()
        player_pos = Agent.GetXY(player_id)

        # Check player position (-687, -3780 ± 25)
        target_x, target_y = -687, -3780
        dx = player_pos[0] - target_x
        dy = player_pos[1] - target_y
        dist_sq = dx * dx + dy * dy
        if dist_sq > (25 * 25):
            ConsoleLog(MODULE_NAME, f"[WARN] Player not in position (dist={(dist_sq**0.5):.1f})", Py4GW.Console.MessageType.Warning)
            yield
            return

        # Wait for initial enemies (≥4 within 1000, max 30s)
        initial_timeout = Timer()
        initial_timeout.Start()
        while self.GetFoeCountNearby(1000) < 4:
            if Agent.IsDead(player_id) or initial_timeout.GetElapsedTime() >= 30000:
                yield
                return
            yield from Routines.Yield.wait(500)

        # Constants (from AutoIt lines 1887-1892)
        TICK_MS = 500
        NO_ARRIVAL_MS = 18000  # Inter-pack gap timeout
        RESOLVE_RATIO = 0.98   # 98% resolution threshold
        SEEN_TARGET = 60       # Skip no-arrivals gate once this many seen
        DWELL_MS = 12000       # How long to tolerate enemies at 1000 but not 200
        DWELL_TICKS = int(DWELL_MS / TICK_MS)  # 24 ticks

        # Tracking arrays (up to 60 unique enemies)
        seen_1000 = {}          # {agent_id: index} - ever seen within 1000
        stuck_200 = set()       # {index} - has been in 200 at least once
        in_1000_now = set()     # {index} - scratch bitmap per tick
        dwell_1000_not_200 = {} # {index: tick_count} - tick counter per foe

        last_new_seen_timer = Timer()
        last_new_seen_timer.Start()
        global_timeout = Timer()
        global_timeout.Start()

        martyr_used = False
        martyr_counter = 0

        while True:
            # Survival priority
            yield from self.StayAliveCoro()

            # Martyr logic (lines 1907-1919): Cast on burning/deep wound
            if not martyr_used:
                # Check for burning or deep wound effects
                # Note: Py4GW doesn't have direct "has effect" check, so simplified
                if Agent.GetHealth(player_id) < 0.65:  # Simplified condition
                    martyr_used = True
                    yield from Routines.Yield.Keybinds.HeroSkill(4, 1, log=True)  # Prot Mes - Martyr
            else:
                martyr_counter += 1
                if martyr_counter >= 10:
                    martyr_counter = 0
                    martyr_used = False

            # Get enemy arrays in zones
            all_enemies = AgentArray.GetEnemyArray()
            foes_in_1000 = []
            foes_in_200 = []

            for enemy_id in all_enemies:
                if not Agent.IsValid(enemy_id) or Agent.IsDead(enemy_id):
                    continue

                enemy_pos = Agent.GetXY(enemy_id)
                dx = enemy_pos[0] - player_pos[0]
                dy = enemy_pos[1] - player_pos[1]
                dist_sq = dx * dx + dy * dy

                if dist_sq <= 1000 * 1000:
                    foes_in_1000.append(enemy_id)
                    if dist_sq <= 200 * 200:
                        foes_in_200.append(enemy_id)

            # Clear scratch bitmap
            in_1000_now.clear()

            # Track new enemies in 1000 range
            for enemy_id in foes_in_1000:
                if enemy_id not in seen_1000 and len(seen_1000) < SEEN_TARGET:
                    idx = len(seen_1000) + 1
                    seen_1000[enemy_id] = idx
                    dwell_1000_not_200[idx] = 0
                    last_new_seen_timer.Start()  # Reset timer on new arrival

                if enemy_id in seen_1000:
                    idx = seen_1000[enemy_id]
                    in_1000_now.add(idx)

            # Mark enemies that entered 200 range
            for enemy_id in foes_in_200:
                if enemy_id in seen_1000:
                    idx = seen_1000[enemy_id]
                    stuck_200.add(idx)

            # Update dwell counters (enemies in 1000 but not in 200)
            for enemy_id, idx in seen_1000.items():
                if idx in in_1000_now and idx not in stuck_200:
                    dwell_1000_not_200[idx] = dwell_1000_not_200.get(idx, 0) + 1
                else:
                    dwell_1000_not_200[idx] = 0

            # Calculate resolution (enemies that are: in 200, dead/gone, or dwelling)
            count_seen = len(seen_1000)
            resolved = 0
            for enemy_id, idx in seen_1000.items():
                if (idx in stuck_200 or  # Has balled (entered 200)
                    idx not in in_1000_now or  # Dead or left
                    dwell_1000_not_200.get(idx, 0) >= DWELL_TICKS):  # Dwelling too long
                    resolved += 1

            # Check exit conditions (lines 1961-1968)
            seen_target_reached = (count_seen >= SEEN_TARGET)
            no_arrivals_gate = seen_target_reached or (last_new_seen_timer.GetElapsedTime() >= NO_ARRIVAL_MS)

            if count_seen > 0 and no_arrivals_gate and resolved >= int(count_seen * RESOLVE_RATIO):
                ConsoleLog(MODULE_NAME, f"WaitForFoes: Resolved {resolved}/{count_seen} enemies", Py4GW.Console.MessageType.Info)
                break

            # Global timeouts
            if Agent.IsDead(player_id):
                ConsoleLog(MODULE_NAME, "WaitForFoes: Player died", Py4GW.Console.MessageType.Warning)
                break
            if global_timeout.GetElapsedTime() >= 60000:
                ConsoleLog(MODULE_NAME, "WaitForFoes: 60s timeout", Py4GW.Console.MessageType.Warning)
                break

            yield from Routines.Yield.wait(TICK_MS)

    def SpikeExecutionCoro(self):
        """Execute the spike with precise timing (AutoIt Spike equivalent)."""
        player_id = GLOBAL_CACHE.Player.GetAgentID()

        # Position heroes for spike
        self.FlagHeroByIndex(3, -6341, -2751)  # Mesmer
        self.FlagHeroByIndex(5, -6341, -2751)  # BiP
        yield from Routines.Yield.wait(500)

        weapon_active = False
        honor_active = False
        allies_out_of_range = False

        timeout = Timer()
        timeout.Start()

        # Wait for spike conditions
        while timeout.GetElapsedTime() < 15000:
            # Apply Resilient Weapon
            if not weapon_active:
                hero_5_id = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(5)
                if hero_5_id and self.IsHeroSkillReady(5, 4):
                    SkillBar.HeroUseSkill(player_id, 4, 5)
                    weapon_active = True
                    # Move SoS away
                    self.FlagHeroByIndex(5, -4950, -7955)
                    yield from Routines.Yield.wait(250)

            # Active survival
            yield from self.StayAliveCoro()

            # Cast Honor
            if not honor_active:
                yield from Routines.Yield.Skills.CastSkillSlot(7)  # Honor
                honor_active = True

            # Check allies out of compass range
            if not allies_out_of_range:
                compass_range = Range.Compass.value
                allies_in_range = 0
                for i in range(4, 7):  # Check heroes 4-6
                    hero_id = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(i)
                    if hero_id:
                        hero_pos = Agent.GetXY(hero_id)
                        player_pos = GLOBAL_CACHE.Player.GetXY()
                        if Utils.Distance(hero_pos, player_pos) < compass_range:
                            allies_in_range += 1

                if allies_in_range == 0:
                    allies_out_of_range = True

            # Check if ready to spike - simplified (check energy and weapon active)
            player_energy = Agent.GetEnergy(player_id)

            if player_energy >= 5 and weapon_active and honor_active and allies_out_of_range:
                break

            yield from Routines.Yield.wait(500)

        # Execute spike - Prime 100 Blades then Whirlwind
        spike_timeout = Timer()
        spike_timeout.Start()

        target_foe = None

        # Prime 100 Blades and execute Whirlwind
        while spike_timeout.GetElapsedTime() < 15000:
            # Cast 100 Blades
            yield from Routines.Yield.Skills.CastSkillSlot(2)  # 100 Blades

            # Get foes in range and execute Whirlwind
            player_pos = GLOBAL_CACHE.Player.GetXY()
            # Use safe method instead of Routines.Agents.GetFilteredEnemyArray
            if self.GetFoeCountNearby(200) > 0:
                # Use safe method instead of Routines.Agents.GetNearestEnemy
                target_foe = self.GetNearestEnemySafe(200)
                # Execute Whirlwind
                if target_foe:
                    GLOBAL_CACHE.Player.Interact(target_foe, False)
                    yield from Routines.Yield.Skills.CastSkillSlot(4)  # Whirlwind
                    break

            yield from Routines.Yield.wait(500)

        # Wait for target to die
        if target_foe:
            death_timeout = Timer()
            death_timeout.Start()
            while death_timeout.GetElapsedTime() < 5000:
                if Agent.IsDead(target_foe):
                    # Move Mesmer back to safe position
                    self.FlagHeroByIndex(4, -5606, -2916) # Mesmer
                    # Apply post-spike defensives
                    yield from self.PostSpikeDefensivesCoro()
                    break
                yield from Routines.Yield.wait(250)

    def StayAliveCoro(self):
        """Active survival with profession-specific healing (AutoIt StayAlive equivalent)."""
        player_id = GLOBAL_CACHE.Player.GetAgentID()
        player_hp = Agent.GetHealth(player_id)

        # Adrenaline skill
        yield from Routines.Yield.Skills.CastSkillSlot(3)  # "To the Limit!"

        # Defensive buffs
        yield from Routines.Yield.Skills.CastSkillSlot(1)  # Dwarven Stability / Feel No Pain

        # Emergency heal at low HP
        if player_hp <= 0.7:
            yield from Routines.Yield.Skills.CastSkillSlot(8)  # "I Will Survive!" / profession heal

        # Apply weapon spell if low HP
        if player_hp < 1.0:
            hero_5_id = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(5)
            if hero_5_id and self.IsHeroSkillReady(5, 4):
                SkillBar.HeroUseSkill(player_id, 4, 5)

        # Profession-specific sustain at medium-low HP
        if player_hp <= 0.85:
            yield from Routines.Yield.Skills.CastSkillSlot(6)  # Profession self-heal

    def PostSpikeDefensivesCoro(self):
        """Apply immediate post-spike defensives with profession-specific logic (AutoIt PostSpikeDefensives equivalent).

        AutoIt profession-specific logic (lines 3228-3267):
        - Assassin: Slot 8 ("I Will Survive!") -> Slot 5 (Dark Escape) -> Slot 6 (Shadow Refuge)
        - Dervish: Slot 6 (Conviction) -> Slot 1 (Feel No Pain)
        - Ranger: Slot 8 (Healing Spring) or Slot 6 (Troll Unguent) -> Slot 1 (Feel No Pain)
        - Warrior: Slot 8 ("I Will Survive!") -> Slot 6 (Conviction) -> Slot 1 (Feel No Pain)
        - Paragon: Slot 8 ("I Will Survive!") -> Slot 1 (Feel No Pain) -> Slot 6 (Healing Signet)
        - Ritualist/Monk: Slot 8 ("I Will Survive!") -> Slot 6 (MBaS/Healing Breeze if no Backfire) -> Slot 1
        - Elementalist/Mesmer: Slot 8 ("I Will Survive!") -> Slot 1 (Feel No Pain)
        - Necromancer: Slot 8 ("I Will Survive!") -> Slot 1 (Feel No Pain) -> Slot 6 (Blood Renewal if HP >= 70%)

        Simplified implementation: Cast defensive slots in sequence.
        The skill template loads profession-specific skills per build.
        """
        yield from Routines.Yield.Skills.CastSkillSlot(8)  # Emergency heal
        yield from Routines.Yield.Skills.CastSkillSlot(1)  # Feel No Pain / Dwarven Stability
        yield from Routines.Yield.Skills.CastSkillSlot(5)  # Movement/defensive (Dark Escape for Assassin)
        yield from Routines.Yield.Skills.CastSkillSlot(6)  # Profession sustain

    def GetFoeCountNearby(self, radius=Range.Earshot.value) -> int:
        try:
            player_id = GLOBAL_CACHE.Player.GetAgentID()
            if not player_id: return 0

            player_pos = Agent.GetXY(player_id)
            all_enemies = AgentArray.GetEnemyArray()

            # Filter chain: IsValid -> HasModel -> IsAlive -> Distance
            # This order minimizes native crashes on unstable agents
            valid_enemies = AgentArray.Filter.ByAttribute(all_enemies, 'IsValid')
            loaded_enemies = AgentArray.Filter.ByAttribute(valid_enemies, 'GetModelID')
            alive_enemies = AgentArray.Filter.ByAttribute(loaded_enemies, 'IsAlive')
            nearby_enemies = AgentArray.Filter.ByDistance(alive_enemies, player_pos, radius)

            return len(nearby_enemies)
        except:
            return 0

    def GetNearestEnemySafe(self, radius=Range.Earshot.value) -> int:
        """Exception-safe way to get nearest enemy using native sorting."""
        try:
            player_id = GLOBAL_CACHE.Player.GetAgentID()
            if not player_id: return 0

            player_pos = Agent.GetXY(player_id)
            all_enemies = AgentArray.GetEnemyArray()

            # Filter chain: IsValid -> HasModel -> IsAlive -> Distance
            valid_enemies = AgentArray.Filter.ByAttribute(all_enemies, 'IsValid')
            loaded_enemies = AgentArray.Filter.ByAttribute(valid_enemies, 'GetModelID')
            alive_enemies = AgentArray.Filter.ByAttribute(loaded_enemies, 'IsAlive')
            nearby_enemies = AgentArray.Filter.ByDistance(alive_enemies, player_pos, radius)

            if not nearby_enemies:
                return 0

            # Sort by distance native
            sorted_enemies = AgentArray.Sort.ByDistance(nearby_enemies, player_pos)

            if sorted_enemies:
                return sorted_enemies[0]
            return 0
        except:
            return 0
    # endregion

    # region Sequential Combat Routine
    def CombatRoutine(self):
        """Sequential combat routine replacing managed coroutines."""
        ConsoleLog(MODULE_NAME, "Starting Sequential Combat Routine", Py4GW.Console.MessageType.Info)

        # Wait for enemies to spawn/aggro (up to 15s)
        spawn_wait = Timer()
        spawn_wait.Start()
        ConsoleLog(MODULE_NAME, "Waiting for enemies...", Py4GW.Console.MessageType.Info)

        while spawn_wait.GetElapsedTime() < 15000:
            try:
                # Ultra-safe check: Just count the array size, don't touch the agents
                all_enemies = AgentArray.GetEnemyArray()
                if len(all_enemies) > 0:
                    ConsoleLog(MODULE_NAME, f"Enemies detected (Raw Count: {len(all_enemies)}). Waiting for stability...", Py4GW.Console.MessageType.Info)
                    # Wait 2 seconds for entity initialization to complete to avoid crashes
                    yield from Routines.Yield.wait(2000)
                    break
            except:
                pass
            yield from Routines.Yield.wait(250)

        # Timeout for safety
        timeout = Timer()
        timeout.Start()

        call_target_counter = 0  # For last enemy call target (every 10 ticks)

        while timeout.GetElapsedTime() < 120000: # 2 minutes max
            try:
                ConsoleLog(MODULE_NAME, "CombatRoutine: Start Loop", Py4GW.Console.MessageType.Info)

                # 1. Update targeting/enemies (Stateless)
                self.foe_count = self.GetFoeCountNearby(Range.Compass.value)
                ConsoleLog(MODULE_NAME, f"CombatRoutine: Foe Count {self.foe_count}", Py4GW.Console.MessageType.Info)

                # Special logic when only 1 enemy remains (AutoIt line 1600)
                # Ensure player targets last enemy every 10 ticks
                if self.foe_count == 1:
                    if call_target_counter % 10 == 0:
                        last_enemy = self.GetNearestEnemySafe(Range.Compass.value)
                        if last_enemy:
                            # Target the last enemy to ensure continued focus
                            GLOBAL_CACHE.Player.ChangeTarget(last_enemy)
                            ConsoleLog(MODULE_NAME, f"Targeting last enemy", Py4GW.Console.MessageType.Info)
                    call_target_counter += 1

                # Exit if no enemies left
                if self.foe_count <= 0:
                    ConsoleLog(MODULE_NAME, "Combat complete - foes defeated", Py4GW.Console.MessageType.Info)
                    break

                # Exit if map changed or player dead
                if not Map.IsExplorable() or Agent.IsDead(GLOBAL_CACHE.Player.GetAgentID()):
                    break

                # 2. Run Hero Support Logic
                ConsoleLog(MODULE_NAME, "CombatRoutine: Running HeroSupportLogic", Py4GW.Console.MessageType.Info)
                # yield from self.HeroSupportLogic()

                # 3. Run Combat Logic
                ConsoleLog(MODULE_NAME, "CombatRoutine: Running CombatLogic", Py4GW.Console.MessageType.Info)
                yield from self.CombatLogic()

            except Exception as e:
                ConsoleLog(MODULE_NAME, f"Combat Loop Error: {str(e)}", Py4GW.Console.MessageType.Warning)

            # 4. Wait a short bit to yield control
            yield from Routines.Yield.wait(100)

    def GetAgentIDByModelIDSafe(self, model_id):
        """Exception-safe way to find an NPC by model ID."""
        try:
            # Scan ALL agents to ensure we don't miss based on allegiance
            # This is safe because we use try-except for property access
            candidates = AgentArray.GetAgentArray()

            for agent_id in candidates:
                try:
                    if Agent.IsValid(agent_id):
                         if Agent.GetModelID(agent_id) == model_id:
                             return agent_id
                except:
                    continue
            return 0
        except:
            return 0

    def IsHeroSkillReady(self, hero_index: int, slot: int) -> bool:
        """Check if a hero skill is ready (recharge == 0)."""
        try:
            # GetHeroSkillbar expects 1-based index? Or 0-based?
            # Based on Py4GWCoreLib/Skillbar.py: GetHeroSkillbar(hero_index) calls GetHeroSkillbar(hero_index).
            # Usually these match the Party index.
            # slot is 1-based. List is 0-based.
            skillbar = SkillBar.GetHeroSkillbar(hero_index)
            if skillbar and len(skillbar) >= slot:
                return skillbar[slot-1].recharge == 0
            return False
        except:
            return False

    def HeroSupportLogic(self):
        """Logic from HeroSupportCoroutine + ProtAndHealSupport, adapted for sequential execution."""
        try:
            hero_4_agent = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(4) # Mesmer (Martyr)
            hero_5_agent = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(5) # SoS
            hero_6_agent = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(6) # BiP
            hero_7_agent = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(7) # ST
            player_agent = GLOBAL_CACHE.Player.GetAgentID()

            # --- 1. Miku & Party Healing (ProtAndHealSupport) ---
            # Find Miku (Model ID 58) using safe method
            miku_agent = self.GetAgentIDByModelIDSafe(58)

            # Priority Targets for Healing: Miku -> Player -> Fire Ele (Pos 1) -> SoS (Pos 5)
            heal_targets = []
            if miku_agent: heal_targets.append(miku_agent)
            if player_agent: heal_targets.append(player_agent)

            hero_1 = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(1) # Fire Ele
            if hero_1: heal_targets.append(hero_1)
            if hero_5_agent: heal_targets.append(hero_5_agent)

            for target_id in heal_targets:
                if not Agent.IsValid(target_id) or Agent.IsDead(target_id): continue

                hp = Agent.GetHealth(target_id)
                if hp < 0.7: # 70% Threshold
                    # Mesmer: Spirit Bond (Slot 7)
                    if hero_4_agent and Agent.IsValid(hero_4_agent) and not Agent.IsDead(hero_4_agent):
                        if self.IsHeroSkillReady(4, 7):
                            SkillBar.HeroUseSkill(target_id, 7, 4)

                    # SoS: Mend Body and Soul (Slot 5)
                    if hero_5_agent and Agent.IsValid(hero_5_agent) and not Agent.IsDead(hero_5_agent):
                        if self.IsHeroSkillReady(5, 5):
                            SkillBar.HeroUseSkill(target_id, 5, 5)

                    # BiP: Spirit Light (Slot 6)
                    if hero_6_agent and Agent.IsValid(hero_6_agent) and not Agent.IsDead(hero_6_agent) and hp < 0.6:
                         if self.IsHeroSkillReady(6, 6):
                             SkillBar.HeroUseSkill(target_id, 6, 6)

            # --- 2. ST Spirits Upkeep (Slot 2 & 3) ---
            if hero_7_agent and Agent.IsValid(hero_7_agent) and not Agent.IsDead(hero_7_agent):
                if self.IsHeroSkillReady(7, 2):
                    yield from Routines.Yield.Keybinds.HeroSkill(7, 2, log=True)  # ST Rit - Shelter
                if self.IsHeroSkillReady(7, 3):
                    yield from Routines.Yield.Keybinds.HeroSkill(7, 3, log=True)  # ST Rit - Union

            # --- 3. BiP Energy Management ---
            if hero_6_agent and Agent.IsValid(hero_6_agent) and not Agent.IsDead(hero_6_agent) and Agent.GetHealth(hero_6_agent) > 0.9:
                # Give energy to heroes < 50%
                hero_count = GLOBAL_CACHE.Party.GetHeroCount()
                for i in range(1, hero_count + 1):
                    target_agent = GLOBAL_CACHE.Party.Heroes.GetHeroAgentIDByPartyPosition(i)
                    if target_agent and Agent.IsValid(target_agent) and not Agent.IsDead(target_agent) and Agent.GetEnergy(target_agent) < 0.5:
                        if self.IsHeroSkillReady(6, 1):
                            SkillBar.HeroUseSkill(target_agent, 1, 6)
                        break
                # Give energy to player < 50%
                if player_agent and Agent.GetEnergy(player_agent) < 0.5:
                    if self.IsHeroSkillReady(6, 1):
                        SkillBar.HeroUseSkill(player_agent, 1, 6)

        except Exception as e:
            pass
        yield

    def CombatLogic(self):
        """Logic from CombatLogicCoroutine + PlayerAssistance, adapted with proper skill state checks."""
        try:
            player_id = GLOBAL_CACHE.Player.GetAgentID()
            if not player_id:
                yield
                return

            # Check if we can act
            if not Routines.Checks.Player.CanAct():
                yield
                return

            player_hp = Agent.GetHealth(player_id)

            # 1. Survival: Self Heal (Slot 6) if HP < 85% AND skill is ready
            if player_hp < 0.85:
                skill_6_data = GLOBAL_CACHE.SkillBar.GetSkillData(6)  # 1-based slot
                if skill_6_data and skill_6_data.recharge == 0 and not Agent.IsCasting(player_id):
                    ConsoleLog(MODULE_NAME, "CombatLogic: Casting Self Heal", Py4GW.Console.MessageType.Info)
                    yield from Routines.Yield.Skills.CastSkillSlot(6)

            # 2. Adrenaline skill (Slot 3) - "To the Limit!" - only cast when needed
            skill_3_data = GLOBAL_CACHE.SkillBar.GetSkillData(3)  # 1-based slot
            if skill_3_data and skill_3_data.recharge == 0 and not Agent.IsCasting(player_id):
                skill_3_id = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(3)
                adrenaline_cost = GLOBAL_CACHE.Skill.Data.GetAdrenaline(skill_3_id)
                if adrenaline_cost > 0:  # It's an adrenaline skill
                    # Cast if we have enough adrenaline
                    if skill_3_data.adrenaline_a >= adrenaline_cost:
                        yield from Routines.Yield.Skills.CastSkillSlot(3)
                else:
                    # Not an adrenaline skill (energy-based), cast if ready
                    yield from Routines.Yield.Skills.CastSkillSlot(3)

            # 3. Dwarven Stability (Slot 1) - stance, cast if off cooldown
            skill_1_data = GLOBAL_CACHE.SkillBar.GetSkillData(1)  # 1-based slot
            if skill_1_data and skill_1_data.recharge == 0 and not Agent.IsCasting(player_id):
                yield from Routines.Yield.Skills.CastSkillSlot(1)

            # 4. Attack Logic - continuously acquire and attack targets
            nearest = self.GetNearestEnemySafe(Range.Adjacent.value)
            if nearest:
                # Attack if not already attacking (includes re-targeting dead/invalid targets)
                if not Agent.IsAttacking(player_id):
                    GLOBAL_CACHE.Player.Interact(nearest, False)

                # 100 Blades (Slot 2) - elite attack skill
                skill_2_data = GLOBAL_CACHE.SkillBar.GetSkillData(2)  # 1-based slot
                if skill_2_data and skill_2_data.recharge == 0 and not Agent.IsCasting(player_id):
                    skill_2_id = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(2)
                    adrenaline_cost = GLOBAL_CACHE.Skill.Data.GetAdrenaline(skill_2_id)
                    if adrenaline_cost > 0:
                        if skill_2_data.adrenaline_a >= adrenaline_cost:
                            yield from Routines.Yield.Skills.CastSkillSlot(2)
                    else:
                        # No adrenaline required
                        yield from Routines.Yield.Skills.CastSkillSlot(2)

                # Whirlwind Attack (Slot 4)
                skill_4_data = GLOBAL_CACHE.SkillBar.GetSkillData(4)  # 1-based slot
                if skill_4_data and skill_4_data.recharge == 0 and not Agent.IsCasting(player_id):
                    skill_4_id = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(4)
                    adrenaline_cost = GLOBAL_CACHE.Skill.Data.GetAdrenaline(skill_4_id)
                    if adrenaline_cost > 0:
                        if skill_4_data.adrenaline_a >= adrenaline_cost:
                            yield from Routines.Yield.Skills.CastSkillSlot(4)
                    else:
                        # No adrenaline required
                        yield from Routines.Yield.Skills.CastSkillSlot(4)

        except Exception as e:
            ConsoleLog(MODULE_NAME, f"CombatLogic Error: {str(e)}", Py4GW.Console.MessageType.Warning)
        yield
    # endregion

bot = ChanceEncounterBot()

def main():
    try:
        # Handle map loading
        if Map.IsMapLoading():
            GLOBAL_CACHE._ActionQueueManager.ResetAllQueues()
            return

        # Update bot FSM
        bot.Update()

        # Draw GUI window
        bot.UI.draw_window()

    except Exception as e:
        ConsoleLog(MODULE_NAME, f"Error: {str(e)}", Py4GW.Console.MessageType.Error)

if __name__ == "__main__":
    main()
