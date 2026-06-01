"""Map the legacy rule-bot `fish_leak` tells onto the unified tiered engine.

Casino fish used to be `RuleBotController` bots (the `fish` strategy + a `FishLeak`
enum). We now route them through the tiered `calling_station` archetype (one
decision engine — see docs/plans/FISH_AS_CALLING_STATION.md), whose width-tier
station table already produces the loose-passive caller shape (VPIP ~45 / PFR ~16
/ sticky / pays off). This module re-expresses each old `fish_leak` as the
nearest **spot tendency** so the deliberate, recognizable tells survive the
migration as bounded reshapes on top of the station base.

Most fish leaks are passive can't-fold/pays-off behaviours → `sticky` (the river
bluff-catch over-call, the exact spot the value overbet punishes). The spew/spite
aggression leaks → `over_bluff`. A few have no clean spot-tendency analogue today
(transparent size=strength sizing — tendencies reshape action frequencies, not bet
sizes; limps-every-hand — no limp in the charts) and fall back to the bare
calling_station, which already plays loose-passive. See the catalog in
PERSONALITY_PRICING_AND_VARIETY.md for the spot-tendency registry.
"""

from .deviation_profiles import parse_spot_tendencies

# fish_leak value → ((tendency, strength), ...). Empty tuple = no spot tendency
# (the bare calling_station already covers it). Strengths are bounded again by
# the profile's max_per_action_shift, so these are upper-intent, not literal.
_FISH_LEAK_TO_TENDENCIES = {
    # ── passive / can't-fold / pays-off leaks → sticky ──────────────────────
    'sticky_then_pops': (('sticky', 0.85),),
    'calls_river_light': (('sticky', 0.75),),
    'doesnt_believe_big_bets': (('sticky', 0.75),),
    'calls_down_top_pair': (('sticky', 0.65),),
    'pot_committed_early': (('sticky', 0.65),),
    'chases_any_draw': (('sticky', 0.55),),
    'overvalues_face_cards': (('sticky', 0.55),),
    # ── aggression leaks → over_bluff ───────────────────────────────────────
    'spews_bluffs': (('over_bluff', 0.8),),
    'spite_raises_when_losing': (('over_bluff', 0.6),),
    # ── no clean spot-tendency analogue (bare calling_station covers it) ─────
    'bets_strong_transparently': (),  # size=strength tell — no sizing tendency yet
    'limps_every_hand': (),  # no limp in the charts; station is already loose
}


def fish_spot_tendencies(fish_leak):
    """Return the parsed spot-tendency override for a fish's `fish_leak`.

    ``None`` / unknown / a no-analogue leak → ``()`` (the bare calling_station,
    which is already a loose-passive caller). The result is in the canonical
    ``((name, strength), ...)`` form the controller's
    ``_spot_tendencies_override`` expects.
    """
    return parse_spot_tendencies(_FISH_LEAK_TO_TENDENCIES.get(fish_leak or '', ()))
