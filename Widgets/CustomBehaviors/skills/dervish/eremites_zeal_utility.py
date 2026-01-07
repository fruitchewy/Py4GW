from re import S
from typing import Any, Callable, Generator, override
import random

from Py4GWCoreLib import GLOBAL_CACHE, Routines, Agent
from Py4GWCoreLib.enums import Profession, Range
from Widgets.CustomBehaviors.primitives.behavior_state import BehaviorState
from Widgets.CustomBehaviors.primitives.bus.event_bus import EventBus
from Widgets.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Widgets.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Widgets.CustomBehaviors.primitives.helpers.targeting_order import TargetingOrder
from Widgets.CustomBehaviors.primitives.parties.custom_behavior_party import CustomBehaviorParty
from Widgets.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Widgets.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Widgets.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase

class EremitesZealUtility(CustomSkillUtilityBase):
    def __init__(
            self,
            event_bus: EventBus,
            current_build: list[CustomSkill],
            score_definition: ScoreStaticDefinition = ScoreStaticDefinition(80),
            allowed_states: list[BehaviorState] = [BehaviorState.CLOSE_TO_AGGRO, BehaviorState.IN_AGGRO]
            ) -> None:

        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("Eremites_Zeal"),
            in_game_build=current_build,
            score_definition=score_definition,
            allowed_states=allowed_states)
        
        self.score_definition: ScoreStaticDefinition = score_definition

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        has_buff = Routines.Checks.Effects.HasBuff(GLOBAL_CACHE.Player.GetAgentID(), self.custom_skill.skill_id)
        is_moving = Agent.IsMoving(GLOBAL_CACHE.Player.GetAgentID())
        player = GLOBAL_CACHE.Player.GetAgent().living_agent
        missing_energy = player.max_energy - player.energy
        enemy_count = len(custom_behavior_helpers.Targets.get_all_possible_enemies_ordered_by_priority_raw(
            within_range=Range.Earshot,
            range_to_count_enemies=GLOBAL_CACHE.Skill.Data.GetAoERange(self.custom_skill.skill_id)))
        
        if missing_energy >= enemy_count*3 : return self.score_definition.get_score()
        return None

    @override
    def _execute(self, state: BehaviorState) -> Generator[Any, None, BehaviorResult]:
        result:BehaviorResult = yield from custom_behavior_helpers.Actions.cast_skill(self.custom_skill)
        return result 