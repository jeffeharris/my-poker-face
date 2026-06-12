"""
Deviation profiles: archetype-keyed limits on personality distortion.

Each profile controls how far a player archetype can deviate from the
solver baseline in logit space.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from ..archetypes import classify_from_anchors

if TYPE_CHECKING:
    # Type-only import to avoid a circular import: tilt_conditioning imports
    # DeviationProfile from this module. The runtime default is an empty tuple,
    # so no concrete TiltScenarioRule is needed at import time.
    from .tilt_conditioning import TiltScenarioRule


@dataclass(frozen=True)
class DeviationProfile:
    """Controls how far an archetype can deviate from solver baseline."""

    max_kl: float  # Max KL divergence from base
    max_per_action_shift: float  # Max absolute shift per action
    aggression_scale: float  # Multiplier for aggression offsets
    looseness_scale: float  # Multiplier for looseness offsets
    risk_scale: float  # Multiplier for risk identity offsets
    ego_fold_penalty: float  # Penalty applied to fold when ego > 0
    # item 3: spot/line-specific tendencies as ((name, strength), ...) — a
    # hashable, frozen-safe map. Empty = none active (default). Each name is a
    # registered rule in spot_tendencies.py; strength in [0, 1] scales the
    # reshape (then bounded by max_per_action_shift). Priced + budgeted before
    # a profile turns one on. See PERSONALITY_PRICING_AND_VARIETY.md.
    spot_tendencies: Tuple[Tuple[str, float], ...] = ()
    # Dimensional-awareness leak: how position-BLIND the player is, in [0, 1].
    # 0 = fully position-aware (reads its seat correctly). >0 = looks up the
    # preflop chart at a LATER (looser) seat than it actually has — the classic
    # recreational "doesn't respect position" mistake (opens BTN-wide from UTG).
    # 1.0 shifts all the way to BTN; 0.33 ≈ one seat later. A −EV error on EVERY
    # hand from every seat, so (unlike the multi-street pay-off leak) it is NOT
    # capped by short stacks — the realistic answer to a 40bb bottom-tier trickle.
    # Applied at the node-lookup level in TieredBotController (the chart cell it
    # reads), so distortion + floors still layer on top. See FISH_AS_CALLING_STATION.md.
    position_blind: float = 0.0
    # Facing-a-raise (3-bet/4-bet) aggression split. The global aggression_scale /
    # max_per_action_shift drive ALL streets, so taming preflop 3-bet wars with
    # them also nerfs postflop aggression (the maniac's defining wildness). These
    # OPTIONAL overrides are applied ONLY at preflop vs_open/vs_3bet/vs_4bet nodes
    # (TieredBotController swaps them in via dataclasses.replace) — so opening
    # width (VPIP/PFR) and postflop AF are untouched, only re-raise FREQUENCY is
    # dampened. None = inherit the global value (no split, byte-identical). See
    # docs/technical/ARCHETYPE_SHAPING_FINDINGS.md (the pre/postflop split).
    reraise_aggression_scale: Optional[float] = None
    reraise_max_per_action_shift: Optional[float] = None
    # Tilt-conditioning reach (PERCEPTIBILITY_CONDITIONING.md Phase 2, the
    # Option-C `tilt_conditioning` layer). `tilt_conditioning_cap` is the binding
    # lever — the max logit-space offset the conditioner may apply when a tilt
    # rule fires (0.0 = the layer is a byte-identical no-op for this archetype).
    # `tilt_scenario_rules` are the per-tilt-type rules this archetype has opted
    # into. BOTH default inert — every shipped profile keeps cap=0.0 / rules=()
    # in Phase 2 (no archetype opted in until Phase 3 opts maniac in), so the
    # no-op invariant holds (test-locked). See poker/strategy/tilt_conditioning.py.
    tilt_conditioning_cap: float = 0.0
    tilt_scenario_rules: Tuple['TiltScenarioRule', ...] = field(default_factory=tuple)


# Predefined profiles from architecture doc:
# | Archetype        | max_kl | max_per_action | aggression | looseness | risk  | ego_fold |
# |------------------|--------|----------------|------------|-----------|-------|----------|
# | Nit              | 0.2    | 0.10           | 0.3        | 0.3       | 0.2   | 0.05     |
# | Rock             | 0.3    | 0.15           | 0.5        | 0.4       | 0.3   | 0.10     |
# | TAG              | 0.3    | 0.15           | 0.7        | 0.4       | 0.4   | 0.10     |
# | Calling Station  | 0.4    | 0.20           | 0.3        | 0.8       | 0.3   | 0.25     |
# | LAG              | 0.5    | 0.25           | 0.8        | 0.7       | 0.6   | 0.20     |
# | Maniac           | 0.6    | 0.30           | 1.0        | 1.0       | 0.8   | 0.30     |

# Division of labour (post-width-tier architecture, 2026-05-29): the per-archetype
# preflop TABLE (ARCHETYPE_WIDTH_TABLE) now carries the coarse VPIP *envelope*
# (tight ~21% / std ~25% / loose ~50% / station 43%/19%); these distortion scales
# carry the *flavor within* it — the aggression/passivity character and the
# tight-end separation distortion CAN reach (it can always boost fold). The
# binding lever is max_per_action_shift (the per-action clip in clamp_divergence
# runs before the KL check and pulls realized KL under max_kl, so for the
# aggressive profiles max_kl is inert). Variety is *expected* to cost EV — the
# bleed IS the skill gradient; weak characters are budgeted generously, not ~0.
DEVIATION_PROFILES: Dict[str, DeviationProfile] = {
    # Nit: very tight, very passive. The old 0.20 cap throttled nit's tighter
    # anchors down to rock's realized play (the two measured byte-identical);
    # a 0.30 cap + stronger looseness_scale lets the fold boost express, so nit
    # sits clearly below rock on the same tight table.
    'nit': DeviationProfile(
        max_kl=0.6,
        max_per_action_shift=0.30,
        # 0.6 -> 0.72: the nit was drifting tight-PASSIVE (AF 1.44, c-bet ~45,
        # fold-to-3bet 58 all below band) — but nit is the tight-AGGRESSIVE seat
        # (rock is the passive one). A modest lift restores postflop aggression +
        # strong-hand c-bets (auto_cbet only covers medium/weak/air) without
        # moving VPIP off its ceiling (looseness_scale governs entry, not this).
        aggression_scale=0.72,
        looseness_scale=1.2,
        risk_scale=0.3,
        ego_fold_penalty=0.05,
        # Nit is tight-AGGRESSIVE: its low aggression_scale (right for overall bet
        # volume) was crushing its FLOP c-bet (~42% vs a 55-70 target — a nit
        # c-bets its strong range). auto_cbet pumps flop continuation betting with
        # initiative, restoring the tight-aggressive read without loosening entry.
        # 0.9: at 0.6 the boost (bounded by the 0.30 per-action cap, fighting
        # aggression_scale 0.6) only reached ~45%.
        # Tight-AGGRESSIVE = bet-or-FOLD. auto_cbet pumps the betting side; without
        # a fold lever the nit just called down its (now stronger, post-preflop-
        # tighten) range, so WTSD ran high (~34 vs 22-28) and fold-to-cbet low (~53
        # vs 55-70) — indistinguishable from rock on the call-down axis. fit_or_fold
        # over-folds the weak/air range to a flop c-bet; give_up_turn folds it on the
        # turn. Together they give nit the "folds when it doesn't have it" identity
        # (drops WTSD into band, lifts fold-to-cbet), distinct from rock's stickiness.
        spot_tendencies=(('auto_cbet', 0.9), ('fit_or_fold', 0.55), ('give_up_turn', 0.45)),
    ),
    # Rock: the classic TIGHT-PASSIVE archetype (backlog #10, Option A) — the
    # tightest entry in the field, plays those few hands PASSIVELY (checks/calls
    # rather than bets/raises). Distinct read from nit (tight-AGGRESSIVE: few
    # hands, played hard). The fix has two halves (see HANDOFF #10):
    #   1. Postflop passivity is carried by the `passive_postflop` SPOT TENDENCY
    #      (not a distortion scale). A tight range value-bets a LOT on the shared
    #      solver chart, and `aggression_scale` is near-inert on postflop AF
    #      (chart/floor-pinned ~1.5 at both 0.5 and 1.9), so the only lever that
    #      gets rock's AF genuinely BELOW nit's is routing bet/raise→check/call on
    #      every postflop street. strength 0.30 lands rock AF ~0.95 (< nit ~1.31,
    #      comfortably above the 0.8 station floor). The existing slowplay/
    #      under_bluff are too narrow (nuts-only / river-air) to move whole-range
    #      AF — hence a dedicated tendency.
    #   2. Preflop tightness + the wider VPIP−PFR gap via MODERATED knobs (the
    #      first pass leaned on field-EXTREME values — looseness 2.9, cap 0.55 — to
    #      brute-force VPIP below nit; pulled back now that AF moved to the tendency):
    #   - looseness_scale 2.4: for a TIGHT character a HIGHER looseness_scale =
    #     STRONGER fold boost (loose_dev<0, fold offset is -loose_dev*scale = +) →
    #     LOWER VPIP. 2.4 (below the old 2.9) keeps rock VPIP just under nit's.
    #   - max_per_action_shift 0.45: the binding lever (the fold boost saturates
    #     it). Must exceed nit's 0.30 for rock's fold to surpass nit's; 0.45 lands
    #     rock as the tightest entry (below the old field-extreme 0.55).
    #   - aggression_scale 2.4: for a low-agg char (agg_dev<0) HIGHER scale = MORE
    #     preflop raise→call → LOWER PFR relative to VPIP. This is what makes rock
    #     raise a SMALLER fraction of its range than nit (PFR/VPIP 0.53 < nit 0.54)
    #     — the tight-PASSIVE gap. Postflop AF is carried by the tendency, not this.
    #   - risk_scale 0.2: low-jam passivity (below nit's 0.3) → all_in ~1%.
    #   - ego_fold_penalty 0.08: kept LOW. Raising it un-folds (RAISES VPIP),
    #     fighting the tightest-in-field goal. See
    #     docs/technical/ARCHETYPE_SHAPING_FINDINGS.md (rock band inversion) and
    #     docs/plans/ARCHETYPE_SHAPING_HANDOFF.md #10.
    'rock': DeviationProfile(
        max_kl=0.6,
        max_per_action_shift=0.45,
        aggression_scale=2.4,
        looseness_scale=2.4,
        risk_scale=0.2,
        ego_fold_penalty=0.08,
        # passive_postflop eased 0.30->0.22: at 0.30 it suppressed rock's flop
        # c-bet to ~20% (below even a passive band). 0.22 keeps AF below nit's
        # while letting c-bet recover toward the (lowered) rock band. 0.18: 0.22
        # still left c-bet ~22% (below the lowered band); easing further nudges it
        # up without lifting AF above nit's.
        # Tight-PASSIVE = call-down sticky (passive_postflop), so a HIGHER WTSD is
        # on-brand and rock's band is wider (22-30). But post preflop-tighten it ran
        # just over (~34) with fold-to-cbet a touch low. A MILD fit_or_fold (well
        # below nit's) trims the weakest flop spots into band while keeping rock
        # stickier than nit — the deliberate nit(bet-or-fold) vs rock(call-down) split.
        spot_tendencies=(('passive_postflop', 0.18), ('fit_or_fold', 0.25)),
    ),
    # TAG: tight-aggressive — the competent-reg anchor, so it sits at the LOWER
    # edge of the TAG band (~22/19), not over the ceiling. High aggression_scale
    # gives the AF character but also boosts preflop opens (raise-or-fold RFI),
    # nudging VPIP up; the higher looseness_scale boosts fold to pull entry back
    # down without touching the aggression flavor.
    'tag': DeviationProfile(
        max_kl=0.6,
        max_per_action_shift=0.30,
        aggression_scale=1.6,
        looseness_scale=1.6,
        risk_scale=0.9,
        ego_fold_penalty=0.20,
        # De-polarize the facing-3-bet defense. The base/standard chart plays a
        # too-polarized 4-bet-or-fold vs_3bet (fold ~61%/4-bet ~16% even
        # distortion-OFF), so TAG over-folds (exploitable) and 4-bets a hair wide.
        # `defend_3bet` (preflop-scoped spot tendency) routes fold→call + a slice
        # of 4-bet→call so TAG flats more — fold ~68→~52, 4-bet ~16→~12. It's the
        # only clean lever: the fold is chart-driven (can't touch the shared base
        # chart) and the 4-bet is chart-driven too (so reraise_aggression_scale,
        # which scales distortion, barely moves it). See ARCHETYPE_SHAPING_FINDINGS.
        spot_tendencies=(('defend_3bet', 0.24),),
    ),
    # Calling Station: loose-passive. The station TABLE creates the high VPIP /
    # low PFR via wide flat-calling; this distortion reinforces passivity
    # (aggression_scale amplifies the negative agg_dev -> shifts raise->call)
    # and stickiness (high ego_fold_penalty -> pays off, doesn't fold).
    'calling_station': DeviationProfile(
        max_kl=0.8,
        max_per_action_shift=0.40,
        aggression_scale=1.2,
        looseness_scale=0.8,
        risk_scale=0.4,
        ego_fold_penalty=0.55,
        # The station had NO postflop tendency, so once the base chart's calling
        # discipline tightened, it folded flop c-bets too much (fold-to-CB ~48%,
        # WTSD below band). `sticky` (now flop+turn+river) restores its defining
        # float-calling with weak/medium made hands → WTSD up, fold-to-CB down.
        # 0.85: 0.75 left WTSD just under band (32.5) and fold-to-CB still ~40.
        spot_tendencies=(('sticky', 0.85),),
    ),
    # LAG: loose-aggressive. Loose table + strong aggression. Global aggression
    # stays high (postflop AF is LAG's identity); the facing-raise SPLIT
    # (reraise_*) tames 3-bet/4-bet frequency without touching postflop. At the
    # old global 1.8 the realized facing-open 3-bet hit ~44% (target 16–26); the
    # reraise scale pulls the distortion's contribution off. NOTE: LAG's base
    # loose_mid chart already 3-bets ~30%, so the split lands it at the chart
    # floor — getting fully into band also needs the chart trim (Knob 2). See
    # docs/technical/ARCHETYPE_SHAPING_FINDINGS.md.
    'lag': DeviationProfile(
        max_kl=1.0,
        max_per_action_shift=0.50,
        aggression_scale=1.8,
        looseness_scale=1.0,
        risk_scale=1.2,
        ego_fold_penalty=0.40,
        # reraise split re-tuned against the opener-conditioned metric (#244):
        # the contaminated metric understated 4-bet, so the old 0.6/0.20 left it
        # over band. Tightening the CAP (the binding lever) → 6k mixed: 4-bet
        # 24.6→21.2 (band 10-20, minor WARN) and 3-bet 26.7→25.3 (now in band).
        # 4-bet floors at ~21 on the loose_mid chart's vs_3bet mass (~15%); fully
        # closing the WARN needs a loose_mid chart trim (lag-only — backlog #5).
        reraise_aggression_scale=0.45,
        reraise_max_per_action_shift=0.10,
    ),
    # Weak fish: the weakest realistic player (the $2-tier trickle). Same passive
    # caller shape as calling_station but pushed to the believable floor — the
    # widest entry (weak_station table, via ARCHETYPE_WIDTH_TABLE), the strongest
    # can't-fold (ego_fold_penalty 0.70), maximally passive (aggression_scale 1.5
    # amplifies the negative agg_dev → raise→call), and TWO −EV leaks baked in
    # (sticky pays off river value; over_bluff spews). The per-action cap + the
    # math/defense floors keep even this realistic (a drunk tourist, not a bot).
    'weak_fish': DeviationProfile(
        max_kl=0.9,
        max_per_action_shift=0.45,
        aggression_scale=1.5,
        looseness_scale=1.0,
        risk_scale=0.3,
        ego_fold_penalty=0.70,
        # sticky trimmed 0.85->0.55: `sticky` now spans flop/turn/river (was
        # river-only), so the same strength calls far more — 0.55 keeps weak_fish
        # WTSD in band rather than over-sticky across every street.
        spot_tendencies=(('sticky', 0.55), ('over_bluff', 0.55)),
        position_blind=0.8,
    ),
    # Isolation profile (measurement only): calling_station + position_blind, on
    # the standard station table, to price the position-blindness lever ALONE
    # (vs plain calling_station) and test its depth-independence. Not assigned in
    # production. See FISH_AS_CALLING_STATION.md.
    'calling_station_pblind': DeviationProfile(
        max_kl=0.8,
        max_per_action_shift=0.40,
        aggression_scale=1.2,
        looseness_scale=0.8,
        risk_scale=0.4,
        ego_fold_penalty=0.55,
        position_blind=0.8,
    ),
    # Isolation profile (measurement only): calling_station + over_bluff, on the
    # standard station table, to price the over-bluff (spew) lever ALONE (vs
    # plain calling_station) — the honest cost of bluffing vs a competent
    # folder-and-barreler (the punisher clone). over_bluff strength matches the
    # weak_fish loadout (0.55). Not assigned in production. See
    # docs/eval_results/VARIETY_VALIDATION_RESULTS.md (punisher test).
    'calling_station_overbluff': DeviationProfile(
        max_kl=0.8,
        max_per_action_shift=0.40,
        aggression_scale=1.2,
        looseness_scale=0.8,
        risk_scale=0.4,
        ego_fold_penalty=0.55,
        spot_tendencies=(('over_bluff', 0.55),),
    ),
    # Maniac: the wildest — loose table + the highest aggression so its AF tops
    # the field (its VPIP shares the loose envelope with LAG; the wildness shows
    # in aggression). Global aggression stays at the priced ceiling (postflop AF
    # is the maniac's whole identity — the field's wildest). The facing-raise
    # SPLIT (reraise_*) pulls the preflop 3-bet/4-bet wars down toward the chart
    # floor without touching that postflop wildness. Looseness no longer boosts
    # raise (Knob 1b). See ARCHETYPE_SHAPING_FINDINGS.md.
    #
    # #9 / PERCEPTIBILITY_CONDITIONING.md Phase 3 — BASELINE LOWERED + tilt opt-in.
    # The believability thesis: a high frequency is realistic; a *constant* high
    # frequency is a caricature. Research puts a live maniac's *sustained* facing-
    # open 3-bet at ~15–25% (expert estimate), so the old ~37 baseline read as a
    # flat caricature. We lower the baseline as far as the cap can pull it and let
    # the tilt_conditioning layer push it transiently into the 30s/low-40s, so
    # 30+ reads as a *state* (a fresh bad-beat / loss), not a constant.
    #   - reraise_max_per_action_shift 0.08→0.01 (the binding lever) + scale
    #     0.8→0.4. 4k mixed: 3-bet 36.4→30.0, 4-bet 40.2→31.8 (both still in band;
    #     4-bet was at the ceiling, now mid-band). VPIP/PFR/AF/all_in unchanged
    #     (the split is isolated to facing-raise nodes).
    #   - FLOOR caveat: the maniac's loose chart's OWN re-raise mass is ~29–30%
    #     combo-weighted (cap=0.0 floors 3-bet at 29.4), so the ~20–25 target is
    #     NOT reachable via the cap alone — it would need a chart change, and the
    #     loose chart is SHARED with spewy_fish/maniac_overbluff (DON'T touch it).
    #     ~30 is the lowest cleanly-achievable baseline; the band is re-set to it.
    #     Closing the last ~5pt to 25 is deferred (a maniac-only loose chart, #5).
    #   - tilt_conditioning_cap 0.35 + the 6 aggressive Tendler-type rules: when
    #     freshly tilted by a concrete CAUSE (bad_beat/got_sucked_out/big_loss/
    #     losing_streak/nemesis_loss/crippled) the conditioner lifts re-raise-spot
    #     aggression up to +0.35 logits, pushing 3-bet from the ~30 baseline into
    #     the 30s/low-40s (a transient state that recovers as composure recovers).
    #     GATED by TILT_CONDITIONING_ENABLED (off by default), so the flag-OFF
    #     default = the new ~30 baseline above (byte-identical to flag-off + inert).
    #     See poker/strategy/tilt_conditioning.py.
    'maniac': DeviationProfile(
        max_kl=1.2,
        max_per_action_shift=0.35,
        aggression_scale=2.2,
        looseness_scale=1.2,
        risk_scale=1.6,
        ego_fold_penalty=0.60,
        reraise_aggression_scale=0.4,
        reraise_max_per_action_shift=0.01,
        # tilt opt-in (#9 / Phase 3): cap + rules assigned at the bottom of the
        # module (MANIAC_TILT_RULES) to avoid the deviation_profiles <-
        # tilt_conditioning circular import. tilt_conditioning_cap is set there too.
    ),
    # Balanced defender (measurement only): the apex anti-aggression reg, to test
    # whether a competent DEFENSE neutralizes the maniac's edge (the field-overfold
    # vs engine-flaw question). The anti-aggression weapons, expressed via levers:
    # calls DOWN to catch bluffs (ego_fold_penalty 0.45 — folds less than TAG's
    # 0.20, so it doesn't over-fold to barrels) + TRAPS strong hands to induce the
    # spew (slowplay 0.5) + 3-bets back (moderate aggression). On the standard
    # competent table. Not a station (it still folds air), not a nit (it doesn't
    # over-fold). Not assigned in production.
    'balanced_defender': DeviationProfile(
        max_kl=0.6,
        max_per_action_shift=0.30,
        aggression_scale=1.3,
        looseness_scale=1.0,
        risk_scale=0.7,
        ego_fold_penalty=0.45,
        spot_tendencies=(('slowplay', 0.5),),
    ),
    # Spewy aggressive fish (the frat-bro who can't stop bluffing). Unlike the
    # passive calling_station fish (loses by paying off), this fish loses by
    # SPEWING: a loose-aggressive base (loose table, so it enters as the raiser
    # and TAKES the betting lead — the precondition over_bluff needs to fire) +
    # a cranked over_bluff (barrels air it should give up on) + sticky (can't
    # fold when a grinder plays back, so the spew gets paid off). The aggression
    # is deliberately MIS-calibrated: a real maniac is +EV because foldy fields
    # over-fold to it, but this one bluffs into callers and can't fold to raises,
    # so it bleeds vs disciplined opponents (the casino's grinders) while staying
    # swingy/fun vs passive tables. max_per_action_shift bumped to 0.45 so the
    # spew actually shifts (the cap throttled it to ~+3pts on the maniac base).
    # See docs/eval_results/VARIETY_VALIDATION_RESULTS.md (spewy fish).
    'spewy_fish': DeviationProfile(
        max_kl=1.0,
        max_per_action_shift=0.45,
        aggression_scale=1.8,
        looseness_scale=1.2,
        risk_scale=1.2,
        ego_fold_penalty=0.55,
        spot_tendencies=(('over_bluff', 0.8), ('sticky', 0.5)),
    ),
    # Validation (measurement only): the maniac base + over_bluff, to confirm the
    # over_bluff lever FIRES and shifts EV on an AGGRESSIVE base (one that takes
    # the betting lead) — the control for the finding that it's inert on a passive
    # station base. Compare vs plain 'maniac'. Not assigned in production.
    'maniac_overbluff': DeviationProfile(
        max_kl=1.2,
        max_per_action_shift=0.35,
        aggression_scale=2.2,
        looseness_scale=1.2,
        risk_scale=1.6,
        ego_fold_penalty=0.60,
        spot_tendencies=(('over_bluff', 0.55),),
    ),
}


# ── Maniac tilt opt-in (#9 / PERCEPTIBILITY_CONDITIONING.md Phase 3) ──────────
# The maniac is the first (and only) archetype opted into the tilt_conditioning
# layer. The import is deferred to HERE (the bottom of the module, after the
# DeviationProfile class AND the DEVIATION_PROFILES dict are fully defined) to
# break the circular import: tilt_conditioning imports DeviationProfile from this
# module at its top, so we cannot import it at OUR top — but by the time this
# line runs, DeviationProfile is bound, so tilt_conditioning loads cleanly.
#
# Rules: the 6 AGGRESSIVE Tendler-type rules (bad_beat, got_sucked_out, big_loss,
# losing_streak, nemesis_loss, crippled). bluff_called is registered with
# magnitude 0.0 (V1 conservative — telegraphable but no shift) so we EXCLUDE it
# from the opt-in (a caught bluff shouldn't spike the maniac's 3-bet).
#
# Cap 0.35: sized so a forced EXTREME tilt (intensity 0.95 on every decision —
# the worst case) lifts the ~30 baseline 3-bet/4-bet into the low-40s (probe:
# 3-bet 30.6→41.4, 4-bet 29.0→41.9), a transient STATE that recovers as composure
# recovers. Bounded (never absurd) — the per-rule max_magnitude (0.5–0.6) is
# clamped down to this cap. Real tilt is intermittent (not every decision at 0.95),
# so realized session spikes land in the 30s/low-40s. See
# scripts/tilt_conditioning_probe.py. dataclasses.replace keeps every other field.
import dataclasses as _dataclasses  # noqa: E402  (deferred import, see above)

from .tilt_conditioning import TILT_TYPE_RULES as _TILT_TYPE_RULES  # noqa: E402

_MANIAC_AGGRESSIVE_TILT_TYPES = (
    'bad_beat',
    'got_sucked_out',
    'big_loss',
    'losing_streak',
    'nemesis_loss',
    'crippled',
)
MANIAC_TILT_RULES: Tuple['TiltScenarioRule', ...] = tuple(
    _TILT_TYPE_RULES[t] for t in _MANIAC_AGGRESSIVE_TILT_TYPES
)
DEVIATION_PROFILES['maniac'] = _dataclasses.replace(
    DEVIATION_PROFILES['maniac'],
    tilt_conditioning_cap=0.35,
    tilt_scenario_rules=MANIAC_TILT_RULES,
)


# Width-tier preflop table per archetype profile (filename in
# poker/strategy/data/). None = the standard base chart. The personality
# *distortion* layer can TIGHTEN a chart (boost fold mass) but CANNOT open a
# hand the base chart folds ~100% (no mass to amplify; the per-action cap pins
# it near 0), so the loose/station archetypes need a wider base TABLE — the
# table carries the coarse VPIP envelope, distortion carries the flavor within
# it. Selected in TieredBotController._select_preflop_table. Measured envelopes
# (Baseline hero, no distortion, vs a Baseline roster): tight 21% VPIP, std 25%,
# loose 50%, station 43% VPIP / 19% PFR (a real caller). See
# docs/plans/PERSONALITY_PRICING_AND_VARIETY.md.
ARCHETYPE_WIDTH_TABLE: Dict[str, Optional[str]] = {
    'nit': 'preflop_100bb_6max_tight_rfi.json',
    'rock': 'preflop_100bb_6max_tight_rfi.json',
    'tag': None,  # standard base chart
    'calling_station': 'preflop_100bb_6max_station.json',
    'lag': 'preflop_100bb_6max_loose_mid.json',  # between TAG and Maniac
    'maniac': 'preflop_100bb_6max_loose.json',
    # weak_fish: the weakest realistic player (the $2-tier trickle). Same passive
    # caller shape as calling_station but on the wider weak_station table (flats
    # almost anything vs a raise) + a heavier can't-fold/pays-off loadout. NOT
    # reachable via anchor classification (select_deviation_profile_key) — it's an
    # explicit loadout assigned to $2 fish. See FISH_AS_CALLING_STATION.md.
    'weak_fish': 'preflop_100bb_6max_weak_station.json',
    'calling_station_pblind': 'preflop_100bb_6max_station.json',  # isolation: station table
    'calling_station_overbluff': 'preflop_100bb_6max_station.json',  # isolation: station table
    'maniac_overbluff': 'preflop_100bb_6max_loose.json',  # validation: maniac base + over_bluff
    'spewy_fish': 'preflop_100bb_6max_loose.json',  # aggressive fish: wide loose entry
}


def parse_spot_tendencies(raw) -> Tuple[Tuple[str, float], ...]:
    """Normalize a personality config's `spot_tendencies` to the canonical form.

    Accepts a list/tuple of ``[name, strength]`` pairs (JSON arrays from
    personalities.json) or ``((name, strength), ...)``; ``None``/empty -> ``()``.
    Strength is coerced to float. Used by the per-personality override hook so a
    specific character can carry its own tendencies independent of its archetype
    profile (see TieredBotController.deviation_profile).
    """
    if not raw:
        return ()
    return tuple((str(name), float(strength)) for name, strength in raw)


def select_deviation_profile_key(anchors) -> str:
    """Resolve the DEVIATION_PROFILES key from personality anchors.

    Uses classify_from_anchors() to get base archetype, then extends:
    - Very low looseness (<0.25) AND very low aggression (<0.25) -> 'nit'
    - Very high looseness (>0.80) AND very high aggression (>0.80) -> 'maniac'
    - tight_passive -> 'rock'
    - tight_aggressive -> 'tag'
    - loose_passive -> 'calling_station'
    - loose_aggressive -> 'lag'
    - default (balanced) -> 'tag' (reasonable middle ground)

    Returning the key (not just the profile) lets the caller also look up the
    archetype's width-tier preflop table (ARCHETYPE_WIDTH_TABLE).
    """
    # Extreme checks first
    if anchors.baseline_looseness < 0.25 and anchors.baseline_aggression < 0.25:
        return 'nit'
    if anchors.baseline_looseness > 0.80 and anchors.baseline_aggression > 0.80:
        return 'maniac'

    archetype = classify_from_anchors(anchors.baseline_looseness, anchors.baseline_aggression)

    mapping = {
        'tight_passive': 'rock',
        'tight_aggressive': 'tag',
        'loose_passive': 'calling_station',
        'loose_aggressive': 'lag',
        'default': 'tag',
    }
    return mapping[archetype]


def select_deviation_profile(anchors) -> DeviationProfile:
    """Select deviation profile from personality anchors (see
    select_deviation_profile_key for the classification rules)."""
    return DEVIATION_PROFILES[select_deviation_profile_key(anchors)]
