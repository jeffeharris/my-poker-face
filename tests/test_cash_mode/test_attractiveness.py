"""Tests for `cash_mode.attractiveness` — the pure table-scoring core.

Spec: `docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md` (v1).

These assert *qualitative* properties (monotonicity, bounds, ordering,
the spec's load-bearing behaviors) rather than magic numbers — the
constants are sim-tunable starting points, so pinning exact outputs
would make the tests brittle without testing anything real.
"""

from __future__ import annotations

import pytest

from cash_mode.attractiveness import (
    AFFORDABLE_BAND_BUYINS,
    FillableTable,
    SeatSeeker,
    _affordable_tier_index,
    assign_seats_greedy,
    base_attractor,
    glory_appetite,
    hunger,
    occ_prestige,
    room_prestige,
    stake_fit,
    status_appetite,
    table_attractiveness,
    table_deadness,
    wealth,
    wealth_over_tier,
)
from cash_mode.stakes_ladder import STAKES_ORDER, table_buy_in_window

RICH = 300_000  # comfortably above the $1000 max buy-in (100k)
BROKE = 200  # below the cheapest comfortable roll
START = 10_000  # default starting bankroll


# --- room_prestige -----------------------------------------------------


def test_room_prestige_monotonic_and_bounded():
    vals = [room_prestige(s) for s in STAKES_ORDER]
    assert vals == sorted(vals), "prestige must rise with the stake ladder"
    assert all(0.0 <= v <= 1.0 for v in vals)
    assert vals[0] == 0.0  # cheapest tier
    assert vals[-1] == 1.0  # the Pit


def test_room_prestige_curve_makes_top_stand_out():
    # Squared curve: the gap to the top tier exceeds a linear one's.
    assert room_prestige(STAKES_ORDER[-2]) < 0.75


def test_room_prestige_override_wins_and_clamps():
    assert room_prestige("$2", override=0.9) == 0.9
    assert room_prestige("$1000", override=2.0) == 1.0
    assert room_prestige("$1000", override=-1.0) == 0.0


def test_room_prestige_unknown_stake_is_zero():
    assert room_prestige("$999999") == 0.0


# --- wealth_over_tier --------------------------------------------------


def test_wealth_over_tier_broke_is_zero():
    assert wealth_over_tier(BROKE, "$2") == 0.0


def test_wealth_over_tier_rich_slumming():
    # 300k at $50 (max 5,000) → ~59× over tier (the spec's headline case).
    assert wealth_over_tier(RICH, "$50") > 50.0


def test_wealth_over_tier_monotonic_in_bankroll():
    assert wealth_over_tier(50_000, "$50") > wealth_over_tier(20_000, "$50")


def test_wealth_over_tier_unknown_stake_is_zero():
    assert wealth_over_tier(RICH, "$nope") == 0.0


# --- wealth ------------------------------------------------------------


def test_wealth_bounds_and_extremes():
    # Zero only at/below the cheapest tier's min buy-in (the $2 floor);
    # near-broke reads small; saturates to 1 past the top tier.
    assert wealth(50) == 0.0
    assert wealth(BROKE) < 0.2
    assert wealth(RICH) == 1.0
    for b in (500, 5_000, 40_000, 99_999):
        assert 0.0 <= wealth(b) <= 1.0


def test_wealth_monotonic():
    samples = [200, 1_000, 5_000, 20_000, 50_000, 150_000]
    vals = [wealth(b) for b in samples]
    assert vals == sorted(vals)


# --- affordable tier index ---------------------------------------------


def test_affordable_index_bounds():
    assert _affordable_tier_index(BROKE) == 0.0
    assert _affordable_tier_index(RICH) == float(len(STAKES_ORDER) - 1)


def test_affordable_index_monotonic_and_matches_band():
    assert _affordable_tier_index(5_000) < _affordable_tier_index(50_000)
    # A bankroll exactly at AFFORDABLE_BAND_BUYINS × min_buy_in of a tier
    # should land at (or just below) that tier's index.
    _, min_bi, _ = table_buy_in_window("$50")
    idx = _affordable_tier_index(int(AFFORDABLE_BAND_BUYINS * min_bi))
    assert abs(idx - STAKES_ORDER.index("$50")) < 0.01


# --- stake_fit ---------------------------------------------------------


def test_stake_fit_bounded():
    for s in STAKES_ORDER:
        assert 0.0 <= stake_fit(START, "$10", s) <= 1.0


def test_stake_fit_peaks_near_fit_center():
    # A modest grinder anchored at $10 fits $10 better than $1000.
    assert stake_fit(START, "$10", "$10") > stake_fit(START, "$10", "$1000")


def test_stake_fit_character_matters_at_equal_wealth():
    # Two equally-rich AIs: the gambler (anchor $200) fits the Pit better
    # than the nit (anchor $2) does. Wealth drags both fit centers up, but
    # the anchor still differentiates them (ANCHOR_DRIFT < 1) — character
    # isn't fully overridden by a fat roll.
    gambler_pit = stake_fit(RICH, "$200", "$1000")
    nit_pit = stake_fit(RICH, "$2", "$1000")
    assert gambler_pit > nit_pit
    # And the rich nit still prefers a mid table to the very top Pit —
    # character keeps it from maxing out.
    assert stake_fit(RICH, "$2", "$50") > stake_fit(RICH, "$2", "$1000")


def test_stake_fit_wealth_drifts_center_upward():
    # Two AIs with the same $10 anchor: the richer one fits a higher table
    # better (its fit center drifts up toward what it can afford).
    poor_at_50 = stake_fit(5_000, "$10", "$50")
    rich_at_50 = stake_fit(80_000, "$10", "$50")
    assert rich_at_50 > poor_at_50


def test_stake_fit_unknown_stake_is_zero():
    assert stake_fit(START, "$10", "$bogus") == 0.0


# --- hunger ------------------------------------------------------------


def test_hunger_extremes():
    assert hunger(START, START) == 0.0  # full roll
    assert hunger(2_000, START) == 1.0  # desperate (20% of starting)
    assert hunger(START * 5, START) == 0.0  # above full roll, still 0


def test_hunger_monotonic_decreasing_in_bankroll():
    vals = [hunger(b, START) for b in (1_000, 3_000, 6_000, 9_000, 10_000)]
    assert vals == sorted(vals, reverse=True)


def test_hunger_zero_starting_is_safe():
    assert hunger(5_000, 0) == 0.0


# --- base_attractor ----------------------------------------------------


def test_base_attractor_nonnegative():
    for b in (BROKE, START, RICH):
        for s in STAKES_ORDER:
            assert base_attractor(projected_bankroll=b, comfort_zone="$10", stake_label=s) >= 0.0


def test_base_attractor_climb_only_for_the_rich():
    # The room-prestige climb term lifts a rich AI's attraction to the Pit
    # above a broke AI's (whose wealth≈0 kills the climb term).
    rich_pit = base_attractor(projected_bankroll=RICH, comfort_zone="$10", stake_label="$1000")
    broke_pit = base_attractor(projected_bankroll=BROKE, comfort_zone="$10", stake_label="$1000")
    assert rich_pit > broke_pit


# --- table_attractiveness ----------------------------------------------


def _attr(**over):
    base = dict(
        projected_bankroll=START,
        starting_bankroll=START,
        comfort_zone="$10",
        stake_label="$10",
        fish_chips=0,
        whale_chips=0,
        other_grinders=0,
    )
    base.update(over)
    return table_attractiveness(**base)


def test_fish_table_beats_fishless():
    assert _attr(fish_chips=800) > _attr(fish_chips=0)


def test_whale_outdraws_equal_fish_chips():
    # Same chips on the felt, but a whale weighs heavier than a fish.
    fishy = _attr(fish_chips=1_000)
    whaley = _attr(whale_chips=1_000)
    assert whaley > fishy


def test_crowd_penalizes():
    assert _attr(fish_chips=800, other_grinders=0) > _attr(fish_chips=800, other_grinders=4)


def test_hungry_grinder_pulled_harder_to_fish():
    # A near-broke grinder values a fish table more (relative to its own
    # fishless baseline) than a flush one does.
    hungry_gain = _attr(projected_bankroll=2_000, fish_chips=800) - _attr(
        projected_bankroll=2_000, fish_chips=0
    )
    flush_gain = _attr(projected_bankroll=START, fish_chips=800) - _attr(
        projected_bankroll=START, fish_chips=0
    )
    assert hungry_gain > flush_gain


def test_hunger_amplifies_whale_only_tables_too():
    # A whale (no fish) is also bait — a hungry grinder gets the hunger
    # multiplier on a whale-only table, not just a fish one.
    hungry_gain = _attr(projected_bankroll=2_000, whale_chips=5_000) - _attr(
        projected_bankroll=2_000, whale_chips=0
    )
    flush_gain = _attr(projected_bankroll=START, whale_chips=5_000) - _attr(
        projected_bankroll=START, whale_chips=0
    )
    assert hungry_gain > flush_gain


def test_rich_ai_prefers_prestige_room_without_fish():
    # No fish anywhere: a rich AI still finds the Pit more attractive than
    # a dead low table, purely via the climb term.
    pit = _attr(projected_bankroll=RICH, comfort_zone="$10", stake_label="$1000")
    dive = _attr(projected_bankroll=RICH, comfort_zone="$10", stake_label="$2")
    assert pit > dive


def test_broke_ai_not_pulled_to_prestige_room():
    # A broke AI gets ~no climb pull — the Pit isn't magically attractive.
    pit = _attr(projected_bankroll=BROKE, comfort_zone="$10", stake_label="$1000")
    home = _attr(projected_bankroll=BROKE, comfort_zone="$10", stake_label="$10")
    assert home > pit


# --- venue appeal (casino = the grindy public room) --------------------


def test_casino_less_attractive_than_lobby_same_stake():
    casino = _attr(stake_label="$2", comfort_zone="$2", venue_appeal=0.5)
    lobby = _attr(stake_label="$2", comfort_zone="$2", venue_appeal=1.0)
    assert 0 < casino < lobby  # less appealing, but still a valid fallback


def test_fishy_casino_beats_dead_lobby():
    # The fish draw rides over the venue penalty: a fishy casino out-pulls a
    # dead (fishless) lobby table at the same stake.
    casino_fish = _attr(stake_label="$2", comfort_zone="$2", venue_appeal=0.5, fish_chips=600)
    dead_lobby = _attr(stake_label="$2", comfort_zone="$2", venue_appeal=1.0, fish_chips=0)
    assert casino_fish > dead_lobby


# --- table_deadness (the dead-table push) ------------------------------


def test_deadness_zero_with_fish():
    assert table_deadness(is_casino=True, has_fish=True, grinder_count=5) == 0.0


def test_deadness_zero_for_lobby():
    # Grinders playing each other at a lobby table IS the game, not "dead".
    assert table_deadness(is_casino=False, has_fish=False, grinder_count=5) == 0.0


def test_deadness_zero_when_empty():
    assert table_deadness(is_casino=True, has_fish=False, grinder_count=0) == 0.0


def test_deadness_rises_with_fishless_casino_crowd():
    a = table_deadness(is_casino=True, has_fish=False, grinder_count=1)
    b = table_deadness(is_casino=True, has_fish=False, grinder_count=2)
    assert 0.0 < a < b <= 1.0
    assert table_deadness(is_casino=True, has_fish=False, grinder_count=10) == 1.0


# --- assign_seats_greedy (the loop inversion core) ---------------------


def _table(tid, *, stake="$10", opens=1, grinders=0, fish=0, whale=0, marquee=0.0):
    _, mn, mx = table_buy_in_window(stake)
    return FillableTable(
        table_id=tid,
        stake_label=stake,
        min_buy_in=mn,
        max_buy_in=mx,
        open_count=opens,
        grinder_count=grinders,
        fish_chips=fish,
        whale_chips=whale,
        marquee_prestige=marquee,
    )


def _seeker(pid, allowed, *, bankroll=5_000, start=START, comfort="$10", mult=1.0, appetite=0.0):
    return SeatSeeker(
        personality_id=pid,
        projected_bankroll=bankroll,
        starting_bankroll=start,
        comfort_zone=comfort,
        allowed_table_ids=frozenset(allowed),
        buy_in_multiplier=mult,
        status_appetite=appetite,
    )


def test_greedy_picks_juiciest_affordable():
    tables = {"dead": _table("dead", fish=0), "fishy": _table("fishy", fish=900)}
    out = assign_seats_greedy([_seeker("g", {"dead", "fishy"})], tables)
    assert out == [("g", "fishy")]


def test_greedy_respects_open_count():
    tables = {"t": _table("t", opens=2)}
    seekers = [_seeker(f"g{i}", {"t"}) for i in range(5)]
    out = assign_seats_greedy(seekers, tables)
    assert len(out) == 2
    assert tables["t"].open_count == 0
    assert tables["t"].grinder_count == 2  # occupancy mutated as sharks sit


def test_greedy_respects_affordability():
    # $200 table (min buy-in 8000) — a 5,000-bankroll grinder can't sit.
    tables = {"hi": _table("hi", stake="$200", opens=2)}
    out = assign_seats_greedy([_seeker("poor", {"hi"}, bankroll=5_000)], tables)
    assert out == []
    assert tables["hi"].open_count == 2  # untouched


def test_greedy_respects_allowed_set():
    # "a" is juicier but not in the allowed set (e.g. cooldown blocked it
    # upstream) → seated at the allowed "b" instead.
    tables = {"a": _table("a", fish=900), "b": _table("b", fish=0)}
    out = assign_seats_greedy([_seeker("g", {"b"})], tables)
    assert out == [("g", "b")]


def test_greedy_spreads_across_equal_fish_via_crowd():
    # Two identical fish tables, four grinders → sequential greedy +
    # W_CROWD spreads them evenly rather than dogpiling one.
    tables = {
        "a": _table("a", opens=4, fish=900),
        "b": _table("b", opens=4, fish=900),
    }
    out = assign_seats_greedy([_seeker(f"g{i}", {"a", "b"}) for i in range(4)], tables)
    from collections import Counter

    counts = Counter(tid for _, tid in out)
    assert len(out) == 4
    assert abs(counts["a"] - counts["b"]) <= 1


def test_greedy_skips_seeker_with_no_candidate():
    tables = {"t": _table("t")}
    out = assign_seats_greedy([_seeker("g", set())], tables)  # empty allowed set
    assert out == []
    assert tables["t"].open_count == 1


# --- B4: occupant prestige / status appetite (the marquee pull) -------------


def test_occ_prestige_empty_and_zero_is_zero():
    assert occ_prestige([]) == 0.0
    assert occ_prestige([0.0, 0.0]) == 0.0


def test_occ_prestige_dominated_by_top_with_damped_lineup():
    # The single biggest name leads; an additional notable adds a damped bonus.
    solo = occ_prestige([0.9])
    duo = occ_prestige([0.9, 0.6])
    assert solo == pytest.approx(0.9)
    assert duo > solo  # the lineup bonus
    assert duo <= 1.0  # clamped


def test_glory_appetite_blends_and_bounds():
    assert glory_appetite(expressiveness=0.0, ego=0.0) == 0.0
    assert glory_appetite(expressiveness=1.0, ego=1.0) == pytest.approx(1.0)
    assert glory_appetite() == pytest.approx(0.5)  # neutral defaults


def test_status_appetite_zero_when_no_factors():
    assert status_appetite(own_percentile=0.0, glory=0.0) == 0.0


def test_status_appetite_rises_with_renown_and_glory():
    base = status_appetite(own_percentile=0.2, glory=0.2)
    assert status_appetite(own_percentile=0.9, glory=0.2) > base  # the famous
    assert status_appetite(own_percentile=0.2, glory=0.9) > base  # the showman


def test_marquee_term_inert_when_either_factor_zero():
    # A famous table pulls nobody who has no appetite, and an eager seeker
    # feels nothing at a table of nobodies — the term needs BOTH.
    kw = dict(
        projected_bankroll=5_000, starting_bankroll=START, comfort_zone="$10",
        stake_label="$10", fish_chips=0, whale_chips=0, other_grinders=0,
    )
    plain = table_attractiveness(**kw)
    assert table_attractiveness(**kw, marquee_prestige=0.9, status_appetite=0.0) == pytest.approx(plain)
    assert table_attractiveness(**kw, marquee_prestige=0.0, status_appetite=0.9) == pytest.approx(plain)


def test_marquee_term_raises_attractiveness_for_status_seeker():
    kw = dict(
        projected_bankroll=5_000, starting_bankroll=START, comfort_zone="$10",
        stake_label="$10", fish_chips=0, whale_chips=0, other_grinders=0,
    )
    plain = table_attractiveness(**kw)
    famous = table_attractiveness(**kw, marquee_prestige=0.9, status_appetite=0.8)
    assert famous > plain


def test_greedy_status_seeker_prefers_the_marquee_table():
    # Two otherwise-identical tables; one seats a legend. A high-appetite AI
    # picks the marquee table; the term tips an otherwise-tied choice.
    tables = {
        "plain": _table("plain", opens=1, marquee=0.0),
        "marquee": _table("marquee", opens=1, marquee=0.9),
    }
    out = assign_seats_greedy([_seeker("g", {"plain", "marquee"}, appetite=0.8)], tables)
    assert out == [("g", "marquee")]


def test_greedy_indifferent_seeker_unaffected_by_marquee():
    # Appetite 0 → the marquee table has no edge; deterministic id tie-break
    # ('marquee' < 'plain') decides, same as it would with no marquee at all.
    tables = {
        "plain": _table("plain", opens=1, marquee=0.0),
        "marquee": _table("marquee", opens=1, marquee=0.9),
    }
    out = assign_seats_greedy([_seeker("g", {"plain", "marquee"}, appetite=0.0)], tables)
    assert out == [("g", "marquee")]  # tie broken by sorted id, not prestige
