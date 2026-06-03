"""Build a tournament field from a sandbox's REAL personas (P3 foundation).

The headless engine fields synthetic seats (`P01`..`PNN` mapped to archetype
strings — see `tournament.director.build_initial_state`). That's fine for pure
mechanics tests, but the circuit Main Event (P3) wants the cast the player knows
from the cash felt: the seats are the sandbox's actual AI personalities, so

  - eliminations, standings, and the winner attribute to real `personality_id`s,
  - the real-chip **payout credits real `ai:<pid>` bankrolls** (the redistribution
    that the P2 economy's synthetic-field branch could only sweep to the pool),
  - relationship / prestige context carries for free (same entities).

The `entries` map this produces is `{personality_id: archetype}` — the KEY is the
real persona (the economic identity), the VALUE is a valid
`experiments.simulate_bb100.ARCHETYPES` key the funny-money resolver builds a
no-LLM solver bot from. The persona's true LLM personality does not drive the
funny-money hands (v1 tournaments are no-LLM, like the headless field); only its
identity matters for the economy. See `docs/plans/TOURNAMENT_CIRCUIT_SURFACING.md`.
"""

from __future__ import annotations

import logging
import random
from typing import Optional

from tournament.config import DEFAULT_FIELD_ARCHETYPES

logger = logging.getLogger(__name__)


def assign_archetypes(
    personality_ids: list[str],
    archetypes: tuple[str, ...] = DEFAULT_FIELD_ARCHETYPES,
) -> dict[str, str]:
    """Pure: map each persona id to an archetype, cycled in order.

    Insertion order is preserved (it becomes seat order in
    `build_initial_state`). Raises if `archetypes` is empty.
    """
    if not archetypes:
        raise ValueError("archetypes must be non-empty")
    return {pid: archetypes[i % len(archetypes)] for i, pid in enumerate(personality_ids)}


def select_persona_field(
    *,
    personality_repo,
    owner_id: Optional[str],
    field_size: int,
    archetypes: tuple[str, ...] = DEFAULT_FIELD_ARCHETYPES,
    rng_seed: int = 0,
    human_id: Optional[str] = None,
    exclude: Optional[set] = None,
    scored_order: Optional[list[str]] = None,
) -> dict[str, str]:
    """Build a real-persona `entries` map for a tournament of (up to) `field_size`.

    Draws from `personality_repo.list_eligible_for_cash_mode(user_id=owner_id)`
    — the same circulating, non-fish, cash-eligible pool the lobby seat-filler
    uses — minus `exclude` (personas currently seated at a cash table / off-grid
    / already in a tournament, so we never draft a busy persona into the field —
    the double-presence guard), shuffles it deterministically by `rng_seed` (so
    successive Main Events field a varied cast), and assigns archetypes by
    cycling `archetypes`.

    When `scored_order` is given (the invite's draw-`reserved_pids`, highest pull
    first — tournaments-as-a-draw), the eligible pool is ordered by that ranking
    instead of shuffled: reserved personas that are STILL eligible take seats
    first in draw order, then any remaining seats fill from the rest of the pool
    (deterministically shuffled). A reserved persona that's in `exclude` (e.g.
    still cash-seated, not yet vacated) is simply skipped — fail-closed against
    double-presence — so until the Phase-C vacate runs the field just falls back
    to fill. Empty/None `scored_order` keeps the legacy random draft.

    When `human_id` is given the human takes one seat (prize-eligible, live-
    driven — its archetype is a placeholder the resolver never consults) and the
    remaining seats are personas. Returns an ordered `{id: archetype}` map with
    the human (if any) first.

    The field is CAPPED at the eligible pool size: a tournament can't field more
    distinct personas than the sandbox has. The caller validates the minimum
    (≥2) and reads `len(entries)` as the true field size. A too-small pool is
    logged, not raised — the caller decides whether to fall back to a synthetic
    field or refuse.
    """
    pool = (
        personality_repo.list_eligible_for_cash_mode(user_id=owner_id) if personality_repo else []
    )
    blocked = exclude or set()
    persona_ids = [
        row['personality_id']
        for row in pool
        if row.get('personality_id') and row['personality_id'] not in blocked
    ]
    # Deterministic shuffle off a local RNG (no global-state mutation).
    rng = random.Random(rng_seed)
    rng.shuffle(persona_ids)
    if scored_order:
        # Reorder by draw rank: reserved-and-eligible first (in scored_order),
        # then the shuffled remainder. `persona_ids` is already exclude-filtered,
        # so a reserved persona that's still seated/busy never sneaks in here.
        rank = {pid: i for i, pid in enumerate(scored_order)}
        eligible = set(persona_ids)
        ranked = sorted((p for p in scored_order if p in eligible), key=lambda p: rank[p])
        rest = [p for p in persona_ids if p not in rank]
        persona_ids = ranked + rest

    seats_for_personas = field_size - (1 if human_id else 0)
    if seats_for_personas < 0:
        seats_for_personas = 0
    chosen = persona_ids[:seats_for_personas]

    if len(chosen) < seats_for_personas:
        logger.warning(
            "[TOURNAMENT] persona pool too small for owner=%s: wanted %d, got %d "
            "(field will be smaller than requested)",
            owner_id,
            seats_for_personas,
            len(chosen),
        )

    ordered_ids: list[str] = []
    if human_id:
        ordered_ids.append(human_id)
    ordered_ids.extend(chosen)

    return assign_archetypes(ordered_ids, archetypes)
