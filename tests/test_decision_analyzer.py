"""Tests for decision analyzer, focusing on stack-aware EV calculations."""

import pytest
from poker.decision_analyzer import (
    DecisionAnalyzer,
    DecisionAnalysis,
    calculate_max_winnable,
)


class TestCalculateMaxWinnable:
    """Tests for the calculate_max_winnable helper function."""

    def test_short_stack_limited(self):
        """Short stack can only win a portion of the pot.

        Example from plan:
        - Player has 100 chips, opponent bet 500, pot = 600
        - max_winnable = 200 (100 from player + 100 matched from opponent)
        """
        # Hero has bet 0, stack=100, opponent bet 500
        # cost_to_call = 500 (but hero can only call 100)
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

        # Hero contributes 100 (min(500, 100))
        # Hero wins: min(0, 100) + min(500, 100) = 0 + 100 = 100
        # Wait, this doesn't include hero's own contribution in the pot
        # Let me re-check the algorithm...
        # Actually, the all_players_bets includes ALL players including hero
        # So hero's bet (0) + effective_call (100) = player_contribution = 100
        # Sum: min(0, 100) [hero's bet] + min(500, 100) [villain's bet] = 0 + 100 = 100
        # But this seems wrong - hero puts in 100 to win 100 total?
        #
        # Re-reading the algorithm:
        # - player_contribution = player_bet + min(cost_to_call, player_stack) = 0 + 100 = 100
        # - For hero: min(0, 100) = 0
        # - For villain: min(500, 100) = 100
        # - Total: 100
        #
        # Hmm, this doesn't add hero's contribution to the winnings.
        # The pot they win should be their own chips + matched opponent chips = 200
        #
        # Looking at the docstring example again, it says max_winnable = 200
        # So the algorithm should sum ALL bets capped at player_contribution,
        # and hero's effective_call IS added to the pot (becomes a bet).
        #
        # Actually I think the issue is that when hero calls, their bet becomes 100
        # So the all_players_bets AFTER the call would be:
        # [(100, False), (500, False)] and then sum min(100, 100) + min(500, 100) = 200
        #
        # But we're calculating BEFORE the call. So we need to include the
        # effective_call in player's contribution to the pot they can win.
        #
        # Let me re-check the calculate_max_winnable function...
        # It does: player_contribution = player_bet + effective_call = 0 + 100 = 100
        # Then sums: min(each_bet, player_contribution)
        # For hero: min(0, 100) = 0
        # For villain: min(500, 100) = 100
        # Total = 100
        #
        # This seems to miss that hero is ADDING 100 to the pot!
        # The pot after hero calls all-in would have:
        # - Hero's 100
        # - Villain's 100 (matched portion)
        # - The rest of villain's 400 goes to side pot
        #
        # So the function should return 200, not 100.
        # The bug is that we're not counting hero's contribution to the pot.
        #
        # Actually, reading more carefully: all_players_bets contains the CURRENT
        # bets before hero acts. After hero acts, hero's bet would be 100.
        # So the pot hero is eligible to win includes their own contribution.
        #
        # I think the fix is: we should add player's effective contribution
        # to the max_winnable, since they're putting that in.
        #
        # Let me check if the function adds this or not...
        # Looking at the code: it sums min(bet, contribution) for ALL players
        # including hero. But hero's bet is 0 currently, not 100.
        #
        # The issue is the function calculates what hero can win from others'
        # bets, but doesn't include hero's own contribution as "winnable".
        # That's actually correct from an EV perspective - you don't "win"
        # your own chips back, you just get them back if you win.
        #
        # Wait, let me think about this from EV formula perspective:
        # EV = P(win) * pot_won - P(lose) * call_cost
        # pot_won = pot after calling = pot_total + call_amount
        # But for short stack, pot_won = matched_pot only
        #
        # In the example:
        # - Pot before hero's action = 600 (includes villain's 500 + prior 100)
        # - Hero calls all-in for 100
        # - Main pot = 100 (hero) + 100 (matched from villain) = 200
        # - Hero's max winnable = 200
        #
        # So max_winnable SHOULD include hero's contribution because that's
        # the pot hero is playing for.
        #
        # Let me re-examine the algorithm in the plan:
        # max_winnable = sum of min(each_player_bet, player_contribution)
        # where player_contribution = player.bet + min(cost, stack)
        #
        # So if hero's bet=0 and effective_call=100:
        # - player_contribution = 100
        # - hero's contribution: min(0, 100) = 0 (current bet before action)
        # - villain's contribution: min(500, 100) = 100
        # - Total = 100
        #
        # This is only the villain's matched chips, not hero's.
        # To get the full main pot (200), we need to also add hero's contribution.
        #
        # I think the original algorithm in the plan has a bug. Let me fix it
        # by adding player_contribution to the result.
        #
        # Actually wait - let me re-read the plan example more carefully...
        # "max_winnable = 200 (100 from each)"
        # So the 200 is: 100 from hero + 100 from villain = 200
        #
        # So the algorithm should return 200 for this case.
        # Let me check our implementation again...

        # Expected: 200 (hero's 100 + villain's matched 100)
        # The issue is the current implementation doesn't include hero's
        # contribution because hero's current bet is 0.
        assert max_winnable == 200

    def test_big_stack_unchanged(self):
        """Big stack EV uses full pot (no side pot limit)."""
        # Hero has bet 0, stack=1000, opponent bet 100
        # cost_to_call = 100
        all_players_bets = [
            (0, False),    # Hero: bet=0
            (100, False),  # Villain: bet=100
        ]

        max_winnable = calculate_max_winnable(
            player_bet=0,
            player_stack=1000,
            cost_to_call=100,
            all_players_bets=all_players_bets,
        )

        # Hero can cover the full call
        # player_contribution = 0 + min(100, 1000) = 100
        # hero: min(0, 100) = 0
        # villain: min(100, 100) = 100
        # Total from others = 100
        # Plus hero's contribution = 100
        # Expected total = 200
        assert max_winnable == 200

    def test_multiway_short_stack(self):
        """3-way pot: short stack can only win from matched contributions."""
        # Hero has bet 0, stack=100
        # Villain1 bet 300, Villain2 bet 300
        # cost_to_call = 300 (but hero can only call 100)
        all_players_bets = [
            (0, False),    # Hero
            (300, False),  # Villain1
            (300, False),  # Villain2
        ]

        max_winnable = calculate_max_winnable(
            player_bet=0,
            player_stack=100,
            cost_to_call=300,
            all_players_bets=all_players_bets,
        )

        # Hero's contribution = 100
        # hero: min(0, 100) = 0
        # villain1: min(300, 100) = 100
        # villain2: min(300, 100) = 100
        # Total = 200 from villains + 100 from hero = 300
        assert max_winnable == 300

    def test_with_folded_players(self):
        """Folded players' bets are dead money - still winnable."""
        # Hero has bet 50, stack=100
        # Folded player bet 50 earlier
        # Active villain bet 200
        # cost_to_call = 150
        all_players_bets = [
            (50, False),   # Hero
            (50, True),    # Folded player
            (200, False),  # Villain
        ]

        max_winnable = calculate_max_winnable(
            player_bet=50,
            player_stack=100,
            cost_to_call=150,
        all_players_bets=all_players_bets,
        )

        # Hero's contribution = 50 + min(150, 100) = 150
        # hero: min(50, 150) = 50
        # folded: min(50, 150) = 50 (dead money)
        # villain: min(200, 150) = 150
        # Total = 250 (plus hero's effective call of 100, total pot hero plays for = 350)
        # Actually: 50 + 50 + 150 = 250 from current bets
        # But hero adds 100 more, so hero's effective contribution is 150
        # The formula sums min(each_bet, hero_contribution) for each player
        # = min(50, 150) + min(50, 150) + min(200, 150) = 50 + 50 + 150 = 250
        # But we need to add hero's additional 100 to get the full main pot
        # Main pot = 50 (hero existing) + 100 (hero call) + 50 (folded) + 150 (villain matched)
        # = 350
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
