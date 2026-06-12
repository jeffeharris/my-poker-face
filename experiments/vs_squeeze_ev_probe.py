"""EV measurement for the blind vs_squeeze over-fold (VS_SQUEEZE_DEFENSE_HANDOFF step 1).

Per docs/strategy/VS_SQUEEZE_DEFENSE_HANDOFF.md the blinds have NO squeeze-defense
range, so a sharp hero folds ~89% of squeeze spots and 100% when it's in the BB.
Step 1 of the plan is to MEASURE whether that over-fold actually leaks bb/100 BEFORE
building any defense range (feedback_measure_spot_before_building). This is that
measurement — NOT a chart build.

WHAT IT DOES
  1. Runs a squeeze-heavy 6-max field ([Maniac, Maniac, LAG, Rock, Rock] by default,
     the handoff field) with a sharp hero and RECORDS every BB/SB `vs_squeeze`
     decision the hero reaches: its actual hole cards, position, cost-to-call, and the
     pot it's folding into (dead money = open + 3-bet + blinds), read off the hero's
     `_last_pipeline_snapshot`.
  2. Prices fold (EV = 0) vs the best CALL line for each recorded spot, against a
     SWEEP of squeezer range widths — from a tight value squeeze (folding is correct)
     to a wide maniac squeeze (where the dead money is large and the squeeze is weak).
  3. Reports the leak: Σ max(0, EV_call) over spots = bb left on the table by folding
     the +EV hands; normalized by hands → a bb/100 estimate, split by squeezer width
     and by an equity-realization haircut.

EV MODEL (preflop call, forward EV from the decision point, in bb)
  fold : 0
  call : eq · (pot_bb + cost_bb) − cost_bb
  where eq = hero all-in equity vs the squeezer's CONTINUE range (eval7 Monte-Carlo),
  pot_bb is the pot the hero calls into (already holds open + 3-bet + blinds, incl.
  the hero's own posted blind as sunk), cost_bb the extra chips to call.

  A realization haircut r ∈ {1.0, 0.7} multiplies the equity term: the blind is OOP
  vs the squeezer and cannot realize raw equity (no position, can't see free cards).
  A call must clear FOLD even after the haircut to count as a real leak. This is the
  conservative knob — raw all-in equity OVERSTATES the case for defending, so the
  haircut keeps the verdict honest.

CAVEATS (read before trusting a number)
  - All-in-equity, heads-up vs the squeezer. If the opener cold-called the 3-bet the
    pot is 3-way and calling is WORSE than priced here (eq vs two ranges). The common
    case is open-folds-to-3bet → HU vs squeezer, which this assumes.
  - CALL is the floor; a 4-bet/jam defense could be better for the very top. So this
    UNDER-states the leak for premium hands. Fine — we only need to know if the
    over-fold leaks at all and roughly how much.
  - The squeezer width is SWEPT, not read from the bots. The deliverable is "leak as a
    function of how wide the squeeze is," which the reader maps to a real field.

Run: docker compose exec -T backend python3 -m experiments.vs_squeeze_ev_probe
     QUICK=1 ...  (fewer hands / seeds — fast wiring check)
"""

from __future__ import annotations

import os
import random
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Set, Tuple

import experiments.simulate_bb100 as sim
from poker.card_utils import card_to_string, normalize_card_string
from poker.hand_ranges import (
    LATE_POSITION_RANGE,
    MIDDLE_POSITION_RANGE,
    STANDARD_3BET_RANGE,
    _get_all_combos_for_hand,
    estimate_range_from_vpip,
)
from poker.tiered_bot_controller import TieredBotController

HERO = "TAG"
FIELD = ["Maniac", "Maniac", "LAG", "Rock", "Rock"]  # handoff squeeze-heavy field
SEEDS = [7, 107, 207]
HANDS_PER_SEED = 5000
BB = 100
EQ_ITERS = 20000
EQ_SEED = 1234
REALIZATION = [1.0, 0.7]  # OOP equity-realization haircuts

# Squeezer CONTINUE ranges by width. A blind facing a squeeze is up against the
# squeezer's range; how wide it is decides whether folding is correct. We sweep from
# a tight value squeeze (fold should win) to a wide maniac squeeze (where the dead
# money + weak range should make the top of the blind's range a +EV defend). Widths
# are MEASURED (combos / 1326) and printed, so the labels are self-documenting.
_VALUE_TIGHT: Set[str] = {"AA", "KK", "QQ", "JJ", "AKs", "AKo", "AQs"}
SQUEEZE_RANGES: Dict[str, Set[str]] = {
    "tight_value": _VALUE_TIGHT,
    "standard_3bet": set(STANDARD_3BET_RANGE),
    "wide": set(MIDDLE_POSITION_RANGE) | set(LATE_POSITION_RANGE),
    "maniac": estimate_range_from_vpip(0.5),
}

_EQ_CACHE: Dict[str, float] = {}


def _range_width(range_set: Set[str]) -> float:
    return sum(len(_get_all_combos_for_hand(h)) for h in range_set) / 1326.0


def _equity_vs_range(hero: List[str], range_set: Set[str], range_id: str) -> float:
    """Hero preflop all-in equity (win + ½·tie) vs one villain drawn uniformly over
    the COMBOS of range_set. Seeded Monte-Carlo, cached per (hero, range)."""
    import eval7

    key = "".join(sorted(hero)) + "|" + range_id
    if key in _EQ_CACHE:
        return _EQ_CACHE[key]

    hero_cards = [eval7.Card(normalize_card_string(c)) for c in hero]
    known = set(hero_cards)
    excluded = set(hero)
    combos: List[Tuple[str, str]] = [
        combo
        for canonical in range_set
        for combo in _get_all_combos_for_hand(canonical)
        if combo[0] not in excluded and combo[1] not in excluded
    ]
    if not combos:
        _EQ_CACHE[key] = 0.0
        return 0.0

    rng = random.Random(EQ_SEED)
    base_deck = [c for c in eval7.Deck().cards if c not in known]
    score = 0.0
    for _ in range(EQ_ITERS):
        v0, v1 = rng.choice(combos)
        villain = [
            eval7.Card(normalize_card_string(v0)),
            eval7.Card(normalize_card_string(v1)),
        ]
        deck = [c for c in base_deck if c != villain[0] and c != villain[1]]
        rng.shuffle(deck)
        board = deck[:5]
        hs = eval7.evaluate(hero_cards + board)
        vs = eval7.evaluate(villain + board)
        if hs > vs:
            score += 1.0
        elif hs == vs:
            score += 0.5
    eq = score / EQ_ITERS
    _EQ_CACHE[key] = eq
    return eq


# ── Spot recorder: wrap the hero's preflop decision, capture blind squeeze spots ──
class _Recorder:
    def __init__(self):
        self.spots: List[dict] = []
        self.hero_preflop_decisions = 0
        self.squeeze_all = 0
        self.miss_by_pos = Counter()
        self.hit_by_pos = Counter()


_REC = _Recorder()
_orig_decide = TieredBotController._get_ai_decision


def _wrapped_decide(self, message, **context):
    result = _orig_decide(self, message, **context)
    # HERO-ONLY: every seat in this field is a TieredBot, but run_6max_matchup
    # attaches an opponent_model_manager only to the hero seat. Recording all seats
    # would aggregate 6 players' blind spots and report a per-TABLE leak mislabeled
    # as per-player bb/100 (~6× too large). The manager presence isolates the hero.
    if getattr(self, "opponent_model_manager", None) is None:
        return result
    snap = getattr(self, "_last_pipeline_snapshot", {}) or {}
    if snap.get("phase") != "PRE_FLOP":
        return result
    _REC.hero_preflop_decisions += 1
    node_key = snap.get("node_key", "")
    parts = node_key.split("|")
    if len(parts) != 4 or parts[0] != "vs_squeeze":
        return result
    _scenario, position, opener_comp, hand = parts
    _REC.squeeze_all += 1
    src = snap.get("chart_lookup_source", "?")
    (_REC.hit_by_pos if src in ("hit", "squeeze_degrade") else _REC.miss_by_pos)[position] += 1
    if position not in ("BB", "SB"):
        return result  # only the blinds are the uncovered case
    # hole cards (suit-exact) straight off the live game state, by hero name
    gs = getattr(self.state_machine, "game_state", None)
    hole: List[str] = []
    if gs is not None:
        for p in gs.players:
            if p.name == self.player_name and getattr(p, "hand", None):
                hole = [card_to_string(c) for c in p.hand]
                break
    if len(hole) != 2:
        return result
    bb = snap.get("big_blind") or BB
    _REC.spots.append(
        {
            "position": position,
            "hand": hand,
            "hole": hole,
            "squeezer": opener_comp.split("_vs_")[-1],
            "cost_bb": (snap.get("cost_to_call") or 0) / bb,
            "pot_bb": (snap.get("pot_total") or 0) / bb,
            "src": src,
        }
    )
    return result


def _record_spots() -> int:
    """Run the field, fill _REC. Returns total hands run."""
    TieredBotController._get_ai_decision = _wrapped_decide
    try:
        st = sim.load_strategy_table()
        total = 0
        for seed in SEEDS:
            sim.run_6max_matchup(
                HERO,
                HANDS_PER_SEED,
                st,
                big_blind=BB,
                starting_stack=100 * BB,
                base_seed=seed,
                opponents=FIELD,
            )
            total += HANDS_PER_SEED
        return total
    finally:
        TieredBotController._get_ai_decision = _orig_decide


def _ev_call(eq: float, pot_bb: float, cost_bb: float, realization: float) -> float:
    return realization * eq * (pot_bb + cost_bb) - cost_bb


def main() -> int:
    global HANDS_PER_SEED
    if os.environ.get("HANDS"):
        HANDS_PER_SEED = int(os.environ["HANDS"])
    if os.environ.get("SEEDS"):
        SEEDS[:] = [int(s) for s in os.environ["SEEDS"].split(",")]
    if os.environ.get("QUICK"):
        del SEEDS[1:]
        HANDS_PER_SEED = 1500

    total_hands = _record_spots()
    spots = _REC.spots
    print("=" * 90)
    print("vs_squeeze OVER-FOLD — per-decision EV measurement (step 1, measure before building)")
    print(f"hero={HERO}  field={FIELD}  {len(SEEDS)}x{HANDS_PER_SEED} = {total_hands} hands")
    print("=" * 90)
    print(
        f"hero preflop decisions: {_REC.hero_preflop_decisions}   "
        f"vs_squeeze spots (all pos): {_REC.squeeze_all} "
        f"({100.0 * _REC.squeeze_all / max(1, _REC.hero_preflop_decisions):.1f}% of decisions, "
        f"{100.0 * _REC.squeeze_all / total_hands:.1f}% of hands)"
    )
    print("  chart coverage by position (hit / miss):")
    for pos in ("HJ", "CO", "BTN", "SB", "BB"):
        h, m = _REC.hit_by_pos[pos], _REC.miss_by_pos[pos]
        if h or m:
            print(f"    {pos:>3}: hit={h:>4}  miss={m:>4}  miss%={100.0 * m / (h + m):.0f}%")
    print(f"\nblind squeeze spots recorded (BB/SB w/ hole cards): {len(spots)}")
    if not spots:
        print("NO blind squeeze spots — nothing to price. (Field may not squeeze enough.)")
        return 1
    bypos = Counter(s["position"] for s in spots)
    print(f"  by position: {dict(bypos)}")
    mean_pot = sum(s["pot_bb"] for s in spots) / len(spots)
    mean_cost = sum(s["cost_bb"] for s in spots) / len(spots)
    print(f"  mean pot calling into: {mean_pot:.1f}bb   mean cost to call: {mean_cost:.1f}bb")
    print(
        f"  (folding forfeits a {mean_pot:.0f}bb pot; pot odds to call ≈ "
        f"{100.0 * mean_cost / (mean_pot + mean_cost):.0f}%)"
    )

    print("\nsqueezer range widths swept:")
    for label, rng in SQUEEZE_RANGES.items():
        print(f"    {label:14s} ~{100.0 * _range_width(rng):.0f}% of hands")

    # Price each spot vs each width × realization haircut.
    print("\n" + "=" * 90)
    print(
        "LEAK = Σ max(0, EV_call) over the recorded blind spots, in bb, then bb/100 (÷hands×100)."
    )
    print("A +EV call means folding that hand leaks; the leak is the dead money left behind.")
    print("=" * 90)
    print(
        f"  (blind squeeze spots = {100.0 * _REC.squeeze_all / total_hands:.1f}% of hands; "
        "'leak/spot' is freq-independent (bb left on table per blind-squeeze spot), "
        "'leak bb/100' = leak/spot × that frequency.)"
    )
    header = (
        f"{'squeezer width':16s}{'realize':>8s}{'+EV defends':>13s}{'defend%':>9s}"
        f"{'leak/spot':>11s}{'leak bb/100':>13s}{'mean +EV/defend':>17s}"
    )
    print(header)
    # cache equities per (spot, width)
    summary: Dict[Tuple[str, float], dict] = {}
    defend_sets: Dict[str, Counter] = defaultdict(Counter)
    for width_label, rng in SQUEEZE_RANGES.items():
        eqs = [_equity_vs_range(s["hole"], rng, width_label) for s in spots]
        for r in REALIZATION:
            n_defend = 0
            leak_bb = 0.0
            defend_ev_sum = 0.0
            for s, eq in zip(spots, eqs, strict=False):
                ev = _ev_call(eq, s["pot_bb"], s["cost_bb"], r)
                if ev > 0:
                    n_defend += 1
                    leak_bb += ev
                    defend_ev_sum += ev
                    if r == 1.0:
                        defend_sets[width_label][s["hand"]] += 1
            leak_per_100 = leak_bb / total_hands * 100.0
            leak_per_spot = leak_bb / len(spots)
            defend_pct = 100.0 * n_defend / len(spots)
            mean_ev = defend_ev_sum / n_defend if n_defend else 0.0
            summary[(width_label, r)] = {
                "n_defend": n_defend,
                "defend_pct": defend_pct,
                "leak_per_100": leak_per_100,
                "leak_per_spot": leak_per_spot,
            }
            print(
                f"{width_label:16s}{r:>8.1f}{n_defend:>13d}{defend_pct:>8.0f}%"
                f"{leak_per_spot:>11.2f}{leak_per_100:>13.2f}{mean_ev:>17.2f}"
            )

    print("\nWhat a defense range would look like (hands with +EV call at realization=1.0):")
    for width_label in SQUEEZE_RANGES:
        top = defend_sets[width_label].most_common(12)
        hands = ", ".join(f"{h}" for h, _ in top)
        print(f"    vs {width_label:14s}: {hands or '(none)'}")

    print("\nREADING IT:")
    print("  - If the leak is ~0 across all widths -> the over-fold is CORRECT; do NOT build a")
    print("    defense range (the handoff's null hypothesis; MDF is high OOP vs two raises).")
    print("  - If the leak is ~0 for tight_value but grows with width -> the leak is REAL and")
    print("    OPPONENT-DEPENDENT: fold vs a tight squeeze, defend vs a wide one. That argues for")
    print("    the EXPLOITATION-widen instrument (detect a wide squeezer), not a static range.")
    print("  - If even tight_value leaks -> a static blind squeeze-defend range is justified.")
    print("  - Compare the 0.7 realization rows: a leak that survives the OOP haircut is the")
    print("    trustworthy one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
