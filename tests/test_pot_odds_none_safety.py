"""Defensive tests for the ``pot_odds`` contract.

``pot_odds`` is ``Optional[float]`` in this codebase. It is intentionally
set to ``None`` when ``cost_to_call == 0`` (free check — math is
undefined). See ``poker/hybrid_ai_controller.py:314`` for the canonical
source.

Several downstream consumers historically assumed pot_odds was always a
positive float and crashed when None reached them (format strings,
``1 / (pot_odds + 1)`` arithmetic, ``pot_odds >= 3`` comparisons in
``eval`` namespaces). These tests pin the None-safe behavior so the
crashes don't regress.
"""
from __future__ import annotations

import unittest

from poker.rule_strategies import (
    BUILT_IN_STRATEGIES,
    _strategy_abc,
    _evaluate_condition,
)
from poker.prompt_manager import PromptManager, _safe_pot_odds


# ---------------------------------------------------------------------------
# Shared baseline contexts
# ---------------------------------------------------------------------------

def _free_check_context(**overrides):
    """Context that mirrors what HybridAIController produces on a free check."""
    ctx = {
        'player_name': 'TestBot',
        'player_stack': 5000,
        'stack_bb': 50.0,
        'pot_total': 300,
        'pot_odds': None,  # The semantic under test
        'cost_to_call': 0,
        'highest_bet': 0,
        'min_raise': 200,
        'max_raise': 5000,
        'big_blind': 100,
        'equity': 0.55,
        'canonical_hand': 'AKo',
        'hole_cards': ['Ah', 'Kd'],
        'community_cards': [],
        'phase': 'FLOP',
        'position': 'button',
        'num_opponents': 1,
        'effective_stack': 5000,
        'effective_stack_bb': 50.0,
        'spr': 16.67,
        'valid_actions': ['check', 'raise'],
        'is_premium': False,
        'is_top_10': False,
        'is_top_20': True,
        'is_suited': False,
        'is_pair': False,
    }
    ctx.update(overrides)
    return ctx


def _facing_bet_context_with_none_pot_odds(**overrides):
    """Pathological context: cost_to_call>0 but pot_odds erroneously None.

    Should never happen in production (HybridAIController computes
    pot_odds = pot/cost when cost>0), but the defensive code paths must
    survive it without crashing.
    """
    ctx = _free_check_context(
        cost_to_call=100,
        pot_odds=None,
        highest_bet=100,
        valid_actions=['fold', 'call', 'raise'],
    )
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------------------
# Rule strategies — None pot_odds must not crash arithmetic
# ---------------------------------------------------------------------------

class TestRuleStrategiesNoneSafety(unittest.TestCase):
    def test_abc_free_check_with_none_pot_odds(self):
        """`_strategy_abc` reaches `1 / (pot_odds + 1)` only when cost>0,
        but if a caller leaks None into that arm it must not crash."""
        # Force the post-free-check arm to be exercised
        ctx = _facing_bet_context_with_none_pot_odds(
            equity=0.30,
            canonical_hand='72o',
            is_top_20=False,
        )
        # Should not raise
        decision = _strategy_abc(ctx)
        self.assertIn(decision['action'], {'fold', 'call', 'check', 'raise'})

    def test_evaluate_condition_pot_odds_none(self):
        """`_evaluate_condition` evaluating `pot_odds >= 3` with None pot_odds
        must coerce to a large value (free-to-act = infinite pot odds)."""
        ctx = _free_check_context(pot_odds=None, cost_to_call=0)
        # When free to act, "pot_odds >= 3" should be True (infinite odds)
        self.assertTrue(_evaluate_condition('pot_odds >= 3', ctx))
        # And `pot_odds < 3` should be False
        self.assertFalse(_evaluate_condition('pot_odds < 3', ctx))

    def test_evaluate_condition_default_with_none(self):
        """The `default` condition must still pass when pot_odds is None."""
        ctx = _free_check_context(pot_odds=None)
        self.assertTrue(_evaluate_condition('default', ctx))

    def test_all_built_in_strategies_handle_none_pot_odds_facing_bet(self):
        """Every registered strategy must survive a context where pot_odds
        is None even when cost_to_call > 0 (defensive: shouldn't happen,
        but a bug upstream shouldn't crash the table)."""
        ctx = _facing_bet_context_with_none_pot_odds()
        for name, fn in BUILT_IN_STRATEGIES.items():
            with self.subTest(strategy=name):
                # Must not raise — actual action choice is strategy-specific
                decision = fn(ctx)
                self.assertIsInstance(decision, dict)
                self.assertIn('action', decision)


# ---------------------------------------------------------------------------
# prompt_manager — `{pot_odds:.1f}` format spec is the dangerous bit
# ---------------------------------------------------------------------------

class TestPromptManagerNoneSafety(unittest.TestCase):
    def setUp(self):
        # PromptManager loads templates from poker/prompts/ — uses default path
        self.pm = PromptManager()

    def test_safe_pot_odds_helper(self):
        """`_safe_pot_odds` coerces None and non-numerics to the default."""
        self.assertEqual(_safe_pot_odds(None), 0.0)
        self.assertEqual(_safe_pot_odds(None, default=1.5), 1.5)
        self.assertEqual(_safe_pot_odds(4.2), 4.2)
        self.assertEqual(_safe_pot_odds('not a number'), 0.0)
        self.assertEqual(_safe_pot_odds(0), 0.0)

    def test_decision_prompt_pot_committed_with_none(self):
        """`pot_committed_info` with None pot_odds must not blow up the
        `.format(pot_odds=..., ...)` call. The template uses `{pot_odds}`
        without a format spec here, but coercion is still required for
        consistency."""
        pot_committed_info = {
            'pot_odds': None,  # Pathological: shouldn't happen, must survive
            'required_equity': 30,
            'already_bet_bb': 50,
            'stack_bb': 5,
            'cost_to_call_bb': 4,
        }
        # Should not raise
        prompt = self.pm.render_decision_prompt(
            message='test message',
            pot_committed_info=pot_committed_info,
            include_pot_odds=False,
        )
        self.assertIsInstance(prompt, str)

    def test_decision_prompt_pot_odds_guidance_with_none(self):
        """`pot_odds_guidance` template uses `{pot_odds:.1f}` — a None
        would historically crash. Defensive coercion keeps it rendering."""
        pot_odds_info = {
            'pot_odds': None,
            'equity_needed': 30,
            'pot_fmt': '3 BB',
            'call_fmt': '1 BB',
            'pot_odds_extra': '',
            # `free` deliberately not set so we hit the guidance branch
        }
        prompt = self.pm.render_decision_prompt(
            message='test',
            include_pot_odds=True,
            pot_odds_info=pot_odds_info,
        )
        self.assertIsInstance(prompt, str)
        # None should be coerced to 0.0 — the section still renders
        self.assertIn('0.0', prompt)

    def test_decision_prompt_pot_odds_free_branch(self):
        """The intended path: when `pot_odds_info['free']=True`, the
        `pot_odds_free` template renders without touching the numeric."""
        prompt = self.pm.render_decision_prompt(
            message='test',
            include_pot_odds=True,
            pot_odds_info={'free': True},
        )
        self.assertIsInstance(prompt, str)


# ---------------------------------------------------------------------------
# coach_assistant — already gated, but verify the gate holds
# ---------------------------------------------------------------------------

class TestCoachAssistantPotOddsFormatting(unittest.TestCase):
    def test_format_stats_for_prompt_with_none_pot_odds(self):
        """The coach prompt formatter must not render `None:1` literally."""
        from flask_app.services.coach_assistant import _format_stats_for_prompt
        data = {
            'pot_total': 300,
            'cost_to_call': 0,
            'pot_odds': None,
            'available_actions': ['check', 'raise'],
            'big_blind': 100,
            'stack': 5000,
            'equity': 0.55,
        }
        result = _format_stats_for_prompt(data)
        self.assertNotIn('None:1', result)
        self.assertNotIn('Pot odds: None', result)
        # Free-to-check messaging should appear instead
        self.assertIn('free to check', result)

    def test_format_stats_for_prompt_with_numeric_pot_odds(self):
        """Numeric pot_odds renders with one decimal place."""
        from flask_app.services.coach_assistant import _format_stats_for_prompt
        data = {
            'pot_total': 300,
            'cost_to_call': 100,
            'pot_odds': 3.0,
            'available_actions': ['fold', 'call', 'raise'],
            'big_blind': 100,
            'stack': 5000,
            'equity': 0.55,
        }
        result = _format_stats_for_prompt(data)
        self.assertIn('Pot odds: 3.0:1', result)


# ---------------------------------------------------------------------------
# HybridAIController integration: free-check path must not crash
# ---------------------------------------------------------------------------

class TestHybridChoicePromptFreeCheck(unittest.TestCase):
    """End-to-end: a hybrid bot acting after a free check on previous street
    receives a context with ``pot_odds=None``. Building the choice prompt
    must not crash on that — it should rephrase to the 'free to check'
    branch via ``format_options_for_prompt``.
    """

    def test_build_choice_prompt_with_none_pot_odds(self):
        from poker.hybrid_ai_controller import HybridAIController
        from poker.bounded_options import BoundedOption
        from poker.prompt_config import PromptConfig
        from types import SimpleNamespace

        # Bypass __init__ — we only need to test _build_choice_prompt which
        # is a pure method that reads `context` and produces a string.
        # The relationship-context branch reads self.state_machine and
        # self.prompt_config; stub both so the no-op path (flag off) works.
        ctrl = HybridAIController.__new__(HybridAIController)
        ctrl.prompt_config = PromptConfig()  # relationship_context defaults to False
        ctrl.opponent_model_manager = None
        ctrl.state_machine = SimpleNamespace(
            game_state=SimpleNamespace(players=[], current_player=None),
        )

        options = [
            BoundedOption(
                action='check',
                raise_to=0,
                rationale='Free card with a draw',
                ev_estimate='neutral',
                style_tag='standard',
            ),
            BoundedOption(
                action='raise',
                raise_to=200,
                rationale='Steal the pot',
                ev_estimate='+EV',
                style_tag='aggressive',
            ),
        ]
        context = {
            'equity': 0.45,
            'pot_odds': None,       # Free to act — the contract under test
            'cost_to_call': 0,
            'pot_total': 300,
        }
        # Should not raise even though pot_odds is None
        prompt = ctrl._build_choice_prompt('Game state here', options, context)
        self.assertIsInstance(prompt, str)
        # Free-to-check wording from format_options_for_prompt should appear
        self.assertIn('free to check', prompt)
        # And we should not have leaked the literal 'None' into the output
        self.assertNotIn('pot odds: None', prompt.lower())


if __name__ == '__main__':
    unittest.main()
