from typing import Any, Generator, override

from Py4GWCoreLib import GLOBAL_CACHE, Range, Routines, Agent
from Widgets.CustomBehaviors.primitives.behavior_state import BehaviorState
from Widgets.CustomBehaviors.primitives.bus.event_bus import EventBus
from Widgets.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Widgets.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Widgets.CustomBehaviors.primitives.helpers.targeting_order import TargetingOrder
from Widgets.CustomBehaviors.primitives.scores.healing_score import HealingScore
from Widgets.CustomBehaviors.primitives.scores.score_per_health_gravity_definition import (
    ScorePerHealthGravityDefinition,
)
from Widgets.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Widgets.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase


class ImbueHealthUtility(CustomSkillUtilityBase):
    """
    Imbue_Health utility.
    """

    def __init__(
        self,
        event_bus: EventBus,
        current_build: list[CustomSkill],
        score_definition: ScorePerHealthGravityDefinition = ScorePerHealthGravityDefinition(10),
        mana_required_to_cast: int = 10,
        allowed_states: list[BehaviorState] = [
            BehaviorState.IN_AGGRO,
            BehaviorState.CLOSE_TO_AGGRO,
            BehaviorState.FAR_FROM_AGGRO,
        ],
    ) -> None:
        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("Imbue_Health"),
            in_game_build=current_build,
            score_definition=score_definition,
            mana_required_to_cast=mana_required_to_cast,
            allowed_states=allowed_states,
        )

        self.score_definition: ScorePerHealthGravityDefinition = score_definition

    def _get_targets(self) -> list[custom_behavior_helpers.SortableAgentData]:
        """
        Return allies ordered by priority (lowest HP, then distance) within spellcast range.
        """
        player_agent = GLOBAL_CACHE.Player.GetAgentID()

        targets: list[custom_behavior_helpers.SortableAgentData] = (
            custom_behavior_helpers.Targets.get_all_possible_allies_ordered_by_priority_raw(
                within_range=Range.Spellcast,
                condition=lambda agent_id: player_agent != agent_id
                and (Agent.GetHealth(agent_id) is not None and Agent.GetHealth(agent_id) < 1.0),
                sort_key=(TargetingOrder.HP_ASC, TargetingOrder.DISTANCE_ASC),
            )
        )
        return targets

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        player_agent = GLOBAL_CACHE.Player.GetAgentID()
        player_hp = min(Agent.GetHealth(player_agent), 300)

        # Get Mysticism attribute level (ID 44)
        attributes = Agent.GetAttributesDict(player_agent)
        mysticism_level = attributes.get(44, 0)
        
        # Calculate health sacrifice ratio based on user formula: .05 + (mysticism * 3)
        # Interpreted as a percentage value (e.g. 36.05%), so divided by 100.
        health_percentage_scaling = 0.05 + (mysticism_level * .03)
        total_heal = player_hp * health_percentage_scaling
        
        # Calculate efficiency ratio based on a standard 150 HP heal
        efficiency_ratio = total_heal / 150.0

        targets = self._get_targets()
        if len(targets) == 0:
            return None

        top = targets[0]
        base_score = 0.0
        
        if top.hp < 0.40:
            base_score = self.score_definition.get_score(HealingScore.MEMBER_DAMAGED_EMERGENCY)
        elif top.hp < 0.85:
            base_score = self.score_definition.get_score(HealingScore.MEMBER_DAMAGED)
        
        if base_score > 0:
            return base_score * efficiency_ratio

        return None

    @override
    def _execute(self, state: BehaviorState) -> Generator[Any, None, BehaviorResult]:
        """
        Execution path re-checks buffs defensively and then casts on the top target.
        """
        targets = self._get_targets()
        if len(targets) == 0:
            return BehaviorResult.ACTION_SKIPPED

        target = targets[0]
        result = yield from custom_behavior_helpers.Actions.cast_skill_to_target(
            self.custom_skill, target_agent_id=target.agent_id
        )
        return result
