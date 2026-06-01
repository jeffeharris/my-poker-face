"""
Opponent Modeling System.

Tracks opponent tendencies and memorable hands for AI learning.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..strategy.exploitation import AggregatedOpponentStats

from ..archetypes import (
    AF_PASSIVE as AGGRESSION_FACTOR_LOW,
    AF_VERY_AGGRESSIVE as AGGRESSION_FACTOR_VERY_HIGH,
    VPIP_LOOSE as VPIP_LOOSE_THRESHOLD,
    VPIP_VERY_SELECTIVE,
)
from ..config import (
    MEMORABLE_HAND_THRESHOLD,
    MIN_HANDS_FOR_STYLE_LABEL,
    MIN_HANDS_FOR_SUMMARY,
    OPPONENT_SUMMARY_TOKENS,
)
from .relationship_events import (
    AxisShift,
    RelationshipEvent,
    actor_shift,
    mirror_shift,
)


def _load_window_size() -> int:
    """Read the recent-window maxlen from phase_7_5_config.

    Lazy load avoids import-time circular dependency with the strategy
    package. Falls back to 50 if config is unavailable (shouldn't
    happen in production).
    """
    try:
        from ..strategy.phase_7_5_config import CONFIG

        return CONFIG.tier_decay.window_size
    except Exception:
        return 50


# ── Sizing-aware modeling Phase A thresholds ──────────────────────────
# A bet is "big" when the bettor's increment is >= this fraction of the
# pot-before-their-action. 0.75 ≈ the boundary between a standard 1/2–2/3
# pot bet and a polar overbet-ish bet (matches the bet_size_classification
# `large` bucket intent). Two bins only — four are sample-starved.
SIZING_BIG_BET_POT_RATIO = 0.75
# The polarization score (big−small equity gap) stays at its neutral 0.0
# prior until BOTH size bins have at least this many showdown observations
# — guards against a 1-sample bin swinging the read.
SIZING_MIN_BIN_SAMPLE = 4
# fold_to_big_bet (live, all-hands) is the offensive trigger; it needs a
# smaller floor than the showdown-gated polarization score.
SIZING_MIN_BIG_BET_FACED = 6


@dataclass
class OpponentTendencies:
    """Statistical model of an opponent's play style."""

    hands_observed: int = 0  # Hands where opponent took at least one action

    # Hands the opponent was at the table — regardless of whether they
    # ever acted. This is the correct denominator for VPIP/PFR/all_in_
    # frequency: folding before action reaches you is a relevant outcome
    # ("opted out of pot"), not an unobserved one. When hands_dealt is 0,
    # ratio calculations fall back to hands_observed (preserves behavior
    # for callers that don't call record_hand_dealt yet).
    hands_dealt: int = 0

    # Core stats
    vpip: float = 0.5  # Voluntarily put in pot % (how often they enter pots)
    pfr: float = 0.5  # Pre-flop raise % (how often they raise pre-flop)
    aggression_factor: float = 1.0  # (bet+raise+all-in) / call ratio
    fold_to_cbet: float = 0.5  # Fold to continuation bet %
    cbet_attempt_rate: float = 0.5  # Phase 8.1a: PFR's c-bet attempt rate
    # Phase B Item 1: street-resolved barrel rates. The exploit
    # induce_override targets is "PFR fires multiple streets after
    # being called" — barrel_frequency measures it directly instead
    # of relying on AF_pf×cbet_attempt as a proxy.
    barrel_frequency: float = 0.5  # turn bet rate after cbet+call
    third_barrel_frequency: float = 0.5  # river bet rate after barrel+call
    # Phase B Item 4: flop-check-then-barrel rate. Measures the
    # open-spot trap-bait pattern — how often this player checks flop
    # OOP and then bets turn after a check-through. Drives the
    # open-spot IP induce branch's signal.
    flop_check_then_barrel_rate: float = 0.5
    bluff_frequency: float = 0.3  # Estimated bluff rate
    showdown_win_rate: float = 0.5  # Win rate at showdown
    all_in_frequency: float = 0.0  # All-in actions per hand dealt

    # Phase 7.5 Step 0: opportunity-normalized stats for the three-tier
    # exploitation clamp. Computed from new postflop-only counters
    # (below). Default to 0.0 / 0 when no opportunities observed.
    #
    # See docs/plans/PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md "Stat-definition
    # glossary" for exact denominators.
    aggression_factor_postflop: float = 1.0  # postflop bet/raise/all-in / postflop call
    all_in_per_facing_bet: float = 0.0  # response-aggression axis
    postflop_jam_open_rate: float = 0.0  # open-aggression axis

    # Opportunity-normalized preflop stats. The legacy `vpip` and `pfr`
    # use hands_dealt as denominator, which causes 1/N scaling with
    # player count: a ManiacBot raising 100% of preflop opportunities
    # accumulates vpip=0.50 in HU, 0.33 in 3-player, 0.17 in 6-max
    # because the opener seat rotates. Detection thresholds calibrated
    # against vpip don't transfer across table sizes.
    #
    # These per-opportunity variants normalize against the situations
    # the opponent actually faced:
    #   - pfr_per_open_opportunity: numerator = preflop raises;
    #     denominator = hands the opponent had a chance to be the
    #     preflop opener (action came to them without a live raise).
    #   - vpip_per_voluntary_opportunity: numerator = voluntary preflop
    #     pot entries (call/raise/bet/all-in); denominator = hands the
    #     opponent had any voluntary preflop decision (any non-blind
    #     preflop action by them).
    #
    # Stay at neutral prior 0.5 until at least one observed
    # opportunity (mirrors fold_to_cbet / cbet_attempt_rate's
    # "no sample = neutral" stance).
    pfr_per_open_opportunity: float = 0.5
    vpip_per_voluntary_opportunity: float = 0.5

    # Limp rate: of the spots where this opponent could open (no live raise
    # above the blind in front of them), how often they just limp-called
    # instead of raising or folding. Numerator is _limp_count; denominator
    # reuses _preflop_open_opportunities (the same open-spot denominator that
    # feeds pfr_per_open_opportunity). Unlike a 0.5 prior, limps are the
    # exception not the coin-flip, so this stays at 0.0 ("no evidence of
    # limping") until an open opportunity is observed.
    limp_rate: float = 0.0

    # Trend tracking
    recent_trend: str = 'stable'  # 'tightening', 'loosening', 'stable'

    # Action counters (for calculating stats)
    _vpip_count: int = 0  # Hands where player voluntarily put money in pot
    _pfr_count: int = 0  # Hands where player raised pre-flop
    _bet_raise_count: int = 0  # Total bets, raises, and all-ins (aggressive)
    _call_count: int = 0  # Total calls
    _all_in_count: int = 0  # Total all-in actions (subset of _bet_raise_count)
    _fold_to_cbet_count: int = 0
    _cbet_faced_count: int = 0
    # Phase 8.1a: PFR-side c-bet attempt tracking. Denominator is hands
    # where this player WAS the preflop aggressor AND had a clean c-bet
    # opportunity on the flop (i.e. wasn't donk-bet into). Numerator is
    # hands where they took the c-bet. Together they yield
    # `cbet_attempt_rate` — a station-vs-LAG signal that VPIP alone
    # can't distinguish (a passive PFR with cbet_attempt_rate=0.20 plays
    # very differently from a LAG with 0.85 at the same VPIP).
    _cbet_attempt_count: int = 0
    _postflop_seen_as_pfr_count: int = 0
    # Phase B Item 1: barrel tracking. The direct signal that
    # induce_override's Phase B gate will read (replacing the
    # AF_postflop × cbet_attempt_rate proxy). Denominator is
    # opportunities — hands where this player cbet flop AND got
    # called AND had a clean turn decision. Numerator is barrels
    # actually fired. Same for third_barrel (turn→river).
    _barrel_count: int = 0
    _barrel_opportunity_count: int = 0
    _third_barrel_count: int = 0
    _third_barrel_opportunity_count: int = 0
    # Phase B Item 4: flop-check-then-barrel counters. Denominator is
    # opportunities — hands where this player was the first voluntary
    # flop checker AND the flop went check-through AND they had a
    # clean turn-first decision. Numerator is turn bets actually
    # fired in that spot.
    _flop_check_barrel_count: int = 0
    _flop_check_barrel_opportunity_count: int = 0
    _showdowns: int = 0
    _showdowns_won: int = 0

    # Phase 7.5 Step 0: per-axis counters for the new postflop-only stats.
    # Updated only when phase is FLOP/TURN/RIVER. See
    # `_apply_postflop_counters()` for the logic.
    _postflop_bet_raise_count: int = 0  # postflop bets/raises/all-ins
    _postflop_call_count: int = 0  # postflop calls
    _facing_bet_opportunities: int = 0  # postflop decisions while facing a bet
    _all_ins_facing_bet: int = 0  # subset: opponent went all-in in response
    _postflop_open_opportunities: int = (
        0  # postflop decisions with no live bet (legal bet/all-in available)
    )
    _postflop_jam_opens: int = 0  # subset: opponent went all-in into no-bet pot

    # Opportunity-normalized preflop counters. Counted ONCE per hand
    # (same as _vpip_count / _pfr_count) so the resulting ratios stay
    # bounded by 1.0 for a 100%-action opponent regardless of how
    # many decisions they faced inside the hand.
    # _preflop_voluntary_opportunities: hand had any voluntary preflop
    #   decision by this opponent.
    # _preflop_open_opportunities: hand had any voluntary preflop
    #   decision where there was no live raise above the blind (open
    #   opportunity).
    # _preflop_open_raise_count: numerator for pfr_per_open_opportunity.
    #   Counts hands where opponent took an open RAISE (first voluntary
    #   raise of the hand with no prior raise above the blind).
    #   Different from _pfr_count, which includes 3-bets/4-bets — a
    #   ratio of _pfr_count / open_opportunities can exceed 1.0 because
    #   3-bets happen when there's no open opportunity.
    # _preflop_voluntary_action_count: numerator for vpip_per_voluntary_
    #   opportunity. Counts hands where the opponent took any voluntary
    #   chip-commit action (call/raise/bet/all-in) given they had a
    #   voluntary decision.  Mirrors _vpip_count but with a per-hand
    #   gate that fires only when an opportunity was registered.
    _preflop_voluntary_opportunities: int = 0
    _preflop_open_opportunities: int = 0
    _preflop_open_raise_count: int = 0
    _preflop_voluntary_action_count: int = 0
    # Numerator for limp_rate: hands where the opponent limped — voluntarily
    # CALLED preflop in an open spot (no live raise above the blind). Counted
    # once per hand, gated against _preflop_open_opportunities like the open-
    # raise numerator. A call while facing a raise is a cold-call, NOT a limp,
    # so it does not tick this counter.
    _limp_count: int = 0

    # Polarization Phase A: equity-at-action tracking. Populated at
    # showdown when hole cards are revealed; the showdown caller walks
    # the player's postflop actions and records the equity-they-had-at-
    # that-decision into the matching action bucket. The derived means
    # let downstream rules distinguish polarized opponents (raise with
    # nuts) from noisy callers (raise with anything).
    #
    # Per-action means start at neutral 0.5 prior and only become
    # meaningful once their corresponding _count exceeds a minimum
    # sample threshold (defined in the polarization detection spec).
    # Sums are running totals to support incremental mean update without
    # storing the full sample history.
    equity_when_betting_postflop: float = 0.5  # mean equity on bets
    equity_when_raising_postflop: float = 0.5  # mean equity on raises
    equity_when_calling_postflop: float = 0.5  # mean equity on calls
    _equity_betting_sum: float = 0.0
    _equity_raising_sum: float = 0.0
    _equity_calling_sum: float = 0.0
    _equity_betting_count: int = 0
    _equity_raising_count: int = 0
    _equity_calling_count: int = 0

    # ── Sizing-aware modeling Phase A (docs/plans/SIZING_AWARE_OPPONENT_MODELING.md) ──
    # Does this opponent's BET SIZE telegraph hand strength? Two signals:
    #
    # (1) sizing_polarization_score = equity_when_betting_big − equity_when_betting_small.
    #     Positive ⇒ bets bigger with stronger hands ⇒ FACE-UP/polar (a human read
    #     we can defend against: fold marginals to their big bets, call their small
    #     ones). Showdown-gated (needs revealed cards) so it matures slowly — two
    #     size bins (big = bettor's increment ≥ SIZING_BIG_BET_POT_RATIO of the pot-
    #     before, small = below). Stays at the neutral 0.0 prior until both bins
    #     clear SIZING_MIN_BIN_SAMPLE.
    equity_when_betting_big: float = 0.5
    equity_when_betting_small: float = 0.5
    sizing_polarization_score: float = 0.0
    _equity_betting_big_sum: float = 0.0
    _equity_betting_small_sum: float = 0.0
    _equity_betting_big_count: int = 0
    _equity_betting_small_count: int = 0
    # (2) fold_to_big_bet — live-updated on ALL hands (not showdown-gated), like
    #     fold_to_cbet: when this opponent faces a large/jam-sized bet, did they
    #     fold? High ⇒ an over-folder we can ATTACK (overbet wider). Far better
    #     sample coverage than the polarization score → the primary offensive
    #     trigger (Phase C). Neutral 0.5 prior until _big_bet_faced_count matures.
    fold_to_big_bet: float = 0.5
    _fold_to_big_bet_count: int = 0
    _big_bet_faced_count: int = 0

    # Per-hand opportunity flags (reset on new hand, mirror _vpip_this_hand /
    # _pfr_this_hand).
    _preflop_voluntary_opp_this_hand: bool = False
    _preflop_open_opp_this_hand: bool = False
    _preflop_open_raised_this_hand: bool = False
    _preflop_vol_action_this_hand: bool = False
    _limped_this_hand: bool = False

    # Phase 7.5 Item 2b: sliding window of recent postflop events for
    # tier decay. Each entry is (action, was_facing_bet). Push on each
    # postflop update_from_action call; deque auto-pops oldest when
    # length exceeds maxlen. Maxlen is sized from
    # phase_7_5_config.tier_decay.window_size at first append (so
    # config changes via reload_for_testing apply to new instances).
    _recent_postflop_events: Deque[Tuple[str, bool]] = field(
        default_factory=lambda: deque(maxlen=_load_window_size())
    )

    # Per-hand tracking (reset each new hand)
    _vpip_this_hand: bool = False
    _pfr_this_hand: bool = False

    def record_hand_dealt(self):
        """Record that the opponent was at the table for one more hand.

        Should be called once per hand per opponent, regardless of whether
        they ever acted in the hand. Folding before action reaches you is
        a relevant "opted out" outcome that affects VPIP, not an
        unobservable event.

        Resets the per-hand flags so this hand's VPIP/PFR tracking starts
        clean. update_from_action() can still also reset these flags via
        count_hand=True for backwards compatibility.
        """
        self.hands_dealt += 1
        self._vpip_this_hand = False
        self._pfr_this_hand = False
        self._preflop_voluntary_opp_this_hand = False
        self._preflop_open_opp_this_hand = False
        self._preflop_open_raised_this_hand = False
        self._preflop_vol_action_this_hand = False
        self._limped_this_hand = False
        self._recalculate_stats()

    def update_from_action(
        self,
        action: str,
        phase: str,
        is_voluntary: bool = True,
        count_hand: bool = True,
        was_facing_bet: Optional[bool] = None,
    ):
        """Update stats based on observed action.

        Args:
            action: The action taken ('fold', 'check', 'call', 'raise', 'bet', 'all_in')
            phase: Game phase ('PRE_FLOP', 'FLOP', 'TURN', 'RIVER')
            is_voluntary: Whether this was a voluntary action (not forced blind)
            count_hand: Whether to increment hands_observed (only once per hand)
            was_facing_bet: Phase 7.5 Step 0. True if opponent was facing a live
                bet at decision time (i.e. fold/call/raise/all-in was the choice
                set). False if no live bet (check/bet/all-in into no-bet pot).
                None when caller can't determine — postflop counters skipped.
                Required for postflop_open_opportunities vs
                facing_bet_opportunities accounting. Preflop opportunity
                counters (open / voluntary) also key off this flag —
                "facing a bet" preflop = a live RAISE above the blind has
                been made by another player (i.e. opponent's decision is
                call/3-bet/fold rather than open/check-BB-option).
        """
        if count_hand:
            self.hands_observed += 1
            # Reset per-hand flags for new hand
            self._vpip_this_hand = False
            self._pfr_this_hand = False
            self._preflop_voluntary_opp_this_hand = False
            self._preflop_open_opp_this_hand = False
            self._preflop_open_raised_this_hand = False
            self._preflop_vol_action_this_hand = False
            self._limped_this_hand = False

        # Track VPIP (voluntary pot entry) - only count ONCE per hand.
        # all_in is voluntary chip commitment and counts as VPIP.
        if phase == 'PRE_FLOP' and is_voluntary and not self._vpip_this_hand:
            if action in ('call', 'raise', 'bet', 'all_in'):
                self._vpip_count += 1
                self._vpip_this_hand = True

        # Track PFR (pre-flop raise) - only count ONCE per hand.
        # A preflop all-in is the most aggressive raise possible; counts as PFR.
        if phase == 'PRE_FLOP' and action in ('raise', 'all_in') and not self._pfr_this_hand:
            self._pfr_count += 1
            self._pfr_this_hand = True

        # Track aggression. all_in is the most aggressive action and contributes
        # to both the general aggression counter and its own dedicated counter.
        if action in ('bet', 'raise', 'all_in'):
            self._bet_raise_count += 1
            if action == 'all_in':
                self._all_in_count += 1
        elif action == 'call':
            self._call_count += 1

        # Phase 7.5 Step 0: postflop-only counters for opportunity-
        # normalized stats. Skipped when was_facing_bet is None
        # (caller couldn't determine context) or when phase is preflop.
        if phase in ('FLOP', 'TURN', 'RIVER') and was_facing_bet is not None:
            self._apply_postflop_counters(action, was_facing_bet)

        # Opportunity-normalized preflop counters. Bumped on every
        # voluntary preflop action where the caller supplied facing-bet
        # context. Forced blind posts ('sb'/'bb', is_voluntary=False)
        # are not opportunities — the chips are auto-posted, not a
        # decision. When was_facing_bet is None the caller couldn't
        # determine context, so skip rather than guess wrong.
        if phase == 'PRE_FLOP' and is_voluntary and was_facing_bet is not None:
            self._apply_preflop_opportunity_counters(action, was_facing_bet)

        # Recalculate stats
        self._recalculate_stats()

    def _apply_preflop_opportunity_counters(
        self,
        action: str,
        was_facing_bet: bool,
    ) -> None:
        """Update preflop opportunity counters from one voluntary action.

        Counted ONCE per hand on both sides so the resulting ratios
        stay bounded by 1.0 for a 100%-action opponent regardless of
        how many decisions they faced inside the hand.

        Denominator counters (the "opportunities"):
        - `_preflop_voluntary_opportunities` ticks once when the
          opponent first acts voluntarily preflop.
        - `_preflop_open_opportunities` ticks once when the opponent
          first acts voluntarily preflop AND there's no live raise
          above the blind (was_facing_bet=False — they could have
          been the preflop opener).

        Numerator counters (the "took the action"):
        - `_preflop_voluntary_action_count` ticks once when the
          opponent first voluntarily puts chips in the pot (call /
          raise / bet / all-in). Counted only against
          _preflop_voluntary_opportunities for `vpip_per_voluntary_
          opportunity`.
        - `_preflop_open_raise_count` ticks once when the opponent
          OPENS preflop with a raise/all-in (raise as the first
          voluntary raiser — i.e. while not facing a raise). This is
          the numerator for `pfr_per_open_opportunity`. NOT the same
          as `_pfr_count`, which counts ANY preflop raise (3-bet,
          4-bet, etc.) and can exceed open opportunities.
        """
        # Denominators
        if not self._preflop_voluntary_opp_this_hand:
            self._preflop_voluntary_opportunities += 1
            self._preflop_voluntary_opp_this_hand = True
        if not was_facing_bet and not self._preflop_open_opp_this_hand:
            self._preflop_open_opportunities += 1
            self._preflop_open_opp_this_hand = True

        # Numerators
        if action in ('call', 'raise', 'bet', 'all_in') and not self._preflop_vol_action_this_hand:
            self._preflop_voluntary_action_count += 1
            self._preflop_vol_action_this_hand = True
        if (
            action in ('raise', 'all_in')
            and not was_facing_bet
            and not self._preflop_open_raised_this_hand
        ):
            self._preflop_open_raise_count += 1
            self._preflop_open_raised_this_hand = True
        # A limp is a CALL in an open spot (no live raise to face). Counted
        # against the same open-opportunity denominator as the open raise.
        if action == 'call' and not was_facing_bet and not self._limped_this_hand:
            self._limp_count += 1
            self._limped_this_hand = True

    def _apply_postflop_counters(self, action: str, was_facing_bet: bool) -> None:
        """Update Phase 7.5 postflop-only counters from an action.

        - Postflop AF counters: count bet/raise/all-in vs call regardless
          of opportunity type (matches legacy AF semantics, but postflop-
          scoped).
        - Opportunity counters: every postflop decision increments
          exactly one of `_facing_bet_opportunities` or
          `_postflop_open_opportunities`. The jam subcounters fire only
          when the action is all-in.
        - Sliding window: append (action, was_facing_bet) tuple. Deque's
          maxlen handles old-event eviction automatically.
        """
        # Postflop AF counters
        if action in ('bet', 'raise', 'all_in'):
            self._postflop_bet_raise_count += 1
        elif action == 'call':
            self._postflop_call_count += 1

        # Opportunity + jam subcounters
        if was_facing_bet:
            self._facing_bet_opportunities += 1
            if action == 'all_in':
                self._all_ins_facing_bet += 1
        else:
            self._postflop_open_opportunities += 1
            if action == 'all_in':
                self._postflop_jam_opens += 1

        # Phase 7.5 Item 2b: sliding-window event log for tier decay.
        self._recent_postflop_events.append((action, was_facing_bet))

    def recent_postflop_stats(self) -> 'AggregatedOpponentStats':
        """Build an AggregatedOpponentStats from the sliding-window events.

        Returns the same shape as the cumulative stats but computed over
        ONLY the recent postflop events in `_recent_postflop_events`.
        Consumed by `_determine_clamp` as `recent_stats` to enable tier
        ratchet-down when an opponent's recent behavior diverges from
        their cumulative profile.

        Legacy fields (vpip/pfr/aggression_factor/all_in_frequency/etc.)
        are left at their default values — only the Phase 7.5 fields
        and the relevant opportunity counts are populated. `_determine_clamp`
        only reads the Phase 7.5 fields, so the rest can stay neutral.
        """
        from ..strategy.exploitation import AggregatedOpponentStats

        events = self._recent_postflop_events
        if not events:
            return AggregatedOpponentStats()

        facing_bet_opps = 0
        all_ins_facing_bet = 0
        open_opps = 0
        jam_opens = 0
        pf_br = 0
        pf_call = 0

        for action, was_facing_bet in events:
            if was_facing_bet:
                facing_bet_opps += 1
                if action == 'all_in':
                    all_ins_facing_bet += 1
            else:
                open_opps += 1
                if action == 'all_in':
                    jam_opens += 1

            if action in ('bet', 'raise', 'all_in'):
                pf_br += 1
            elif action == 'call':
                pf_call += 1

        # Derive rates. AF cap on the call_count=0 fallback matches the
        # postflop-AF cap in _recalculate_postflop_stats — keep the
        # semantic identical between cumulative and recent paths.
        if pf_call == 0:
            if pf_br == 0:
                recent_af = 1.0
            else:
                from ..strategy.phase_7_5_config import CONFIG

                recent_af = min(float(pf_br), CONFIG.signal_thresholds.medium_af_postflop)
        else:
            recent_af = pf_br / pf_call

        recent_aipfb = all_ins_facing_bet / facing_bet_opps if facing_bet_opps > 0 else 0.0
        recent_jam_open = jam_opens / open_opps if open_opps > 0 else 0.0

        return AggregatedOpponentStats(
            # Legacy fields left at defaults — only Phase 7.5 fields
            # matter for _determine_clamp's recent-window check.
            aggression_factor_postflop=recent_af,
            all_in_per_facing_bet=recent_aipfb,
            facing_bet_opportunities=facing_bet_opps,
            postflop_jam_open_rate=recent_jam_open,
            postflop_open_opportunities=open_opps,
        )

    def update_showdown(self, won: bool):
        """Update showdown statistics."""
        self._showdowns += 1
        if won:
            self._showdowns_won += 1
        self._recalculate_stats()

    def update_fold_to_cbet(self, folded: bool):
        """Update fold to continuation bet stats."""
        self._cbet_faced_count += 1
        if folded:
            self._fold_to_cbet_count += 1
        self._recalculate_stats()

    def update_cbet_attempt(self, attempted: bool):
        """Phase 8.1a: record one PFR-flop-attempt event.

        Increments the denominator (`_postflop_seen_as_pfr_count`) on
        every call and the numerator (`_cbet_attempt_count`) only when
        `attempted=True`. Caller should ensure the player had a CLEAN
        c-bet opportunity (CbetDetector emits only those events).
        """
        self._postflop_seen_as_pfr_count += 1
        if attempted:
            self._cbet_attempt_count += 1
        self._recalculate_stats()

    def update_barrel_attempt(self, attempted: bool):
        """Phase B Item 1: record one turn-barrel-opportunity event.

        Increments the denominator on every call and the numerator
        only when `attempted=True`. Caller (MemoryManager via
        CbetDetector.consume_barrel_attempt_events) should only invoke
        this when the PFR had a clean barrel decision — they cbet
        flop, got called, and have a turn action with no donk ahead.
        """
        self._barrel_opportunity_count += 1
        if attempted:
            self._barrel_count += 1
        self._recalculate_stats()

    def update_third_barrel_attempt(self, attempted: bool):
        """Phase B Item 1: record one river-third-barrel-opportunity event.

        Same shape as update_barrel_attempt but for turn→river. Caller
        ensures the PFR barreled turn and got called.
        """
        self._third_barrel_opportunity_count += 1
        if attempted:
            self._third_barrel_count += 1
        self._recalculate_stats()

    def update_flop_check_barrel_attempt(self, attempted: bool):
        """Phase B Item 4: record one flop-check-then-barrel opportunity.

        Increments the denominator on every call and the numerator only
        when `attempted=True`. Caller (MemoryManager via
        CbetDetector.consume_flop_check_barrel_attempt_events) should
        only invoke this when the player checked OOP on the flop, the
        flop went check-through, and they had a clean turn-first
        decision (no donk ahead of them).
        """
        self._flop_check_barrel_opportunity_count += 1
        if attempted:
            self._flop_check_barrel_count += 1
        self._recalculate_stats()

    def update_equity_at_action(self, action: str, equity: float) -> None:
        """Polarization Phase A: record observed equity at the moment of a
        postflop action by this opponent.

        Args:
            action: The action taken at the moment of the observation.
                One of 'bet', 'raise', 'call'. Other actions (fold,
                check, all_in) are no-ops here — fold/check don't reveal
                strength, and all_in is bucketed into raise by the
                caller when it's a raising shove (and ignored when it's
                a call-shove). Caller is responsible for the bucket choice.
            equity: Estimated win probability vs. uniform random / vs.
                live opponent at the moment of the action, in [0, 1].
                Same definition as `player_decision_analysis.equity`.

        Updates the running sum + count for the matching action bucket
        and refreshes the per-action mean. No-op for action types that
        don't map to a tracked bucket so the caller can pass through
        without filtering.
        """
        if not (0.0 <= equity <= 1.0):
            # Silently skip nonsense values — better than corrupting
            # the running average with a guard rail at the seam.
            return

        if action == 'bet':
            self._equity_betting_sum += equity
            self._equity_betting_count += 1
            self.equity_when_betting_postflop = (
                self._equity_betting_sum / self._equity_betting_count
            )
        elif action == 'raise':
            self._equity_raising_sum += equity
            self._equity_raising_count += 1
            self.equity_when_raising_postflop = (
                self._equity_raising_sum / self._equity_raising_count
            )
        elif action == 'call':
            self._equity_calling_sum += equity
            self._equity_calling_count += 1
            self.equity_when_calling_postflop = (
                self._equity_calling_sum / self._equity_calling_count
            )
        # action types outside {bet, raise, call} intentionally ignored

    def update_equity_at_bet_size(self, equity: float, bet_fraction: float) -> None:
        """Sizing-aware Phase A: record the equity this opponent had when they
        bet/raised, BINNED by how big the bet was relative to the pot.

        Called from the showdown-correlation machine for each revealed
        bet/raise action (where we both know the strength via equity AND the
        size via the ordered-replay bet_fraction). `bet_fraction` is the
        bettor's increment over the pot-before-their-action. Feeds
        `sizing_polarization_score` = big-bet equity − small-bet equity: a
        positive gap means they size up with strength (face-up/polar). No-op on
        out-of-range inputs so the caller can pass through without guarding.
        """
        if not (0.0 <= equity <= 1.0) or bet_fraction is None or bet_fraction < 0:
            return
        if bet_fraction >= SIZING_BIG_BET_POT_RATIO:
            self._equity_betting_big_sum += equity
            self._equity_betting_big_count += 1
            self.equity_when_betting_big = (
                self._equity_betting_big_sum / self._equity_betting_big_count
            )
        else:
            self._equity_betting_small_sum += equity
            self._equity_betting_small_count += 1
            self.equity_when_betting_small = (
                self._equity_betting_small_sum / self._equity_betting_small_count
            )

    def update_fold_to_big_bet(self, folded: bool) -> None:
        """Sizing-aware Phase A: live (all-hands) record of how this opponent
        responds when FACING a large/jam-sized bet — did they fold?

        Mirrors `update_fold_to_cbet`: the caller increments this whenever the
        opponent faces a bet bucketed `large`/`jam` and either folds (folded=
        True) or continues (call/raise → folded=False). High fold rate ⇒ an
        over-folder to attack (Phase C overbets wider). Not showdown-gated, so
        it matures far faster than the polarization score.
        """
        self._big_bet_faced_count += 1
        if folded:
            self._fold_to_big_bet_count += 1
        self.fold_to_big_bet = self._fold_to_big_bet_count / self._big_bet_faced_count

    def _recalculate_stats(self):
        """Recalculate derived statistics.

        Uses hands_dealt as denominator when available (correct), falling
        back to hands_observed when no record_hand_dealt() calls have
        happened (backwards-compat for older paths).
        """
        denom = self.hands_dealt if self.hands_dealt > 0 else self.hands_observed
        if denom > 0:
            self.vpip = self._vpip_count / denom
            self.pfr = self._pfr_count / denom
            self.all_in_frequency = self._all_in_count / denom

        total_actions = self._bet_raise_count + self._call_count
        if total_actions == 0:
            # No actions observed yet; use neutral default
            self.aggression_factor = 1.0
        elif self._call_count == 0:
            # All observed actions are bets/raises; pre-Phase-7.5 this was
            # `float(self._bet_raise_count)`, which let raw count drive
            # extreme classification on noisy zero-call samples (a player
            # with 6 raises and 0 calls in 10 hands would show AF=6,
            # indistinguishable from a real maniac with 60 raises and 10
            # calls). Phase 7.5 Item 2 caps this at MEDIUM_AF_THRESHOLD
            # to suppress that noise — the downstream classifier then
            # correctly says "this opponent might be extreme, but we
            # don't have call samples to confirm — stay at MEDIUM clamp."
            from ..strategy.phase_7_5_config import CONFIG

            self.aggression_factor = min(
                float(self._bet_raise_count),
                CONFIG.signal_thresholds.medium_af_postflop,
            )
        else:
            self.aggression_factor = self._bet_raise_count / self._call_count

        if self._cbet_faced_count > 0:
            self.fold_to_cbet = self._fold_to_cbet_count / self._cbet_faced_count

        # Phase 8.1a: cbet_attempt_rate. Stays at the 0.5 neutral
        # default until we have at least one observed opportunity —
        # mirrors fold_to_cbet's "no sample = neutral prior" stance.
        if self._postflop_seen_as_pfr_count > 0:
            self.cbet_attempt_rate = self._cbet_attempt_count / self._postflop_seen_as_pfr_count

        # Phase B Item 1: barrel rates. Same "neutral prior 0.5 until
        # observed" stance. These are the proper signal that Phase B
        # Item 2's induce_override gate will read.
        if self._barrel_opportunity_count > 0:
            self.barrel_frequency = self._barrel_count / self._barrel_opportunity_count
        if self._third_barrel_opportunity_count > 0:
            self.third_barrel_frequency = (
                self._third_barrel_count / self._third_barrel_opportunity_count
            )

        # Phase B Item 4: flop-check-then-barrel rate. Same neutral-prior
        # 0.5 stance as the other Phase B stats.
        if self._flop_check_barrel_opportunity_count > 0:
            self.flop_check_then_barrel_rate = (
                self._flop_check_barrel_count / self._flop_check_barrel_opportunity_count
            )

        if self._showdowns > 0:
            self.showdown_win_rate = self._showdowns_won / self._showdowns

        # Sizing-aware Phase A: polarization score = how much MORE equity this
        # opponent shows on big bets vs small bets. Only meaningful once BOTH
        # bins have a real sample; otherwise hold the neutral 0.0 prior so a
        # lone observation can't flag a balanced player as face-up.
        if (
            self._equity_betting_big_count >= SIZING_MIN_BIN_SAMPLE
            and self._equity_betting_small_count >= SIZING_MIN_BIN_SAMPLE
        ):
            self.sizing_polarization_score = (
                self.equity_when_betting_big - self.equity_when_betting_small
            )
        else:
            self.sizing_polarization_score = 0.0

        # Opportunity-normalized preflop stats. Stay at neutral prior
        # 0.5 until at least one opportunity is observed (mirrors
        # fold_to_cbet / cbet_attempt_rate's "no sample = neutral" stance).
        # Numerators use dedicated _preflop_open_raise_count /
        # _preflop_voluntary_action_count rather than _pfr_count /
        # _vpip_count to keep the ratios bounded by 1.0 — the legacy
        # counters tick for 3-bets too, which happen in non-open spots
        # and would drive pfr_per_open_opportunity > 1.0 for an
        # always-raising opponent.
        if self._preflop_open_opportunities > 0:
            self.pfr_per_open_opportunity = (
                self._preflop_open_raise_count / self._preflop_open_opportunities
            )
        if self._preflop_voluntary_opportunities > 0:
            self.vpip_per_voluntary_opportunity = (
                self._preflop_voluntary_action_count / self._preflop_voluntary_opportunities
            )

        # Limp rate over open opportunities. Stays at the 0.0 prior until an
        # open spot is observed (limping is the exception, not a coin-flip).
        if self._preflop_open_opportunities > 0:
            self.limp_rate = self._limp_count / self._preflop_open_opportunities

        # Phase 7.5 Step 0: postflop opportunity-normalized stats.
        # Has the AF raw-count cap from day one — this field is new, no
        # legacy consumer to protect, so the cap lands here in Step 0.
        self._recalculate_postflop_stats()

    def _recalculate_postflop_stats(self) -> None:
        """Compute the Phase 7.5 postflop-only derived stats."""
        # Postflop AF, with cap from day one.
        if self._postflop_call_count == 0:
            if self._postflop_bet_raise_count == 0:
                self.aggression_factor_postflop = 1.0
            else:
                # No postflop calls observed — cap raw-count at MEDIUM
                # threshold so a zero-call sample can't trigger EXTREME
                # tier classification on noisy signal alone.
                # Import lazily to avoid circular imports at module load.
                from ..strategy.phase_7_5_config import CONFIG

                cap = CONFIG.signal_thresholds.medium_af_postflop
                self.aggression_factor_postflop = min(
                    float(self._postflop_bet_raise_count),
                    cap,
                )
        else:
            self.aggression_factor_postflop = (
                self._postflop_bet_raise_count / self._postflop_call_count
            )

        # Response-aggression axis: all-ins per facing-bet opportunity.
        if self._facing_bet_opportunities > 0:
            self.all_in_per_facing_bet = self._all_ins_facing_bet / self._facing_bet_opportunities
        else:
            self.all_in_per_facing_bet = 0.0

        # Open-aggression axis: opening jams per postflop open opportunity.
        if self._postflop_open_opportunities > 0:
            self.postflop_jam_open_rate = (
                self._postflop_jam_opens / self._postflop_open_opportunities
            )
        else:
            self.postflop_jam_open_rate = 0.0

    def get_play_style_label(self) -> str:
        """Returns play style classification.

        Returns one of:
        - 'tight-aggressive' (TAG)
        - 'loose-aggressive' (LAG)
        - 'tight-passive' (Rock)
        - 'loose-passive' (Calling Station)
        - 'unknown'
        """
        # VPIP uses hands_dealt as the denominator after the
        # opportunity-normalized rework, so gating on hands_observed
        # would label tight-passive players from a too-small pool of
        # voluntary entries.
        sample = self.hands_dealt if self.hands_dealt > 0 else self.hands_observed
        if sample < MIN_HANDS_FOR_STYLE_LABEL:
            return 'unknown'

        from ..archetypes import play_style_label
        return play_style_label(self.vpip, self.aggression_factor)

    def get_summary(self) -> str:
        """Generate human-readable summary for AI prompts."""
        if self.hands_observed < MIN_HANDS_FOR_SUMMARY:
            return "Not enough data"

        style = self.get_play_style_label()
        parts = [f"{style}"]

        if self.vpip > VPIP_LOOSE_THRESHOLD:
            parts.append("plays many hands")
        elif self.vpip < VPIP_VERY_SELECTIVE:
            parts.append("very selective")

        if self.aggression_factor > AGGRESSION_FACTOR_VERY_HIGH:
            parts.append("very aggressive")
        elif self.aggression_factor < AGGRESSION_FACTOR_LOW:
            parts.append("passive")

        if self.bluff_frequency > 0.5:
            parts.append("bluffs often")
        elif self.bluff_frequency < 0.2:
            parts.append("rarely bluffs")

        if self.fold_to_cbet > 0.7:
            parts.append("folds to pressure")
        elif self.fold_to_cbet < 0.3:
            parts.append("calls often")

        # Sizing-aware Phase A reads (only surface once the sample matured —
        # the score self-gates to 0.0 below SIZING_MIN_BIN_SAMPLE).
        if self.sizing_polarization_score > 0.15:
            parts.append("face-up sizing")  # bets big with strength → exploitable
        if (
            self._big_bet_faced_count >= SIZING_MIN_BIG_BET_FACED
            and self.fold_to_big_bet > 0.6
        ):
            parts.append("over-folds to big bets")

        return ", ".join(parts)

    # Canonical (de)serialization registry: one (attr_name, default)
    # entry per persisted scalar field. `to_dict` reads each attr;
    # `from_dict` restores it via `data.get(attr, default)`. Adding a
    # new persisted scalar field means adding ONE entry here — both
    # directions pick it up automatically.
    #
    # CRITICAL: every default below is the migration-era default that
    # old persisted rows rely on — do NOT change any value. The
    # `_recent_postflop_events` sliding window is NOT in this registry;
    # it needs bespoke list/tuple/deque handling (see to_dict/from_dict).
    _SERIAL_FIELDS: Tuple[Tuple[str, Any], ...] = (
        ('hands_observed', 0),
        ('hands_dealt', 0),
        ('vpip', 0.5),
        ('pfr', 0.5),
        ('aggression_factor', 1.0),
        ('fold_to_cbet', 0.5),
        ('cbet_attempt_rate', 0.5),
        ('barrel_frequency', 0.5),
        ('third_barrel_frequency', 0.5),
        ('flop_check_then_barrel_rate', 0.5),
        ('bluff_frequency', 0.3),
        ('showdown_win_rate', 0.5),
        ('all_in_frequency', 0.0),
        # Phase 7.5 derived stats
        ('aggression_factor_postflop', 1.0),
        ('all_in_per_facing_bet', 0.0),
        ('postflop_jam_open_rate', 0.0),
        # Opportunity-normalized preflop stats (neutral prior 0.5)
        ('pfr_per_open_opportunity', 0.5),
        ('vpip_per_voluntary_opportunity', 0.5),
        ('limp_rate', 0.0),
        ('recent_trend', 'stable'),
        ('_vpip_count', 0),
        ('_pfr_count', 0),
        ('_bet_raise_count', 0),
        ('_call_count', 0),
        ('_all_in_count', 0),
        ('_fold_to_cbet_count', 0),
        ('_cbet_faced_count', 0),
        # Phase 8.1a counters
        ('_cbet_attempt_count', 0),
        ('_postflop_seen_as_pfr_count', 0),
        # Phase B Item 1 counters
        ('_barrel_count', 0),
        ('_barrel_opportunity_count', 0),
        ('_third_barrel_count', 0),
        ('_third_barrel_opportunity_count', 0),
        ('_flop_check_barrel_count', 0),
        ('_flop_check_barrel_opportunity_count', 0),
        ('_showdowns', 0),
        ('_showdowns_won', 0),
        # Phase 7.5 counters
        ('_postflop_bet_raise_count', 0),
        ('_postflop_call_count', 0),
        ('_facing_bet_opportunities', 0),
        ('_all_ins_facing_bet', 0),
        ('_postflop_open_opportunities', 0),
        ('_postflop_jam_opens', 0),
        # Opportunity-normalized preflop counters
        ('_preflop_voluntary_opportunities', 0),
        ('_preflop_open_opportunities', 0),
        ('_preflop_open_raise_count', 0),
        ('_preflop_voluntary_action_count', 0),
        ('_limp_count', 0),
        # Polarization Phase A: equity-at-action fields
        ('equity_when_betting_postflop', 0.5),
        ('equity_when_raising_postflop', 0.5),
        ('equity_when_calling_postflop', 0.5),
        ('_equity_betting_sum', 0.0),
        ('_equity_raising_sum', 0.0),
        ('_equity_calling_sum', 0.0),
        ('_equity_betting_count', 0),
        ('_equity_raising_count', 0),
        ('_equity_calling_count', 0),
        # Sizing-aware Phase A: size-binned equity + fold-to-big-bet
        ('equity_when_betting_big', 0.5),
        ('equity_when_betting_small', 0.5),
        ('sizing_polarization_score', 0.0),
        ('fold_to_big_bet', 0.5),
        ('_equity_betting_big_sum', 0.0),
        ('_equity_betting_small_sum', 0.0),
        ('_equity_betting_big_count', 0),
        ('_equity_betting_small_count', 0),
        ('_fold_to_big_bet_count', 0),
        ('_big_bet_faced_count', 0),
    )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {attr: getattr(self, attr) for attr, _ in self._SERIAL_FIELDS}
        # Phase 7.5 Item 2b: sliding-window events (list-serialized).
        out['_recent_postflop_events'] = list(self._recent_postflop_events)
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OpponentTendencies':
        """Deserialize from dict. Missing fields fall back to the
        migration-era defaults in `_SERIAL_FIELDS` — older records that
        predate a field just lose their accumulated history for the new
        axis, which is the intended behavior (data wasn't captured
        before)."""
        tendencies = cls()
        for attr, default in cls._SERIAL_FIELDS:
            setattr(tendencies, attr, data.get(attr, default))
        # Phase 7.5 Item 2b: restore sliding-window events. Old records
        # without this field get an empty window — the tier-decay logic
        # treats sub-threshold windows as "no recent data," falling back
        # to cumulative tier, which is the right behavior for migrated
        # records (no recent samples to overrule cumulative).
        #
        # Coerce dicts/lists back into tuples; deque maxlen comes from
        # the current config so a saved record + new config combine
        # correctly.
        recent_events_raw = data.get('_recent_postflop_events', [])
        recent_events = [(action, bool(facing)) for action, facing in recent_events_raw]
        tendencies._recent_postflop_events = deque(
            recent_events,
            maxlen=_load_window_size(),
        )
        return tendencies


# --- Relationship state ---


# Decay tuning constants. Match the design doc starting calibration
# in `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1 §"Decay". They
# live in code so tests can lock them in; future tuning passes update
# both the constants here and the tests that assert on them.
HEAT_DECAY_PLATEAU_DAYS = 7
HEAT_DECAY_HALF_LIFE_DAYS = 14
HEAT_DECAY_SNAP_THRESHOLD = 0.05


@dataclass
class RelationshipState:
    """Cross-session, per-(observer, opponent) affinity axes.

    Three durable axes plus two presence timestamps. Stored in a
    separate `relationship_states` table (cross-session, cross-game)
    rather than on `opponent_models` (which is per-game-id). Read
    paths apply projection on `heat` via `project_heat` — the stored
    `heat` is the "heat as of last_decay_tick" snapshot; the live
    value is computed on demand from elapsed time. Respect and
    likability are earned state and don't decay.

    NOT on this object (intentionally):
      - session_pnl — lives in CashSessionState per (player, table_id)
      - cumulative_pnl — lives in cash_pair_stats table (cash-mode
        specific, meaningless in tournaments where chips reset)
      - sessions_together — derivable from tendencies.hands_observed
      - familiarity — derived on demand, never stored

    Persistence (Phase 1 commit 4): the schema migration adds the
    `relationship_states` table; this commit only defines the
    dataclass and the projection helper. No repository wiring yet.
    """

    respect: float = 0.5
    heat: float = 0.0  # one-sided: 0 = neutral, 1 = nemesis
    likability: float = 0.5

    # Cross-session presence
    last_seen: Optional[datetime] = None
    last_decay_tick: Optional[datetime] = None


@dataclass
class CashPairStats:
    """Cumulative cash-mode statistics for a (observer, opponent) pair.

    Distinct from `RelationshipState` because PnL is meaningless in
    tournaments (chips reset) — cash-mode-specific concepts don't
    pollute the affinity layer. Persisted in its own
    `cash_pair_stats` table (schema v87).

    `cumulative_pnl` is **observer-POV**: chips this observer has won
    net from this opponent across every cash-mode hand they've shared.
    The mirror pair (`stats[opponent][observer]`) gets the negation.
    Write transactions update both rows so the views can't drift.

    Side-pot allocation rule (cash-mode hand resolution):
      For each (winner, loser) pair the winner's net gain is split
      proportionally to each loser's chip contribution to the pots
      the winner collected. Side pots resolve independently — each
      side pot has its own (winner, loser) PnL pairs. Spec at
      `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1 §"Cash pair
      stats".
    """

    observer_id: str
    opponent_id: str
    cumulative_pnl: int = 0  # chips, observer's lifetime net vs opponent
    hands_played_cash: int = 0


def project_heat(
    state: RelationshipState,
    now: datetime,
    *,
    plateau_days: int = HEAT_DECAY_PLATEAU_DAYS,
    half_life_days: int = HEAT_DECAY_HALF_LIFE_DAYS,
    snap_threshold: float = HEAT_DECAY_SNAP_THRESHOLD,
) -> float:
    """Project the heat axis through plateau-then-exponential decay.

    Pure function. Reads `state.heat` and `state.last_decay_tick`;
    returns the value `heat` would currently have given elapsed time
    since the last mutation. Does NOT mutate the state.

    Schedule:
      - Plateau at the stored value for `plateau_days` after the last
        event (heat peaks and stays there briefly — fresh rivalries
        feel hot for about a week).
      - Exponential decay with `half_life_days` half-life afterward.
      - Snap to 0.0 below `snap_threshold` to keep tiny residuals
        from polluting reads (and to let `> 0` predicates stay
        meaningful).

    `last_decay_tick == None` means no event has ever been recorded
    — returns the stored heat verbatim (which is 0 for new states).

    Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1 §"Decay".
    """
    if state.last_decay_tick is None:
        return state.heat
    days = (now - state.last_decay_tick).total_seconds() / 86400.0
    if days <= plateau_days:
        return state.heat
    decay_days = days - plateau_days
    projected = state.heat * (0.5 ** (decay_days / half_life_days))
    return 0.0 if projected < snap_threshold else projected


@dataclass
class MemorableHand:
    """A specific hand worth remembering.

    The `event` field is the canonical typed surface — it's a
    `RelationshipEvent` enum member that downstream consumers
    (relationship-axis dispatch tables, future chat categorizer,
    diagnostics replays) can branch on without string-matching.

    DB compatibility: the persistence layer's column is still named
    `memory_type` (existing schema at
    `poker/repositories/schema_manager.py`). `to_dict` writes the
    enum's `.value` string to a `"memory_type"` key for that column;
    `from_dict` reads that key and parses it back via
    `RelationshipEvent.from_string`, which coerces unrecognized
    legacy strings to `RelationshipEvent.UNKNOWN` rather than
    raising. Old `memorable_hands` rows load cleanly with the
    quarantine sentinel; a one-shot offline migration script can
    enumerate the corpus and map unknowns to known events
    out-of-band.
    """

    hand_id: int
    event: RelationshipEvent  # was `memory_type: str` before Phase 1
    opponent_name: str
    impact_score: float  # 0-1, how memorable
    narrative: str  # AI-generated or template description
    hand_summary: str  # Brief summary of what happened
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hand_id': self.hand_id,
            # DB column name stays `memory_type` — value is now the
            # enum's `.value` string, not an ad-hoc raw label.
            'memory_type': self.event.value,
            'opponent_name': self.opponent_name,
            'impact_score': self.impact_score,
            'narrative': self.narrative,
            'hand_summary': self.hand_summary,
            'timestamp': self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MemorableHand':
        # Legacy rows may carry strings not in the current enum;
        # `from_string` returns UNKNOWN for those rather than raising,
        # so the load path stays robust without sweeping the DB first.
        raw_type = data['memory_type']
        if isinstance(raw_type, RelationshipEvent):
            event = raw_type
        else:
            event = RelationshipEvent.from_string(raw_type)
        return cls(
            hand_id=data['hand_id'],
            event=event,
            opponent_name=data['opponent_name'],
            impact_score=data['impact_score'],
            narrative=data['narrative'],
            hand_summary=data['hand_summary'],
            timestamp=datetime.fromisoformat(data['timestamp'])
            if isinstance(data['timestamp'], str)
            else data['timestamp'],
        )


class OpponentModel:
    """Tracks observations about a specific opponent.

    Combines statistical tendencies with AI-generated narrative observations
    for richer opponent modeling.
    """

    def __init__(
        self,
        observer: str,
        opponent: str,
        observer_id: Optional[str] = None,
        opponent_id: Optional[str] = None,
    ):
        """Args:
            observer: Display name of the observing player
            opponent: Display name of the observed player
            observer_id: Stable personality_id of the observer (slug),
                None for human-player observers or pre-v85 restore.
            opponent_id: Stable personality_id of the opponent (slug),
                None for human-player opponents or pre-v85 restore.

        Both ids are display-name-independent and survive renames. The
        relationship layer, AI bankrolls, and any cross-session callers
        should key on the ids. Display names remain for UI rendering.
        """
        self.observer = observer
        self.opponent = opponent
        self.observer_id = observer_id
        self.opponent_id = opponent_id
        self.tendencies = OpponentTendencies()
        self.memorable_hands: List[MemorableHand] = []
        self.narrative_observations: List[str] = []  # AI-generated insights about this opponent
        self._last_hand_counted: Optional[int] = None  # Track which hand we last counted

    def record_hand_dealt(self, hand_number: int = None):
        """Record that the opponent was at the table for one more hand.

        Idempotent within a hand: tracks `_last_hand_dealt` so calling
        twice with the same ``hand_number`` only increments once. Required
        for correct VPIP/PFR/all_in_frequency ratios, since folds before
        action mean the opponent never gets observe_action() called for
        that hand.

        Callers passing ``hand_number=None`` are responsible for ensuring
        they don't double-call within a logical hand — without an id we
        can't dedup, and silently swallowing every None call would hide
        real hands from new tables that don't yet number their hands.
        """
        if hand_number is not None:
            if hand_number == getattr(self, '_last_hand_dealt', None):
                return
            self._last_hand_dealt = hand_number
        self.tendencies.record_hand_dealt()

    def observe_action(
        self,
        action: str,
        phase: str,
        is_voluntary: bool = True,
        hand_number: int = None,
        was_facing_bet: Optional[bool] = None,
    ):
        """Record an observed action from this opponent.

        Args:
            was_facing_bet: Phase 7.5 Step 0 context flag passed through
                to OpponentTendencies.update_from_action.
        """
        # Only count hands_observed once per hand
        new_hand = hand_number is not None and hand_number != self._last_hand_counted
        if new_hand:
            self._last_hand_counted = hand_number
        self.tendencies.update_from_action(
            action,
            phase,
            is_voluntary,
            count_hand=new_hand,
            was_facing_bet=was_facing_bet,
        )

    def observe_showdown(self, won: bool, bluffed: bool = False):
        """Record a showdown observation."""
        self.tendencies.update_showdown(won)
        if bluffed and not won:
            # Caught bluffing - update bluff frequency estimate
            current_bluffs = self.tendencies.bluff_frequency * self.tendencies._showdowns
            self.tendencies.bluff_frequency = (current_bluffs + 1) / max(
                self.tendencies._showdowns, 1
            )

    def observe_fold_to_cbet(self, folded: bool):
        """Record fold/call response to continuation bet."""
        self.tendencies.update_fold_to_cbet(folded)

    def observe_cbet_attempt(self, attempted: bool):
        """Phase 8.1a: record this opponent's PFR-flop attempt event."""
        self.tendencies.update_cbet_attempt(attempted)

    def add_narrative_observation(self, observation: str) -> None:
        """Add an AI-generated observation about this opponent.

        Keeps the most recent observations (up to 5) as a sliding window.
        These observations are included in prompts so the AI can remember
        and refine its understanding of opponents over time.

        Args:
            observation: A narrative insight about the opponent (e.g.,
                "Folds to aggression on scary boards", "Overvalues top pair")
        """
        if not observation or not observation.strip():
            return

        observation = observation.strip()

        # Avoid exact duplicates
        if observation in self.narrative_observations:
            return

        self.narrative_observations.append(observation)

        # Keep only most recent 5
        if len(self.narrative_observations) > 5:
            self.narrative_observations = self.narrative_observations[-5:]

    def get_narrative_observations_text(self) -> str:
        """Get narrative observations formatted for prompts.

        Returns a concise string suitable for injection into AI prompts.
        """
        if not self.narrative_observations:
            return ""

        # Return most recent observation for prompt efficiency
        return self.narrative_observations[-1]

    def add_memorable_hand(
        self,
        hand_id: int,
        event,  # RelationshipEvent | str — see below
        impact_score: float,
        narrative: str,
        hand_summary: str,
    ):
        """Add a memorable hand if impact is high enough.

        `event` accepts either a `RelationshipEvent` enum member (the
        canonical form for new callers) or a legacy string. Strings
        coerce via `RelationshipEvent.from_string` so older call sites
        that haven't been migrated yet still work, with unrecognized
        strings landing in `RelationshipEvent.UNKNOWN` — the same
        quarantine path the load layer uses. (When everything's been
        migrated to enum, the `str` branch can be removed.)
        """
        if impact_score >= MEMORABLE_HAND_THRESHOLD:
            if isinstance(event, str):
                event = RelationshipEvent.from_string(event)
            self.memorable_hands.append(
                MemorableHand(
                    hand_id=hand_id,
                    event=event,
                    opponent_name=self.opponent,
                    impact_score=impact_score,
                    narrative=narrative,
                    hand_summary=hand_summary,
                )
            )
            # Keep only most memorable hands
            self.memorable_hands.sort(key=lambda h: h.impact_score, reverse=True)
            self.memorable_hands = self.memorable_hands[:5]

    def get_prompt_summary(self, max_tokens: int = 100) -> str:
        """Generate summary for AI prompt.

        Combines statistical analysis with narrative observations for
        a richer opponent profile.
        """
        parts = [f"{self.opponent}: {self.tendencies.get_summary()}"]

        # Add narrative observation if available
        narrative = self.get_narrative_observations_text()
        if narrative:
            parts.append(f"Notes: {narrative}")

        # Add most memorable hand if any
        if self.memorable_hands:
            most_memorable = self.memorable_hands[0]
            parts.append(f"Remember: {most_memorable.narrative}")

        result = ". ".join(parts)

        # Rough token limit
        estimated_tokens = len(result) / 4
        if estimated_tokens > max_tokens:
            # Fall back to just style + observation
            if narrative:
                result = (
                    f"{self.opponent}: {self.tendencies.get_play_style_label()}. Notes: {narrative}"
                )
            else:
                result = f"{self.opponent}: {self.tendencies.get_play_style_label()}"

        return result

    def get_recent_memorable_hands(self, limit: int = 3) -> List[MemorableHand]:
        """Get most recent memorable hands."""
        sorted_by_time = sorted(self.memorable_hands, key=lambda h: h.timestamp, reverse=True)
        return sorted_by_time[:limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'observer': self.observer,
            'opponent': self.opponent,
            'observer_id': self.observer_id,
            'opponent_id': self.opponent_id,
            'tendencies': self.tendencies.to_dict(),
            'memorable_hands': [h.to_dict() for h in self.memorable_hands],
            'narrative_observations': self.narrative_observations,
            # Idempotency cursors (T1-31): without these, restoring a
            # snapshot mid-session would double-count the next action's
            # hand_dealt / hands_observed and deflate VPIP/PFR.
            'last_hand_dealt': getattr(self, '_last_hand_dealt', None),
            'last_hand_counted': self._last_hand_counted,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OpponentModel':
        model = cls(
            observer=data['observer'],
            opponent=data['opponent'],
            observer_id=data.get('observer_id'),
            opponent_id=data.get('opponent_id'),
        )
        model.tendencies = OpponentTendencies.from_dict(data.get('tendencies', {}))
        model.memorable_hands = [
            MemorableHand.from_dict(h) for h in data.get('memorable_hands', [])
        ]
        model.narrative_observations = data.get('narrative_observations', [])
        model._last_hand_dealt = data.get('last_hand_dealt')
        model._last_hand_counted = data.get('last_hand_counted')
        return model


def _build_aggregate_from_single(t: OpponentTendencies):
    """Build AggregatedOpponentStats from one tendencies object (verbatim).

    Used by both the single-active-opponent path and the 60%-dominant
    branch. All Phase 7.5 Step 0 fields propagate from the tendencies'
    own derived properties + raw counters.
    """
    from poker.strategy.exploitation import AggregatedOpponentStats

    return AggregatedOpponentStats(
        hands_observed=t.hands_observed,
        vpip=t.vpip,
        pfr=t.pfr,
        aggression_factor=t.aggression_factor,
        all_in_frequency=t.all_in_frequency,
        fold_to_cbet=t.fold_to_cbet,
        cbet_faced_count=t._cbet_faced_count,
        cbet_attempt_rate=t.cbet_attempt_rate,
        postflop_seen_as_pfr_count=t._postflop_seen_as_pfr_count,
        barrel_frequency=t.barrel_frequency,
        barrel_opportunities=t._barrel_opportunity_count,
        third_barrel_frequency=t.third_barrel_frequency,
        third_barrel_opportunities=t._third_barrel_opportunity_count,
        flop_check_then_barrel_rate=t.flop_check_then_barrel_rate,
        flop_check_barrel_opportunities=t._flop_check_barrel_opportunity_count,
        # Phase 7.5 Step 0 fields
        aggression_factor_postflop=t.aggression_factor_postflop,
        all_in_per_facing_bet=t.all_in_per_facing_bet,
        facing_bet_opportunities=t._facing_bet_opportunities,
        postflop_jam_open_rate=t.postflop_jam_open_rate,
        postflop_open_opportunities=t._postflop_open_opportunities,
        # Opportunity-normalized preflop fields
        pfr_per_open_opportunity=t.pfr_per_open_opportunity,
        vpip_per_voluntary_opportunity=t.vpip_per_voluntary_opportunity,
        preflop_open_opportunities=t._preflop_open_opportunities,
        preflop_voluntary_opportunities=t._preflop_voluntary_opportunities,
        # Polarization Phase A equity-at-action fields
        equity_when_betting_postflop=t.equity_when_betting_postflop,
        equity_when_raising_postflop=t.equity_when_raising_postflop,
        equity_when_calling_postflop=t.equity_when_calling_postflop,
        _equity_betting_count=t._equity_betting_count,
        _equity_raising_count=t._equity_raising_count,
        _equity_calling_count=t._equity_calling_count,
    )


def _build_aggregate_from_multi(tendencies_list):
    """Build AggregatedOpponentStats by aggregating multiple tendencies.

    Float rate fields use EQUAL-weight average across the list. Sample
    counters (hands_observed, cbet_faced_count, facing_bet_opportunities,
    postflop_open_opportunities, …) use MIN — the limiting factor for
    exploit confidence. Matches the 6.7a aggregator's policy explicitly
    (see plan §"aggregation policy").

    Equal-weight here is INTENTIONALLY different from the spot-aware
    `aggregate_from_spots`, which stake-weights by committed_this_hand.
    Both share the `_aggregate_stats` core; this path converts each
    tendencies object to its derived AggregatedOpponentStats first
    (via `_build_aggregate_from_single`) and then blends with no weights
    (equal weight).
    """
    from poker.strategy.exploitation import (
        AggregatedOpponentStats,
        _aggregate_stats,
    )

    if not tendencies_list:
        return AggregatedOpponentStats()

    return _aggregate_stats(
        [_build_aggregate_from_single(t) for t in tendencies_list],
        weights=None,
    )


class OpponentModelManager:
    """Manages opponent models for all AI players.

    Models are keyed in-memory by display name (observer name → opponent
    name → OpponentModel) for back-compat with the historical lookup
    surface. Each OpponentModel additionally carries observer_id +
    opponent_id (stable personality_ids, populated when the manager
    can resolve them); cross-session callers (relationship layer, cash
    mode bankrolls, repository persistence) consume the ids, not the
    keys.

    Use `register_player_id` at game startup to associate display names
    with their stable personality_ids; subsequent get_model calls will
    annotate new OpponentModel instances with the registered ids.
    """

    def __init__(self, relationship_repo=None):
        """Construct the manager.

        Args:
            relationship_repo: Optional RelationshipRepository for the
                cross-session axis state (heat / respect / likability).
                Required by `record_event`; not required for in-memory
                tendency tracking. Pass None in unit tests that don't
                exercise record_event.
        """
        # observer_name -> opponent_name -> OpponentModel
        self.models: Dict[str, Dict[str, OpponentModel]] = {}
        # display_name -> personality_id (None for players without one)
        self._name_to_id: Dict[str, Optional[str]] = {}
        # Repository for cross-session relationship state. Optional at
        # construction (in-memory unit tests don't need it); required
        # when record_event is invoked. A clear error fires at call
        # time if it's missing rather than at __init__ — keeps test
        # ergonomics light.
        self._relationship_repo = relationship_repo

    @property
    def has_relationship_repo(self) -> bool:
        """True when a `RelationshipRepository` is wired for this manager.

        Public predicate for callers that want to skip a dispatch path
        when persistence isn't available (in-memory tests, older
        replay paths). The alternative is to call `record_event` and
        catch the `RuntimeError` it raises — fine for genuine error
        paths, but noisy when the no-repo case is the documented
        soft-fail.
        """
        return self._relationship_repo is not None

    def resolve_player_id(self, name: str) -> str:
        """Resolve a display name to its registered personality_id.

        Mirrors `HandOutcomeDetector._resolve_id`: returns the
        registered id when one exists, falls back to the display name
        when there's no entry or the entry is None (human guests,
        pre-v85 personalities). Always returns a string — relationship
        state is keyed on whatever this returns, so a name with no
        registered id still gets per-pair state, just keyed on the
        name rather than a stable cross-session id.

        Public counterpart to the detector's private resolver. Use
        when something outside the detector (e.g., chat-send routes)
        needs to dispatch `record_event` with name-keyed input.
        """
        if name in self._name_to_id:
            mapped = self._name_to_id[name]
            return mapped if mapped is not None else name
        return name

    def register_player_id(self, name: str, personality_id: Optional[str]) -> None:
        """Register the stable personality_id for a display name.

        Called at game startup (and on personality changes) so that
        OpponentModel rows get their observer_id / opponent_id populated
        at creation time. Existing models for this name are back-filled
        too — both in their observer slot and as an opponent slot
        across every other observer's mapping.

        Passing None is meaningful: it explicitly registers a name as
        "known not to have a personality_id" (human guests, etc.).
        Future get_model calls won't re-attempt resolution.
        """
        self._name_to_id[name] = personality_id

        # Back-fill existing models. Observer slot:
        if name in self.models:
            for model in self.models[name].values():
                if model.observer_id is None and personality_id is not None:
                    model.observer_id = personality_id

        # Opponent slot across all observers:
        for observer_name, opp_map in self.models.items():
            if name in opp_map:
                model = opp_map[name]
                if model.opponent_id is None and personality_id is not None:
                    model.opponent_id = personality_id

    def get_model(self, observer: str, opponent: str) -> OpponentModel:
        """Get or create an opponent model."""
        if observer not in self.models:
            self.models[observer] = {}

        if opponent not in self.models[observer]:
            self.models[observer][opponent] = OpponentModel(
                observer,
                opponent,
                observer_id=self._name_to_id.get(observer),
                opponent_id=self._name_to_id.get(opponent),
            )

        return self.models[observer][opponent]

    def get_model_if_exists(
        self,
        observer: str,
        opponent: str,
    ) -> Optional[OpponentModel]:
        """Return an existing model, or None. Does NOT create one.

        Phase 6.7a spot construction reads stats for every non-hero
        player in the hand at decision time. Using get_model() there
        would lazily create empty models for every opponent the hero
        ever sat with — polluting `self.models` and slowly inflating
        memory across long runs. The legacy aggregate path at
        aggregate_active_opponents() explicitly avoided this; this
        accessor lets read-only callers do the same.
        """
        return self.models.get(observer, {}).get(opponent)

    def observe_action(
        self,
        observer: str,
        opponent: str,
        action: str,
        phase: str,
        is_voluntary: bool = True,
        hand_number: int = None,
        was_facing_bet: Optional[bool] = None,
    ):
        """Record an action observation.

        Args:
            was_facing_bet: Phase 7.5 Step 0 context flag. See
                OpponentTendencies.update_from_action for semantics.
                None when caller can't determine; postflop counters
                skipped in that case.
        """
        model = self.get_model(observer, opponent)
        model.observe_action(
            action,
            phase,
            is_voluntary,
            hand_number=hand_number,
            was_facing_bet=was_facing_bet,
        )

    def record_hand_dealt(self, observer: str, opponents: List[str], hand_number: int = None):
        """Record that each opponent was dealt one more hand.

        Call once per hand per active opponent at the start of the hand.
        Required for correct VPIP/PFR ratios — opponents that fold before
        action reaches them never trigger observe_action, so without this
        their hands_dealt never increments and ratios are inflated.
        """
        for opp in opponents:
            self.get_model(observer, opp).record_hand_dealt(hand_number=hand_number)

    def get_table_summary(
        self, observer: str, opponents: List[str], max_tokens: int = OPPONENT_SUMMARY_TOKENS
    ) -> str:
        """Get summary of all opponents at the table."""
        if observer not in self.models:
            return ""

        summaries = []
        tokens_per_opponent = max_tokens // max(len(opponents), 1)

        for opponent in opponents:
            if opponent in self.models[observer]:
                model = self.models[observer][opponent]
                if model.tendencies.hands_observed >= MIN_HANDS_FOR_SUMMARY:
                    summaries.append(model.get_prompt_summary(tokens_per_opponent))

        return "\n".join(summaries)

    def select_opponent_observations(
        self,
        observer: str,
        active_opponents: List[str],
        facing_opponent: Optional[str] = None,
        max_observations: int = 2,
        now: Optional[datetime] = None,
    ) -> List[Tuple[str, str]]:
        """Pick the most relevant narrative observations to surface to the LLM.

        Each `OpponentModel` keeps up to 5 narrative observations as a
        sliding window, but only the LATEST is currently injected into
        prompts (via `get_prompt_summary`). This helper selects up to
        `max_observations` total across all active opponents, weighted
        by relevance, for the LLM to key on (or ignore).

        Scoring per (opponent, observation):
          - recency:        +0.0..+0.3 (newest = highest)
          - facing bonus:   +2.0 if opponent is the current `facing_opponent`
                            (typically the recent aggressor — the player
                            hero is actively reacting to)
          - nemesis bonus:  +1.0 if relationship heat > rival threshold
                            (graceful no-op when relationship state isn't
                            wired up — manager._relationship_repo is None
                            or no row exists for the pair)

        Args:
            observer: The AI player whose memory we're reading.
            active_opponents: Opponents still in the hand (not folded).
                Folded opponents are filtered upstream by the caller.
            facing_opponent: Name of the opponent hero is directly facing
                (aggressor, raiser, current bet source). When provided,
                their observation gets the largest bonus. Optional —
                callers without spot-level aggressor info can omit it.
            max_observations: Cap on the returned list. Default 2 — small
                enough that the LLM can latch onto them, large enough to
                cover both an active-opponent read and a nemesis read.
            now: Projection point for relationship heat decay. Defaults to
                `datetime.utcnow()` when None.

        Returns:
            Up to `max_observations` (opponent_name, observation_text)
            tuples, sorted by score descending. Empty list when no active
            opponent has stored observations.
        """
        if observer not in self.models or not active_opponents:
            return []

        # Resolve nemesis lookups lazily — relationship_repo may be None.
        from .relationship_modifier import HEAT_RIVAL_THRESHOLD

        if now is None:
            now = datetime.utcnow()

        scored: List[Tuple[float, str, str]] = []
        for opp_name in active_opponents:
            model = self.models[observer].get(opp_name)
            if model is None or not model.narrative_observations:
                continue

            # Nemesis bonus — look up heat once per opponent. Graceful
            # when relationship_repo isn't wired (heat defaults to 0).
            nemesis_bonus = 0.0
            if self._relationship_repo is not None:
                opp_id = self._name_to_id.get(opp_name)
                observer_id = self._name_to_id.get(observer)
                if opp_id is not None and observer_id is not None:
                    try:
                        state = self._relationship_repo.load_relationship_state(
                            observer_id,
                            opp_id,
                            now=now,
                        )
                        if state is not None and state.heat > HEAT_RIVAL_THRESHOLD:
                            nemesis_bonus = 1.0
                    except Exception:
                        # Relationship lookup failure is non-fatal —
                        # observation selection should never block a
                        # decision.
                        pass

            facing_bonus = 2.0 if opp_name == facing_opponent else 0.0

            # Recency bonus — last entry in the deque is newest.
            observations = list(model.narrative_observations)
            n = len(observations)
            for idx, obs in enumerate(observations):
                recency = 0.3 * (idx + 1) / n  # 0.0..0.3, newer is higher
                score = recency + facing_bonus + nemesis_bonus
                scored.append((score, opp_name, obs))

        if not scored:
            return []

        # Take the highest-scoring observation per opponent (one per
        # opponent to avoid two reads on the same player crowding out
        # other opponents' observations).
        scored.sort(reverse=True)
        seen_opps = set()
        deduped: List[Tuple[str, str]] = []
        for score, opp, obs in scored:
            if opp in seen_opps:
                continue
            seen_opps.add(opp)
            deduped.append((opp, obs))
            if len(deduped) >= max_observations:
                break
        return deduped

    def get_all_models_for_observer(self, observer: str) -> Dict[str, OpponentModel]:
        """Get all opponent models for an observer."""
        return self.models.get(observer, {})

    def aggregate_active_opponents(
        self,
        observer: str,
        active_opponents: List[str],
        money_committed: Optional[Dict[str, float]] = None,
    ) -> 'AggregatedOpponentStats':
        """Aggregate stats across active opponents for an exploit decision.

        Multiway 60% rule: if money_committed is provided and any one
        opponent has put in >60% of the active opponents' total committed
        money this hand, weight that opponent at 100% (focus exploitation
        on the credible threat). Otherwise weight-average across active
        opponents (equal weighting).

        Args:
            observer: The hero whose models are queried (self.models[observer]).
            active_opponents: Names of opponents still in the hand (not folded).
            money_committed: Optional map of opponent name -> chips committed
                this hand. When None, simple equal-weight-average.

        Returns:
            AggregatedOpponentStats. Returns zero-initialized stats
            (hands_observed=0) if no opponents have observation history.
        """
        # Lazy import to avoid circular dependency with poker.strategy
        from poker.strategy.exploitation import AggregatedOpponentStats

        if not active_opponents:
            return AggregatedOpponentStats()

        # Only inspect existing models — never create new ones from a query.
        observer_models = self.models.get(observer, {})
        models_with_history = [
            observer_models[opp]
            for opp in active_opponents
            if opp in observer_models and observer_models[opp].tendencies.hands_observed > 0
        ]

        if not models_with_history:
            return AggregatedOpponentStats()

        if len(models_with_history) == 1:
            return _build_aggregate_from_single(models_with_history[0].tendencies)

        # Multiple opponents with history. Check 60% rule.
        if money_committed:
            # Sum money committed across active opponents that have history
            history_names = {m.opponent for m in models_with_history}
            relevant = {
                name: float(money_committed.get(name, 0.0))
                for name in history_names
                if name in money_committed
            }
            total = sum(relevant.values())
            if total > 0:
                for name, amount in relevant.items():
                    if amount / total > 0.6:
                        # Dominant opponent gets 100% weight
                        dominant = next(m for m in models_with_history if m.opponent == name)
                        return _build_aggregate_from_single(dominant.tendencies)

        return _build_aggregate_from_multi([m.tendencies for m in models_with_history])

    def to_dict(self) -> Dict[str, Any]:
        # Back-compat shape: top-level keys are observer names. Add an
        # underscored sidecar entry for the name→id map so existing
        # consumers that index by observer name continue working,
        # while the round-trip preserves the id registry.
        result: Dict[str, Any] = {}
        for observer, opponents in self.models.items():
            result[observer] = {opponent: model.to_dict() for opponent, model in opponents.items()}
        if self._name_to_id:
            result['__name_to_id__'] = dict(self._name_to_id)
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OpponentModelManager':
        manager = cls()
        for observer, opponents in data.items():
            if observer == '__name_to_id__':
                manager._name_to_id = dict(opponents) if opponents else {}
                continue
            manager.models[observer] = {
                opponent: OpponentModel.from_dict(model_data)
                for opponent, model_data in opponents.items()
            }
        return manager

    # --- Relationship layer (Track B step 2) ---

    def record_event(
        self,
        actor_id: str,
        target_id: str,
        event: RelationshipEvent,
        *,
        impact_score: float = 1.0,
        context_multiplier: float = 1.0,
        narrative: str = "",
        hand_summary: str = "",
        hand_id: Optional[int] = None,
        mirror_shift_override: Optional[AxisShift] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Single entry point for all RelationshipState axis mutations.

        IDs only — never display names. This invariant lives at one
        location (this method); every consumer that mutates affinity
        state goes through here, so the projection-on-read pattern
        and bilateral-update guarantee can't be bypassed by reading
        and writing column-level state elsewhere.

        Project-first-then-apply ordering (load-bearing — a refresh
        event 30 days after a peak must not reset stale heat back to
        its day-zero value):

          1. Resolve `now` (defaults to datetime.utcnow()).
          2. For each pair entry to update:
             a. Load or default-construct the state from the repo.
             b. Project the stored `heat` through decay to `now` —
                state.heat is now the live value, not the snapshot.
             c. Apply the event-table shift (× context_multiplier).
             d. Clamp each axis to its valid range [0,1].
             e. Set last_decay_tick = last_seen = now.
             f. Persist via repository.
          3. Apply actor's-POV shifts to relationship[actor_id][target_id].
          4. Apply mirror shifts to relationship[target_id][actor_id].
          5. If impact_score >= MEMORABLE_HAND_THRESHOLD, also append a
             MemorableHand on the actor's in-memory PlayerModel (when
             one exists) — the memorable hand persists via the
             existing opponent_models save path the next time
             save_opponent_models runs. If no PlayerModel exists for
             the actor (e.g. the actor isn't currently seated), the
             relationship axis update still persists; the memorable
             hand entry is skipped silently. The relationship state is
             the load-bearing surface here.

        `mirror_shift_override`, when provided, replaces the mirror
        (target's-POV) shift only — the actor side always uses the
        neutral `actor_shift(event)`. It is the seam for recipient
        temperament: the chat dispatch resolves the target's social
        disposition and passes a reshaped needle reception (see
        `temperament_adjusted_mirror_shift`). It is still scaled by
        `context_multiplier` like the neutral shift, so intensity
        composes on top. Hand-outcome / staking callers leave it None
        and get the unchanged mirror table.

        Does NOT mutate anything outside RelationshipState and (best-
        effort) MemorableHand. Decay reads, cash-session state,
        cash_pair_stats, and economy events use their own APIs.

        Raises:
            RuntimeError: if `relationship_repo` was not provided at
                construction. Tests that exercise record_event must
                pass a repository (see RelationshipRepository).
        """
        if self._relationship_repo is None:
            raise RuntimeError(
                "OpponentModelManager.record_event requires a " "relationship_repo at construction"
            )
        if event is RelationshipEvent.UNKNOWN:
            # Documented no-op: quarantined events from legacy strings
            # never move axes. Return silently rather than walking the
            # full apply path with all-zero shifts.
            return

        if now is None:
            now = datetime.utcnow()

        # Bilateral pair updates. Each side has its own state row,
        # its own shift lookup, and its own clamp / persist. Both
        # writes go through the same repo so the views can't drift.
        self._apply_one_side(
            observer_id=actor_id,
            other_id=target_id,
            shift=actor_shift(event),
            context_multiplier=context_multiplier,
            now=now,
        )
        self._apply_one_side(
            observer_id=target_id,
            other_id=actor_id,
            shift=mirror_shift_override if mirror_shift_override is not None else mirror_shift(event),
            context_multiplier=context_multiplier,
            now=now,
        )

        # Best-effort MemorableHand on the actor's in-memory model.
        # We look up the actor's display name from the reverse map;
        # if no PlayerModel exists for the actor (or no display name
        # resolves to this id), skip silently — relationship axis
        # state is the load-bearing surface.
        if hand_id is None or impact_score < MEMORABLE_HAND_THRESHOLD:
            return
        actor_name = self._resolve_id_to_name(actor_id)
        target_name = self._resolve_id_to_name(target_id)
        if actor_name is None or target_name is None:
            return
        opponent_map = self.models.get(actor_name)
        if opponent_map is None:
            return
        player_model = opponent_map.get(target_name)
        if player_model is None:
            return
        player_model.add_memorable_hand(
            hand_id=hand_id,
            event=event,
            impact_score=impact_score,
            narrative=narrative,
            hand_summary=hand_summary,
        )

    def _apply_one_side(
        self,
        observer_id: str,
        other_id: str,
        shift,  # AxisShift
        context_multiplier: float,
        now: datetime,
    ) -> None:
        """Apply one side of a bilateral relationship update.

        Internal helper for record_event. Loads → projects → applies
        shift → clamps → persists, all in one place so the ordering
        invariant holds even when one side comes from the mirror
        table.
        """
        # Step 2a: load or default. Use load_raw so we get the stored
        # snapshot — step 2b explicitly projects it.
        state = self._relationship_repo.load_raw_relationship_state(observer_id, other_id)
        if state is None:
            state = RelationshipState()

        # Step 2b: project stored heat through decay to `now`. Stale
        # heat from 30+ days ago is decayed BEFORE the event shift
        # applies — a refresh event won't reset stale heat back to
        # its day-zero peak.
        state.heat = project_heat(state, now)

        # Step 2c: apply event-table shifts, scaled by context.
        state.heat += shift.heat * context_multiplier
        state.respect += shift.respect * context_multiplier
        state.likability += shift.likability * context_multiplier

        # Step 2d: clamp to [0, 1]. heat and likability and respect
        # all use this range; design doc treats heat as one-sided
        # (0 = neutral, 1 = nemesis) and respect/likability as
        # 0.5-default neutrality with [0, 1] bounds.
        state.heat = max(0.0, min(1.0, state.heat))
        state.respect = max(0.0, min(1.0, state.respect))
        state.likability = max(0.0, min(1.0, state.likability))

        # Step 2e: presence timestamps. last_seen and last_decay_tick
        # both anchor to `now` after a write — the next decay window
        # restarts from this point.
        state.last_seen = now
        state.last_decay_tick = now

        # Step 2f: persist.
        self._relationship_repo.save_relationship_state(observer_id, other_id, state)

    def _resolve_id_to_name(self, personality_id: str) -> Optional[str]:
        """Reverse lookup from personality_id → display name.

        Used by the MemorableHand best-effort path in record_event.
        Returns None if no registered name resolves to this id — that
        case is silent (relationship state still persists; only the
        memorable-hand sidecar is skipped).
        """
        for name, pid in self._name_to_id.items():
            if pid == personality_id:
                return name
        return None


def format_opponent_observations(pairs: List[Tuple[str, str]]) -> str:
    """Render a selected-observations list as a prompt block.

    Companion to `OpponentModelManager.select_opponent_observations`.
    Pure formatter — no I/O, no manager dependency.

    Returns the empty string when `pairs` is empty so callers can
    conditionally skip the section. When non-empty, returns a labeled
    block the LLM can key on (or ignore):

        Your reads on opponents:
        - {name}: {observation}
        - {name}: {observation}
    """
    if not pairs:
        return ''
    lines = ['Your reads on opponents:']
    for opp, obs in pairs:
        lines.append(f'- {opp}: {obs}')
    return '\n'.join(lines)
