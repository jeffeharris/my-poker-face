"""
Opponent Modeling System.

Tracks opponent tendencies and memorable hands for AI learning.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, List, Dict, Optional, Any, Tuple

from ..archetypes import (
    VPIP_TIGHT as VPIP_TIGHT_THRESHOLD,
    VPIP_LOOSE as VPIP_LOOSE_THRESHOLD,
    VPIP_VERY_SELECTIVE,
    AF_AGGRESSIVE as AGGRESSION_FACTOR_HIGH,
    AF_VERY_AGGRESSIVE as AGGRESSION_FACTOR_VERY_HIGH,
    AF_PASSIVE as AGGRESSION_FACTOR_LOW,
)
from ..config import (
    OPPONENT_SUMMARY_TOKENS,
    MEMORABLE_HAND_THRESHOLD,
    MIN_HANDS_FOR_STYLE_LABEL,
    MIN_HANDS_FOR_SUMMARY,
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


@dataclass
class OpponentTendencies:
    """Statistical model of an opponent's play style."""
    hands_observed: int = 0     # Hands where opponent took at least one action

    # Hands the opponent was at the table — regardless of whether they
    # ever acted. This is the correct denominator for VPIP/PFR/all_in_
    # frequency: folding before action reaches you is a relevant outcome
    # ("opted out of pot"), not an unobserved one. When hands_dealt is 0,
    # ratio calculations fall back to hands_observed (preserves behavior
    # for callers that don't call record_hand_dealt yet).
    hands_dealt: int = 0

    # Core stats
    vpip: float = 0.5           # Voluntarily put in pot % (how often they enter pots)
    pfr: float = 0.5            # Pre-flop raise % (how often they raise pre-flop)
    aggression_factor: float = 1.0  # (bet+raise+all-in) / call ratio
    fold_to_cbet: float = 0.5   # Fold to continuation bet %
    cbet_attempt_rate: float = 0.5  # Phase 8.1a: PFR's c-bet attempt rate
    bluff_frequency: float = 0.3    # Estimated bluff rate
    showdown_win_rate: float = 0.5  # Win rate at showdown
    all_in_frequency: float = 0.0   # All-in actions per hand dealt

    # Phase 7.5 Step 0: opportunity-normalized stats for the three-tier
    # exploitation clamp. Computed from new postflop-only counters
    # (below). Default to 0.0 / 0 when no opportunities observed.
    #
    # See docs/plans/PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md "Stat-definition
    # glossary" for exact denominators.
    aggression_factor_postflop: float = 1.0   # postflop bet/raise/all-in / postflop call
    all_in_per_facing_bet: float = 0.0        # response-aggression axis
    postflop_jam_open_rate: float = 0.0       # open-aggression axis

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

    # Trend tracking
    recent_trend: str = 'stable'    # 'tightening', 'loosening', 'stable'

    # Action counters (for calculating stats)
    _vpip_count: int = 0        # Hands where player voluntarily put money in pot
    _pfr_count: int = 0         # Hands where player raised pre-flop
    _bet_raise_count: int = 0   # Total bets, raises, and all-ins (aggressive)
    _call_count: int = 0        # Total calls
    _all_in_count: int = 0      # Total all-in actions (subset of _bet_raise_count)
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
    _showdowns: int = 0
    _showdowns_won: int = 0

    # Phase 7.5 Step 0: per-axis counters for the new postflop-only stats.
    # Updated only when phase is FLOP/TURN/RIVER. See
    # `_apply_postflop_counters()` for the logic.
    _postflop_bet_raise_count: int = 0   # postflop bets/raises/all-ins
    _postflop_call_count: int = 0        # postflop calls
    _facing_bet_opportunities: int = 0   # postflop decisions while facing a bet
    _all_ins_facing_bet: int = 0         # subset: opponent went all-in in response
    _postflop_open_opportunities: int = 0  # postflop decisions with no live bet (legal bet/all-in available)
    _postflop_jam_opens: int = 0           # subset: opponent went all-in into no-bet pot

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

    # Per-hand opportunity flags (reset on new hand, mirror _vpip_this_hand /
    # _pfr_this_hand).
    _preflop_voluntary_opp_this_hand: bool = False
    _preflop_open_opp_this_hand: bool = False
    _preflop_open_raised_this_hand: bool = False
    _preflop_vol_action_this_hand: bool = False

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
        self._recalculate_stats()

    def update_from_action(
        self, action: str, phase: str, is_voluntary: bool = True,
        count_hand: bool = True, was_facing_bet: Optional[bool] = None,
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
        if (
            phase == 'PRE_FLOP'
            and is_voluntary
            and was_facing_bet is not None
        ):
            self._apply_preflop_opportunity_counters(action, was_facing_bet)

        # Recalculate stats
        self._recalculate_stats()

    def _apply_preflop_opportunity_counters(
        self, action: str, was_facing_bet: bool,
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
        if (
            action in ('call', 'raise', 'bet', 'all_in')
            and not self._preflop_vol_action_this_hand
        ):
            self._preflop_voluntary_action_count += 1
            self._preflop_vol_action_this_hand = True
        if (
            action in ('raise', 'all_in')
            and not was_facing_bet
            and not self._preflop_open_raised_this_hand
        ):
            self._preflop_open_raise_count += 1
            self._preflop_open_raised_this_hand = True

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

        recent_aipfb = (
            all_ins_facing_bet / facing_bet_opps if facing_bet_opps > 0 else 0.0
        )
        recent_jam_open = (
            jam_opens / open_opps if open_opps > 0 else 0.0
        )

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
            self.cbet_attempt_rate = (
                self._cbet_attempt_count / self._postflop_seen_as_pfr_count
            )

        if self._showdowns > 0:
            self.showdown_win_rate = self._showdowns_won / self._showdowns

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
                self._preflop_open_raise_count
                / self._preflop_open_opportunities
            )
        if self._preflop_voluntary_opportunities > 0:
            self.vpip_per_voluntary_opportunity = (
                self._preflop_voluntary_action_count
                / self._preflop_voluntary_opportunities
            )

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
                    float(self._postflop_bet_raise_count), cap,
                )
        else:
            self.aggression_factor_postflop = (
                self._postflop_bet_raise_count / self._postflop_call_count
            )

        # Response-aggression axis: all-ins per facing-bet opportunity.
        if self._facing_bet_opportunities > 0:
            self.all_in_per_facing_bet = (
                self._all_ins_facing_bet / self._facing_bet_opportunities
            )
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

        is_tight = self.vpip < VPIP_TIGHT_THRESHOLD
        is_aggressive = self.aggression_factor > AGGRESSION_FACTOR_HIGH

        if is_tight and is_aggressive:
            return 'tight-aggressive'
        elif not is_tight and is_aggressive:
            return 'loose-aggressive'
        elif is_tight and not is_aggressive:
            return 'tight-passive'
        else:
            return 'loose-passive'

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

        return ", ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hands_observed': self.hands_observed,
            'hands_dealt': self.hands_dealt,
            'vpip': self.vpip,
            'pfr': self.pfr,
            'aggression_factor': self.aggression_factor,
            'fold_to_cbet': self.fold_to_cbet,
            'cbet_attempt_rate': self.cbet_attempt_rate,
            'bluff_frequency': self.bluff_frequency,
            'showdown_win_rate': self.showdown_win_rate,
            'all_in_frequency': self.all_in_frequency,
            # Phase 7.5 derived stats
            'aggression_factor_postflop': self.aggression_factor_postflop,
            'all_in_per_facing_bet': self.all_in_per_facing_bet,
            'postflop_jam_open_rate': self.postflop_jam_open_rate,
            # Opportunity-normalized preflop stats
            'pfr_per_open_opportunity': self.pfr_per_open_opportunity,
            'vpip_per_voluntary_opportunity': self.vpip_per_voluntary_opportunity,
            'recent_trend': self.recent_trend,
            '_vpip_count': self._vpip_count,
            '_pfr_count': self._pfr_count,
            '_bet_raise_count': self._bet_raise_count,
            '_call_count': self._call_count,
            '_all_in_count': self._all_in_count,
            '_fold_to_cbet_count': self._fold_to_cbet_count,
            '_cbet_faced_count': self._cbet_faced_count,
            # Phase 8.1a counters
            '_cbet_attempt_count': self._cbet_attempt_count,
            '_postflop_seen_as_pfr_count': self._postflop_seen_as_pfr_count,
            '_showdowns': self._showdowns,
            '_showdowns_won': self._showdowns_won,
            # Phase 7.5 counters
            '_postflop_bet_raise_count': self._postflop_bet_raise_count,
            '_postflop_call_count': self._postflop_call_count,
            '_facing_bet_opportunities': self._facing_bet_opportunities,
            '_all_ins_facing_bet': self._all_ins_facing_bet,
            '_postflop_open_opportunities': self._postflop_open_opportunities,
            '_postflop_jam_opens': self._postflop_jam_opens,
            # Opportunity-normalized preflop counters
            '_preflop_voluntary_opportunities': self._preflop_voluntary_opportunities,
            '_preflop_open_opportunities': self._preflop_open_opportunities,
            '_preflop_open_raise_count': self._preflop_open_raise_count,
            '_preflop_voluntary_action_count': self._preflop_voluntary_action_count,
            # Phase 7.5 Item 2b: sliding-window events (list-serialized)
            '_recent_postflop_events': list(self._recent_postflop_events),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OpponentTendencies':
        """Deserialize from dict. Missing Phase 7.5 fields default to 0 /
        0.0 — older records that predate Phase 7.5 just lose their
        accumulated history for the new axes, which is the intended
        behavior (data wasn't captured before)."""
        tendencies = cls(
            hands_observed=data.get('hands_observed', 0),
            hands_dealt=data.get('hands_dealt', 0),
            vpip=data.get('vpip', 0.5),
            pfr=data.get('pfr', 0.5),
            aggression_factor=data.get('aggression_factor', 1.0),
            fold_to_cbet=data.get('fold_to_cbet', 0.5),
            cbet_attempt_rate=data.get('cbet_attempt_rate', 0.5),
            bluff_frequency=data.get('bluff_frequency', 0.3),
            showdown_win_rate=data.get('showdown_win_rate', 0.5),
            all_in_frequency=data.get('all_in_frequency', 0.0),
            # Phase 7.5 derived defaults (0.0 / 1.0 — neutral)
            aggression_factor_postflop=data.get('aggression_factor_postflop', 1.0),
            all_in_per_facing_bet=data.get('all_in_per_facing_bet', 0.0),
            postflop_jam_open_rate=data.get('postflop_jam_open_rate', 0.0),
            # Opportunity-normalized preflop stats (neutral prior 0.5).
            pfr_per_open_opportunity=data.get('pfr_per_open_opportunity', 0.5),
            vpip_per_voluntary_opportunity=data.get(
                'vpip_per_voluntary_opportunity', 0.5,
            ),
            recent_trend=data.get('recent_trend', 'stable')
        )
        tendencies._vpip_count = data.get('_vpip_count', 0)
        tendencies._pfr_count = data.get('_pfr_count', 0)
        tendencies._bet_raise_count = data.get('_bet_raise_count', 0)
        tendencies._call_count = data.get('_call_count', 0)
        tendencies._all_in_count = data.get('_all_in_count', 0)
        tendencies._fold_to_cbet_count = data.get('_fold_to_cbet_count', 0)
        tendencies._cbet_faced_count = data.get('_cbet_faced_count', 0)
        # Phase 8.1a counters — default 0 for migration tolerance.
        tendencies._cbet_attempt_count = data.get('_cbet_attempt_count', 0)
        tendencies._postflop_seen_as_pfr_count = data.get('_postflop_seen_as_pfr_count', 0)
        tendencies._showdowns = data.get('_showdowns', 0)
        tendencies._showdowns_won = data.get('_showdowns_won', 0)
        # Phase 7.5 counter defaults (0 — missing-field tolerance)
        tendencies._postflop_bet_raise_count = data.get('_postflop_bet_raise_count', 0)
        tendencies._postflop_call_count = data.get('_postflop_call_count', 0)
        tendencies._facing_bet_opportunities = data.get('_facing_bet_opportunities', 0)
        tendencies._all_ins_facing_bet = data.get('_all_ins_facing_bet', 0)
        tendencies._postflop_open_opportunities = data.get('_postflop_open_opportunities', 0)
        tendencies._postflop_jam_opens = data.get('_postflop_jam_opens', 0)
        # Opportunity-normalized preflop counter defaults (missing-field tolerance).
        tendencies._preflop_voluntary_opportunities = data.get(
            '_preflop_voluntary_opportunities', 0,
        )
        tendencies._preflop_open_opportunities = data.get(
            '_preflop_open_opportunities', 0,
        )
        tendencies._preflop_open_raise_count = data.get(
            '_preflop_open_raise_count', 0,
        )
        tendencies._preflop_voluntary_action_count = data.get(
            '_preflop_voluntary_action_count', 0,
        )
        # Phase 7.5 Item 2b: restore sliding-window events. Old records
        # without this field get an empty window — the tier-decay logic
        # treats sub-threshold windows as "no recent data," falling back
        # to cumulative tier, which is the right behavior for migrated
        # records (no recent samples to overrule cumulative).
        recent_events_raw = data.get('_recent_postflop_events', [])
        # Coerce dicts/lists back into tuples; deque maxlen comes from
        # the current config so a saved record + new config combine
        # correctly.
        recent_events = [
            (action, bool(facing))
            for action, facing in recent_events_raw
        ]
        tendencies._recent_postflop_events = deque(
            recent_events, maxlen=_load_window_size(),
        )
        return tendencies


@dataclass
class MemorableHand:
    """A specific hand worth remembering."""
    hand_id: int
    memory_type: str          # 'bluff_caught', 'hero_call', 'big_loss', 'bad_beat', etc.
    opponent_name: str
    impact_score: float       # 0-1, how memorable
    narrative: str            # AI-generated or template description
    hand_summary: str         # Brief summary of what happened
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hand_id': self.hand_id,
            'memory_type': self.memory_type,
            'opponent_name': self.opponent_name,
            'impact_score': self.impact_score,
            'narrative': self.narrative,
            'hand_summary': self.hand_summary,
            'timestamp': self.timestamp.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MemorableHand':
        return cls(
            hand_id=data['hand_id'],
            memory_type=data['memory_type'],
            opponent_name=data['opponent_name'],
            impact_score=data['impact_score'],
            narrative=data['narrative'],
            hand_summary=data['hand_summary'],
            timestamp=datetime.fromisoformat(data['timestamp']) if isinstance(data['timestamp'], str) else data['timestamp']
        )


class OpponentModel:
    """Tracks observations about a specific opponent.

    Combines statistical tendencies with AI-generated narrative observations
    for richer opponent modeling.
    """

    def __init__(self, observer: str, opponent: str,
                 observer_id: Optional[str] = None,
                 opponent_id: Optional[str] = None):
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

    def observe_action(self, action: str, phase: str, is_voluntary: bool = True,
                      hand_number: int = None, was_facing_bet: Optional[bool] = None):
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
            action, phase, is_voluntary,
            count_hand=new_hand, was_facing_bet=was_facing_bet,
        )

    def observe_showdown(self, won: bool, bluffed: bool = False):
        """Record a showdown observation."""
        self.tendencies.update_showdown(won)
        if bluffed and not won:
            # Caught bluffing - update bluff frequency estimate
            current_bluffs = self.tendencies.bluff_frequency * self.tendencies._showdowns
            self.tendencies.bluff_frequency = (current_bluffs + 1) / max(self.tendencies._showdowns, 1)

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

    def add_memorable_hand(self, hand_id: int, memory_type: str,
                          impact_score: float, narrative: str, hand_summary: str):
        """Add a memorable hand if impact is high enough."""
        if impact_score >= MEMORABLE_HAND_THRESHOLD:
            self.memorable_hands.append(MemorableHand(
                hand_id=hand_id,
                memory_type=memory_type,
                opponent_name=self.opponent,
                impact_score=impact_score,
                narrative=narrative,
                hand_summary=hand_summary
            ))
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
                result = f"{self.opponent}: {self.tendencies.get_play_style_label()}. Notes: {narrative}"
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
    )


def _build_aggregate_from_multi(tendencies_list):
    """Build AggregatedOpponentStats by aggregating multiple tendencies.

    Float rate fields use equal-weight average across the list. Sample
    counters (hands_observed, cbet_faced_count, facing_bet_opportunities,
    postflop_open_opportunities) use MIN — the limiting factor for
    exploit confidence. Matches the 6.7a aggregator's policy explicitly
    (see plan §"aggregation policy").
    """
    from poker.strategy.exploitation import AggregatedOpponentStats

    if not tendencies_list:
        return AggregatedOpponentStats()

    n = len(tendencies_list)
    avg_vpip = sum(t.vpip for t in tendencies_list) / n
    avg_pfr = sum(t.pfr for t in tendencies_list) / n
    avg_af = sum(t.aggression_factor for t in tendencies_list) / n
    avg_all_in = sum(t.all_in_frequency for t in tendencies_list) / n
    avg_fold_to_cbet = sum(t.fold_to_cbet for t in tendencies_list) / n
    min_hands = min(t.hands_observed for t in tendencies_list)
    min_cbet_faced = min(t._cbet_faced_count for t in tendencies_list)

    # Phase 7.5 Step 0 fields
    avg_af_postflop = sum(t.aggression_factor_postflop for t in tendencies_list) / n
    avg_all_in_pfb = sum(t.all_in_per_facing_bet for t in tendencies_list) / n
    avg_jam_open = sum(t.postflop_jam_open_rate for t in tendencies_list) / n
    min_facing_bet_opps = min(t._facing_bet_opportunities for t in tendencies_list)
    min_open_opps = min(t._postflop_open_opportunities for t in tendencies_list)

    # Opportunity-normalized preflop fields — same policy as Phase 7.5:
    # rates average, counters MIN (limiting factor for confidence).
    avg_pfr_per_open = sum(
        t.pfr_per_open_opportunity for t in tendencies_list
    ) / n
    avg_vpip_per_vol = sum(
        t.vpip_per_voluntary_opportunity for t in tendencies_list
    ) / n
    min_pre_open_opps = min(
        t._preflop_open_opportunities for t in tendencies_list
    )
    min_pre_vol_opps = min(
        t._preflop_voluntary_opportunities for t in tendencies_list
    )

    return AggregatedOpponentStats(
        hands_observed=min_hands,
        vpip=avg_vpip,
        pfr=avg_pfr,
        aggression_factor=avg_af,
        all_in_frequency=avg_all_in,
        fold_to_cbet=avg_fold_to_cbet,
        cbet_faced_count=min_cbet_faced,
        aggression_factor_postflop=avg_af_postflop,
        all_in_per_facing_bet=avg_all_in_pfb,
        facing_bet_opportunities=min_facing_bet_opps,
        postflop_jam_open_rate=avg_jam_open,
        postflop_open_opportunities=min_open_opps,
        pfr_per_open_opportunity=avg_pfr_per_open,
        vpip_per_voluntary_opportunity=avg_vpip_per_vol,
        preflop_open_opportunities=min_pre_open_opps,
        preflop_voluntary_opportunities=min_pre_vol_opps,
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

    def __init__(self):
        # observer_name -> opponent_name -> OpponentModel
        self.models: Dict[str, Dict[str, OpponentModel]] = {}
        # display_name -> personality_id (None for players without one)
        self._name_to_id: Dict[str, Optional[str]] = {}

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
                observer, opponent,
                observer_id=self._name_to_id.get(observer),
                opponent_id=self._name_to_id.get(opponent),
            )

        return self.models[observer][opponent]

    def get_model_if_exists(
        self, observer: str, opponent: str,
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

    def observe_action(self, observer: str, opponent: str, action: str,
                      phase: str, is_voluntary: bool = True, hand_number: int = None,
                      was_facing_bet: Optional[bool] = None):
        """Record an action observation.

        Args:
            was_facing_bet: Phase 7.5 Step 0 context flag. See
                OpponentTendencies.update_from_action for semantics.
                None when caller can't determine; postflop counters
                skipped in that case.
        """
        model = self.get_model(observer, opponent)
        model.observe_action(
            action, phase, is_voluntary,
            hand_number=hand_number, was_facing_bet=was_facing_bet,
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

    def get_table_summary(self, observer: str, opponents: List[str],
                         max_tokens: int = OPPONENT_SUMMARY_TOKENS) -> str:
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
            if opp in observer_models
            and observer_models[opp].tendencies.hands_observed > 0
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
                        dominant = next(
                            m for m in models_with_history if m.opponent == name
                        )
                        return _build_aggregate_from_single(dominant.tendencies)

        return _build_aggregate_from_multi(
            [m.tendencies for m in models_with_history]
        )

    def to_dict(self) -> Dict[str, Any]:
        # Back-compat shape: top-level keys are observer names. Add an
        # underscored sidecar entry for the name→id map so existing
        # consumers that index by observer name continue working,
        # while the round-trip preserves the id registry.
        result: Dict[str, Any] = {}
        for observer, opponents in self.models.items():
            result[observer] = {
                opponent: model.to_dict()
                for opponent, model in opponents.items()
            }
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
