"""Tests for the playstyle-gated rule families (value_vs_station, steal_pressure).

Behavior under test:

  Pure intensity helpers
    - compute_value_vs_station_intensity: station detection, multi-station
      MAX upside, tight-opponent safety dampener, all-in / cold-start filters.
    - compute_steal_pressure_intensity: tight-passive defender detection,
      blind seat weighting, false-steal guard against tight-aggressive
      players behind, all-in / cold-start filters.

  compute_exploitation_offsets integration
    - Both intensities at 0 leaves behavior byte-identical (no new keys).
    - value_vs_station_intensity > 0 pushes bet_* positive and check negative.
    - steal_pressure_intensity > 0 pushes raise-like actions positive.

  Playstyle gate helpers
    - is_value_vs_station_enabled membership per archetype.
    - is_steal_pressure_enabled returns False for every archetype in v1
      (rule ships piping + diagnostics only).

  Controller-level gating
    - hand_strength gates value_vs_station: medium/weak/air → intensity 0.
    - playstyle gates: lag/maniac never see value_vs_station_fired;
      no archetype sees steal_pressure_fired in v1.
    - can_act_behind reflects "yet to act this round" semantics via
      Player.has_acted (handles BB option + 3-bet re-opens without
      seat-order traversal).

  Override interaction
    - When the strong-hand value_override fires on the same decision,
      counters increment value_vs_station_superseded_by_override rather
      than value_vs_station_fired.

  Counter identities
    - eligible == enabled_eligible + diagnostic_only
    - For value_vs_station: enabled_eligible == fired + superseded_by_override
"""

import random
from collections import Counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
    OpponentSpot,
    PFR_LOOSE_THRESHOLD,
    STEAL_PRESSURE_PLAYSTYLES,
    VALUE_VS_STATION_PLAYSTYLES,
    VVS_SAFETY_WEIGHT,
    compute_exploitation_offsets,
    compute_steal_pressure_intensity,
    compute_value_vs_station_intensity,
    is_steal_pressure_enabled,
    is_value_vs_station_enabled,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

def _stats(
    *,
    hands_observed: int = 50,
    vpip: float = 0.50,
    pfr: float = 0.10,
    aggression_factor: float = 1.5,
    all_in_frequency: float = 0.0,
    fold_to_cbet: float = 0.5,
    cbet_faced_count: int = 0,
) -> AggregatedOpponentStats:
    return AggregatedOpponentStats(
        hands_observed=hands_observed,
        vpip=vpip,
        pfr=pfr,
        aggression_factor=aggression_factor,
        all_in_frequency=all_in_frequency,
        fold_to_cbet=fold_to_cbet,
        cbet_faced_count=cbet_faced_count,
    )


def _station_stats(*, vpip: float = 0.85, **kwargs) -> AggregatedOpponentStats:
    """Stats that satisfy _is_hyper_passive: high VPIP, low AF."""
    return _stats(vpip=vpip, aggression_factor=0.4, **kwargs)


def _tight_nit_stats(*, vpip: float = 0.10, **kwargs) -> AggregatedOpponentStats:
    """Stats that satisfy _is_tight_nit: very low VPIP. PFR low so the
    steal-pressure false-steal guard doesn't trip (a tight-passive
    nit, not tight-aggressive)."""
    return _stats(vpip=vpip, pfr=0.05, aggression_factor=1.0, **kwargs)


def _spot(
    name: str = 'Opp',
    *,
    stats=None,
    is_active: bool = True,
    is_all_in: bool = False,
    can_act_behind: bool = False,
    is_blind: bool = False,
) -> OpponentSpot:
    return OpponentSpot(
        name=name,
        stats=stats if stats is not None else _stats(),
        is_active=is_active,
        is_all_in=is_all_in,
        can_act_behind=can_act_behind,
        is_blind=is_blind,
    )


# ── compute_value_vs_station_intensity ─────────────────────────────────────

class TestValueVsStationIntensity:
    def test_empty_spots_returns_zero(self):
        assert compute_value_vs_station_intensity([]) == 0.0

    def test_no_active_spots_returns_zero(self):
        spots = [_spot('A', stats=_station_stats(), is_active=False)]
        assert compute_value_vs_station_intensity(spots) == 0.0

    def test_station_all_in_returns_zero(self):
        # All-in stations can't call more — no upside.
        spots = [_spot('A', stats=_station_stats(), is_all_in=True)]
        assert compute_value_vs_station_intensity(spots) == 0.0

    def test_no_station_returns_zero(self):
        spots = [_spot('A', stats=_stats(vpip=0.3, aggression_factor=2.0))]
        assert compute_value_vs_station_intensity(spots) == 0.0

    def test_cold_start_station_returns_zero(self):
        # Below MIN_HANDS_DEFAULT (15) — sample too noisy.
        spots = [_spot('A', stats=_station_stats(hands_observed=10))]
        assert compute_value_vs_station_intensity(spots) == 0.0

    def test_single_full_station_returns_one(self):
        # VPIP 0.90 = top of hyper_passive ramp → intensity 1.0.
        spots = [_spot('A', stats=_station_stats(vpip=0.90))]
        assert compute_value_vs_station_intensity(spots) == pytest.approx(1.0)

    def test_partial_station_returns_partial(self):
        # VPIP 0.75 sits midway in the 0.60→0.90 ramp.
        spots = [_spot('A', stats=_station_stats(vpip=0.75))]
        result = compute_value_vs_station_intensity(spots)
        assert 0.0 < result < 1.0

    def test_multiple_stations_takes_max(self):
        # Loosest station drives upside — MAX, not MIN.
        spots = [
            _spot('Loose', stats=_station_stats(vpip=0.85)),
            _spot('Mild', stats=_station_stats(vpip=0.65)),
        ]
        result = compute_value_vs_station_intensity(spots)
        loose_only = compute_value_vs_station_intensity(
            [_spot('Loose', stats=_station_stats(vpip=0.85))]
        )
        assert result == pytest.approx(loose_only)

    def test_tight_opponent_dampens_intensity(self):
        # Station + tight nit → safety dampener applied.
        spots = [
            _spot('Station', stats=_station_stats(vpip=0.90)),
            _spot('Nit', stats=_tight_nit_stats(vpip=0.05)),
        ]
        result = compute_value_vs_station_intensity(spots)
        # Full station = 1.0, full tight = 1.0 → safety = 1 - 0.5 = 0.5
        assert result == pytest.approx(1.0 - VVS_SAFETY_WEIGHT)

    def test_dampener_only_from_non_stations(self):
        # A second station should NOT trigger the safety dampener even
        # though it's technically "lower VPIP" than the loose one.
        spots = [
            _spot('Loose', stats=_station_stats(vpip=0.90)),
            _spot('Mild', stats=_station_stats(vpip=0.62)),
        ]
        # Mild station has VPIP > HYPER_PASSIVE_VPIP_THRESHOLD so it's
        # still a station, never enters the non_stations pool, no safety.
        assert compute_value_vs_station_intensity(spots) == pytest.approx(1.0)


# ── compute_steal_pressure_intensity ───────────────────────────────────────

class TestStealPressureIntensity:
    def test_empty_spots_returns_zero(self):
        assert compute_steal_pressure_intensity([]) == 0.0

    def test_nobody_behind_returns_zero(self):
        spots = [_spot('A', stats=_tight_nit_stats(), can_act_behind=False)]
        assert compute_steal_pressure_intensity(spots) == 0.0

    def test_folded_player_behind_returns_zero(self):
        # is_active False (folded) → not behind even with can_act_behind True
        spots = [_spot('A', stats=_tight_nit_stats(),
                       is_active=False, can_act_behind=True)]
        assert compute_steal_pressure_intensity(spots) == 0.0

    def test_all_in_player_behind_ignored(self):
        spots = [_spot('A', stats=_tight_nit_stats(),
                       can_act_behind=True, is_all_in=True)]
        assert compute_steal_pressure_intensity(spots) == 0.0

    def test_tight_passive_defender_returns_positive(self):
        spots = [_spot('Nit', stats=_tight_nit_stats(vpip=0.05),
                       can_act_behind=True)]
        result = compute_steal_pressure_intensity(spots)
        assert result > 0.0

    def test_blind_defender_weighted_heavier(self):
        in_blind = compute_steal_pressure_intensity([
            _spot('BB', stats=_tight_nit_stats(vpip=0.08),
                  can_act_behind=True, is_blind=True),
        ])
        non_blind = compute_steal_pressure_intensity([
            _spot('UTG', stats=_tight_nit_stats(vpip=0.08),
                  can_act_behind=True, is_blind=False),
        ])
        assert in_blind > non_blind

    def test_high_pfr_player_behind_kills_rule(self):
        # A LAG-ish defender (PFR clearly above PFR_LOOSE_THRESHOLD)
        # would 3-bet back rather than fold. False-steal guard returns
        # 0 even though a nit is also behind and would normally drive
        # the rule.
        spots = [
            _spot('Nit', stats=_tight_nit_stats(vpip=0.05),
                  can_act_behind=True),
            _spot('LAG', stats=_stats(vpip=0.35, pfr=0.25),
                  can_act_behind=True),
        ]
        assert compute_steal_pressure_intensity(spots) == 0.0

    def test_pfr_guard_above_threshold(self):
        # PFR exactly at PFR_LOOSE_THRESHOLD trips the guard.
        spots = [
            _spot('TAG', stats=_stats(vpip=0.18, pfr=PFR_LOOSE_THRESHOLD),
                  can_act_behind=True),
        ]
        assert compute_steal_pressure_intensity(spots) == 0.0

    def test_cold_start_defender_excluded(self):
        # Defender qualification needs >= MIN_HANDS_DEFAULT samples.
        spots = [_spot('A', stats=_tight_nit_stats(hands_observed=5),
                       can_act_behind=True)]
        assert compute_steal_pressure_intensity(spots) == 0.0

    def test_cold_start_opponent_does_not_trip_pfr_guard(self):
        # Default pfr=0.5 on an unknown opponent would trip the guard
        # otherwise. The min-hands gate keeps unknown opponents
        # neutral — they neither qualify as defenders nor block the rule.
        spots = [
            _spot('Nit', stats=_tight_nit_stats(),
                  can_act_behind=True),
            _spot('Unknown', stats=_stats(hands_observed=3, pfr=0.5),
                  can_act_behind=True),
        ]
        assert compute_steal_pressure_intensity(spots) > 0.0


# ── compute_exploitation_offsets integration ──────────────────────────────

def _basic_decision_context(**kwargs) -> DecisionContext:
    return DecisionContext(**kwargs)


class TestExploitationOffsetsIntegration:
    def test_both_intensities_zero_is_noop_for_new_rules(self):
        # No new keys appear when both intensities are 0.
        offsets = compute_exploitation_offsets(
            stats=_stats(hands_observed=50, vpip=0.3, aggression_factor=1.5),
            adaptation_bias=0.9,
            decision_context=_basic_decision_context(is_preflop=False),
            available_actions=['fold', 'check', 'bet_33', 'bet_67', 'all_in'],
        )
        # Default-stats opponent doesn't trigger any rule — offsets empty.
        assert offsets == {}

    def test_value_vs_station_intensity_pushes_bet_positive(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(hands_observed=50, vpip=0.3, aggression_factor=1.5),
            adaptation_bias=0.9,
            decision_context=_basic_decision_context(is_preflop=False),
            available_actions=['check', 'bet_33', 'bet_67'],
            value_vs_station_intensity=1.0,
        )
        assert offsets.get('bet_33', 0.0) > 0.0
        assert offsets.get('bet_67', 0.0) > 0.0
        assert offsets.get('check', 0.0) < 0.0

    def test_steal_pressure_intensity_pushes_raise_positive(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(hands_observed=50, vpip=0.3, aggression_factor=1.5),
            adaptation_bias=0.9,
            decision_context=_basic_decision_context(is_preflop=True),
            available_actions=['fold', 'call', 'raise_2.5bb', 'all_in'],
            steal_pressure_intensity=1.0,
        )
        assert offsets.get('raise_2.5bb', 0.0) > 0.0


# ── Playstyle gate helpers ─────────────────────────────────────────────────

class TestPlaystyleGates:
    @pytest.mark.parametrize('archetype', ['nit', 'rock', 'tag'])
    def test_value_vs_station_enabled_for_disciplined_archetypes(self, archetype):
        assert is_value_vs_station_enabled(archetype) is True

    @pytest.mark.parametrize('archetype', ['lag', 'maniac', 'calling_station', 'baseline'])
    def test_value_vs_station_disabled_for_loose_archetypes(self, archetype):
        assert is_value_vs_station_enabled(archetype) is False

    def test_value_vs_station_disabled_for_unknown(self):
        assert is_value_vs_station_enabled(None) is False
        assert is_value_vs_station_enabled('garbage') is False

    def test_value_vs_station_set_contents(self):
        assert VALUE_VS_STATION_PLAYSTYLES == frozenset({'nit', 'rock', 'tag'})

    @pytest.mark.parametrize('archetype',
                             ['nit', 'rock', 'tag', 'lag', 'maniac',
                              'calling_station', 'baseline'])
    def test_steal_pressure_disabled_for_every_archetype_in_v1(self, archetype):
        # v1 ships the rule as piping + diagnostics only.
        assert is_steal_pressure_enabled(archetype) is False

    def test_steal_pressure_set_empty_in_v1(self):
        assert STEAL_PRESSURE_PLAYSTYLES == frozenset()


# ── _build_opponent_spots: can_act_behind preflop walkthrough ─────────────

def _player(
    name: str,
    *,
    is_folded: bool = False,
    is_all_in: bool = False,
    has_acted: bool = False,
    stack: int = 10000,
    bet: int = 0,
):
    return SimpleNamespace(
        name=name,
        stack=stack,
        bet=bet,
        total_bet=bet,
        is_folded=is_folded,
        is_all_in=is_all_in,
        has_acted=has_acted,
        last_action=None,
        hand=(),
        is_human=False,
    )


def _build_spots_for(players, *, hero_name='Hero', sb_idx=0, bb_idx=1):
    """Invoke _build_opponent_spots with a minimal stubbed game state."""
    from poker.tiered_bot_controller import TieredBotController

    game_state = SimpleNamespace(
        players=players,
        small_blind_idx=sb_idx,
        big_blind_idx=bb_idx,
    )

    controller = TieredBotController.__new__(TieredBotController)
    controller.player_name = hero_name
    # Use the SAME phase API the controller reads (current_phase.name).
    controller.state_machine = SimpleNamespace(
        current_phase=SimpleNamespace(name='PRE_FLOP'),
    )
    controller.memory_manager = None
    controller._sim_recent_aggressor = None
    controller._sim_last_preflop_aggressor = None

    manager = MagicMock()
    manager.get_model_if_exists.return_value = None
    return controller._build_opponent_spots(game_state, manager)


class TestCanActBehindPreflop:
    """6-max walkthrough: UTG-MP-CO fold, BTN raises → blinds still to act.

    Hero is BB (player at index 1 in our setup). After BTN's raise,
    Player.has_acted is reset for everyone yet to face the new action.
    """

    def test_after_btn_raise_blinds_still_to_act(self):
        # Players are listed in seat order. Hero is SB so they can see
        # who's still left to act behind them (BB).
        # Seats: SB(Hero) BB UTG MP CO BTN
        # Pre-flop action order: UTG → MP → CO → BTN → SB → BB
        # After UTG-MP-CO fold and BTN raise:
        #   - UTG/MP/CO are folded
        #   - BTN has has_acted=True
        #   - SB/BB have not acted yet (and BTN's raise reset would have
        #     applied to UTG-MP-CO before they folded, but folds don't
        #     reset)
        players = [
            _player('Hero', has_acted=False, bet=50),       # 0 SB
            _player('BB',   has_acted=False, bet=100),      # 1 BB
            _player('UTG',  is_folded=True),                # 2
            _player('MP',   is_folded=True),                # 3
            _player('CO',   is_folded=True),                # 4
            _player('BTN',  has_acted=True, bet=300),       # 5 raised
        ]
        spots = _build_spots_for(players, hero_name='Hero',
                                  sb_idx=0, bb_idx=1)
        by_name = {s.name: s for s in spots}

        # BB has not acted → still to act behind Hero
        assert by_name['BB'].can_act_behind is True
        # BTN already raised → not behind
        assert by_name['BTN'].can_act_behind is False
        # Folded players are not "behind" (is_active=False)
        assert by_name['UTG'].can_act_behind is False
        assert by_name['MP'].can_act_behind is False
        assert by_name['CO'].can_act_behind is False

    def test_after_bb_three_bet_only_btn_can_act(self):
        # SB folds, BB 3-bets. Now action is back on BTN.
        # state machine resets has_acted on BTN (action reopened) but
        # leaves BB has_acted=True (they just acted) and SB is folded.
        players = [
            _player('Hero', is_folded=True),                # 0 SB folded
            _player('BB',   has_acted=True, bet=900),       # 1 just 3-bet
            _player('UTG',  is_folded=True),                # 2
            _player('MP',   is_folded=True),                # 3
            _player('CO',   is_folded=True),                # 4
            _player('BTN',  has_acted=False, bet=300),      # 5 action on
        ]
        # Hero observer is BTN — they're the one deciding now.
        spots = _build_spots_for(players, hero_name='BTN',
                                  sb_idx=0, bb_idx=1)
        by_name = {s.name: s for s in spots}

        # BB just acted — no longer behind
        assert by_name['BB'].can_act_behind is False
        # SB is folded
        assert by_name['Hero'].can_act_behind is False

    def test_is_blind_populated_from_sb_bb_idx(self):
        players = [
            _player('SmallBlind', has_acted=False, bet=50),
            _player('BigBlind',   has_acted=False, bet=100),
            _player('Hero',       has_acted=False, bet=0),
            _player('Other',      has_acted=False, bet=0),
        ]
        spots = _build_spots_for(players, hero_name='Hero',
                                  sb_idx=0, bb_idx=1)
        by_name = {s.name: s for s in spots}

        assert by_name['SmallBlind'].is_blind is True
        assert by_name['BigBlind'].is_blind is True
        assert by_name['Other'].is_blind is False

    def test_all_in_player_not_can_act_behind(self):
        players = [
            _player('Hero', has_acted=False),
            _player('Shorty', has_acted=False, is_all_in=True, stack=0),
        ]
        spots = _build_spots_for(players, hero_name='Hero',
                                  sb_idx=0, bb_idx=1)
        by_name = {s.name: s for s in spots}

        # Shorty has nothing left to put in — can't act behind.
        assert by_name['Shorty'].can_act_behind is False


# ── Counter identities (smoke test the tally helper) ──────────────────────

class TestPlaystyleRuleTally:
    """Spot-check the tally helper writes the right keys when stashed
    intensities and override-fired flag take various combinations.
    """

    def _controller_with_manager(self):
        from poker.tiered_bot_controller import TieredBotController

        controller = TieredBotController.__new__(TieredBotController)
        controller.opponent_model_manager = MagicMock()
        controller.opponent_model_manager._exploitation_counters = Counter()
        return controller

    def _stash(self, controller, *,
               archetype='tag',
               vvs_raw=0.0, vvs_used=0.0,
               steal_raw=0.0, steal_used=0.0,
               override_fired=False,
               will_emit=True):
        controller._last_exploitation_archetype = archetype
        controller._last_value_vs_station_intensity_raw = vvs_raw
        controller._last_value_vs_station_intensity_used = vvs_used
        controller._last_steal_pressure_intensity_raw = steal_raw
        controller._last_steal_pressure_intensity_used = steal_used
        controller._last_value_override_fired = override_fired
        controller._last_phase_8_will_emit = will_emit

    def test_vvs_enabled_no_override_increments_fired(self):
        controller = self._controller_with_manager()
        self._stash(controller, archetype='tag',
                    vvs_raw=0.5, vvs_used=0.5, override_fired=False)
        controller._tally_playstyle_rule_event()
        c = controller.opponent_model_manager._exploitation_counters

        assert c['value_vs_station_eligible_tag'] == 1
        assert c['value_vs_station_enabled_eligible_tag'] == 1
        assert c['value_vs_station_fired_tag'] == 1
        assert c['value_vs_station_superseded_by_override_tag'] == 0
        assert c['value_vs_station_diagnostic_only_tag'] == 0

    def test_vvs_enabled_with_override_increments_superseded(self):
        controller = self._controller_with_manager()
        self._stash(controller, archetype='tag',
                    vvs_raw=0.5, vvs_used=0.5, override_fired=True)
        controller._tally_playstyle_rule_event()
        c = controller.opponent_model_manager._exploitation_counters

        assert c['value_vs_station_fired_tag'] == 0
        assert c['value_vs_station_superseded_by_override_tag'] == 1

    def test_vvs_disabled_archetype_increments_diagnostic_only(self):
        controller = self._controller_with_manager()
        self._stash(controller, archetype='lag',
                    vvs_raw=0.5, vvs_used=0.0)
        controller._tally_playstyle_rule_event()
        c = controller.opponent_model_manager._exploitation_counters

        assert c['value_vs_station_eligible_lag'] == 1
        assert c['value_vs_station_enabled_eligible_lag'] == 0
        assert c['value_vs_station_diagnostic_only_lag'] == 1
        assert c['value_vs_station_fired_lag'] == 0

    def test_steal_pressure_disabled_in_v1_only_diagnostic(self):
        # Any archetype should land in diagnostic_only because the
        # frozenset is empty.
        controller = self._controller_with_manager()
        self._stash(controller, archetype='lag',
                    steal_raw=0.4, steal_used=0.0)
        controller._tally_playstyle_rule_event()
        c = controller.opponent_model_manager._exploitation_counters

        assert c['steal_pressure_eligible_lag'] == 1
        assert c['steal_pressure_diagnostic_only_lag'] == 1
        assert c['steal_pressure_fired_lag'] == 0

    def test_identity_eligible_decomposes(self):
        # Run a mix of decisions and verify the identity holds for each
        # archetype + rule. eligible == enabled_eligible + diagnostic_only.
        controller = self._controller_with_manager()

        decisions = [
            # (archetype, vvs_raw, vvs_used, override_fired)
            ('tag',  0.6, 0.6, False),  # vvs fired
            ('tag',  0.6, 0.6, True),   # vvs superseded
            ('tag',  0.4, 0.4, False),  # vvs fired
            ('lag',  0.5, 0.0, False),  # vvs diagnostic only
            ('lag',  0.5, 0.0, False),  # vvs diagnostic only
            ('nit',  0.0, 0.0, False),  # nothing
        ]
        for archetype, vvs_raw, vvs_used, override_fired in decisions:
            self._stash(controller, archetype=archetype,
                        vvs_raw=vvs_raw, vvs_used=vvs_used,
                        override_fired=override_fired)
            controller._tally_playstyle_rule_event()

        c = controller.opponent_model_manager._exploitation_counters

        # TAG: 3 eligible, 3 enabled, 2 fired + 1 superseded, 0 diagnostic
        assert c['value_vs_station_eligible_tag'] == 3
        assert (
            c['value_vs_station_eligible_tag']
            == c['value_vs_station_enabled_eligible_tag']
            + c['value_vs_station_diagnostic_only_tag']
        )
        assert (
            c['value_vs_station_enabled_eligible_tag']
            == c['value_vs_station_fired_tag']
            + c['value_vs_station_superseded_by_override_tag']
        )

        # LAG: 2 eligible, 0 enabled, 0 fired, 0 superseded, 2 diagnostic
        assert c['value_vs_station_eligible_lag'] == 2
        assert c['value_vs_station_diagnostic_only_lag'] == 2
        assert c['value_vs_station_fired_lag'] == 0
        assert c['value_vs_station_superseded_by_override_lag'] == 0

    def test_no_archetype_means_no_tally(self):
        # _apply_exploitation early-out path → archetype not stashed →
        # tally is a no-op (no counters touched).
        controller = self._controller_with_manager()
        controller._last_exploitation_archetype = None
        controller._tally_playstyle_rule_event()
        c = controller.opponent_model_manager._exploitation_counters
        assert len(c) == 0

    def test_vvs_blocked_by_bias_floor(self):
        # Heavy tilt / very low adaptation_bias → effective_bias <=
        # GATING_FLOOR → compute_exploitation_offsets bails before Phase 8
        # branches. Counter must distinguish this from "fired."
        controller = self._controller_with_manager()
        self._stash(controller, archetype='tag',
                    vvs_raw=0.6, vvs_used=0.6,
                    override_fired=False, will_emit=False)
        controller._tally_playstyle_rule_event()
        c = controller.opponent_model_manager._exploitation_counters

        assert c['value_vs_station_eligible_tag'] == 1
        assert c['value_vs_station_enabled_eligible_tag'] == 1
        assert c['value_vs_station_blocked_by_bias_floor_tag'] == 1
        # Crucially: fired is NOT incremented when offsets weren't emitted.
        assert c['value_vs_station_fired_tag'] == 0
        assert c['value_vs_station_superseded_by_override_tag'] == 0

    def test_steal_pressure_blocked_by_bias_floor(self):
        controller = self._controller_with_manager()
        self._stash(controller, archetype='lag',
                    steal_raw=0.4, steal_used=0.4, will_emit=False)
        controller._tally_playstyle_rule_event()
        c = controller.opponent_model_manager._exploitation_counters

        assert c['steal_pressure_enabled_eligible_lag'] == 1
        assert c['steal_pressure_blocked_by_bias_floor_lag'] == 1
        assert c['steal_pressure_fired_lag'] == 0

    def test_extended_identity_with_bias_floor(self):
        # Extended identity:
        # enabled_eligible = fired + superseded + blocked_by_bias_floor
        controller = self._controller_with_manager()
        decisions = [
            # (vvs_raw, vvs_used, override_fired, will_emit)
            (0.6, 0.6, False, True),   # fired
            (0.6, 0.6, True,  True),   # superseded
            (0.6, 0.6, False, False),  # blocked by bias floor
            (0.6, 0.6, False, False),  # blocked by bias floor
            (0.5, 0.0, False, True),   # diagnostic_only
        ]
        for vvs_raw, vvs_used, override_fired, will_emit in decisions:
            self._stash(controller, archetype='tag',
                        vvs_raw=vvs_raw, vvs_used=vvs_used,
                        override_fired=override_fired, will_emit=will_emit)
            controller._tally_playstyle_rule_event()
        c = controller.opponent_model_manager._exploitation_counters

        assert c['value_vs_station_eligible_tag'] == 5
        assert (
            c['value_vs_station_eligible_tag']
            == c['value_vs_station_enabled_eligible_tag']
            + c['value_vs_station_diagnostic_only_tag']
        )
        assert (
            c['value_vs_station_enabled_eligible_tag']
            == c['value_vs_station_fired_tag']
            + c['value_vs_station_superseded_by_override_tag']
            + c['value_vs_station_blocked_by_bias_floor_tag']
        )
        assert c['value_vs_station_fired_tag'] == 1
        assert c['value_vs_station_superseded_by_override_tag'] == 1
        assert c['value_vs_station_blocked_by_bias_floor_tag'] == 2
        assert c['value_vs_station_diagnostic_only_tag'] == 1


# ── Aggregate cold-start bypass for Phase 8 ───────────────────────────────

class TestAggregateColdStartBypass:
    """compute_exploitation_offsets should let Phase 8 branches fire even
    when the aggregate stats look cold-start, because the intensity
    helpers already self-gate on per-opponent samples.
    """

    def test_cold_start_aggregate_blocks_legacy_rules(self):
        # vpip > 0.60 + AF < 0.80 would trigger hyper_passive, but the
        # aggregate cold-start gate (hands < 15) suppresses it.
        offsets = compute_exploitation_offsets(
            stats=_stats(hands_observed=5, vpip=0.85,
                         aggression_factor=0.4),
            adaptation_bias=0.9,
            decision_context=_basic_decision_context(is_preflop=False),
            available_actions=['check', 'bet_33', 'bet_67', 'fold'],
        )
        # Without Phase 8 intensities and with cold-start aggregate,
        # behavior matches the pre-Phase-8 contract: empty offsets.
        assert offsets == {}

    def test_cold_start_aggregate_does_not_block_value_vs_station(self):
        # Same cold-start aggregate, but value_vs_station_intensity > 0
        # because a per-opponent station was found. Phase 8 branch must
        # still emit bet_* offsets.
        offsets = compute_exploitation_offsets(
            stats=_stats(hands_observed=5, vpip=0.85,
                         aggression_factor=0.4),
            adaptation_bias=0.9,
            decision_context=_basic_decision_context(is_preflop=False),
            available_actions=['check', 'bet_33', 'bet_67'],
            value_vs_station_intensity=1.0,
        )
        assert offsets.get('bet_33', 0.0) > 0.0
        assert offsets.get('check', 0.0) < 0.0

    def test_cold_start_aggregate_does_not_block_steal_pressure(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(hands_observed=5, vpip=0.85,
                         aggression_factor=0.4),
            adaptation_bias=0.9,
            decision_context=_basic_decision_context(is_preflop=True),
            available_actions=['fold', 'call', 'raise_2.5bb'],
            steal_pressure_intensity=1.0,
        )
        assert offsets.get('raise_2.5bb', 0.0) > 0.0

    def test_gating_floor_still_blocks_phase_8(self):
        # adaptation_bias × tilt_factor <= GATING_FLOOR should block
        # ALL rules including Phase 8. The cold-start bypass doesn't
        # imply ignoring the broader bias gate.
        offsets = compute_exploitation_offsets(
            stats=_stats(hands_observed=50, vpip=0.85,
                         aggression_factor=0.4),
            adaptation_bias=0.01,  # well below GATING_FLOOR
            decision_context=_basic_decision_context(is_preflop=False),
            available_actions=['check', 'bet_33'],
            value_vs_station_intensity=1.0,
        )
        assert offsets == {}
