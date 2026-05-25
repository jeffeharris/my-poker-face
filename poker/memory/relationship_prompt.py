"""Relationship → prompt-context formatter.

Pure read-side formatter that turns the durable relationship layer
(per-pair axes + memorable hands) into a short, label-driven block
suitable for injection into an LLM decision prompt. Lives in
`poker.memory` because it reads the manager's relationship repo
directly — same package, no public-API contortions.

Design choices the bucketing reflects:

  - **Labels, not numbers.** LLMs reason poorly about 0–1 floats and
    will over-fixate on small differences. Bucket axes into
    "rival" / "friendly" / (skip) and let the model integrate the
    label however the personality says it should.

  - **Skip neutral opponents.** If every opponent at the table gets
    a line, the block becomes noise. Only mention pairs where
    something actually moved. An empty return string is the
    documented "no relationship signal worth mentioning" case;
    callers can short-circuit on it.

  - **Recent memorable hands only.** The dispatch path attaches a
    `MemorableHand` to the actor's in-memory model when
    `impact_score >= MEMORABLE_HAND_THRESHOLD`. We pull at most 2
    most-recent entries — enough to ground the label in concrete
    history without flooding the prompt.

  - **Pure function, no mutation.** Safe to call from any prompt-
    assembly path, including post-hand commentary and lobby
    tooltips. Graceful degradation: missing manager state /
    missing repo / unknown ids all return empty.

Bucketing thresholds match
`poker/memory/relationship_modifier.py` so the prompt-side label and
the decision-side modifier read the same situation the same way.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Iterable, List, Optional

from .relationship_modifier import (
    HEAT_RIVAL_THRESHOLD,
    LIKABILITY_HIGH_THRESHOLD,
    RESPECT_HIGH_THRESHOLD,
)

if TYPE_CHECKING:
    from .opponent_model import OpponentModelManager


# Maximum number of recent memorable hands to surface per displayed
# opponent. Two strikes the balance between "concrete grounding for
# the label" and "don't flood the prompt with history."
DEFAULT_MAX_MEMORABLE_PER_OPPONENT = 2


def _classify(state) -> Optional[str]:
    """Bucket axis values into a single label, or None to skip.

    Order matters: rival takes precedence over friendly when a pair
    is both heated AND has high respect/likability (rare but
    possible — e.g., a long-running rivalry with mutual respect).
    The emotional foreground is the heat.

    Pure helper. Public for unit tests.
    """
    if state.heat > HEAT_RIVAL_THRESHOLD:
        return "rival"
    if state.respect > RESPECT_HIGH_THRESHOLD and state.likability > LIKABILITY_HIGH_THRESHOLD:
        return "friendly"
    return None


def _format_memorable_lines(
    manager: OpponentModelManager,
    observer_name: str,
    opponent_name: str,
    max_hands: int,
) -> List[str]:
    """Return the most-recent N memorable-hand narratives for the pair.

    Reads from the in-memory `OpponentModel.memorable_hands` list —
    the same field `record_event` writes to on bilateral updates.
    Returns an empty list when no model exists for the pair or no
    memorable hands have been attached yet.

    Sorted by timestamp descending so the most recent narrative
    appears first; truncated to `max_hands` entries.
    """
    opponent_map = manager.models.get(observer_name)
    if not opponent_map:
        return []
    model = opponent_map.get(opponent_name)
    if model is None or not model.memorable_hands:
        return []
    sorted_hands = sorted(
        model.memorable_hands,
        key=lambda h: h.timestamp,
        reverse=True,
    )[:max_hands]
    return [h.narrative for h in sorted_hands if h.narrative]


def build_relationship_context(
    *,
    observer_name: str,
    opponents: Iterable[str],
    opponent_model_manager: OpponentModelManager,
    now: Optional[datetime] = None,
    max_memorable_per_opponent: int = DEFAULT_MAX_MEMORABLE_PER_OPPONENT,
) -> str:
    """Build the relationship-context block for the decision prompt.

    Returns a multi-line string (header + one line per qualifying
    opponent + bulleted memorable hands) or the empty string when no
    opponent at the table is in rival/friendly territory.

    `now` defaults to `datetime.utcnow()` so the heat projection
    pins to the moment of prompt assembly. Explicit `now` lets
    replay paths reproduce a historical prompt verbatim.

    Graceful no-ops:
      - opponent_model_manager has no relationship_repo: return ""
      - observer_name has no registered id and no axis rows exist:
        return ""
      - opponent name not in repo: skip that opponent (label is
        None → skipped from the output)
    """
    if not opponent_model_manager.has_relationship_repo:
        return ""

    if now is None:
        now = datetime.utcnow()

    observer_id = opponent_model_manager.resolve_player_id(observer_name)
    if not observer_id:
        return ""

    # Access the repo directly: this module lives inside poker.memory,
    # same package as the OpponentModelManager, so private-field
    # access matches the existing `get_relationship_modifier` pattern.
    repo = opponent_model_manager._relationship_repo

    lines: List[str] = []
    for opponent_name in opponents:
        if opponent_name == observer_name:
            continue
        opponent_id = opponent_model_manager.resolve_player_id(opponent_name)
        if not opponent_id or opponent_id == observer_id:
            continue
        state = repo.load_relationship_state(observer_id, opponent_id, now=now)
        if state is None:
            continue
        label = _classify(state)
        if label is None:
            continue

        header = f"- {opponent_name}: {label}"
        memorable = _format_memorable_lines(
            opponent_model_manager,
            observer_name,
            opponent_name,
            max_memorable_per_opponent,
        )
        lines.append(header)
        # Indent memorable narratives so the relationship between the
        # label line and its supporting history is visible to the
        # LLM at a glance.
        lines.extend(f"    {n}" for n in memorable)

    if not lines:
        return ""

    return "RECENT HISTORY WITH OPPONENTS AT THIS TABLE:\n" + "\n".join(lines)
