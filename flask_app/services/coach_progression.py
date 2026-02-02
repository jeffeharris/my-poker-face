"""Coach progression service — state machine, gate management, and coaching decisions.

Orchestrates skill evaluation, state transitions, gate unlocks,
and coaching mode selection for the progression system.
"""

import logging
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Dict, List, Optional

from .coach_models import (
    CoachingDecision, CoachingMode, GateProgress, PlayerSkillState,
    SKILL_STATE_ORDER, SkillState,
)
from .skill_definitions import ALL_GATES, ALL_SKILLS, get_skills_for_gate
from .situation_classifier import SituationClassifier, SituationClassification
from .skill_evaluator import SkillEvaluation, SkillEvaluator

logger = logging.getLogger(__name__)


class SessionMemory:
    """In-memory tracking of coaching activity within a game session.

    Stored in game_data['coach_session_memory']. Resets on game end
    or server restart. Not persisted to database.
    """

    def __init__(self):
        self.coached_skills_this_hand: set = set()
        self.concept_count: Dict[str, int] = defaultdict(int)
        self.current_hand_number: int = 0

    def new_hand(self, hand_number: int) -> None:
        """Reset per-hand tracking when a new hand starts."""
        if hand_number != self.current_hand_number:
            self.coached_skills_this_hand.clear()
            self.current_hand_number = hand_number

    def record_coaching(self, skill_id: str) -> None:
        """Record that coaching was delivered for a skill this hand."""
        self.coached_skills_this_hand.add(skill_id)
        self.concept_count[skill_id] += 1

    def was_coached_this_hand(self, skill_id: str) -> bool:
        """Check if this skill was already coached in the current hand."""
        return skill_id in self.coached_skills_this_hand

    def should_shorten(self, skill_id: str) -> bool:
        """After 3+ explanations of the same concept, shorten to stat-only."""
        return self.concept_count[skill_id] >= 3


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

    def get_or_initialize_player(self, user_id: str) -> Dict:
        """Load player state, auto-initializing if no profile exists."""
        state = self.get_player_state(user_id)
        if not state['profile']:
            state = self.initialize_player(user_id)
        return state

    def initialize_player(self, user_id: str, level: str = 'beginner') -> Dict:
        """Initialize a coaching profile at the given level.

        Level determines initial gate/skill states:
        - beginner: Gate 1 unlocked, all Gate 1 skills Introduced
        - intermediate: Gate 1 Practicing, Gate 2 unlocked + Introduced
        - experienced: Gate 1 Reliable, Gate 2 Practicing
        """
        self._persistence.save_coach_profile(
            user_id, self_reported_level=level, effective_level=level
        )

        now = datetime.now().isoformat()

        # Gate 1 is always unlocked
        self._persistence.save_gate_progress(
            user_id, GateProgress(gate_number=1, unlocked=True, unlocked_at=now)
        )

        if level == 'beginner':
            gate1_state = SkillState.INTRODUCED
        elif level == 'intermediate':
            gate1_state = SkillState.PRACTICING
        else:  # experienced
            gate1_state = SkillState.RELIABLE

        # Initialize Gate 1 skills
        for skill_def in get_skills_for_gate(1):
            ss = PlayerSkillState(
                skill_id=skill_def.skill_id,
                state=gate1_state,
                first_seen_at=now,
            )
            self._persistence.save_skill_state(user_id, ss)

        # Intermediate and experienced: unlock Gate 2
        if level in ('intermediate', 'experienced'):
            self._persistence.save_gate_progress(
                user_id, GateProgress(gate_number=2, unlocked=True, unlocked_at=now)
            )
            gate2_state = (SkillState.PRACTICING if level == 'experienced'
                           else SkillState.INTRODUCED)
            for skill_def in get_skills_for_gate(2):
                ss = PlayerSkillState(
                    skill_id=skill_def.skill_id,
                    state=gate2_state,
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
        session_memory: Optional[SessionMemory] = None,
        hand_number: int = 0,
    ) -> CoachingDecision:
        """Determine what to coach on for the current situation.

        Applies per-skill cadence rules via session_memory:
        - Introduced: coach every relevant action (no cadence limit)
        - Practicing: at most once per hand for this skill
        - Reliable: only on deviation (post-action), not pre-action
        - Automatic: silent
        """
        unlocked = [g for g, gp in gate_progress.items() if gp.unlocked]

        classification = self._classifier.classify(
            coaching_data, unlocked, skill_states,
        )

        if not classification.primary_skill:
            return CoachingDecision(mode=CoachingMode.SILENT)

        primary_state = skill_states.get(classification.primary_skill)
        mode = self._determine_mode(primary_state)

        # Apply session cadence rules
        if session_memory and mode != CoachingMode.SILENT:
            session_memory.new_hand(hand_number)
            skill_id = classification.primary_skill
            state = primary_state.state if primary_state else SkillState.INTRODUCED

            if state == SkillState.PRACTICING:
                if session_memory.was_coached_this_hand(skill_id):
                    return CoachingDecision(mode=CoachingMode.SILENT)
            elif state == SkillState.RELIABLE:
                # Reliable skills: silent pre-action. Post-action feedback via
                # evaluate_and_update() — not yet surfaced to player (planned M3).
                return CoachingDecision(mode=CoachingMode.SILENT)

        skill_def = ALL_SKILLS.get(classification.primary_skill)
        shorten = (session_memory.should_shorten(classification.primary_skill)
                   if session_memory else False)
        prompt = self._build_coaching_prompt(
            mode, classification, skill_def, primary_state, shorten=shorten,
        )

        # Record that we're coaching this skill
        if session_memory and mode != CoachingMode.SILENT:
            session_memory.record_coaching(classification.primary_skill)

        return CoachingDecision(
            mode=mode,
            primary_skill_id=classification.primary_skill,
            relevant_skill_ids=tuple(classification.relevant_skills),
            coaching_prompt=prompt,
            situation_tags=tuple(classification.situation_tags),
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

        return evaluations

    def check_hand_end(self, user_id: str) -> None:
        """Run end-of-hand checks: gate unlocks and silent downgrades.

        Call this once per hand after all evaluate_and_update() calls are done,
        so that gate transitions never occur mid-hand.
        """
        self._check_gate_unlocks(user_id)
        self._check_silent_downgrade(user_id)

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
        is_marginal = evaluation.evaluation == 'marginal'

        # Marginal evaluations are neutral — no progression effect.
        # Only update the timestamp so we know when we last looked at this skill.
        if is_marginal:
            skill_state = replace(skill_state, last_evaluated_at=now)
            self._persistence.save_skill_state(user_id, skill_state)
            return skill_state

        # Compute new totals (correct and incorrect only)
        new_total_opps = skill_state.total_opportunities + 1
        new_total_correct = skill_state.total_correct + (1 if is_correct else 0)

        # Compute new window values
        new_window_opps = skill_state.window_opportunities + 1
        new_window_correct = skill_state.window_correct + (1 if is_correct else 0)

        # Compute new streaks
        if is_correct:
            new_streak_correct = skill_state.streak_correct + 1
            new_streak_incorrect = 0
        else:
            new_streak_correct = 0
            new_streak_incorrect = skill_state.streak_incorrect + 1

        skill_state = replace(
            skill_state,
            total_opportunities=new_total_opps,
            total_correct=new_total_correct,
            window_opportunities=new_window_opps,
            window_correct=new_window_correct,
            streak_correct=new_streak_correct,
            streak_incorrect=new_streak_incorrect,
            last_evaluated_at=now,
        )

        # Trim window if it exceeds window_size
        skill_def = ALL_SKILLS.get(skill_id)
        window_size = skill_def.evidence_rules.window_size if skill_def else 20
        if skill_state.window_opportunities > window_size:
            skill_state = self._trim_window(skill_state, window_size)

        # Check state transitions
        skill_state = self._check_state_transitions(skill_state, skill_def)

        # Persist
        self._persistence.save_skill_state(user_id, skill_state)
        return skill_state

    def _trim_window(self, skill_state: PlayerSkillState, window_size: int) -> PlayerSkillState:
        """Proportionally trim window to window_size."""
        if skill_state.window_opportunities <= window_size:
            return skill_state
        ratio = skill_state.window_correct / skill_state.window_opportunities
        return replace(
            skill_state,
            window_opportunities=window_size,
            # int() truncates deliberately — conservative bias prevents premature
            # advancement near thresholds
            window_correct=int(ratio * window_size),
        )

    def _check_state_transitions(
        self, skill_state: PlayerSkillState, skill_def
    ) -> PlayerSkillState:
        """Check and apply state transitions based on evidence rules."""
        if not skill_def:
            return skill_state

        rules = skill_def.evidence_rules
        acc = skill_state.window_accuracy
        opps = skill_state.window_opportunities

        current = skill_state.state
        new_state = current

        if current == SkillState.INTRODUCED:
            if skill_state.total_opportunities >= rules.introduced_min_opps:
                new_state = SkillState.PRACTICING
                logger.info(f"Skill {skill_state.skill_id}: introduced -> practicing")

        elif current == SkillState.PRACTICING:
            if opps >= rules.min_opportunities and acc >= rules.advancement_threshold:
                new_state = SkillState.RELIABLE
                logger.info(f"Skill {skill_state.skill_id}: practicing -> reliable "
                            f"(accuracy={acc:.2f}, opps={opps})")

        elif current == SkillState.RELIABLE:
            if opps >= rules.automatic_min_opps and acc >= rules.automatic_threshold:
                new_state = SkillState.AUTOMATIC
                logger.info(f"Skill {skill_state.skill_id}: reliable -> automatic "
                            f"(accuracy={acc:.2f}, opps={opps})")
            elif opps >= rules.min_opportunities and acc < rules.regression_threshold:
                new_state = SkillState.PRACTICING
                logger.info(f"Skill {skill_state.skill_id}: reliable -> practicing "
                            f"(regression, accuracy={acc:.2f})")

        elif current == SkillState.AUTOMATIC:
            if opps >= rules.min_opportunities and acc < rules.automatic_regression:
                new_state = SkillState.RELIABLE
                logger.info(f"Skill {skill_state.skill_id}: automatic -> reliable "
                            f"(regression, accuracy={acc:.2f})")

        if new_state != current:
            return replace(skill_state, state=new_state)
        return skill_state

    def _check_gate_unlocks(self, user_id: str) -> None:
        """Check if any new gates should be unlocked.

        A gate N unlocks when gate N-1's required_reliable threshold is met
        by gate N-1's skills reaching Reliable or Automatic.
        """
        skill_states = self._persistence.load_all_skill_states(user_id)
        gate_progress = self._persistence.load_gate_progress(user_id)

        for gate_num in sorted(ALL_GATES.keys()):
            gp = gate_progress.get(gate_num)
            if gp and gp.unlocked:
                continue  # Already unlocked

            # Gate N unlocks when gate N-1 meets its required_reliable
            prev_gate_num = gate_num - 1
            prev_gate_def = ALL_GATES.get(prev_gate_num)
            if not prev_gate_def:
                continue  # No previous gate to check

            # Count reliable skills in the previous gate
            reliable_count = sum(
                1 for sid in prev_gate_def.skill_ids
                if sid in skill_states and skill_states[sid].state in (
                    SkillState.RELIABLE, SkillState.AUTOMATIC
                )
            )

            if reliable_count >= prev_gate_def.required_reliable:
                new_gp = GateProgress(
                    gate_number=gate_num,
                    unlocked=True,
                    unlocked_at=datetime.now().isoformat(),
                )
                self._persistence.save_gate_progress(user_id, new_gp)
                logger.info(f"Gate {gate_num} unlocked for user {user_id} "
                            f"(gate {prev_gate_num} has {reliable_count} reliable skills)")

                # Initialize skills for the newly unlocked gate
                now = datetime.now().isoformat()
                for skill_def in get_skills_for_gate(gate_num):
                    if skill_def.skill_id not in skill_states:
                        ss = PlayerSkillState(
                            skill_id=skill_def.skill_id,
                            state=SkillState.INTRODUCED,
                            first_seen_at=now,
                        )
                        self._persistence.save_skill_state(user_id, ss)

                # Reload after mutations so subsequent iterations see fresh data
                skill_states = self._persistence.load_all_skill_states(user_id)
                gate_progress = self._persistence.load_gate_progress(user_id)

    def _check_silent_downgrade(self, user_id: str) -> None:
        """Downgrade effective_level if observed play contradicts self-reported level.

        Only downgrades — never upgrades. Requires sufficient data (min_opportunities
        on at least 2 skills) before triggering.
        """
        profile = self._persistence.load_coach_profile(user_id)
        if not profile or profile['effective_level'] == 'beginner':
            return

        skill_states = self._persistence.load_all_skill_states(user_id)
        gate1_skills = get_skills_for_gate(1)
        gate2_skills = get_skills_for_gate(2)

        def all_at_or_below(skills, max_state):
            """Check if all skills with sufficient data are at or below max_state."""
            evaluated = [
                skill_states[s.skill_id] for s in skills
                if s.skill_id in skill_states and skill_states[s.skill_id].total_opportunities >= 5
            ]
            if len(evaluated) < 2:
                return False  # Not enough data to judge
            return all(SKILL_STATE_ORDER[ss.state] <= SKILL_STATE_ORDER[max_state] for ss in evaluated)

        current_level = profile['effective_level']

        if current_level == 'experienced':
            # If gate 1 skills are all at practicing or below → beginner
            if all_at_or_below(gate1_skills, SkillState.PRACTICING):
                self._persistence.save_coach_profile(
                    user_id, self_reported_level=profile['self_reported_level'],
                    effective_level='beginner',
                )
                logger.info(f"Silent downgrade: {user_id} experienced -> beginner")
                return
            # If gate 2 skills are all at practicing or below → intermediate
            if all_at_or_below(gate2_skills, SkillState.PRACTICING):
                self._persistence.save_coach_profile(
                    user_id, self_reported_level=profile['self_reported_level'],
                    effective_level='intermediate',
                )
                logger.info(f"Silent downgrade: {user_id} experienced -> intermediate")
                return

        elif current_level == 'intermediate':
            # If gate 1 skills are all at practicing or below → beginner
            if all_at_or_below(gate1_skills, SkillState.PRACTICING):
                self._persistence.save_coach_profile(
                    user_id, self_reported_level=profile['self_reported_level'],
                    effective_level='beginner',
                )
                logger.info(f"Silent downgrade: {user_id} intermediate -> beginner")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _determine_mode(self, skill_state: Optional[PlayerSkillState]) -> CoachingMode:
        """Determine coaching mode from skill state."""
        if not skill_state:
            return CoachingMode.LEARN

        # TODO (M3): Split PRACTICING into LEARN (accuracy < 0.60) and COMPETE
        # (accuracy >= 0.60) per §5.2 of requirements
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
        shorten: bool = False,
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

        if shorten:
            parts.append(
                "BREVITY: This concept has been explained multiple times. "
                "Give stats only — no explanation needed."
            )
        elif mode == CoachingMode.LEARN:
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
