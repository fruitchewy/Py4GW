from typing import override

from Widgets.CustomBehaviors.primitives.behavior_state import BehaviorState
from Widgets.CustomBehaviors.primitives.scores.score_per_agent_quantity_definition import (
    ScorePerAgentQuantityDefinition,
)
from Widgets.CustomBehaviors.primitives.scores.score_per_health_gravity_definition import (
    ScorePerHealthGravityDefinition,
)
from Widgets.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Widgets.CustomBehaviors.primitives.skillbars.custom_behavior_base_utility import CustomBehaviorBaseUtility
from Widgets.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Widgets.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase
from Widgets.CustomBehaviors.skills.generic.keep_self_effect_up_utility import KeepSelfEffectUpUtility
from Widgets.CustomBehaviors.skills.common.breath_of_the_great_dwarf_utility import BreathOfTheGreatDwarfUtility
from Widgets.CustomBehaviors.skills.common.ebon_battle_standard_of_honor_utility import EbonBattleStandardOfHonorUtility
from Widgets.CustomBehaviors.skills.common.ebon_vanguard_assassin_support_utility import (
    EbonVanguardAssassinSupportUtility,
)
from Widgets.CustomBehaviors.skills.common.great_dwarf_weapon_utility import GreatDwarfWeaponUtility
from Widgets.CustomBehaviors.skills.dervish.eremites_zeal_utility import EremitesZealUtility
from Widgets.CustomBehaviors.skills.dervish.imbue_health_utility import ImbueHealthUtility
from Widgets.CustomBehaviors.skills.generic.generic_resurrection_utility import GenericResurrectionUtility
from Widgets.CustomBehaviors.skills.ritualist.life_utility import LifeUtility
from Widgets.CustomBehaviors.skills.ritualist.mend_body_and_soul_utility import MendBodyAndSoulUtility
from Widgets.CustomBehaviors.skills.ritualist.spirit_light_utility import SpiritLightUtility


class DervishVor_UtilitySkillBar(CustomBehaviorBaseUtility):

    def __init__(self):
        super().__init__()
        in_game_build = list(self.skillbar_management.get_in_game_build().values())

        # core
        self.vow_of_revolution_utility: CustomSkillUtilityBase = KeepSelfEffectUpUtility(
            event_bus=self.event_bus,
            skill=CustomSkill("Vow_of_Revolution"),
            current_build=in_game_build,
            score_definition=ScoreStaticDefinition(80),
            allowed_states=[BehaviorState.IN_AGGRO, BehaviorState.CLOSE_TO_AGGRO],
        )
        self.spirit_light_utility: CustomSkillUtilityBase = SpiritLightUtility(
            event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(8)
        )
        self.imbue_health_utility: CustomSkillUtilityBase = ImbueHealthUtility(
            event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(10)
        )

        # optional
        self.eremites_zeal_utility: CustomSkillUtilityBase = EremitesZealUtility(
            event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(50)
        )
        self.life_utility: CustomSkillUtilityBase = LifeUtility(
            event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(5)
        )
        self.breath_of_the_great_dwarf_utility: CustomSkillUtilityBase = BreathOfTheGreatDwarfUtility(
            event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(6)
        )
        self.great_dwarf_weapon_utility: CustomSkillUtilityBase = GreatDwarfWeaponUtility(
            event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(75)
        )
        self.flesh_of_my_flesh_utility: CustomSkillUtilityBase = GenericResurrectionUtility(
            event_bus=self.event_bus,
            skill=CustomSkill("Flesh_of_My_Flesh"),
            current_build=in_game_build,
            score_definition=ScoreStaticDefinition(12),
        )
        self.mend_body_and_soul_utility: CustomSkillUtilityBase = MendBodyAndSoulUtility(
            event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(7)
        )

        # common
        self.ebon_battle_standard_of_honor_utility: CustomSkillUtilityBase = EbonBattleStandardOfHonorUtility(
            event_bus=self.event_bus,
            score_definition=ScorePerAgentQuantityDefinition(
                lambda enemy_qte: 68 if enemy_qte >= 3 else 50 if enemy_qte <= 2 else 25
            ),
            current_build=in_game_build,
            mana_required_to_cast=15,
        )
        self.ebon_vanguard_assassin_support: CustomSkillUtilityBase = EbonVanguardAssassinSupportUtility(
            event_bus=self.event_bus,
            score_definition=ScoreStaticDefinition(71),
            current_build=in_game_build,
            mana_required_to_cast=15,
        )

    @property
    @override
    def complete_build_with_generic_skills(self) -> bool:
        return False

    @property
    @override
    def skills_allowed_in_behavior(self) -> list[CustomSkillUtilityBase]:
        return [
            self.vow_of_revolution_utility,
            self.imbue_health_utility,
            self.eremites_zeal_utility,
            self.spirit_light_utility,
            self.mend_body_and_soul_utility,
            self.life_utility,
            self.breath_of_the_great_dwarf_utility,
            self.great_dwarf_weapon_utility,
            self.ebon_battle_standard_of_honor_utility,
            self.ebon_vanguard_assassin_support,
            self.flesh_of_my_flesh_utility,
        ]

    @property
    @override
    def skills_required_in_behavior(self) -> list[CustomSkill]:
        return [self.vow_of_revolution_utility.custom_skill]
