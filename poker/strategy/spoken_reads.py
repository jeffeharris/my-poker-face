"""Spoken reads — surface the SHARP/TIERED bot's earned opponent read as a
player-facing, intuition-framed line ("I'm starting to get a read on you" →
"you've folded every 3-bet I've made tonight").

This is Phase 1 of backlog #12 (Perceptibility & conditioning). The bot already
adapts invisibly (Finding 3: the exploitation layer fires on a sample-gated
confidence ramp but is never *felt*). This module makes the read perceptible by
turning the same per-opponent sample accrual into an audible "figuring you out"
arc and handing it to the LLM to voice.

Design contract (locked):
  - HYBRID surfacing: code detects the read + its confidence ARC tier + picks
    the line's INTUITION framing; the LLM voices it. We never emit a raw stat or
    number into the text — only a feel ("starting to get a read", "I've clocked
    your pattern").
  - Frequency-NEUTRAL: this runs in the Layer-3 expression path AFTER the action
    is locked. It must never touch decision/frequency.
  - Anti-spam: a per-(observer -> opponent) cooldown that advances on hands where
    the read was ELIGIBLE to speak (not only hands it actually voiced), so silent
    streets don't reset or spam.

The read DATA comes from `poker.memory.opponent_reads.deep_reads_from_tendencies`
(shared with the dossier + coach). Each read is gated on its own sample counter,
and the arc tier is derived from that sample's confidence ramp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── Arc tiers ────────────────────────────────────────────────────────────────
# The "figuring you out" escalation. Derived from the read's own sample count
# (its confidence ramp), NOT from hands_observed globally — a read is only as
# mature as the number of times its specific spot has been seen.
ARC_TENTATIVE = 'tentative'  # ~5-8 samples: a hunch
ARC_CONFIDENT = 'confident'  # ~25 samples: a working read
ARC_SURE = 'sure'  # ~60 samples: a locked-in pattern


@dataclass(frozen=True)
class _ReadSpec:
    """One surfaceable read: how to find it in deep_reads, its sample counter on
    the tendencies object, the maturation floor, and its intuition phrasings per
    arc tier.

    `phrasings[tier]` are templates with no raw numbers/stat names — only a feel.
    The tier itself carries the escalation; the LLM riffs on the supplied line.
    """

    read_key: str  # key in deep_reads_from_tendencies output
    sample_attr: str  # the _count attr on OpponentTendencies gating this read
    min_samples: int  # below this, no read (matches the deep_reads sample gate)
    # Phrasings keyed by arc tier. Each is a complete intuition-framed line the
    # LLM is handed to voice (it may rephrase, but must keep it number-free).
    phrasings: Dict[str, str]
    # Optional SECOND sample counter for reads whose deep_reads gate is a dual
    # constraint (e.g. sizing_polarization needs BOTH equity bins matured). When
    # set, the read's effective sample count = min(sample_attr, second_sample_attr)
    # — the binding bin drives both the min_samples gate and the arc tier, so the
    # read is only as mature as its weaker bin.
    second_sample_attr: Optional[str] = None


# Priority order = legibility + maturation. fold_to_cbet is the most
# player-legible, fastest-maturing read. The Phase-4 sizing tells
# (sizing_polarization_score, fold_to_big_bet) slot in BELOW the three
# action-frequency reads but ABOVE all_in_frequency: they mature slower
# (showdown-gated equity bins / big-bets-faced) so they shouldn't pre-empt the
# faster reads, but a matured sizing tell ("big bet, big hand") is far more
# evocative than the coarse global all-in rate, so it outranks it.
#
# NOTE on ordering: the list order IS the priority. _select_best_read walks it
# top-down and returns the first matured read, so put the most legible first.
READ_PRIORITY: Tuple[_ReadSpec, ...] = (
    _ReadSpec(
        read_key='fold_to_cbet',
        sample_attr='_cbet_faced_count',
        min_samples=5,
        phrasings={
            ARC_TENTATIVE: "I'm starting to get a feel for how you handle a bet on the flop.",
            ARC_CONFIDENT: "You give up on the flop more than you think when I bet into you.",
            ARC_SURE: "You've folded to my flop bets all night — I've got your number there.",
        },
    ),
    _ReadSpec(
        read_key='cbet_attempt_rate',
        sample_attr='_postflop_seen_as_pfr_count',
        min_samples=5,
        phrasings={
            ARC_TENTATIVE: "I'm starting to notice when you do and don't follow through after raising.",
            ARC_CONFIDENT: "I'm getting a read on your continuation bets — there's a pattern there.",
            ARC_SURE: "I've clocked exactly when you bet that flop and when you chicken out.",
        },
    ),
    _ReadSpec(
        read_key='barrel_frequency',
        sample_attr='_barrel_opportunity_count',
        min_samples=5,
        phrasings={
            ARC_TENTATIVE: "I'm starting to sense whether you'll keep firing on later streets.",
            ARC_CONFIDENT: "I've got a feel for whether your turn bets are real or just momentum.",
            ARC_SURE: "I know when you'll keep firing and when you're out of bullets.",
        },
    ),
    # ── Phase 4 sizing tells ──────────────────────────────────────────────
    # sizing_polarization_score: high ⇒ the opponent bets big with strong hands
    # and small with weak ones — their size is face-up. Gated on BOTH equity
    # bins (showdown-derived) maturing, so its effective sample = the weaker
    # bin (second_sample_attr). The deep_reads value is already None until both
    # bins clear SIZING_MIN_BIN_SAMPLE; min_samples mirrors that gate.
    _ReadSpec(
        read_key='sizing_polarization_score',
        sample_attr='_equity_betting_big_count',
        second_sample_attr='_equity_betting_small_count',
        min_samples=4,  # == SIZING_MIN_BIN_SAMPLE (the deep_reads gate)
        phrasings={
            ARC_TENTATIVE: "I'm starting to think your bet size gives away how strong you are.",
            ARC_CONFIDENT: "The way you size your bets is telling me something — big means big.",
            ARC_SURE: "Your sizing tells me everything — when you bet big, you've got it.",
        },
    ),
    # fold_to_big_bet: high ⇒ the opponent over-folds to large bets / overbets.
    # Lives on a live all-hands counter (not showdown-gated), so it matures off
    # _big_bet_faced_count; min_samples mirrors SIZING_MIN_BIG_BET_FACED.
    _ReadSpec(
        read_key='fold_to_big_bet',
        sample_attr='_big_bet_faced_count',
        min_samples=6,  # == SIZING_MIN_BIG_BET_FACED (the deep_reads gate)
        phrasings={
            ARC_TENTATIVE: "I'm starting to notice you back down when the bet gets big.",
            ARC_CONFIDENT: "You don't like it when I really lean on the bet — you tend to let it go.",
            ARC_SURE: "Put a big enough bet out there and you fold every time.",
        },
    ),
    _ReadSpec(
        read_key='all_in_frequency',
        # all_in_frequency is denominated on hands_dealt, so its sample is the
        # global observation count, not a per-spot counter.
        sample_attr='hands_dealt',
        min_samples=15,
        phrasings={
            ARC_TENTATIVE: "I'm starting to get a sense for when you're willing to put it all in.",
            ARC_CONFIDENT: "I'm reading your shove tendencies — you tip your hand on those.",
            ARC_SURE: "I've got your shoving pattern down cold by now.",
        },
    ),
)


# Arc-tier sample thresholds. A read graduates as its specific spot accrues
# samples. These mirror the spirit of exploitation's confidence ramps
# (CONFIDENCE_RAMP_HANDS=100, _cbet_sample_confidence 5->10) but are coarser —
# three audible steps the player can feel.
ARC_TENTATIVE_FLOOR = 5
ARC_CONFIDENT_FLOOR = 25
ARC_SURE_FLOOR = 60


def _arc_tier(samples: int) -> Optional[str]:
    """Map a read's sample count to its arc tier, or None below the floor."""
    if samples >= ARC_SURE_FLOOR:
        return ARC_SURE
    if samples >= ARC_CONFIDENT_FLOOR:
        return ARC_CONFIDENT
    if samples >= ARC_TENTATIVE_FLOOR:
        return ARC_TENTATIVE
    return None


@dataclass(frozen=True)
class SpokenReadConfig:
    """Tuning for spoken-read surfacing. Frozen — one instance per controller."""

    max_observations_per_decision: int = 2
    # Per-stat cooldown in ELIGIBLE hands (not voiced hands). After a read on an
    # opponent is surfaced, the same observer->opponent pair won't surface again
    # until this many eligible hands have elapsed — keeps the bot from harping.
    cooldown_hands: int = 8
    # Arc-tier floors (exposed for tests / tuning; defaults match module consts).
    tentative_floor: int = ARC_TENTATIVE_FLOOR
    confident_floor: int = ARC_CONFIDENT_FLOOR
    sure_floor: int = ARC_SURE_FLOOR


@dataclass
class SpokenReadState:
    """Per-(observer -> opponent) anti-spam state, held on the controller
    instance for the session (NOT persisted).

    `eligible_hand_index` is a monotonic counter of hands on which ANY read was
    eligible to speak — it advances whether or not we actually voiced one, so a
    streak of silent streets neither resets nor spams the cooldown.
    `last_spoken` records, per opponent, the eligible-hand index at which we last
    surfaced a read about them.
    """

    eligible_hand_index: int = 0
    last_spoken: Dict[str, int] = field(default_factory=dict)


def _read_sample_count(tendencies, spec: _ReadSpec) -> int:
    """The read's effective sample count = its gating counter, or — for a
    dual-gated read (sizing_polarization) — the MIN of both bin counters, so the
    weaker bin drives both the min_samples gate and the arc tier."""
    primary = int(getattr(tendencies, spec.sample_attr, 0) or 0)
    if spec.second_sample_attr is None:
        return primary
    secondary = int(getattr(tendencies, spec.second_sample_attr, 0) or 0)
    return min(primary, secondary)


def _select_best_read(
    tendencies,
    deep_reads: Optional[dict],
    config: SpokenReadConfig,
) -> Optional[Tuple[str, str, str]]:
    """Pick the single most-surfaceable read for one opponent.

    Returns (read_key, arc_tier, observation_text), or None when no read has
    matured past its sample floor. Priority is the READ_PRIORITY order
    (legibility + maturation): fold_to_cbet > cbet_attempt_rate >
    barrel_frequency > sizing_polarization_score > fold_to_big_bet >
    all_in_frequency.
    """
    if tendencies is None or not deep_reads:
        return None

    def tier_for(samples: int) -> Optional[str]:
        if samples >= config.sure_floor:
            return ARC_SURE
        if samples >= config.confident_floor:
            return ARC_CONFIDENT
        if samples >= config.tentative_floor:
            return ARC_TENTATIVE
        return None

    for spec in READ_PRIORITY:
        # The deep_reads value being non-None means the read's own gate passed;
        # all_in_frequency is never None, so we additionally require samples.
        if deep_reads.get(spec.read_key) is None:
            continue
        samples = _read_sample_count(tendencies, spec)
        if samples < spec.min_samples:
            continue
        tier = tier_for(samples)
        if tier is None:
            continue
        text = spec.phrasings.get(tier)
        if not text:
            continue
        return (spec.read_key, tier, text)
    return None


@dataclass(frozen=True)
class SpokenRead:
    """A single surfaced spoken read — the full record (richer than the
    (name, text) tuple the speech channel consumes) so the narration_facts
    channel can carry the arc tier as a certainty cue."""

    opponent: str
    read_key: str
    arc_tier: str  # ARC_TENTATIVE / ARC_CONFIDENT / ARC_SURE
    observation: str


def select_spoken_reads(
    observer_name: str,
    active_opponents: List[str],
    facing_opponent: Optional[str],
    opponent_model_manager,
    state: SpokenReadState,
    config: SpokenReadConfig,
) -> Tuple[List[Tuple[str, str]], SpokenReadState, List['SpokenRead']]:
    """Select up to `config.max_observations_per_decision` intuition-framed
    spoken reads for this decision.

    Returns (observations, new_state, reads) where:
      - observations is a list of (opponent_name, observation_text) tuples —
        the same shape `ExpressionContext.opponent_observations` expects;
      - new_state is the advanced anti-spam state;
      - reads is the parallel list of full `SpokenRead` records (carrying the
        arc tier) for the narration_facts channel.

    Anti-spam: the eligible-hand counter advances by one whenever at least one
    opponent has a matured read this decision (regardless of whether the
    cooldown lets us voice it), so silent hands still progress the cooldown.
    Per-opponent, a read is voiced only if `cooldown_hands` eligible hands have
    elapsed since we last spoke about them.

    Graceful: returns ([], state, []) unchanged when the manager is absent,
    there are no opponents, or no read has matured.
    """
    if opponent_model_manager is None or not active_opponents:
        return [], state, []

    from poker.memory.opponent_reads import deep_reads_from_tendencies

    # Score each opponent's best matured read. Facing opponent first (the player
    # actively reacting to us), then the rest in stable order.
    ordered = sorted(
        active_opponents,
        key=lambda n: (0 if n == facing_opponent else 1, n),
    )

    matured: List[Tuple[str, str, str, str]] = []  # (opp, read_key, tier, text)
    for opp in ordered:
        try:
            model = opponent_model_manager.get_model_if_exists(observer_name, opp)
        except Exception:
            model = None
        if model is None:
            continue
        tendencies = getattr(model, 'tendencies', None)
        if tendencies is None:
            continue
        try:
            deep_reads = deep_reads_from_tendencies(tendencies)
        except Exception:
            deep_reads = None
        best = _select_best_read(tendencies, deep_reads, config)
        if best is None:
            continue
        read_key, tier, text = best
        matured.append((opp, read_key, tier, text))

    if not matured:
        # No read eligible to speak this decision — do NOT advance the counter
        # (nothing was eligible), and leave state untouched.
        return [], state, []

    # A read WAS eligible this decision → advance the eligible-hand counter once.
    new_index = state.eligible_hand_index + 1
    last_spoken = dict(state.last_spoken)

    observations: List[Tuple[str, str]] = []
    reads: List[SpokenRead] = []
    for opp, read_key, tier, text in matured:
        last = last_spoken.get(opp)
        if last is not None and (new_index - last) < config.cooldown_hands:
            continue  # still cooling down for this opponent
        observations.append((opp, text))
        reads.append(SpokenRead(opponent=opp, read_key=read_key, arc_tier=tier, observation=text))
        last_spoken[opp] = new_index
        if len(observations) >= config.max_observations_per_decision:
            break

    new_state = SpokenReadState(eligible_hand_index=new_index, last_spoken=last_spoken)
    return observations, new_state, reads
