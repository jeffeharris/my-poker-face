"""Tests for decision analyzer, focusing on stack-aware EV calculations."""

from poker.decision_analyzer import (
    DecisionAnalyzer,
    DecisionAnalysis,
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
            (0, False),    # Hero: bet=0
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
            (0, False),    # Hero
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
            (0, False),    # Hero (stack=100)
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
            (50, False),   # Hero (stack=100, already bet 50)
            (50, True),    # Folded player (dead money)
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
        assert analysis.valence is None
        assert analysis.arousal is None
        assert analysis.control is None
        assert analysis.focus is None
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
            valence=-0.4,
            arousal=0.6,
            control=0.3,
            focus=0.5,
            display_emotion="angry",
            elastic_aggression=0.7,
            elastic_bluff_tendency=0.4,
        )
        assert analysis.tilt_level == 0.35
        assert analysis.tilt_source == "bad_beat"
        assert analysis.valence == -0.4
        assert analysis.arousal == 0.6
        assert analysis.control == 0.3
        assert analysis.focus == 0.5
        assert analysis.display_emotion == "angry"
        assert analysis.elastic_aggression == 0.7
        assert analysis.elastic_bluff_tendency == 0.4

    def test_to_dict_includes_psychology_fields(self):
        """to_dict() includes all psychology snapshot fields."""
        analysis = DecisionAnalysis(
            game_id="test",
            player_name="Hero",
            tilt_level=0.5,
            valence=-0.2,
            display_emotion="nervous",
        )
        d = analysis.to_dict()
        assert d['tilt_level'] == 0.5
        assert d['valence'] == -0.2
        assert d['display_emotion'] == "nervous"
        # None fields should also be present
        assert 'arousal' in d
        assert d['arousal'] is None

    def test_analyzer_passes_psychology_snapshot(self):
        """DecisionAnalyzer.analyze() applies psychology_snapshot to result."""
        analyzer = DecisionAnalyzer(iterations=10)
        snapshot = {
            'tilt_level': 0.45,
            'tilt_source': 'losing_streak',
            'valence': -0.3,
            'arousal': 0.7,
            'control': 0.4,
            'focus': 0.6,
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
        assert analysis.valence == -0.3
        assert analysis.arousal == 0.7
        assert analysis.control == 0.4
        assert analysis.focus == 0.6
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
        assert analysis.valence is None
        assert analysis.display_emotion is None
