"""Behavioral ceiling probe for `_apply_relationship_modifier_to_offsets`.

The relationship modifier (ON by default) scales the pattern-derived
exploitation `offsets` dict by a per-target multiplier before the clamp:
  - rival   (heat>0.5)      → bluff_freq_mult 1.3  (chase rivals harder)
  - respect (>0.7)          → fold_to_pressure_mult 0.7
  - friend  (likability>0.7)→ bluff_freq_mult 0.85 (soft on friends)

Two facts make this layer suspect (matrix doc: "ON by default, never
EV-measured"):
  1. It is a STRUCTURAL no-op in every existing sim harness:
     `simulate_bb100` sets `apply_relationship_modifier = False` (sims don't
     seed relationship_states); `exploit_bb100` attaches a bare
     OpponentModelManager with no `_relationship_repo`. So it cannot be
     EV-measured without a seeded-heat bed.
  2. It scales the SAME additive-logit `offsets` the exploit re-architecture
     proved behaviorally inert (bluff 59.9%→59.8% even at full intensity —
     the reason hard-overrides were added). It does NOT touch the
     hard-override channel (`_maybe_stop_bluff_override`) or value-vs-station
     intensity.

This probe measures the BEHAVIORAL CEILING: force the strongest possible
modifier (max-rival: aggressive offsets ×1.3, fold ×0.7) on every decision,
bypassing the repo/heat machinery, and ask whether the sampled aggression
moves at all. If the ceiling is ~0, the layer is inert-by-channel — it rides
the dead nudge, so the "never EV-measured" gap is closeable by inspection:
there is no EV to measure until it is rebuilt on the hard-override channel.

NB: `simulate_bb100`'s controller factory hard-sets the flag False on the
INSTANCE (~L550), overriding any class-level True — so the probe forces it
True per-decision in the hero wrapper. Without that, the modifier runs 0× and
emits a clean-looking 0.0pp that is theater, not a measurement. The SCALE_STATS
call-counter exists to catch exactly that (confirm it FIRES before trusting a
null).

Arms, TAG hero vs CallStation HU (exploitation ON, strength 1.0):
  - OFF        : modifier seam returns offsets unchanged (identity)
  - ON+RIVAL   : aggressive offsets ×1.3, fold-offset magnitude ×0.7
  - ON+FRIEND  : aggressive offsets ×0.85 (soft-on-friends ceiling)

Result (2026-06-12): 11,886 engagements, 21,494 offsets scaled, 0.0pp behavior
change → inert-by-channel. See docs/guides/STRATEGY_LAYER_VALIDATION.md.

Run: docker compose exec -T backend python -m experiments.relationship_modifier_probe
"""

import os
import sys
from collections import Counter

import experiments.simulate_bb100 as sim
from poker.tiered_bot_controller import TieredBotController

HANDS = 2500
SEED = 42
# Opponent that POPULATES the additive offsets dict the modifier scales.
# CallStation is the right bed: the exploitation rules emit positive
# aggressive offsets on 75.9% of decisions there (value-vs-station +
# bluff-reduction deltas), so the rival multiplier (×1.3) has plenty to
# scale. FoldyBot, counterintuitively, emits ZERO additive offsets HU
# (its c-bet exploit rides other channels) → the modifier never engages,
# a vacuous bed. Override with RM_OPPONENT only to spot-check.
OPPONENT = os.environ.get("RM_OPPONENT", "CallStation")
AGGRO = {"bet", "raise", "all_in"}

# Strongest modifiers the v1 axis→modifier mapping can ever emit.
RIVAL = dict(bluff_freq_mult=1.3, fold_to_pressure_mult=0.7)
FRIEND = dict(bluff_freq_mult=0.85, fold_to_pressure_mult=1.0)


# Tallies what the modifier actually had to work with (guards a vacuous
# test: if offsets never carry a positive aggressive delta, the rival
# multiplier has nothing to scale and a null result is uninformative).
SCALE_STATS = Counter()


def _forced_modifier_fn(mods):
    """Return a drop-in for `_apply_relationship_modifier_to_offsets` that
    applies `mods` to every offsets dict, bypassing the repo/heat guards.
    Mirrors the real per-action composition (aggressive positive offsets
    scale by bluff_freq_mult; negative `fold` offset scales by
    fold_to_pressure_mult)."""

    def _apply(self, offsets, manager, spots, primary_spot):
        SCALE_STATS["calls"] += 1
        if offsets:
            SCALE_STATS["nonempty_offsets"] += 1
        scaled = dict(offsets)
        for action, delta in offsets.items():
            if delta > 0 and TieredBotController._is_aggressive_action_label(action):
                scaled[action] = delta * mods["bluff_freq_mult"]
                SCALE_STATS["scaled_aggressive"] += 1
            elif action == "fold" and delta < 0:
                scaled[action] = delta * mods["fold_to_pressure_mult"]
                SCALE_STATS["scaled_fold"] += 1
        return scaled

    return _apply


def _run_arm(modifier_fn):
    TieredBotController.exploitation_strength = 1.0
    TieredBotController.apply_relationship_modifier = True
    agg = Counter()
    tot = Counter()
    orig_dec = TieredBotController._get_postflop_decision
    orig_mod = TieredBotController._apply_relationship_modifier_to_offsets

    def wrapped(self, *a, **k):
        # simulate_bb100's controller factory hard-sets the instance flag
        # False (line ~550) — the structural no-op. Force it True on the
        # hero so the modifier gate (`if offsets and self.apply_...`) can
        # actually pass and our forced modifier engages.
        self.apply_relationship_modifier = True
        d = orig_dec(self, *a, **k)
        snap = getattr(self, "_last_pipeline_snapshot", {}) or {}
        hs = snap.get("hand_strength")
        act = snap.get("resolved_action")
        if hs and act:
            tot[hs] += 1
            if act in AGGRO:
                agg[hs] += 1
        return d

    TieredBotController._get_postflop_decision = wrapped
    if modifier_fn is not None:
        TieredBotController._apply_relationship_modifier_to_offsets = modifier_fn
    try:
        st = sim.load_strategy_table()
        sim.run_matchup(
            "TAG",
            OPPONENT,
            HANDS,
            st,
            big_blind=100,
            starting_stack=10000,
            base_seed=SEED,
        )
    finally:
        TieredBotController._get_postflop_decision = orig_dec
        TieredBotController._apply_relationship_modifier_to_offsets = orig_mod
    return agg, tot


def _rate(agg, tot, cls):
    a, t = agg[cls], tot[cls]
    return (100.0 * a / t if t else 0.0), t


def main():
    print(f"=== relationship-modifier behavioral CEILING — TAG vs {OPPONENT} HU ===")
    # OFF arm: leave the real (guarded) method in place; with no repo it
    # returns offsets unchanged — the production no-op baseline.
    arms = {
        "OFF": _run_arm(None),
        "ON+RIVAL": _run_arm(_forced_modifier_fn(RIVAL)),
        "ON+FRIEND": _run_arm(_forced_modifier_fn(FRIEND)),
    }
    print(f"{'arm':<12}{'air_no_draw aggr':>18}{'air_strong_draw aggr':>22}")
    res = {}
    for name, (agg, tot) in arms.items():
        nd, nnd = _rate(agg, tot, "air_no_draw")
        sd, nsd = _rate(agg, tot, "air_strong_draw")
        res[name] = nd
        print(f"{name:<12}{nd:8.1f}% (n={nnd:<5}){sd:13.1f}% (n={nsd})")
    off = res["OFF"]
    rival = res["ON+RIVAL"]
    friend = res["ON+FRIEND"]
    print()
    print(f"  Δ RIVAL−OFF  (air_no_draw): {rival - off:+.1f}pp   (rival should bluff MORE)")
    print(f"  Δ FRIEND−OFF (air_no_draw): {friend - off:+.1f}pp   (friend should bluff LESS)")
    moved = max(abs(rival - off), abs(friend - off))
    print()
    print(
        f"  modifier work (RIVAL+FRIEND arms): {SCALE_STATS['calls']} calls, "
        f"{SCALE_STATS['nonempty_offsets']} w/ non-empty offsets, "
        f"{SCALE_STATS['scaled_aggressive']} aggressive deltas scaled, "
        f"{SCALE_STATS['scaled_fold']} fold deltas scaled"
    )
    print(
        f"  behavioral ceiling: {moved:.1f}pp max swing at the STRONGEST modifier"
    )
    print(
        "  → if ~0, the layer is inert-by-channel (rides the dead additive nudge);"
    )
    print(
        "    'never EV-measured' is closeable by inspection — no EV to measure."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
