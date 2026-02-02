"""Coach progression service — state machine, gate management, and coaching decisions.

Orchestrates skill evaluation, state transitions, gate unlocks,
and coaching mode selection for the progression system.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from .skill_definitions import (
    ALL_GATES, ALL_SKILLS, CoachingDecision, CoachingMode,
    GateProgress, PlayerSkillState, SkillState,
    get_skills_for_gate,
)
from .situation_classifier import SituationClassifier, SituationClassification
from .skill_evaluator import SkillEvaluation, SkillEvaluator

logger = logging.getLogger(__name__)


class CoachProgressionService:
    """Manages player skill progression and coaching decisions."""

    def __init__(self, persistence):
        self._persistence = persistence
        self._classifier = SituationClassifier()
        self._evaluator = SkillEvaluator()

    # ------------------------------------------------------------------
    # Player state
    # ------------------------------------------------------------------

    def get_player_state(self, user_id: str) -> Dict:
        """Load all progression state for a player."""
        skill_states = self._persistence.load_all_skill_states(user_id)
        gate_progress = self._persistence.load_gate_progress(user_id)
        profile = self._persistence.load_coach_profile(user_id)
        return {
            'skill_states': skill_states,
            'gate_progress': gate_progress,
            'profile': profile,
        }

    def initialize_player(self, user_id: str, level: str = 'beginner') -> Dict:
        """Auto-initialize a beginner coaching profile.

        Creates the profile, unlocks gate 1, and initializes all gate-1
        skills as 'introduced'.
        """
        self._persistence.save_coach_profile(
            user_id, self_reported_level=level, effective_level=level
        )

        # Unlock gate 1
        gate1 = GateProgress(gate_number=1, unlocked=True,
                             unlocked_at=datetime.now().isoformat())
        self._persistence.save_gate_progress(user_id, gate1)

        # Initialize gate 1 skills
        now = datetime.now().isoformat()
        for skill_def in get_skills_for_gate(1):
            ss = PlayerSkillState(
                skill_id=skill_def.skill_id,
                state=SkillState.INTRODUCED,
                first_seen_at=now,
            )
            self._persistence.save_skill_state(user_id, ss)

        return self.get_player_state(user_id)

    # ------------------------------------------------------------------
    # Coaching decision
    # ------------------------------------------------------------------

    def get_coaching_decision(
        self,
        user_id: str,
        coaching_data: Dict,
        skill_states: Dict[str, PlayerSkillState],
        gate_progress: Dict[int, GateProgress],
    ) -> CoachingDecision:
        """Determine what to coach on for the current situation."""
        unlocked = [g for g, gp in gate_progress.items() if gp.unlocked]

        classification = self._classifier.classify(
            coaching_data, unlocked, skill_states,
        )

        if not classification.primary_skill:
            return CoachingDecision(mode=CoachingMode.SILENT)

        primary_state = skill_states.get(classification.primary_skill)
        mode = self._determine_mode(primary_state)

        skill_def = ALL_SKILLS.get(classification.primary_skill)
        prompt = self._build_coaching_prompt(
            mode, classification, skill_def, primary_state
        )

        return CoachingDecision(
            mode=mode,
            primary_skill_id=classification.primary_skill,
            relevant_skill_ids=list(classification.relevant_skills),
            coaching_prompt=prompt,
            situation_tags=list(classification.situation_tags),
        )

    # ------------------------------------------------------------------
    # Evaluation + state update
    # ------------------------------------------------------------------

    def evaluate_and_update(
        self,
        user_id: str,
        action: str,
        coaching_data: Dict,
        classification: SituationClassification,
    ) -> List[SkillEvaluation]:
        """Evaluate player action against relevant skills and update progress."""
        evaluations = []

        for skill_id in classification.relevant_skills:
            evaluation = self._evaluator.evaluate(
                skill_id, action, coaching_data
            )

            if evaluation.evaluation == 'not_applicable':
                continue

            evaluations.append(evaluation)

            # Update skill progress
            self._update_skill_progress(user_id, skill_id, evaluation)

        # Check for gate unlocks after all evaluations
        self._check_gate_unlocks(user_id)

        return evaluations

    def _update_skill_progress(
        self, user_id: str, skill_id: str, evaluation: SkillEvaluation
    ) -> PlayerSkillState:
        """Update a player's skill progress based on an evaluation."""
        skill_state = self._persistence.load_skill_state(user_id, skill_id)
        now = datetime.now().isoformat()

        if not skill_state:
            skill_state = PlayerSkillState(
                skill_id=skill_id,
                first_seen_at=now,
            )

        is_correct = evaluation.evaluation == 'correct'
        # Marginal counts as half-correct for window tracking
        is_marginal = evaluation.evaluation == 'marginal'

        # Update totals
        skill_state.total_opportunities += 1
        if is_correct:
            skill_state.total_correct += 1

        # Update window
        skill_def = ALL_SKILLS.get(skill_id)
        window_size = skill_def.evidence_rules.window_size if skill_def else 20

        skill_state.window_opportunities += 1
        if is_correct:
            skill_state.window_correct += 1
        elif is_marginal:
            # Marginal counts as 0.5 — but since we track integers,
            # only count it every other time (simpler: don't count marginal)
            pass

        # Trim window if it exceeds window_size
        if skill_state.window_opportunities > window_size:
            self._trim_window(skill_state, window_size)

        # Update streaks
        if is_correct:
            skill_state.streak_correct += 1
            skill_state.streak_incorrect = 0
        elif evaluation.evaluation == 'incorrect':
            skill_state.streak_incorrect += 1
            skill_state.streak_correct = 0
        # Marginal doesn't reset streaks

        skill_state.last_evaluated_at = now

        # Check state transitions
        self._check_state_transitions(skill_state, skill_def)

        # Persist
        self._persistence.save_skill_state(user_id, skill_state)
        return skill_state

    def _trim_window(self, skill_state: PlayerSkillState, window_size: int) -> None:
        """Proportionally trim window to window_size."""
        if skill_state.window_opportunities <= window_size:
            return
        ratio = skill_state.window_correct / skill_state.window_opportunities
        skill_state.window_opportunities = window_size
        skill_state.window_correct = round(ratio * window_size)

    def _check_state_transitions(
        self, skill_state: PlayerSkillState, skill_def
    ) -> None:
        """Check and apply state transitions based on evidence rules."""
        if not skill_def:
            return

        rules = skill_def.evidence_rules
        acc = skill_state.window_accuracy
        opps = skill_state.window_opportunities

        current = skill_state.state

        if current == SkillState.INTRODUCED:
            # Advance to practicing after minimum opportunities
            if skill_state.total_opportunities >= rules.introduced_min_opps:
                skill_state.state = SkillState.PRACTICING
                logger.info(f"Skill {skill_state.skill_id}: introduced -> practicing")

        elif current == SkillState.PRACTICING:
            # Advance to reliable
            if opps >= rules.min_opportunities and acc >= rules.advancement_threshold:
                skill_state.state = SkillState.RELIABLE
                logger.info(f"Skill {skill_state.skill_id}: practicing -> reliable "
                            f"(accuracy={acc:.2f}, opps={opps})")

        elif current == SkillState.RELIABLE:
            # Advance to automatic
            if opps >= rules.automatic_min_opps and acc >= rules.automatic_threshold:
                skill_state.state = SkillState.AUTOMATIC
                logger.info(f"Skill {skill_state.skill_id}: reliable -> automatic "
                            f"(accuracy={acc:.2f}, opps={opps})")
            # Regress to practicing
            elif opps >= rules.min_opportunities and acc < rules.regression_threshold:
                skill_state.state = SkillState.PRACTICING
                logger.info(f"Skill {skill_state.skill_id}: reliable -> practicing "
                            f"(regression, accuracy={acc:.2f})")

        elif current == SkillState.AUTOMATIC:
            # Regress to reliable
            if opps >= rules.min_opportunities and acc < rules.automatic_regression:
                skill_state.state = SkillState.RELIABLE
                logger.info(f"Skill {skill_state.skill_id}: automatic -> reliable "
                            f"(regression, accuracy={acc:.2f})")

    def _check_gate_unlocks(self, user_id: str) -> None:
        """Check if any new gates should be unlocked."""
        skill_states = self._persistence.load_all_skill_states(user_id)
        gate_progress = self._persistence.load_gate_progress(user_id)

        for gate_num, gate_def in sorted(ALL_GATES.items()):
            gp = gate_progress.get(gate_num)
            if gp and gp.unlocked:
                continue  # Already unlocked

            # Check if enough skills are reliable+
            reliable_count = sum(
                1 for sid in gate_def.skill_ids
                if sid in skill_states and skill_states[sid].state in (
                    SkillState.RELIABLE, SkillState.AUTOMATIC
                )
            )

            if reliable_count >= gate_def.required_reliable:
                new_gp = GateProgress(
                    gate_number=gate_num,
                    unlocked=True,
                    unlocked_at=datetime.now().isoformat(),
                )
                self._persistence.save_gate_progress(user_id, new_gp)
                logger.info(f"Gate {gate_num} ({gate_def.name}) unlocked for user {user_id}")

                # Also check if next gate needs unlocking/initializing
                next_gate = gate_num + 1
                if next_gate in ALL_GATES:
                    next_gp = gate_progress.get(next_gate)
                    if not next_gp or not next_gp.unlocked:
                        # Initialize skills for the next gate
                        now = datetime.now().isoformat()
                        for skill_def in get_skills_for_gate(next_gate):
                            if skill_def.skill_id not in skill_states:
                                ss = PlayerSkillState(
                                    skill_id=skill_def.skill_id,
                                    state=SkillState.INTRODUCED,
                                    first_seen_at=now,
                                )
                                self._persistence.save_skill_state(user_id, ss)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _determine_mode(self, skill_state: Optional[PlayerSkillState]) -> CoachingMode:
        """Determine coaching mode from skill state."""
        if not skill_state:
            return CoachingMode.LEARN

        mode_map = {
            SkillState.INTRODUCED: CoachingMode.LEARN,
            SkillState.PRACTICING: CoachingMode.LEARN,
            SkillState.RELIABLE: CoachingMode.COMPETE,
            SkillState.AUTOMATIC: CoachingMode.SILENT,
        }
        return mode_map.get(skill_state.state, CoachingMode.LEARN)

    def _build_coaching_prompt(
        self,
        mode: CoachingMode,
        classification: SituationClassification,
        skill_def,
        skill_state: Optional[PlayerSkillState],
    ) -> str:
        """Build a coaching prompt fragment for the LLM."""
        if mode == CoachingMode.SILENT:
            return ''

        parts = []
        parts.append(f"[Coach Mode: {mode.value.upper()}]")

        if skill_def:
            parts.append(f"Focus skill: {skill_def.name} — {skill_def.description}")

        if skill_state:
            parts.append(
                f"Progress: {skill_state.state.value} "
                f"({skill_state.window_correct}/{skill_state.window_opportunities} "
                f"recent accuracy)"
            )

        if classification.situation_tags:
            parts.append(f"Situation: {', '.join(classification.situation_tags)}")

        if mode == CoachingMode.LEARN:
            parts.append(
                "Teaching mode: Explain the concept clearly. "
                "Tell the player what to look for and why it matters."
            )
        elif mode == CoachingMode.COMPETE:
            parts.append(
                "Compete mode: Brief reminder only. "
                "The player knows this concept — just reinforce."
            )

        return '\n'.join(parts)
