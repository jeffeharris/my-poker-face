"""Smoke test: every BUILT_IN_STRATEGIES strategy runs against a typical
rule_context without raising and returns a well-formed decision dict.

After the rule strategy library extraction (rule_based_controller.py →
rule_strategies.py), RuleBotController imports from the new module while
RuleBasedController re-exports the old names. A strategy that broke during
extraction would only surface at game time without this test.
"""
import unittest

from poker.rule_strategies import BUILT_IN_STRATEGIES


def _baseline_context(**overrides):
    """Realistic mid-hand context: BTN, premium hand, top-pair-ish equity, facing a 2bb open."""
    ctx = {
        'player_name': 'TestBot',
        'player_stack': 5000,
        'stack_bb': 50.0,
        'pot_total': 300,
        'pot_odds': 3.0,
        'cost_to_call': 100,
        'highest_bet': 200,
        'min_raise': 400,
        'max_raise': 5000,
        'big_blind': 100,
        'equity': 0.55,
        'canonical_hand': 'AKo',
        'hole_cards': ['Ah', 'Kd'],
        'community_cards': [],
        'phase': 'PRE_FLOP',
        'position': 'button',
        'num_opponents': 2,
        'effective_stack': 5000,
        'effective_stack_bb': 50.0,
        'spr': 16.67,
        'valid_actions': ['fold', 'call', 'raise'],
        # Opponent stats (adaptive strategies may consult these)
        'vpip_opps': 0.30,
        'pfr_opps': 0.20,
        'aggression_factor': 1.5,
        # Adaptive flags
        'is_premium': True,
        'is_top_10': True,
        'is_top_20': True,
        'is_suited': False,
        'is_pair': False,
    }
    ctx.update(overrides)
    return ctx


class TestBuiltInStrategiesSmoke(unittest.TestCase):
    """Each strategy must return a valid decision shape on a baseline context."""

    VALID_ACTIONS = {'fold', 'check', 'call', 'raise', 'all_in', 'all-in'}

    def test_built_in_strategies_registered(self):
        """Sanity check: the strategies the route advertises are all present."""
        expected = {
            'always_fold', 'always_call', 'always_raise', 'always_all_in',
            'abc', 'foldy', 'position_aware', 'pot_odds_robot',
            'maniac', 'trap_bait', 'bluffbot', 'case_based',
        }
        self.assertTrue(
            expected.issubset(set(BUILT_IN_STRATEGIES.keys())),
            f"Missing strategies: {expected - set(BUILT_IN_STRATEGIES.keys())}",
        )

    def test_each_strategy_returns_valid_decision(self):
        """Every registered strategy runs and returns {action, [amount]}."""
        ctx = _baseline_context()
        for name, fn in BUILT_IN_STRATEGIES.items():
            with self.subTest(strategy=name):
                decision = fn(ctx)
                self.assertIsInstance(decision, dict, f"{name} returned non-dict")
                self.assertIn('action', decision, f"{name} missing 'action' key")
                self.assertIn(
                    decision['action'], self.VALID_ACTIONS,
                    f"{name} returned unknown action: {decision['action']!r}",
                )
                # If the action is a raise, amount must be a number
                if decision['action'] == 'raise':
                    amt = decision.get('amount') or decision.get('raise_to')
                    self.assertIsNotNone(amt, f"{name} raise without amount")
                    self.assertIsInstance(amt, (int, float))

    def test_strategies_handle_free_check_context(self):
        """Strategies must not crash when cost_to_call=0 (free check available)."""
        ctx = _baseline_context(
            cost_to_call=0, pot_odds=None, highest_bet=0,
            valid_actions=['check', 'raise'],
        )
        for name, fn in BUILT_IN_STRATEGIES.items():
            with self.subTest(strategy=name):
                decision = fn(ctx)
                self.assertIn(decision['action'], self.VALID_ACTIONS)


if __name__ == '__main__':
    unittest.main()
