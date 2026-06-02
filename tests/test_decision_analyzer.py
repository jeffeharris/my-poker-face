"""Tests for decision analyzer, focusing on stack-aware EV calculations."""

import pytest

from poker.decision_analyzer import (
    DecisionAnalysis,
    DecisionAnalyzer,
    calculate_max_winnable,
)


class TestCalculateMaxWinnable:
    """Tests for the calculate_max_winnable helper function."""

    def test_short_stack_limited(self):
        """Short stack can only win a portion of the pot.

        Example: Player has 100 chips, opponent bet 500
        - Hero calls all-in for 100
        - Main pot = 100 (hero) + 100 (matched from villain) = 200
        """
        all_players_bets = [
            (0, False),  # Hero: bet=0
            (500, False),  # Villain: bet=500
        ]

        max_winnable = calculate_max_winnable(
            player_bet=0,
            player_stack=100,
            cost_to_call=500,
            all_players_bets=all_players_bets,
        )

        # Hero's 100 + villain's matched 100 = 200
        assert max_winnable == 200

    def test_big_stack_unchanged(self):
        """Big stack EV uses full pot (no side pot limit)."""
        all_players_bets = [
            (0, False),  # Hero
            (100, False),  # Villain
        ]

        max_winnable = calculate_max_winnable(
            player_bet=0,
            player_stack=1000,
            cost_to_call=100,
            all_players_bets=all_players_bets,
        )

        # Hero's 100 + villain's 100 = 200
        assert max_winnable == 200

    def test_multiway_short_stack(self):
        """3-way pot: short stack can only win from matched contributions."""
        all_players_bets = [
            (0, False),  # Hero (stack=100)
            (300, False),  # Villain1
            (300, False),  # Villain2
        ]

        max_winnable = calculate_max_winnable(
            player_bet=0,
            player_stack=100,
            cost_to_call=300,
            all_players_bets=all_players_bets,
        )

        # Hero's 100 + 100 matched from each villain = 300
        assert max_winnable == 300

    def test_with_folded_players(self):
        """Folded players' bets are dead money - still winnable."""
        all_players_bets = [
            (50, False),  # Hero (stack=100, already bet 50)
            (50, True),  # Folded player (dead money)
            (200, False),  # Villain
        ]

        max_winnable = calculate_max_winnable(
            player_bet=50,
            player_stack=100,
            cost_to_call=150,
            all_players_bets=all_players_bets,
        )

        # Hero's contribution = 50 (existing) + 100 (call) = 150
        # Matched: 50 (hero) + 50 (folded) + 150 (villain) + 100 (hero's call) = 350
        assert max_winnable == 350

    def test_player_already_all_in(self):
        """Player already all-in - cost_to_call is 0."""
        # Hero already all-in for 200, no more to call
        all_players_bets = [
            (200, False),  # Hero (all-in)
            (500, False),  # Villain
        ]

        max_winnable = calculate_max_winnable(
            player_bet=200,
            player_stack=0,  # All-in, no more chips
            cost_to_call=0,  # Already matched or all-in
            all_players_bets=all_players_bets,
        )

        # Hero's contribution = 200 + min(0, 0) = 200
        # hero: min(200, 200) = 200
        # villain: min(500, 200) = 200
        # Total = 400
        assert max_winnable == 400


class TestAnalyzerEVCalculation:
    """Tests for EV calculation with max_winnable."""

    def test_ev_short_stack_limited(self):
        """Short stack EV uses max_winnable, not full pot."""
        analyzer = DecisionAnalyzer(iterations=100)

        # Setup: player has 100, opponent bet 500, pot = 600
        # With 50% equity:
        # - Full pot EV (incorrect): 0.5 * 600 - 0.5 * 100 = +250
        # - Short stack EV (correct): 0.5 * 200 - 0.5 * 100 = +50
        analysis = analyzer.analyze(
            game_id="test",
            player_name="Hero",
            hand_number=1,
            phase="FLOP",
            player_hand=["Ah", "Kh"],  # Strong hand
            community_cards=["Qh", "Jh", "2d"],  # Flush draw
            pot_total=600,
            cost_to_call=500,
            player_stack=100,
            num_opponents=1,
            action_taken="call",
            player_bet=0,
            all_players_bets=[(0, False), (500, False)],
        )

        # Verify max_winnable was calculated correctly
        assert analysis.max_winnable == 200

        # Verify EV uses the limited winnable amount
        # With whatever equity the Monte Carlo gives, EV should be based on 200 pot
        # not 600 pot. We can verify the formula was applied correctly.
        if analysis.equity is not None:
            expected_ev = (analysis.equity * 200) - ((1 - analysis.equity) * 100)
            assert abs(analysis.ev_call - expected_ev) < 0.01

    def test_ev_big_stack_unchanged(self):
        """Big stack EV uses full pot (max_winnable equals pot_total)."""
        analyzer = DecisionAnalyzer(iterations=100)

        analysis = analyzer.analyze(
            game_id="test",
            player_name="Hero",
            hand_number=1,
            phase="FLOP",
            player_hand=["Ah", "Kh"],
            community_cards=["Qh", "Jh", "2d"],
            pot_total=200,
            cost_to_call=100,
            player_stack=1000,
            num_opponents=1,
            action_taken="call",
            player_bet=0,
            all_players_bets=[(0, False), (100, False)],
        )

        # Max winnable = 200 (hero 100 + villain 100), same as pot
        assert analysis.max_winnable == 200

    def test_ev_without_player_bets_data_uses_pot_total(self):
        """Without player bets data, falls back to pot_total for EV."""
        analyzer = DecisionAnalyzer(iterations=100)

        analysis = analyzer.analyze(
            game_id="test",
            player_name="Hero",
            hand_number=1,
            phase="FLOP",
            player_hand=["Ah", "Kh"],
            community_cards=["Qh", "Jh", "2d"],
            pot_total=600,
            cost_to_call=100,
            player_stack=100,
            num_opponents=1,
            action_taken="call",
            # No player_bet or all_players_bets - should use pot_total
        )

        # max_winnable should be None when data not provided
        assert analysis.max_winnable is None

        # EV should use pot_total (600) as fallback
        if analysis.equity is not None:
            expected_ev = (analysis.equity * 600) - ((1 - analysis.equity) * 100)
            assert abs(analysis.ev_call - expected_ev) < 0.01


class TestDecisionAnalysisDataclass:
    """Tests for DecisionAnalysis dataclass."""

    def test_max_winnable_field_exists(self):
        """max_winnable field is present in DecisionAnalysis."""
        analysis = DecisionAnalysis(
            game_id="test",
            player_name="Hero",
        )
        assert hasattr(analysis, 'max_winnable')
        assert analysis.max_winnable is None

    def test_to_dict_includes_max_winnable(self):
        """to_dict() includes max_winnable field."""
        analysis = DecisionAnalysis(
            game_id="test",
            player_name="Hero",
            max_winnable=500,
        )
        d = analysis.to_dict()
        assert 'max_winnable' in d
        assert d['max_winnable'] == 500


class TestPsychologySnapshot:
    """Tests for psychology snapshot fields on DecisionAnalysis."""

    def test_psychology_fields_default_to_none(self):
        """All psychology fields default to None."""
        analysis = DecisionAnalysis(game_id="test", player_name="Hero")
        assert analysis.tilt_level is None
        assert analysis.tilt_source is None
        assert analysis.display_emotion is None
        assert analysis.elastic_aggression is None
        assert analysis.elastic_bluff_tendency is None

    def test_psychology_fields_set_directly(self):
        """Psychology fields can be set on construction."""
        analysis = DecisionAnalysis(
            game_id="test",
            player_name="Hero",
            tilt_level=0.35,
            tilt_source="bad_beat",
            display_emotion="angry",
            elastic_aggression=0.7,
            elastic_bluff_tendency=0.4,
        )
        assert analysis.tilt_level == 0.35
        assert analysis.tilt_source == "bad_beat"
        assert analysis.display_emotion == "angry"
        assert analysis.elastic_aggression == 0.7
        assert analysis.elastic_bluff_tendency == 0.4

    def test_to_dict_includes_psychology_fields(self):
        """to_dict() includes all psychology snapshot fields."""
        analysis = DecisionAnalysis(
            game_id="test",
            player_name="Hero",
            tilt_level=0.5,
            display_emotion="nervous",
        )
        d = analysis.to_dict()
        assert d['tilt_level'] == 0.5
        assert d['display_emotion'] == "nervous"
        # None fields should also be present
        assert 'tilt_source' in d
        assert d['tilt_source'] is None

    def test_analyzer_passes_psychology_snapshot(self):
        """DecisionAnalyzer.analyze() applies psychology_snapshot to result."""
        analyzer = DecisionAnalyzer(iterations=10)
        snapshot = {
            'tilt_level': 0.45,
            'tilt_source': 'losing_streak',
            'display_emotion': 'nervous',
            'elastic_aggression': 0.65,
            'elastic_bluff_tendency': 0.3,
        }
        analysis = analyzer.analyze(
            game_id="test",
            player_name="Hero",
            hand_number=5,
            phase="FLOP",
            player_hand=["As", "Kd"],
            community_cards=["Jh", "2d", "5s"],
            pot_total=200,
            cost_to_call=50,
            player_stack=500,
            num_opponents=1,
            action_taken="call",
            psychology_snapshot=snapshot,
        )
        assert analysis.tilt_level == 0.45
        assert analysis.tilt_source == 'losing_streak'
        assert analysis.display_emotion == 'nervous'
        assert analysis.elastic_aggression == 0.65
        assert analysis.elastic_bluff_tendency == 0.3

    def test_analyzer_without_psychology_snapshot(self):
        """analyze() works fine without psychology_snapshot (backward compat)."""
        analyzer = DecisionAnalyzer(iterations=10)
        analysis = analyzer.analyze(
            game_id="test",
            player_name="Hero",
            hand_number=1,
            phase="PRE_FLOP",
            player_hand=["As", "Kd"],
            community_cards=[],
            pot_total=150,
            cost_to_call=100,
            player_stack=1000,
            num_opponents=1,
            action_taken="call",
        )
        assert analysis.tilt_level is None
        assert analysis.display_emotion is None


class TestPositionAdjustments:
    """Tests for position-based equity adjustments in determine_optimal_action."""

    def _make_analyzer(self):
        """Create analyzer with minimal iterations for speed."""
        return DecisionAnalyzer(iterations=10)

    def test_early_position_adjustment(self):
        """Early position adds +0.08 to required equity threshold."""
        analyzer = self._make_analyzer()
        adjustment = analyzer._get_position_adjustment('under_the_gun')
        assert adjustment == 0.08

    def test_middle_position_adjustment(self):
        """Middle position adds +0.03 to required equity threshold."""
        analyzer = self._make_analyzer()
        adjustment = analyzer._get_position_adjustment('middle_position_1')
        assert adjustment == 0.03

        # Also test other middle positions
        assert analyzer._get_position_adjustment('middle_position_2') == 0.03
        assert analyzer._get_position_adjustment('middle_position_3') == 0.03

    def test_late_position_adjustment(self):
        """Late position subtracts -0.05 from required equity threshold."""
        analyzer = self._make_analyzer()
        adjustment = analyzer._get_position_adjustment('button')
        assert adjustment == -0.05

        # Also test cutoff
        assert analyzer._get_position_adjustment('cutoff') == -0.05

    def test_blind_position_adjustment(self):
        """Blind position subtracts -0.03 from required equity threshold."""
        analyzer = self._make_analyzer()
        adjustment = analyzer._get_position_adjustment('small_blind_player')
        assert adjustment == -0.03

        # Also test big blind
        assert analyzer._get_position_adjustment('big_blind_player') == -0.03

    def test_unknown_position_defaults_to_late(self):
        """Unknown position defaults to LATE position (-0.05 adjustment).

        This is a conservative default that gives the player benefit of the doubt.
        """
        analyzer = self._make_analyzer()
        adjustment = analyzer._get_position_adjustment('unknown_position')
        # Unknown positions default to LATE in get_position_group
        assert adjustment == -0.05

    def test_none_position_no_adjustment(self):
        """None position returns 0.0 adjustment."""
        analyzer = self._make_analyzer()
        adjustment = analyzer._get_position_adjustment(None)
        assert adjustment == 0.0

    def test_button_is_late_position(self):
        """Button should map to late position group."""
        from poker.hand_ranges import Position, get_position_group

        position_group = get_position_group('button')
        assert position_group == Position.LATE

    def test_utg_is_early_position(self):
        """UTG should map to early position group."""
        from poker.hand_ranges import Position, get_position_group

        position_group = get_position_group('under_the_gun')
        assert position_group == Position.EARLY

    def test_position_affects_raise_threshold(self):
        """Position adjustment affects the raise threshold in determine_optimal_action."""
        analyzer = self._make_analyzer()

        # High equity (60%) that might be borderline for raising
        base_args = {
            'equity': 0.60,
            'ev_call': 30.0,
            'required_equity': 0.25,
            'num_opponents': 1,
            'phase': 'FLOP',
            'pot_total': 100,
            'cost_to_call': 25,
            'player_stack': 500,
        }

        # Late position (button) - more likely to raise with 60% equity
        late_result = analyzer.determine_optimal_action(**base_args, player_position='button')

        # Early position (UTG) - less likely to raise, needs more equity
        early_result = analyzer.determine_optimal_action(
            **base_args, player_position='under_the_gun'
        )

        # At 60% equity:
        # - Late position: raise threshold ~0.50 (0.55 - 0.05), should raise
        # - Early position: raise threshold ~0.63 (0.55 + 0.08), might not raise
        # Both should at least call (EV is positive)
        assert late_result in ('raise', 'call')
        assert early_result in ('raise', 'call')

    def test_position_affects_call_threshold(self):
        """Position adjustment affects call threshold in determine_optimal_action."""
        analyzer = self._make_analyzer()

        # Borderline calling spot with 28% equity
        base_args = {
            'equity': 0.28,
            'ev_call': 5.0,  # Small positive EV
            'required_equity': 0.25,
            'num_opponents': 1,
            'phase': 'FLOP',
            'pot_total': 100,
            'cost_to_call': 33,
            'player_stack': 500,
        }

        # Late position - more willing to call
        late_result = analyzer.determine_optimal_action(**base_args, player_position='button')

        # Both positions should call with positive EV
        assert late_result == 'call'


class TestDetermineOptimalAction:
    """Additional tests for determine_optimal_action edge cases."""

    def _make_analyzer(self):
        return DecisionAnalyzer(iterations=10)

    def test_check_when_can_and_medium_equity(self):
        """With 0 cost to call and medium equity, should check (not bet)."""
        analyzer = self._make_analyzer()
        result = analyzer.determine_optimal_action(
            equity=0.40,
            ev_call=0.0,
            required_equity=0.0,
            num_opponents=1,
            phase='FLOP',
            pot_total=50,
            cost_to_call=0,
            player_stack=500,
        )
        assert result == 'check'

    def test_bet_when_high_equity_and_can_check(self):
        """With high equity and 0 cost to call, should bet for value."""
        analyzer = self._make_analyzer()
        result = analyzer.determine_optimal_action(
            equity=0.75,
            ev_call=0.0,
            required_equity=0.0,
            num_opponents=1,
            phase='FLOP',
            pot_total=50,
            cost_to_call=0,
            player_stack=500,
        )
        assert result == 'raise'  # 'raise' is used for both bet and raise

    def test_fold_negative_ev(self):
        """With negative EV, should fold."""
        analyzer = self._make_analyzer()
        result = analyzer.determine_optimal_action(
            equity=0.15,
            ev_call=-20.0,
            required_equity=0.33,
            num_opponents=1,
            phase='FLOP',
            pot_total=100,
            cost_to_call=50,
            player_stack=500,
        )
        assert result == 'fold'

    def test_call_positive_ev_below_raise_threshold(self):
        """With positive EV but below raise threshold, should call."""
        analyzer = self._make_analyzer()
        result = analyzer.determine_optimal_action(
            equity=0.40,
            ev_call=15.0,
            required_equity=0.25,
            num_opponents=1,
            phase='FLOP',
            pot_total=100,
            cost_to_call=33,
            player_stack=500,
        )
        assert result == 'call'


class TestQualityScore:
    """Tests for composite quality_score field."""

    def _make_analyzer(self):
        return DecisionAnalyzer(iterations=10)

    def _make_analysis(self, **kwargs):
        defaults = dict(
            game_id="test",
            player_name="Hero",
            pot_total=100,
            cost_to_call=50,
            player_stack=500,
            num_opponents=1,
        )
        defaults.update(kwargs)
        return DecisionAnalysis(**defaults)

    def test_correct_gets_100(self):
        """Correct decision should get quality_score=100."""
        analyzer = self._make_analyzer()
        # Set up a clear fold scenario (low equity, negative EV)
        analysis = self._make_analysis(
            equity=0.1,
            ev_call=-30.0,
            required_equity=0.33,
            action_taken='fold',
            phase='FLOP',
        )
        analyzer._evaluate_quality(analysis)
        assert analysis.decision_quality == 'correct'
        assert analysis.quality_score == 100.0

    def test_mistake_gets_0(self):
        """Mistake should get quality_score=0."""
        analyzer = self._make_analyzer()
        # Folding when can check for free = mistake
        analysis = self._make_analysis(
            cost_to_call=0,
            equity=0.5,
            ev_call=0,
            action_taken='fold',
            phase='FLOP',
        )
        analyzer._evaluate_quality(analysis)
        assert analysis.decision_quality == 'mistake'
        assert analysis.quality_score == 0.0

    def test_marginal_gets_50(self):
        """Marginal decision should get quality_score=50."""
        analyzer = self._make_analyzer()
        # Call when should raise = marginal
        analysis = self._make_analysis(
            equity=0.7,
            ev_call=50.0,
            required_equity=0.25,
            action_taken='call',
            phase='FLOP',
        )
        analyzer._evaluate_quality(analysis)
        assert analysis.decision_quality == 'marginal'
        assert analysis.quality_score == 50.0

    def test_unknown_gets_none(self):
        """Unknown decision (no ev_call) should get quality_score=None."""
        analyzer = self._make_analyzer()
        analysis = self._make_analysis(
            ev_call=None,
            action_taken='call',
            phase='FLOP',
        )
        analyzer._evaluate_quality(analysis)
        assert analysis.decision_quality == 'unknown'
        assert analysis.quality_score is None


class TestEffectiveEquity:
    """Tests for v2: range-based equity preferred over random-hand equity.

    Random-hand equity (analysis.equity) systematically overestimates hero's
    chances against typical opponent ranges. Quality scoring should prefer
    equity_vs_ranges when present and fall back to equity when not.
    """

    def _make_analyzer(self):
        return DecisionAnalyzer(iterations=10)

    def _make_analysis(self, **kwargs):
        defaults = dict(
            game_id="test",
            player_name="Hero",
            pot_total=100,
            cost_to_call=50,
            player_stack=500,
            num_opponents=1,
        )
        defaults.update(kwargs)
        return DecisionAnalysis(**defaults)

    def test_prefers_range_equity_when_both_set(self):
        """When both equity fields are populated, range-based wins."""
        analysis = self._make_analysis(equity=0.60, equity_vs_ranges=0.25)
        assert DecisionAnalyzer._effective_equity(analysis) == 0.25

    def test_falls_back_to_random_equity(self):
        """Without range-based equity, fall back to vs-random."""
        analysis = self._make_analysis(equity=0.40, equity_vs_ranges=None)
        assert DecisionAnalyzer._effective_equity(analysis) == 0.40

    def test_returns_none_when_both_missing(self):
        analysis = self._make_analysis(equity=None, equity_vs_ranges=None)
        assert DecisionAnalyzer._effective_equity(analysis) is None

    def test_quality_flips_to_fold_when_range_equity_is_low(self):
        # Random-hand equity makes this look +EV (called -EV would be marginal),
        # but vs actual ranges the call is a clear -EV mistake.
        analyzer = self._make_analyzer()
        analysis = self._make_analysis(
            equity=0.55,
            equity_vs_ranges=0.20,
            ev_call=-15.0,  # negative because computed from range equity upstream
            required_equity=0.33,
            action_taken='call',
            phase='FLOP',
        )
        analyzer._evaluate_quality(analysis)
        assert analysis.optimal_action == 'fold'
        assert analysis.decision_quality == 'mistake'
        assert analysis.ev_lost == 15.0

    def test_fold_when_can_check_uses_range_equity_for_ev_lost(self):
        # Folding for free should burn `eq_range * pot`, not `eq_random * pot`.
        analyzer = self._make_analyzer()
        analysis = self._make_analysis(
            cost_to_call=0,
            equity=0.60,
            equity_vs_ranges=0.30,
            ev_call=0,
            action_taken='fold',
            phase='FLOP',
        )
        analyzer._evaluate_quality(analysis)
        assert analysis.decision_quality == 'mistake'
        assert analysis.ev_lost == pytest.approx(0.30 * 100)


class TestMenuCompliance:
    """Tests for evaluate_menu_compliance()."""

    def _make_analyzer(self):
        return DecisionAnalyzer(iterations=10)

    def _make_analysis(self, action='call', raise_amount=None):
        return DecisionAnalysis(
            game_id="test",
            player_name="Hero",
            action_taken=action,
            raise_amount=raise_amount,
        )

    def test_picks_best_plus_ev_option(self):
        """AI picks +EV option when it's the best available."""
        analyzer = self._make_analyzer()
        analysis = self._make_analysis(action='call')
        options = [
            {'action': 'fold', 'ev_estimate': '-EV', 'raise_to': None},
            {'action': 'call', 'ev_estimate': '+EV', 'raise_to': None},
            {'action': 'raise', 'ev_estimate': 'neutral', 'raise_to': 200},
        ]
        analyzer.evaluate_menu_compliance(analysis, options)
        assert analysis.menu_picked_best is True
        assert analysis.menu_best_ev == '+EV'
        assert analysis.menu_chosen_ev == '+EV'
        assert analysis.menu_num_options == 3

    def test_picks_suboptimal_option(self):
        """AI picks -EV fold when +EV call was available."""
        analyzer = self._make_analyzer()
        analysis = self._make_analysis(action='fold')
        options = [
            {'action': 'fold', 'ev_estimate': '-EV', 'raise_to': None},
            {'action': 'call', 'ev_estimate': '+EV', 'raise_to': None},
        ]
        analyzer.evaluate_menu_compliance(analysis, options)
        assert analysis.menu_picked_best is False
        assert analysis.menu_best_ev == '+EV'
        assert analysis.menu_chosen_ev == '-EV'

    def test_raise_matches_by_raise_to(self):
        """Raise options match by raise_to amount."""
        analyzer = self._make_analyzer()
        analysis = self._make_analysis(action='raise', raise_amount=300)
        options = [
            {'action': 'raise', 'ev_estimate': '+EV', 'raise_to': 200},
            {'action': 'raise', 'ev_estimate': 'neutral', 'raise_to': 300},
            {'action': 'call', 'ev_estimate': '+EV', 'raise_to': None},
        ]
        analyzer.evaluate_menu_compliance(analysis, options)
        assert analysis.menu_chosen_ev == 'neutral'
        assert analysis.menu_picked_best is False  # +EV raise was better

    def test_no_bounded_options_leaves_none(self):
        """No bounded options = all menu fields stay None."""
        analyzer = self._make_analyzer()
        analysis = self._make_analysis(action='call')
        analyzer.evaluate_menu_compliance(analysis, [])
        assert analysis.menu_picked_best is None
        assert analysis.menu_best_ev is None

    def test_tie_prefers_non_fold(self):
        """When EV labels tie, non-fold is preferred as best."""
        analyzer = self._make_analyzer()
        analysis = self._make_analysis(action='check')
        options = [
            {'action': 'fold', 'ev_estimate': 'neutral', 'raise_to': None},
            {'action': 'check', 'ev_estimate': 'neutral', 'raise_to': None},
        ]
        analyzer.evaluate_menu_compliance(analysis, options)
        assert analysis.menu_picked_best is True
        assert analysis.menu_best_ev == 'neutral'

    def test_dataclass_fields_default_none(self):
        """New menu fields default to None in dataclass."""
        analysis = DecisionAnalysis(game_id="test", player_name="Hero")
        assert analysis.quality_score is None
        assert analysis.menu_best_ev is None
        assert analysis.menu_chosen_ev is None
        assert analysis.menu_picked_best is None
        assert analysis.menu_num_options is None


class TestGetAnalyzerIterations:
    """PRH-30: the in-game analyzer's MC iteration count is env-configurable."""

    def _reset_singleton(self):
        import poker.decision_analyzer as da

        da._analyzer_instance = None

    def test_default_iterations(self, monkeypatch):
        monkeypatch.delenv("DECISION_ANALYSIS_ITERATIONS", raising=False)
        self._reset_singleton()
        from poker.decision_analyzer import get_analyzer

        assert get_analyzer().iterations == 2000
        self._reset_singleton()

    def test_env_lowers_iterations(self, monkeypatch):
        monkeypatch.setenv("DECISION_ANALYSIS_ITERATIONS", "500")
        self._reset_singleton()
        from poker.decision_analyzer import get_analyzer

        assert get_analyzer().iterations == 500
        self._reset_singleton()

    def test_explicit_arg_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("DECISION_ANALYSIS_ITERATIONS", "500")
        self._reset_singleton()
        from poker.decision_analyzer import get_analyzer

        assert get_analyzer(iterations=123).iterations == 123
        self._reset_singleton()
