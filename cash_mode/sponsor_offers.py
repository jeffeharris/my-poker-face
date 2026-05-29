"""Sponsor archetype pool + offer generation.

When a player is sponsor-eligible at a given stake (bankroll < that
table's min buy-in but ≥ the tier-below's min buy-in), they pick from
3 randomly sampled sponsor offers. Each offer encodes a loan with
three knobs:

  - `amount`: principal in chips, delivered directly to the table
    stack (never to bankroll — closes the "pocket the spare loan"
    exploit by construction).
  - `floor`: repayment multiplier on the principal. 1.00 = repay
    principal then split; 1.30 = repay 130% before any split kicks
    in. The "predatory" knob — high floor means small wins go
    entirely to the sponsor.
  - `rate`: sponsor's cut of post-floor remaining. 0.0 = player
    keeps everything past the floor; 0.50 = sponsor takes half.

Archetypes are parameterized by the table's `min_buy_in` and
`max_buy_in` so the pool scales across the stakes ladder without
hardcoded dollar amounts. Every archetype produces a valid amount
in `[min_buy_in, max_buy_in]` at every stake by construction.

Spec: `docs/plans/CASH_MODE_SPONSORSHIP_HANDOFF.md`.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from cash_mode.staker_profile import StakerProfile
from cash_mode.stakes import BORROWER_KIND_HUMAN
from cash_mode.staking_tier import (
    TIER_HOUSE_ONLY,
    TIER_PREMIUM,
    TIER_RESTRICTED,
    TIER_STANDARD,
    resolve_tier,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SponsorOffer:
    """One concrete sponsor offer for a specific table.

    Concrete amounts (not multipliers) — already computed against the
    table's min/max buy-in. The frontend renders these directly; the
    `/api/cash/sponsor-and-sit` route validates that the loan params
    match an offer produced by the same archetype/table combo to
    prevent client-side tampering with terms.
    """

    archetype_id: str
    name: str
    amount: int
    floor: float
    rate: float
    flavor: str


@dataclass(frozen=True)
class _Archetype:
    """Template for producing a SponsorOffer at a given table.

    `amount_fn(min_buy_in, max_buy_in) -> int` lets each archetype
    pick its loan size relative to the table's buy-in window. The
    clamp to `[min_buy_in, max_buy_in]` is applied by the caller so
    pathological templates can't slip a value outside the valid
    range — defensive, not normally exercised.
    """

    id: str
    name: str
    amount_fn: Callable[[int, int], int]
    floor: float
    rate: float
    flavor: str


SPONSOR_ARCHETYPES: List[_Archetype] = [
    _Archetype(
        id="friendly_boost",
        name="Friendly Boost",
        amount_fn=lambda mn, mx: mn,
        floor=1.00,
        rate=0.20,
        flavor="Just enough to get back in. Small stake, small dues.",
    ),
    _Archetype(
        id="square_deal",
        name="Square Deal",
        amount_fn=lambda mn, mx: int(mn * 1.5),
        floor=1.10,
        rate=0.25,
        flavor="A little extra. Pay back with a tip, then we split.",
    ),
    _Archetype(
        id="the_premium",
        name="The Premium",
        amount_fn=lambda mn, mx: int(mx * 0.5),
        floor=1.30,
        rate=0.00,
        flavor="Pay 30% upfront. Keep every chip you win past that.",
    ),
    _Archetype(
        id="skin_in_the_game",
        name="Skin in the Game",
        amount_fn=lambda mn, mx: int(mx * 0.7),
        floor=1.15,
        rate=0.15,
        flavor="Deep stack. Mostly your win, mostly your loss.",
    ),
    _Archetype(
        id="whale_backer",
        name="Whale Backer",
        amount_fn=lambda mn, mx: mx,
        floor=1.00,
        rate=0.50,
        flavor="Maximum stake. Half the upside goes home.",
    ),
    _Archetype(
        id="loan_shark",
        name="Loan Shark",
        amount_fn=lambda mn, mx: int(mx * 0.8),
        floor=1.30,
        rate=0.40,
        flavor="Cheap to take. Brutal floor. Win big or owe everything.",
    ),
]


def _materialize(arch: _Archetype, min_buy_in: int, max_buy_in: int) -> SponsorOffer:
    """Concretize an archetype against a specific table's buy-in range.

    The amount is clamped to `[min_buy_in, max_buy_in]` defensively
    — every archetype's `amount_fn` is already constructed to land
    in-range, but the clamp ensures a typo in a future archetype
    can't produce an unbuyable amount.
    """
    raw = arch.amount_fn(min_buy_in, max_buy_in)
    amount = max(min_buy_in, min(max_buy_in, raw))
    return SponsorOffer(
        archetype_id=arch.id,
        name=arch.name,
        amount=amount,
        floor=arch.floor,
        rate=arch.rate,
        flavor=arch.flavor,
    )


def compute_offers_for_table(
    min_buy_in: int,
    max_buy_in: int,
    *,
    count: int = 3,
    rng: Optional[random.Random] = None,
) -> List[SponsorOffer]:
    """Sample `count` distinct sponsor offers for a table.

    `rng` lets tests pin the sample for determinism. Default uses
    a fresh `random.Random()` so each bust gets a different mix —
    keeps the sponsor screen feeling alive across sessions.

    Raises `ValueError` if `count` exceeds the archetype pool size
    — currently 6 archetypes, so up to 6 distinct offers.
    """
    if count > len(SPONSOR_ARCHETYPES):
        raise ValueError(
            f"Requested {count} offers but only {len(SPONSOR_ARCHETYPES)} archetypes exist"
        )
    rng = rng or random.Random()
    archetypes = list(SPONSOR_ARCHETYPES)
    rng.shuffle(archetypes)
    chosen = archetypes[:count]
    return [_materialize(a, min_buy_in, max_buy_in) for a in chosen]


def find_archetype(archetype_id: str) -> Optional[_Archetype]:
    """Look up an archetype by id — used by sponsor-and-sit to
    validate the offer the client claims to have accepted.

    Returns None if no archetype matches; caller should treat this
    as a client-tampering or stale-offer condition and reject.
    """
    for arch in SPONSOR_ARCHETYPES:
        if arch.id == archetype_id:
            return arch
    return None


def offer_for_archetype(
    archetype_id: str,
    min_buy_in: int,
    max_buy_in: int,
) -> Optional[SponsorOffer]:
    """Re-materialize a specific archetype against a table.

    Used server-side to recompute the offer terms when the client
    sends `{archetype_id, stake_label}` — the server never trusts
    client-provided amount/floor/rate, only the archetype id, and
    derives terms freshly from the table's buy-in range.
    """
    arch = find_archetype(archetype_id)
    if arch is None:
        return None
    return _materialize(arch, min_buy_in, max_buy_in)


# --- Path B: AI-personality sponsorship --------------------------------


@dataclass(frozen=True)
class PersonalitySponsorOffer:
    """One concrete loan offer from a named AI personality.

    Distinct from `SponsorOffer` (anonymous archetypes) — this carries
    the lender's `personality_id` so the route can wire
    `active_loan_lender_id` on the bankroll, and so the modal can
    render avatar + name + relationship hint.

    Same `amount`/`floor`/`rate` shape as `SponsorOffer` — the rest of
    the system (leave-time math, lender-credit) is agnostic to whether
    the loan came from an archetype or a personality. `flavor` is the
    per-personality blurb shown on the offer card (may be generated
    on-the-fly or stored on the personality config in v2).
    """

    lender_id: str
    lender_name: str
    amount: int
    floor: float
    rate: float
    flavor: str
    relationship_hint: str
    capacity: int  # the lender's available bankroll at offer time,
    # used for sorting and for capacity-disclosure UX in v2.


@dataclass(frozen=True)
class LenderRejection:
    """Captured reason a personality lender didn't surface an offer.

    Phase 2 Commit 3 will surface these in the lobby response so the
    player can see *why* Napoleon won't back them. Kept here (next to
    the offer dataclasses) so the offer-generation site naturally
    produces them as side-output, and the route layer can join them
    onto the response without re-deriving the eligibility logic.

    `reason` is a short stable identifier (good for analytics / UI
    styling); `detail` is a free-text explanation suitable for display.
    """

    lender_id: str
    lender_name: str
    reason: str
    detail: str


# Phase 2 — 7-day cooldown gating re-stakes after a default. Locked
# decision #1 from the backing-system handoff. Mirrors the player-
# staker side cooldown in `player_staking.PLAYER_STAKE_DEFAULT_COOLDOWN_SECONDS`
# (kept as a separate constant rather than imported to avoid a
# cash_mode → cash_mode cross-dependency cycle).
LENDER_DEFAULT_COOLDOWN_SECONDS = 7 * 24 * 60 * 60


# Per-tier knobs for the cut bump applied on top of the relationship-
# axis adjustments. Tunable; midpoints of the ranges given in the
# Phase 2 spec ("standard cuts bumped 5-10%, restricted bumped 15-25%").
TIER_RATE_BUMP = {
    TIER_PREMIUM: 0.00,
    TIER_STANDARD: 0.075,
    TIER_RESTRICTED: 0.20,
    TIER_HOUSE_ONLY: 0.00,  # unused — house_only returns empty list
}

# Per-tier minimums on relationship axes for a personality lender to
# surface at all. Falling below either kicks the lender out of the pool.
# Premium is open to everyone the legacy gates allow; standard is mildly
# selective; restricted requires high trust on both axes.
TIER_RELATIONSHIP_FLOORS = {
    TIER_PREMIUM: {"likability": 0.0, "respect": 0.0},
    TIER_STANDARD: {"likability": 0.4, "respect": 0.5},
    TIER_RESTRICTED: {"likability": 0.6, "respect": 0.6},
    TIER_HOUSE_ONLY: {"likability": 1.1, "respect": 1.1},  # impossible
}

# Per-staker garnishment cap (locked decision: +20pp max). The bump
# itself is `outstanding_carry / new_principal`, clamped to this.
GARNISHMENT_RATE_CAP = 0.20

# Player-prestige hook 2 (backing economy). When the human borrower's
# room-level REGARD (the beloved↔reviled reputation axis, ∈[-1,1]; see
# cash_mode/prestige.py) is at or below this floor, the named-personality
# sponsor pool CLOSES — "nobody stakes a villain." The caller's house /
# anonymous-archetype fallback still extends offers, so this is the
# self-funded *hard mode*, not a dead end (a reviled player can always
# re-enter on the impersonal house book, just without warm personality
# backing). Set deeper than the prestige warm/hostile line (0.05) so only a
# genuinely reviled player loses the pool, not anyone slightly under neutral.
# Only ever applied to the HUMAN borrower: AI-borrower callers leave
# `human_regard=None`, which is a no-op.
VILLAIN_REGARD_FLOOR = -0.35


def _adjusted_terms(
    profile: StakerProfile,
    *,
    likability: float,
    heat: float,
    respect: float,
) -> tuple[float, float]:
    """Trim floor/rate by relationship axes (handoff §B.2 "Term adjustment").

    Returns `(floor, rate)` clamped to `[1.00, 1.50]` / `[0.00, 0.55]`.

    Adjustments are additive deltas off the profile's anchors:
      - High likability (>0.5): "friend tax" — floor and rate trim by
        0.05 each. Friends lend on softer terms.
      - High heat (>0.4): "I'll lend, but you'll pay" — floor and rate
        each bump up 0.10. Heat overrides likability when both fire.
      - High respect (>0.5): "I think you'll win, fair terms" — floor
        and rate trim by 0.03 each.

    Clamps prevent edge-case profiles from generating predatory or
    impossibly-generous offers via stacked modifiers.
    """
    floor = profile.floor_anchor
    rate = profile.rate_anchor
    if likability > 0.5:
        floor -= 0.05
        rate -= 0.05
    if heat > 0.4:
        floor += 0.10
        rate += 0.10
    if respect > 0.5:
        floor -= 0.03
        rate -= 0.03
    floor = max(1.00, min(1.50, floor))
    rate = max(0.00, min(0.55, rate))
    return floor, rate


def _relationship_hint(
    *,
    likability: float,
    heat: float,
    respect: float,
) -> str:
    """Generate a short relationship hint string for the offer card.

    Surfaces the underlying axes without exposing raw numbers. Most
    severe condition wins — heat dominates if present, then high
    respect, then likability, finally a neutral default. Empty
    string means "no special relationship vibe" — modal can omit the
    hint chip entirely.
    """
    if heat > 0.4:
        return "wants their money back"
    if heat > 0.2:
        return "watching you"
    if respect > 0.6 and likability > 0.5:
        return "trusts you"
    if respect > 0.5:
        return "respects your game"
    if likability > 0.5:
        return "friendly"
    return ""


def _capacity_for_lender(
    profile: StakerProfile,
    projected_bankroll: int,
    *,
    min_buy_in: int,
    max_buy_in: int,
) -> int:
    """Loan amount this lender will extend at this table.

    `pct × projected_bankroll`, clamped to the table's
    `[min_buy_in, max_buy_in]` window. The caller still checks
    capacity >= min_buy_in (eligibility gate 2 in the handoff) — a
    lender too poor to lend at the table's minimum is filtered out.
    """
    raw = int(profile.max_loan_pct_of_bankroll * projected_bankroll)
    return max(min_buy_in, min(max_buy_in, raw)) if raw >= min_buy_in else raw


def compute_personality_offers(
    *,
    player_owner_id: str,
    min_buy_in: int,
    max_buy_in: int,
    candidate_personalities: List[dict],
    bankroll_repo,
    relationship_repo,
    sandbox_id: str,
    now: Optional[datetime] = None,
    count: int = 3,
    # Phase 2 additions — when provided, the function applies tier
    # filtering + per-staker garnishment. Backward compatible: callers
    # that omit both knobs get pre-Phase-2 behavior (no tier logic, no
    # garnishment). Production callers always pass them.
    stake_repo=None,
    stake_label: Optional[str] = None,
    borrower_kind: str = BORROWER_KIND_HUMAN,
    rejections_out: Optional[List[LenderRejection]] = None,
    human_regard: Optional[float] = None,
) -> List[PersonalitySponsorOffer]:
    """Generate up to `count` AI-personality sponsor offers.

    Each candidate is a dict with at least `{"personality_id": str,
    "name": str}` — same shape `_build_cash_game` already builds for
    seated AIs (matches `cash_personality_ids` mapping).

    Eligibility gates per candidate (all must pass):
      1. `profile.willing == True`
      2. Loan capacity (`pct × projected_bankroll`, table-clamped) is
         at least the table's `min_buy_in`. A lender too poor to fund
         a min buy-in is filtered.
      3. `relationship.respect >= profile.respect_floor`
      4. `relationship.projected_heat <= profile.heat_ceiling`
      5. **Tier floors (Phase 2):** when `stake_repo`+`stake_label`
         are provided, the borrower's tier is resolved and lenders
         falling below the tier's likability/respect floors drop out
         (standard ≥ 0.4 likability + 0.5 respect; restricted ≥ 0.6 on
         both; house_only returns []).

    For each qualifying candidate, terms are trimmed by relationship
    axes (see `_adjusted_terms`) and then bumped by:
      - Tier rate-bump (standard ≈ +7.5pp, restricted ≈ +20pp).
      - Per-staker garnishment (Phase 2): if the borrower has an
        existing carry with this specific lender, the lender's rate
        rises by `outstanding_carry / new_principal`, capped at
        `GARNISHMENT_RATE_CAP` (+20pp).

    No-relationship-row case: `relationship_repo.load_relationship_state`
    returns None → treated as default neutral state (respect=0.5,
    heat=0.0, likability=0.5). This means a stranger lender extends
    their anchor terms unmodified.

    "No outstanding loan from THIS lender" gate (eligibility 5 in the
    Path B handoff): unchanged — still the caller's responsibility.
    The Phase 2 per-staker garnishment doesn't *block* a same-lender
    re-stake; it makes the terms worse so the borrower feels the
    weight of the prior unpaid carry on the new offer.

    Returns offers sorted by capacity descending — bigger-stake
    lenders surface first. Empty list when `tier == 'house_only'`.

    Side-output: `rejections_out` is an optional list the function
    appends `LenderRejection` rows to as candidates fail eligibility
    gates 3/4/5. Phase 2 Commit 3 reads this so the sponsor modal
    can surface "Napoleon refuses — you defaulted last week"-style
    UI. Pass `None` to skip the side-output (zero overhead).

    `now` defaults to `datetime.utcnow()`; explicit `now` lets tests
    pin the projection point for stable results.

    `human_regard` (player-prestige hook 2): the human borrower's
    room-level regard ∈ [-1, 1]. When supplied and at or below
    `VILLAIN_REGARD_FLOOR`, the named-personality pool closes entirely
    (returns []) — "nobody stakes a villain." The caller's house fallback
    still extends offers, so this is the self-funded hard mode, not a dead
    end. Default None = no gate (AI-borrower callers, legacy callers, tests).
    """
    if now is None:
        now = datetime.utcnow()

    # Player-prestige hook 2: a reviled human can't get named-AI backing.
    # Short-circuit before any per-candidate work — the whole pool is closed.
    if human_regard is not None and human_regard <= VILLAIN_REGARD_FLOOR:
        return []

    # Phase 2: resolve the borrower's tier if the caller supplied the
    # bits we need. Tier knobs are no-op (premium-equivalent) when
    # the caller didn't opt in.
    tier = TIER_PREMIUM
    if stake_repo is not None and stake_label is not None:
        tier = resolve_tier(
            borrower_id=player_owner_id,
            borrower_kind=borrower_kind,
            current_stake_label=stake_label,
            stake_repo=stake_repo,
        )
        if tier == TIER_HOUSE_ONLY:
            # House-only — no personality offers surface; route falls
            # back to anonymous archetypes entirely.
            return []

    # Per-staker carry lookup, indexed by staker_id for cheap inner-loop
    # access. Same `stake_repo` opt-in as tier resolution.
    carries_by_staker: dict = {}
    if stake_repo is not None:
        carries = stake_repo.list_carries_for_borrower(
            player_owner_id,
            borrower_kind,
        )
        for c in carries:
            if c.staker_id is None:
                continue  # house stakes never carry; defensive skip
            carries_by_staker.setdefault(c.staker_id, 0)
            carries_by_staker[c.staker_id] += int(c.carry_amount)

    # Phase 2 — 7-day default cooldown. One bulk SQL to build the set
    # of lenders the player defaulted on within the window; per-candidate
    # check is then O(1) in the loop below. Mirrors the carries-by-staker
    # lookup pattern. Skipped when `stake_repo` is None (legacy callers).
    defaulted_staker_ids: set = set()
    if stake_repo is not None:
        cooldown_threshold = now - timedelta(
            seconds=LENDER_DEFAULT_COOLDOWN_SECONDS,
        )
        with stake_repo._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT staker_id FROM stakes
                WHERE borrower_id = ?
                  AND borrower_kind = ?
                  AND status = 'defaulted'
                  AND settled_at IS NOT NULL
                  AND settled_at >= ?
                  AND staker_id IS NOT NULL
                """,
                (
                    player_owner_id,
                    borrower_kind,
                    cooldown_threshold.isoformat(),
                ),
            ).fetchall()
        defaulted_staker_ids = {row[0] for row in rows}

    tier_floors = TIER_RELATIONSHIP_FLOORS[tier]
    tier_rate_bump = TIER_RATE_BUMP[tier]

    qualifying: List[PersonalitySponsorOffer] = []

    for entry in candidate_personalities:
        pid = entry.get("personality_id")
        name = entry.get("name") or pid
        if not pid:
            continue

        profile = bankroll_repo.load_staker_profile(pid)
        if not profile.willing:
            continue

        # Projected bankroll via projection-on-read.
        projected = bankroll_repo.load_ai_bankroll_current(
            pid,
            sandbox_id=sandbox_id,
            now=now,
        )
        if projected is None:
            # No bankroll row yet — can't lend out of nothing. Skip.
            continue

        capacity = _capacity_for_lender(
            profile,
            projected,
            min_buy_in=min_buy_in,
            max_buy_in=max_buy_in,
        )
        if capacity < min_buy_in:
            continue

        # Phase 2 — 7-day default cooldown. If the player defaulted
        # on a stake from THIS lender within the window, the lender
        # refuses outright. Surfaces a specific "you defaulted on
        # them recently" reason in the rejections side-output so the
        # sponsor modal can render it without re-deriving the
        # eligibility logic.
        if pid in defaulted_staker_ids:
            if rejections_out is not None:
                rejections_out.append(
                    LenderRejection(
                        lender_id=pid,
                        lender_name=name,
                        reason="recent_default",
                        detail=(f"{name} won't back you yet — you defaulted " "on them recently."),
                    )
                )
            continue

        # Relationship state — lender's POV of the player. None → default neutral.
        rel = relationship_repo.load_relationship_state(
            observer_id=pid,
            opponent_id=player_owner_id,
            now=now,
        )
        if rel is None:
            respect, heat, likability = 0.5, 0.0, 0.5
        else:
            respect = rel.respect
            heat = rel.heat
            likability = rel.likability

        if respect < profile.respect_floor:
            if rejections_out is not None:
                rejections_out.append(
                    LenderRejection(
                        lender_id=pid,
                        lender_name=name,
                        reason="respect_too_low",
                        detail=f"{name} doesn't respect your game right now.",
                    )
                )
            continue
        if heat > profile.heat_ceiling:
            if rejections_out is not None:
                rejections_out.append(
                    LenderRejection(
                        lender_id=pid,
                        lender_name=name,
                        reason="heat_too_high",
                        detail=f"{name} is too heated to stake you.",
                    )
                )
            continue

        # Tier floors — only applied when the caller opted in. Lenders
        # that pass legacy gates 1-4 may still fail tier 5 here.
        if likability < tier_floors["likability"] or respect < tier_floors["respect"]:
            if rejections_out is not None:
                rejections_out.append(
                    LenderRejection(
                        lender_id=pid,
                        lender_name=name,
                        reason="tier_floor",
                        detail=(
                            f"{name} won't back you at the {tier} tier — "
                            "you haven't built up enough goodwill."
                        ),
                    )
                )
            continue

        floor, rate = _adjusted_terms(
            profile,
            likability=likability,
            heat=heat,
            respect=respect,
        )

        # Tier-based rate bump (standard / restricted).
        rate = rate + tier_rate_bump

        # Per-staker garnishment (Phase 2): if the borrower has a
        # carry owed to THIS lender, bump the cut so the new offer's
        # economics partially pay it down.
        carry_owed = carries_by_staker.get(pid, 0)
        if carry_owed > 0 and capacity > 0:
            garnish = min(carry_owed / capacity, GARNISHMENT_RATE_CAP)
            rate += garnish

        # Re-clamp to the same window `_adjusted_terms` enforces so the
        # tier + garnishment bumps can't push past 0.55.
        rate = max(0.00, min(0.55, rate))

        hint = _relationship_hint(
            likability=likability,
            heat=heat,
            respect=respect,
        )

        qualifying.append(
            PersonalitySponsorOffer(
                lender_id=pid,
                lender_name=name,
                amount=capacity,
                floor=floor,
                rate=rate,
                flavor=f"{name} offers you a loan.",
                relationship_hint=hint,
                capacity=capacity,
            )
        )

    qualifying.sort(key=lambda o: o.capacity, reverse=True)
    return qualifying[:count]
