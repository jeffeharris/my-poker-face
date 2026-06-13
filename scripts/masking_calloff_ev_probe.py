"""Paired-CRN cash A/B for the call-off masking fix (strategy_table._is_action_legal).

Gotcha #2: _mask_and_renormalize deleted a chart's `call` mass in a call-off spot
(legal = ['fold','all_in'], no flat 'call') and renormalized toward fold — while a
chart `raise` survived (maps to all_in). The fix treats a chart `call` as legal when
'all_in' is legal (it resolves to the all_in call-off downstream).

Design (avoids the same-seed desync trap, reference_cash_sim_ab_paired):
  * Per-hand paired CRN. Each hand is independent — fresh stacks, fresh controllers
    seeded by hand_seed, deterministic deck. We run the SAME hand twice: arm OLD
    (_is_action_legal = pre-fix) and arm NEW (post-fix). Identical seeds => common
    random numbers; the ONLY difference is the masking rule.
  * No opponent_manager (no cross-hand adaptation) so hands stay fully independent
    and CRN-clean. The exploitation layer no-ops without a manager — irrelevant here.
  * Per-seat metric: sum(delta_NEW - delta_OLD) over hands. A seat that never hits a
    divergence plays identically in both arms => contributes exactly 0 (CRN). A seat
    that folded a call-off under OLD but calls off under NEW accumulates the realized
    EV. Both arms run out the SAME deck, so an all-in divergence resolves cleanly (no
    future-street desync) — the per-hand delta IS the call-off EV.
  * Headline = per-seat bb/100 (sum_diff / n_hands / bb * 100) + the divergence
    fire-rate (how often a real cash decision hit the leak).

Run: docker compose exec -T backend python < scripts/masking_calloff_ev_probe.py
"""

import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, '/app')

from experiments.simulate_bb100 import (
    ARCHETYPES,
    make_controller,
    make_game_state,
    run_hand,
)
from poker.card_utils import card_to_string
from poker.controllers import calculate_quick_equity
from poker.poker_state_machine import PokerStateMachine
from poker.strategy import strategy_table as st
from poker.strategy.strategy_table import load_strategy_table

N_HANDS = int(os.environ.get('PROBE_HANDS', '15000'))
BASE_SEED = 4242
BIG_BLIND = 100
STARTING_STACK = 10000  # 100 BB

# A realistic cash field (prod cash = all tiered): a couple regs, an aggro, and the
# recreational tiers (fish / stations) that get into committed call-off spots.
FIELD = ['TAG', 'LAG', 'Maniac', 'WeakFish', 'StationPBlind', 'Nit']

strategy_table = load_strategy_table()

# ── The two masking implementations ──────────────────────────────────────────
_NEW_IS_LEGAL = st._is_action_legal  # current (post-fix) module function


def _old_is_action_legal(action, legal_actions):
    """Pre-fix _is_action_legal: 'call' legal only if literally in legal_actions."""
    if action in ('fold', 'check', 'call'):
        return action in legal_actions
    if action in st._RAISE_ACTIONS:
        return 'raise' in legal_actions or 'all_in' in legal_actions
    if action == st._JAM_ACTION:
        return 'all_in' in legal_actions
    if action.startswith(('bet_', 'raise_')):
        return 'raise' in legal_actions or 'all_in' in legal_actions
    return False


# ── Divergence instrumentation (runs in the NEW arm) ─────────────────────────
diverge = {
    'decisions': 0,  # total tiered decisions seen
    'calloff_spots': 0,  # facing a call-off (all_in legal, call not)
    'kept_call_mass': 0,  # call-off spot where base_strategy kept call (OLD would delete)
    'resolved_calloff': 0,  # of those, the seat actually continued (all_in) this run
    'math_floor_covered': 0,  # of kept_call_mass, math_floor already mandated continue (fix #1 covers)
}
# Frequency-weighted EV of the fix, priced at EVERY postflop kept-call-mass spot
# (low variance — uses all ~400 spots, not just realized all-ins). The masking
# fix shifts continue-probability by Δp = continue_NEW - continue_OLD on the base
# distribution (before downstream floors, which only AMPLIFY the gap since OLD has
# no 'call' key for defense_floor to pump — so this UNDER-counts the benefit). The
# call-off EV relative to folding is equity*(pot+stack) - stack. ΔEV = Δp*ev_chips.
#   base = {call:c, fold:f}           : continue_OLD=0,        continue_NEW=c
#   base = {call:c, raise:r, fold:f}  : continue_OLD=r/(r+f),  continue_NEW=c+r
ev = {'n': 0, 'sum_bb': 0.0, 'pos': 0, 'neg': 0, 'evs_bb': [], 'dev_bb': []}
_samples = []


def _continue_probs(base):
    """(continue_OLD, continue_NEW) from the post-new-mask base distribution."""
    c = base.get('call', 0.0)
    f = base.get('fold', 0.0)
    r = sum(p for a, p in base.items() if a not in ('call', 'fold'))  # raise/jam mass
    continue_new = c + r
    continue_old = (r / (r + f)) if (r + f) > 0 else 0.0  # OLD deletes call, renorms
    return continue_old, continue_new


def _observer(current_player, controller, action, raise_to, phase_name, gs, street, decision):
    snap = getattr(controller, '_last_pipeline_snapshot', None)
    if not snap:
        return
    diverge['decisions'] += 1
    legal = snap.get('legal_actions') or []
    if 'all_in' not in legal or 'call' in legal:
        return
    diverge['calloff_spots'] += 1
    base = snap.get('base_strategy_probs') or {}
    if base.get('call', 0.0) <= 0.0:
        return
    # NEW masking kept call mass that OLD masking would have deleted -> divergence.
    diverge['kept_call_mass'] += 1
    resolved = snap.get('resolved_action')
    if resolved == 'all_in':
        diverge['resolved_calloff'] += 1
    trace = getattr(controller, '_last_intervention_trace', None) or []
    mf_fired = any(
        getattr(t, 'layer', None) == 'math_floor' and getattr(t, 'fired', False) for t in trace
    )
    if mf_fired:
        diverge['math_floor_covered'] += 1

    # ── Frequency-weighted EV pricing (postflop spots) ──
    board = list(gs.community_cards or [])
    priced = None
    if board:
        hole = [card_to_string(c) for c in current_player.hand]
        board_str = [card_to_string(c) for c in board]
        n_opp = max(1, sum(1 for p in gs.players if not getattr(p, 'is_folded', False)) - 1)
        equity = calculate_quick_equity(hole, board_str, num_simulations=400, num_opponents=n_opp)
        pot = snap.get('pot_total') or 0
        stack = snap.get('player_stack') or 0  # call-off amount (cost >= stack)
        if equity is not None and stack > 0:
            ev_chips = equity * (pot + stack) - stack  # call-off value vs folding
            c_old, c_new = _continue_probs(base)
            dev_bb = (c_new - c_old) * ev_chips / BIG_BLIND  # frequency-weighted ΔEV
            ev['n'] += 1
            ev['sum_bb'] += dev_bb
            ev['dev_bb'].append(dev_bb)
            ev['evs_bb'].append(ev_chips / BIG_BLIND)
            ev['pos' if ev_chips >= 0 else 'neg'] += 1
            priced = (round(equity, 3), round(ev_chips / BIG_BLIND, 1), round(dev_bb, 2))

    if len(_samples) < 14:
        _samples.append(
            {
                'seat': current_player.name,
                'street': phase_name,
                'base_call': round(base.get('call', 0.0), 3),
                'base_fold': round(base.get('fold', 0.0), 3),
                'resolved': resolved,
                'pot': snap.get('pot_total'),
                'stack': snap.get('player_stack'),
                'mf_fired': mf_fired,
                'eq/ev_bb': priced,
            }
        )


def _run_arm(is_legal_impl, observer):
    """Run the full N-hand field with a given masking impl. Returns name->total delta."""
    st._is_action_legal = is_legal_impl
    totals = defaultdict(float)
    names = list(FIELD)
    for hand_num in range(N_HANDS):
        hand_seed = BASE_SEED + hand_num
        dealer_idx = hand_num % len(names)
        random.seed(hand_seed)  # both arms start each hand from the same global RNG
        gs = make_game_state(
            player_names=names,
            big_blind=BIG_BLIND,
            starting_stack=STARTING_STACK,
            dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)
        sm.current_hand_seed = hand_seed
        controllers = [
            make_controller(
                n, ARCHETYPES[n], strategy_table, sm, rng_seed=hand_seed + i * 1_000_000
            )
            for i, n in enumerate(names)
        ]
        final = run_hand(
            sm,
            controllers,
            BIG_BLIND,
            equity_seed=hand_seed,
            decision_observer=observer,
        )
        for n in names:
            totals[n] += final.get(n, STARTING_STACK) - STARTING_STACK
    return totals


def main():
    print(f"Paired-CRN call-off masking A/B  |  {N_HANDS} hands x 2 arms  |  field={FIELD}")
    print("=" * 78)

    # Arm OLD first (no observer), then NEW (with divergence observer).
    old_totals = _run_arm(_old_is_action_legal, None)
    new_totals = _run_arm(_NEW_IS_LEGAL, _observer)
    st._is_action_legal = _NEW_IS_LEGAL  # leave module in the shipped state

    print("\nPer-seat paired delta (NEW - OLD), positive = fix earns the seat chips:")
    print(f"  {'seat':<16}{'OLD bb/100':>12}{'NEW bb/100':>12}{'Δ bb/100':>12}")
    net = 0.0
    for n in FIELD:
        old_bb = old_totals[n] / N_HANDS / BIG_BLIND * 100
        new_bb = new_totals[n] / N_HANDS / BIG_BLIND * 100
        d = new_bb - old_bb
        net += d
        print(f"  {n:<16}{old_bb:>12.3f}{new_bb:>12.3f}{d:>+12.3f}")
    print(f"  {'(sum, ~0 zero-sum)':<16}{'':>12}{'':>12}{net:>+12.3f}")

    d = diverge
    print("\nDivergence fire-rate (NEW arm):")
    print(f"  tiered decisions seen        : {d['decisions']}")
    print(
        f"  call-off spots (all_in,no call): {d['calloff_spots']}"
        f"  ({100*d['calloff_spots']/max(d['decisions'],1):.3f}% of decisions)"
    )
    print(
        f"  ...kept call mass (leak bit)  : {d['kept_call_mass']}"
        f"  ({100*d['kept_call_mass']/max(d['decisions'],1):.4f}% of decisions)"
    )
    print(f"     ...resolved to call-off    : {d['resolved_calloff']}")
    print(f"     ...already covered by math_floor (fix #1): {d['math_floor_covered']}")
    incremental = d['kept_call_mass'] - d['math_floor_covered']
    print(f"     ...INCREMENTAL to gotcha #2 (not math_floor): {incremental}")

    print("\nFrequency-weighted call-off EV of the fix (postflop kept-call-mass spots):")
    if ev['n']:
        evs = sorted(ev['evs_bb'])  # raw call-off EV (equity-priced), all spots
        print(f"  priced spots                 : {ev['n']}")
        print(
            f"  call-off itself +EV / -EV    : {ev['pos']} / {ev['neg']}"
            f"  (raw equity*(pot+stack)-stack)"
        )
        print(
            f"  raw call-off EV  median/mean : {evs[len(evs)//2]:+.1f} / {sum(evs)/len(evs):+.1f} BB"
        )
        print(f"  raw call-off EV  min / max   : {evs[0]:+.1f} / {evs[-1]:+.1f} BB")
        print(
            f"  ΔEV of fix (freq-weighted)   : {ev['sum_bb']:+.1f} BB total"
            f"  ({ev['sum_bb']/N_HANDS*100:+.3f} BB/100, summed across all 6 seats)"
        )
        print(f"  ΔEV mean per spot            : {ev['sum_bb']/ev['n']:+.2f} BB")
    else:
        print("  (no priced spots)")

    if _samples:
        print("\nSample divergence spots:")
        for s in _samples:
            print(f"  {s}")


if __name__ == '__main__':
    main()
