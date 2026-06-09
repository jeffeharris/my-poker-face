"""Canonical opponent read-shaping — the single owner of the read definitions.

Lives in `poker/` (not `flask_app/`) so the strategy/expression layer can import
it without a backwards `poker -> flask_app` dependency. The flask service module
`flask_app.services.opponent_reads` re-exports these names for its existing
callers (the dossier route + the coach), so there is exactly one definition.

Two pieces live here so their definitions never drift between the
player-facing dossier, the coach, and the in-game spoken-read surfacing:

  - `reconstruct_tendencies_from_lifetime(counts)` rebuilds a full
    `OpponentTendencies` from the durable per-sandbox lifetime COUNTS
    (`opponent_observation_lifetime`), running the canonical
    `_recalculate_stats()` so the derived rates match the live in-game path
    exactly.
  - `deep_reads_from_tendencies(t)` shapes a tendency (live per-game OR
    lifetime-reconstructed) into the Tier-2 postflop "deep reads" — the
    same tells the dossier shows and the tiered bots exploit. Each rate is
    gated on its own sample counter so an unobserved read is `None` (the
    caller renders "—") rather than the model's neutral prior.
"""

from typing import Any, Dict, Optional


def reconstruct_tendencies_from_lifetime(counts: Optional[dict]):
    """Rebuild an `OpponentTendencies` from durable lifetime COUNTS.

    Sets every counter that the fold actually PERSISTS (the
    `_LIFETIME_COUNT_FIELDS` / `_LIFETIME_SUM_FIELDS` set: headline, deep
    postflop, preflop opportunity, equity sums, and the limp numerator), then
    `_recalculate_stats()` so those derived rates match the live path exactly.
    The equity polarization MEANS aren't recomputed by `_recalculate_stats`
    (the live path updates them incrementally), so they're set explicitly here
    as sum/count.

    Live-only counters that the fold does NOT persist — the flop-check-barrel
    pair and the sizing-aware bins (fold-to-big-bet, big/small equity) — stay
    at 0, so their derived rates (`flop_check_then_barrel_rate`,
    `fold_to_big_bet`, `sizing_polarization_score`) hold their neutral priors
    on a reconstructed object. `deep_reads_from_tendencies` deliberately does
    not surface those, so this is faithful for every consumer it feeds (the
    exploitation detectors behind "the read", the coach's opponent block).

    Returns None when there's no lifetime row or no hands yet.
    """
    if not counts or not counts.get('hands_observed'):
        return None

    from poker.memory.opponent_model import OpponentTendencies

    t = OpponentTendencies()
    t.hands_dealt = counts.get('hands_dealt', 0)
    t.hands_observed = counts.get('hands_observed', 0)
    t._vpip_count = counts.get('vpip_count', 0)
    t._pfr_count = counts.get('pfr_count', 0)
    t._bet_raise_count = counts.get('bet_raise_count', 0)
    t._call_count = counts.get('call_count', 0)
    t._showdowns = counts.get('showdowns_seen', 0)
    t._showdowns_won = counts.get('showdowns_won', 0)
    # Deep postflop counters.
    t._all_in_count = counts.get('all_in_count', 0)
    t._fold_to_cbet_count = counts.get('fold_to_cbet_count', 0)
    t._cbet_faced_count = counts.get('cbet_faced_count', 0)
    t._cbet_attempt_count = counts.get('cbet_attempt_count', 0)
    t._postflop_seen_as_pfr_count = counts.get('postflop_seen_as_pfr_count', 0)
    t._barrel_count = counts.get('barrel_count', 0)
    t._barrel_opportunity_count = counts.get('barrel_opportunity_count', 0)
    t._third_barrel_count = counts.get('third_barrel_count', 0)
    t._third_barrel_opportunity_count = counts.get('third_barrel_opportunity_count', 0)
    t._postflop_bet_raise_count = counts.get('postflop_bet_raise_count', 0)
    t._postflop_call_count = counts.get('postflop_call_count', 0)
    t._equity_betting_count = counts.get('equity_betting_count', 0)
    t._equity_raising_count = counts.get('equity_raising_count', 0)
    t._equity_calling_count = counts.get('equity_calling_count', 0)
    t._equity_betting_sum = counts.get('equity_betting_sum', 0.0)
    t._equity_raising_sum = counts.get('equity_raising_sum', 0.0)
    t._equity_calling_sum = counts.get('equity_calling_sum', 0.0)
    # Preflop opportunity counters — drive vpip_per_voluntary_opportunity /
    # pfr_per_open_opportunity, the signals the station/nit detectors gate on.
    t._preflop_voluntary_action_count = counts.get('preflop_voluntary_action_count', 0)
    t._preflop_voluntary_opportunities = counts.get('preflop_voluntary_opportunities', 0)
    t._preflop_open_raise_count = counts.get('preflop_open_raise_count', 0)
    t._preflop_open_opportunities = counts.get('preflop_open_opportunities', 0)
    # v132 limp counter — limp_rate derives off it + preflop_open_opportunities.
    t._limp_count = counts.get('limp_count', 0)
    # v133 sizing-aware counts + sums.
    t._equity_betting_big_count = counts.get('equity_betting_big_count', 0)
    t._equity_betting_small_count = counts.get('equity_betting_small_count', 0)
    t._fold_to_big_bet_count = counts.get('fold_to_big_bet_count', 0)
    t._big_bet_faced_count = counts.get('big_bet_faced_count', 0)
    t._equity_betting_big_sum = counts.get('equity_betting_big_sum', 0.0)
    t._equity_betting_small_sum = counts.get('equity_betting_small_sum', 0.0)
    # v134 postflop aggression-axis counters. Derive directly in
    # _recalculate_postflop_stats (no pre-recalc mean to seed), so just set them.
    t._facing_bet_opportunities = counts.get('facing_bet_opportunities', 0)
    t._all_ins_facing_bet = counts.get('all_ins_facing_bet', 0)
    t._postflop_open_opportunities = counts.get('postflop_open_opportunities', 0)
    t._postflop_jam_opens = counts.get('postflop_jam_opens', 0)
    # v135 flop-check-then-barrel counters (rate derives in _recalculate_stats).
    t._flop_check_barrel_count = counts.get('flop_check_barrel_count', 0)
    t._flop_check_barrel_opportunity_count = counts.get('flop_check_barrel_opportunity_count', 0)

    def _eq(total, n):
        return total / n if n else 0.5

    # The big/small equity MEANS must be set BEFORE _recalculate_stats, because
    # it derives sizing_polarization_score = big_mean − small_mean (gated on
    # the bin counts). The other equity means below don't feed recalc.
    t.equity_when_betting_big = _eq(t._equity_betting_big_sum, t._equity_betting_big_count)
    t.equity_when_betting_small = _eq(t._equity_betting_small_sum, t._equity_betting_small_count)

    t._recalculate_stats()

    # Means recalc doesn't touch (live path updates them incrementally).
    t.equity_when_betting_postflop = _eq(t._equity_betting_sum, t._equity_betting_count)
    t.equity_when_raising_postflop = _eq(t._equity_raising_sum, t._equity_raising_count)
    t.equity_when_calling_postflop = _eq(t._equity_calling_sum, t._equity_calling_count)
    # fold_to_big_bet is updated incrementally live (not by _recalculate_stats).
    if t._big_bet_faced_count:
        t.fold_to_big_bet = t._fold_to_big_bet_count / t._big_bet_faced_count
    return t


def deep_reads_from_tendencies(t) -> Optional[Dict[str, Any]]:
    """Shape an `OpponentTendencies` into the Tier-2 postflop "deep reads".

    Works for either a live per-game tendency (the coach's source in
    non-sandbox games) or one rebuilt from the lifetime store (the dossier's
    source). Each rate is gated on its OWN sample counter so an unobserved
    read is `None` rather than the model's neutral prior — the caller shows
    "—" instead of a misleading default. Returns None for a None tendency.
    """
    if t is None:
        return None

    from poker.memory.opponent_model import (
        SIZING_MIN_BIG_BET_FACED,
        SIZING_MIN_BIN_SAMPLE,
    )

    def _mean(total, n):
        return round(total / n, 2) if n else None

    # Sizing tells gate on their own bin samples. sizing_polarization needs
    # BOTH equity bins matured (it's big_mean − small_mean); fold_to_big_bet
    # needs enough big bets faced to mean something.
    sizing_ready = (
        t._equity_betting_big_count >= SIZING_MIN_BIN_SAMPLE
        and t._equity_betting_small_count >= SIZING_MIN_BIN_SAMPLE
    )

    return {
        'fold_to_cbet': (round(t.fold_to_cbet, 2) if t._cbet_faced_count else None),
        'cbet_attempt_rate': (
            round(t.cbet_attempt_rate, 2) if t._postflop_seen_as_pfr_count else None
        ),
        'barrel_frequency': (round(t.barrel_frequency, 2) if t._barrel_opportunity_count else None),
        'third_barrel_frequency': (
            round(t.third_barrel_frequency, 2) if t._third_barrel_opportunity_count else None
        ),
        # all-in freq uses hands_dealt as denominator; 0% is a legitimate read,
        # so it's never None once there are hands.
        'all_in_frequency': round(t.all_in_frequency, 3),
        'aggression_factor_postflop': (
            round(t.aggression_factor_postflop, 2)
            if (t._postflop_bet_raise_count or t._postflop_call_count)
            else None
        ),
        # Limp rate (v132) — over open spots. None until an open spot is seen.
        'limp_rate': (round(t.limp_rate, 2) if t._preflop_open_opportunities else None),
        # Showdown win rate — already tracked/persisted; None until a showdown.
        'showdown_win_rate': (round(t.showdown_win_rate, 2) if t._showdowns else None),
        # Sizing tells (v133). polarization > 0 ⇒ bets bigger with stronger
        # hands (face-up); fold_to_big_bet high ⇒ over-folds to overbets.
        'sizing_polarization_score': (
            round(t.sizing_polarization_score, 2) if sizing_ready else None
        ),
        'fold_to_big_bet': (
            round(t.fold_to_big_bet, 2)
            if t._big_bet_faced_count >= SIZING_MIN_BIG_BET_FACED
            else None
        ),
        # Postflop aggression axes (v134). None until an opportunity is seen;
        # 0.0 (had chances, never jammed) is a legitimate read once observed.
        'all_in_per_facing_bet': (
            round(t.all_in_per_facing_bet, 2) if t._facing_bet_opportunities else None
        ),
        'postflop_jam_open_rate': (
            round(t.postflop_jam_open_rate, 2) if t._postflop_open_opportunities else None
        ),
        # Trap read (v135): checks flop OOP then bets turn after a check-through.
        'flop_check_then_barrel_rate': (
            round(t.flop_check_then_barrel_rate, 2)
            if t._flop_check_barrel_opportunity_count
            else None
        ),
        # Polarization: mean equity the opponent held at each action type.
        'equity_when_betting': _mean(t._equity_betting_sum, t._equity_betting_count),
        'equity_when_raising': _mean(t._equity_raising_sum, t._equity_raising_count),
        'equity_when_calling': _mean(t._equity_calling_sum, t._equity_calling_count),
    }
