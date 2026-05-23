"""Player-as-staker helpers (Phase 5 refinement, 2026-05-21).

Two surfaces share these helpers:

  - `GET /api/cash/stakable-ai` — returns a curated per-tier list of
    AIs the player can offer a stake to right now. Filters apply
    every Phase 5 gate up front so the frontend gets a clean menu.

  - `POST /api/cash/stakes/offer` — re-runs the gates server-side
    (defense in depth) and then evaluates the AI's willingness
    against the SPECIFIC offer terms (relationship score, cut
    penalty, desperation modifier).

Gates (in order, cheap-to-expensive):

  1. Cash-eligible per `personality_repo.list_eligible_for_cash_mode`.
  2. AI's `borrower_profile.willing == True`.
  3. AI's `stake_comfort_zone` exists in STAKES_ORDER and has a +1
     tier (top-tier AIs aren't stakable — they're already at the top).
  4. Target tier = AI's comfort_zone +1 only. Help-them-work-up-the-
     ranks model, not jump-them-to-the-big-leagues.
  5. Player bankroll >= 1.5 × min_buy_in @ target tier.
  6. AI not currently seated anywhere (would double-seat them).
  7. AI has no active stake as borrower (one-active-stake invariant).
  8. Met-before: a `relationship_states` row exists for (AI → player)
     — staking requires shared history.
  9. Relationship status floor: AI's heat toward player < 0.5,
     likability >= 0.2. Excludes AIs the player has wronged.
  10. AI's tier (resolve_tier) is not house_only.
  11. No defaulted stake from AI to this player within 7-day cooldown.

Willingness evaluation (per-offer, in addition to all gates above):

  effective_threshold = base_willingness
                      + cut_penalty
                      − desperation × DESPERATION_RELIEF

  base_willingness   = profile.willingness_threshold  (per-personality)
  cut_penalty        = max(0, cut − FAIR_CUT_REFERENCE) × CUT_PENALTY_SLOPE
  desperation        = ego × wealth_deficit
    ego              = personality.anchors.ego (0..1)
    wealth_deficit   = max(0, 1 − current_bankroll / starting_bankroll)

The AI accepts iff `score > effective_threshold`. Predatory cuts and
high-ego AIs in good financial shape are hard to convince; high-ego
AIs running below their starting wealth (proud + broke) tolerate
significantly worse terms.

Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 5
+ user feedback iteration 2026-05-21.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from cash_mode.stakes import (
    BORROWER_KIND_PERSONALITY,
    STAKE_STATUS_DEFAULTED,
)
from cash_mode.stakes_ladder import STAKES_ORDER, table_buy_in_window
from cash_mode.staking_tier import TIER_HOUSE_ONLY, resolve_tier

logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

# Same multiplier the route validates against — surfaced here so the
# list endpoint can pre-filter tiers the player can't unlock anyway.
PLAYER_STAKER_BANKROLL_FLOOR_MULT = 1.5

# Same 7-day cooldown as the route — locked decision #1.
PLAYER_STAKE_DEFAULT_COOLDOWN_SECONDS = 7 * 24 * 60 * 60

# Relationship-axes floors for showing the AI as stakable at all.
# Heat above this means recent friction; the AI won't even consider
# the offer. Likability below this means active dislike. Both are
# generous — the willingness math layers a sharper score check on top
# at offer time.
RELATIONSHIP_HEAT_CEILING = 0.5
RELATIONSHIP_LIKABILITY_FLOOR = 0.2

# Willingness math constants (per-offer evaluation).
FAIR_CUT_REFERENCE = 0.30
"""Above this cut, every percentage point adds to the threshold. The
modal's default cut also sits at 0.30 so suggested terms incur zero
penalty — the player has to actively push past this to feel
predatory."""

CUT_PENALTY_SLOPE = 2.0
"""How sharply cut overage raises the threshold. A 50% cut = (0.50-0.30)
× 2.0 = 0.40 penalty — combined with the 0.30 base threshold the AI
would need score > 0.70 (essentially maxed goodwill) to accept."""

DESPERATION_RELIEF = 0.4
"""How much desperation can drop the threshold. A fully-desperate
high-ego AI gets a 0.4 reduction — a Loan-Shark Napoleon down to zero
chips would take almost any terms; a comfortable Buddha (low ego, OR
near starting bankroll) gets no relief and only takes fair offers."""

# How many candidates to surface per tier in the list endpoint.
DEFAULT_CANDIDATES_PER_TIER = 3


# --- Result dataclasses ------------------------------------------------------


@dataclass(frozen=True)
class StakeableAICandidate:
    """One AI eligible to receive a player-offered stake right now.

    Every field is JSON-friendly so the route's serializer is a thin
    pass-through. `target_stake_label` is the only tier the player can
    stake them at (comfort_zone + 1) — surfaced explicitly so the
    modal can lock the tier picker.
    """

    personality_id: str
    name: str
    comfort_zone: str       # AI's natural tier, e.g., "$10"
    target_stake_label: str # the +1 tier the player can stake them into
    min_buy_in: int
    max_buy_in: int
    suggested_principal: int  # default principal for the modal
    relationship_hint: str    # short blurb from the AI's POV of player
    likability: float
    respect: float
    heat: float
    # Optional preview signal — how desperate the AI is right now.
    # Drives the willingness math. Surfaced so the UI can hint at
    # "this AI might take worse terms — they're running low" without
    # exposing the exact formula.
    desperation: float
    # The AI's ego anchor. Stored on the candidate for the
    # willingness math (and so the UI could surface "proud" / "humble"
    # cues without re-loading personality config).
    ego: float


@dataclass(frozen=True)
class OfferEvaluation:
    """Result of an AI evaluating a specific stake offer.

    `accepted=True` → the AI takes the deal; route persists the stake
    row. `accepted=False` → route returns a 200 with reason + breakdown
    so the modal can explain *why* (predatory cut, not enough goodwill,
    etc.).
    """

    accepted: bool
    score: float                # relationship-axes score
    base_threshold: float       # profile.willingness_threshold
    cut_penalty: float          # max(0, cut - FAIR_CUT_REFERENCE) × slope
    desperation: float          # ego × wealth_deficit
    desperation_relief: float   # desperation × DESPERATION_RELIEF
    effective_threshold: float  # base + cut_penalty - desperation_relief
    reason: str                 # 'accepted' | 'cut_too_steep' | 'low_goodwill'


# --- Helpers -----------------------------------------------------------------


def _next_tier(stake_label: str) -> Optional[str]:
    """Return the tier directly above `stake_label`, or None if top-tier."""
    try:
        idx = STAKES_ORDER.index(stake_label)
    except ValueError:
        return None
    if idx + 1 >= len(STAKES_ORDER):
        return None
    return STAKES_ORDER[idx + 1]


def _ego_from_personality(personality_dict: Optional[Dict[str, Any]]) -> float:
    """Extract the `ego` anchor from a personality config blob.

    `personality_repo.load_personality_by_id` returns the parsed
    config dict directly (anchors at the top level alongside `name`,
    `id`, `bankroll_knobs`, etc.) — that's the primary shape. Fall
    back to the nested `config_json` / `config` shapes that other
    loaders use for cross-compat.

    Returns 0.5 (neutral) on any miss — missing anchors, malformed
    config, no personality row. The willingness math is tolerant of
    this default; the worst case is "AI looks slightly less desperate
    than they actually are" which means harder accept, never wrong-
    accept. Fail-safe direction.
    """
    if not personality_dict:
        return 0.5

    # Primary shape: anchors directly on the personality dict
    # (matches load_personality_by_id's return value).
    anchors = personality_dict.get("anchors")

    # Fall-back shape A: nested under `config`.
    if not isinstance(anchors, dict):
        config = personality_dict.get("config")
        if isinstance(config, dict):
            anchors = config.get("anchors")

    # Fall-back shape B: raw JSON in `config_json`.
    if not isinstance(anchors, dict):
        raw = personality_dict.get("config_json")
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                anchors = parsed.get("anchors")

    if not isinstance(anchors, dict):
        return 0.5
    try:
        return float(anchors.get("ego", 0.5))
    except (TypeError, ValueError):
        return 0.5


def _compute_desperation(
    *,
    ego: float,
    current_chips: int,
    starting_bankroll: int,
) -> float:
    """desperation = ego × wealth_deficit, clamped to [0, 1].

    Designed so:
      - A high-ego AI at zero chips ≈ maximum desperation (≈1.0).
      - A high-ego AI at starting wealth ≈ zero desperation.
      - A low-ego AI never gets desperate regardless of wealth state.

    The multiplicative form means BOTH conditions must fire for
    desperation to land — proud broke AIs are desperate, content
    broke AIs (low ego) are not (they accept their station).
    """
    if starting_bankroll <= 0:
        return 0.0
    wealth_deficit = max(0.0, 1.0 - current_chips / starting_bankroll)
    return max(0.0, min(1.0, ego * wealth_deficit))


def _relationship_score(*, likability: float, respect: float, heat: float) -> float:
    """Same shape as the forgiveness route's `_forgiveness_score`. Kept
    duplicated here rather than imported because cash_routes imports
    from cash_mode, not the other way around."""
    return likability * 0.5 + respect * 0.4 - heat * 0.3


def _met_before(
    *,
    relationship_repo,
    observer_id: str,
    opponent_id: str,
) -> bool:
    """True iff a relationship row exists for (observer→opponent).

    The row only gets created on first interaction (a hand together,
    a stake offer, etc.), so its presence is a robust met-before
    signal. We don't need to read the axes here — just the existence.
    `load_relationship_state` returns None when no row.
    """
    try:
        rel = relationship_repo.load_relationship_state(
            observer_id=observer_id, opponent_id=opponent_id,
        )
    except Exception:
        return False
    return rel is not None


def _relationship_axes(
    *,
    relationship_repo,
    observer_id: str,
    opponent_id: str,
    now: datetime,
) -> tuple[float, float, float]:
    """Return (likability, respect, heat) from observer's POV of
    opponent. Falls back to neutral defaults if no row exists.

    Heat is projected through decay on read (the repo handles this);
    the caller doesn't need to apply its own decay."""
    try:
        rel = relationship_repo.load_relationship_state(
            observer_id=observer_id, opponent_id=opponent_id, now=now,
        )
    except Exception:
        rel = None
    if rel is None:
        return 0.5, 0.5, 0.0
    return rel.likability, rel.respect, rel.heat


def _relationship_hint(
    *, likability: float, heat: float, respect: float,
) -> str:
    """Match the lobby/sponsor-offer hint phrasing so the staking UI
    speaks the same language as the lender flow."""
    if heat > 0.4:
        return "still upset with you"
    if heat > 0.2:
        return "wary of you"
    if respect > 0.6 and likability > 0.5:
        return "trusts you"
    if respect > 0.5:
        return "respects your game"
    if likability > 0.5:
        return "friendly"
    return ""


def _has_recent_default_to(
    *,
    stake_repo,
    staker_id: str,
    borrower_id: str,
    since: datetime,
) -> bool:
    """True iff the borrower defaulted on a stake from this staker
    within the cooldown window. The route already uses this signal
    pre-acceptance; the list endpoint pre-filters so the player doesn't
    see candidates they couldn't offer to anyway.
    """
    with stake_repo._get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM stakes
            WHERE staker_id = ?
              AND borrower_id = ?
              AND status = ?
              AND settled_at IS NOT NULL
              AND settled_at >= ?
            LIMIT 1
            """,
            (staker_id, borrower_id, STAKE_STATUS_DEFAULTED, since.isoformat()),
        ).fetchone()
    return row is not None


def _seated_personality_ids(cash_table_repo, sandbox_id: str) -> set:
    """Personality ids currently occupying an AI seat at any table.

    Used to filter idle candidates — a seated AI is in (or about to
    enter) a session, so they can't take a fresh player stake.
    """
    seated: set = set()
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        for slot in table.seats:
            if slot.get("kind") == "ai":
                pid = slot.get("personality_id")
                if pid:
                    seated.add(pid)
    return seated


# --- Public API --------------------------------------------------------------


def list_stakeable_ai(
    *,
    owner_id: str,
    player_bankroll: int,
    sandbox_id: str,
    personality_repo,
    bankroll_repo,
    relationship_repo,
    stake_repo,
    cash_table_repo,
    now: Optional[datetime] = None,
    candidates_per_tier: int = DEFAULT_CANDIDATES_PER_TIER,
    rng: Optional[random.Random] = None,
) -> List[StakeableAICandidate]:
    """Return AIs the player can offer a stake to right now, per tier.

    See module docstring for the full gate list. The result is sorted
    by target_stake_label (ascending tier) then by personality name
    for stable rendering. Per-tier candidates are sampled randomly
    (rather than always returning the same top N) so the player sees
    a refreshing menu — mirrors the sponsor-offers flow's per-call
    sampling.

    No principal/cut evaluation happens here — those are per-offer
    inputs. This endpoint answers "who CAN I offer to?", not "would
    they accept what?".
    """
    if now is None:
        now = datetime.utcnow()
    if rng is None:
        rng = random.Random()

    seated = _seated_personality_ids(cash_table_repo, sandbox_id)
    cooldown_threshold = now - timedelta(
        seconds=PLAYER_STAKE_DEFAULT_COOLDOWN_SECONDS,
    )

    # Group candidates by target_stake_label so we can sample per-tier
    # at the end. Single pass through eligible personalities.
    by_target_tier: Dict[str, List[StakeableAICandidate]] = {}

    eligible = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)
    for entry in eligible:
        pid = entry.get("personality_id")
        if not pid:
            continue
        # Gate 2: willing.
        profile = bankroll_repo.load_borrower_profile(pid)
        if not profile.willing:
            continue

        # Gate 3+4: comfort_zone +1 exists.
        knobs = bankroll_repo.load_personality_knobs(pid)
        comfort = knobs.stake_comfort_zone
        target_stake = _next_tier(comfort)
        if target_stake is None:
            continue

        # Gate 5: player bankroll floor for the target tier.
        _, min_buy_in, max_buy_in = table_buy_in_window(target_stake)
        if player_bankroll < PLAYER_STAKER_BANKROLL_FLOOR_MULT * min_buy_in:
            continue

        # Gate 6: not seated.
        if pid in seated:
            continue

        # Gate 7: no active stake as borrower.
        active = stake_repo.load_active_for_borrower(
            pid, BORROWER_KIND_PERSONALITY,
        )
        if active is not None:
            continue

        # Gate 8: met-before.
        if not _met_before(
            relationship_repo=relationship_repo,
            observer_id=pid,
            opponent_id=owner_id,
        ):
            continue

        # Gate 9: relationship status floor.
        likability, respect, heat = _relationship_axes(
            relationship_repo=relationship_repo,
            observer_id=pid,
            opponent_id=owner_id,
            now=now,
        )
        if heat >= RELATIONSHIP_HEAT_CEILING:
            continue
        if likability < RELATIONSHIP_LIKABILITY_FLOOR:
            continue

        # Gate 10: not at house_only tier.
        tier = resolve_tier(
            borrower_id=pid,
            borrower_kind=BORROWER_KIND_PERSONALITY,
            current_stake_label=target_stake,
            stake_repo=stake_repo,
        )
        if tier == TIER_HOUSE_ONLY:
            continue

        # Gate 11: no recent default to this player.
        if _has_recent_default_to(
            stake_repo=stake_repo,
            staker_id=owner_id,
            borrower_id=pid,
            since=cooldown_threshold,
        ):
            continue

        # Compute desperation for the preview (so the UI can hint at
        # who's likely to accept worse terms).
        current_chips = bankroll_repo.load_ai_bankroll_current(
            pid, sandbox_id=sandbox_id, now=now,
        )
        if current_chips is None:
            current_chips = 0
        ego = _ego_from_personality(
            personality_repo.load_personality_by_id(pid),
        )
        desperation = _compute_desperation(
            ego=ego,
            current_chips=int(current_chips),
            starting_bankroll=knobs.starting_bankroll,
        )

        hint = _relationship_hint(
            likability=likability, heat=heat, respect=respect,
        )

        # Suggested principal = min_buy_in @ target tier (lowest
        # commitment that still funds the seat). Modal lets the player
        # slide higher up to max_buy_in or to their bankroll.
        candidate = StakeableAICandidate(
            personality_id=pid,
            name=entry.get("name") or pid,
            comfort_zone=comfort,
            target_stake_label=target_stake,
            min_buy_in=min_buy_in,
            max_buy_in=max_buy_in,
            suggested_principal=min_buy_in,
            relationship_hint=hint,
            likability=likability,
            respect=respect,
            heat=heat,
            desperation=desperation,
            ego=ego,
        )
        by_target_tier.setdefault(target_stake, []).append(candidate)

    # Sample N per tier (random across the tier's eligible pool) for
    # menu freshness. Sorted within each tier by personality name for
    # stable rendering when the sample is exhausted (small pools).
    result: List[StakeableAICandidate] = []
    for tier_label in STAKES_ORDER:
        bucket = by_target_tier.get(tier_label, [])
        if not bucket:
            continue
        if len(bucket) <= candidates_per_tier:
            picked = sorted(bucket, key=lambda c: c.name)
        else:
            picked = rng.sample(bucket, candidates_per_tier)
            picked.sort(key=lambda c: c.name)
        result.extend(picked)
    return result


def evaluate_player_offer(
    *,
    target_pid: str,
    owner_id: str,
    principal: int,
    cut: float,
    sandbox_id: str,
    personality_repo,
    bankroll_repo,
    relationship_repo,
    now: Optional[datetime] = None,
) -> OfferEvaluation:
    """Run the AI's accept-or-refuse evaluation against a SPECIFIC offer.

    Caller is responsible for the structural gates (cooldown, seated,
    tier, etc.); this function just answers "given the offer's terms +
    AI's state, do they accept?".

    See the module docstring for the math.
    """
    if now is None:
        now = datetime.utcnow()

    profile = bankroll_repo.load_borrower_profile(target_pid)
    knobs = bankroll_repo.load_personality_knobs(target_pid)
    personality = personality_repo.load_personality_by_id(target_pid)
    ego = _ego_from_personality(personality)

    current_chips = bankroll_repo.load_ai_bankroll_current(
        target_pid, sandbox_id=sandbox_id, now=now,
    )
    if current_chips is None:
        current_chips = 0

    likability, respect, heat = _relationship_axes(
        relationship_repo=relationship_repo,
        observer_id=target_pid,
        opponent_id=owner_id,
        now=now,
    )
    score = _relationship_score(
        likability=likability, respect=respect, heat=heat,
    )

    base_threshold = float(profile.willingness_threshold)
    cut_penalty = max(0.0, cut - FAIR_CUT_REFERENCE) * CUT_PENALTY_SLOPE
    desperation = _compute_desperation(
        ego=ego,
        current_chips=int(current_chips),
        starting_bankroll=knobs.starting_bankroll,
    )
    desperation_relief = desperation * DESPERATION_RELIEF
    effective_threshold = base_threshold + cut_penalty - desperation_relief
    accepted = score > effective_threshold

    if accepted:
        reason = 'accepted'
    elif cut_penalty > 0.05:
        # Predatory-cut framing wins when the cut is meaningfully
        # above fair, regardless of desperation status. UX clarity.
        reason = 'cut_too_steep'
    else:
        reason = 'low_goodwill'

    return OfferEvaluation(
        accepted=accepted,
        score=round(score, 3),
        base_threshold=round(base_threshold, 3),
        cut_penalty=round(cut_penalty, 3),
        desperation=round(desperation, 3),
        desperation_relief=round(desperation_relief, 3),
        effective_threshold=round(effective_threshold, 3),
        reason=reason,
    )
