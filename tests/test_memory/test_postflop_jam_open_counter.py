"""Phase 7.5 Step 0 end-to-end tests for postflop jam-open vs response-jam
disambiguation.

Verifies the wiring through MemoryManager.on_action correctly captures
`was_facing_bet` BEFORE the per-action update to `_recent_aggressor_name`,
so:
  - First-in postflop jams (no prior bet on street) → _postflop_jam_opens +1
  - Response jams (facing a prior bet) → _all_ins_facing_bet +1
  - Preflop all-ins → neither (postflop-only scope)
  - Street transitions reset the aggressor state correctly

Tests use real MemoryManager + OpponentModelManager (no mocks) to exercise
the full chain.
"""

from types import SimpleNamespace

import pytest

from poker.memory.memory_manager import AIMemoryManager

# ── Fixtures ─────────────────────────────────────────────────────────────


def _gs(*names: str) -> SimpleNamespace:
    """Minimal game_state stub with named players for on_hand_start.

    HandHistoryRecorder.start_hand reads `player.stack` (or `.money`),
    `.is_human`, `.hand`, and `game_state.table_positions`. Provide
    defaults for each.
    """
    players = [SimpleNamespace(name=n, stack=10000, is_human=False, hand=None) for n in names]
    return SimpleNamespace(players=players, table_positions={})


@pytest.fixture
def mm():
    """Fresh MemoryManager with two AI observers."""
    mm = AIMemoryManager(game_id='test-game')
    mm.initialize_for_player('Hero')
    mm.initialize_for_player('Villain')
    mm.on_hand_start(_gs('Hero', 'Villain'), hand_number=1)
    return mm


def _tendencies(mm: AIMemoryManager, observer: str, opponent: str):
    return mm.opponent_model_manager.get_model(observer, opponent).tendencies


# ── First-in jam (open opportunity) ──────────────────────────────────────


class TestFirstInJam:
    def test_villain_jams_first_on_flop(self, mm):
        """Villain opens flop with all-in → counts as open jam, not response."""
        # Preflop: both call (no aggressor)
        mm.on_action('Hero', 'call', 100, 'PRE_FLOP', 200)
        mm.on_action('Villain', 'check', 0, 'PRE_FLOP', 200)
        # Flop: Villain jams first-to-act
        mm.on_action('Villain', 'all_in', 5000, 'FLOP', 5200)

        t = _tendencies(mm, 'Hero', 'Villain')
        assert t._postflop_jam_opens == 1
        assert t._postflop_open_opportunities == 1
        assert t._all_ins_facing_bet == 0
        assert t._facing_bet_opportunities == 0
        assert t.postflop_jam_open_rate == 1.0

    def test_check_then_jam_behind_still_counts_as_open(self, mm):
        """When player A checks and B jams behind, B's jam is an open
        jam (no live bet faced)."""
        mm.on_action('Hero', 'check', 0, 'FLOP', 200)
        # At Villain's decision: _recent_aggressor_name is None →
        # was_facing_bet=False → Villain's all_in is open.
        mm.on_action('Villain', 'all_in', 5000, 'FLOP', 5200)

        t = _tendencies(mm, 'Hero', 'Villain')
        assert t._postflop_jam_opens == 1
        assert t._postflop_open_opportunities == 1
        assert t._all_ins_facing_bet == 0


# ── Response jam (facing-bet opportunity) ────────────────────────────────


class TestResponseJam:
    def test_villain_jams_in_response_to_hero_bet(self, mm):
        """Hero bets, Villain jams → Villain's all_in is a response jam."""
        # Preflop: both call
        mm.on_action('Hero', 'call', 100, 'PRE_FLOP', 200)
        mm.on_action('Villain', 'check', 0, 'PRE_FLOP', 200)
        # Flop: Hero bets, Villain jams
        mm.on_action('Hero', 'bet', 100, 'FLOP', 300)
        mm.on_action('Villain', 'all_in', 5000, 'FLOP', 5300)

        t = _tendencies(mm, 'Hero', 'Villain')
        assert t._all_ins_facing_bet == 1
        assert t._facing_bet_opportunities == 1
        assert t._postflop_jam_opens == 0
        # Note: Villain checked preflop — no postflop opportunity until now.
        assert t._postflop_open_opportunities == 0
        assert t.all_in_per_facing_bet == 1.0

    def test_villain_calls_facing_bet(self, mm):
        """Hero bets, Villain calls → facing-bet opp +1, no jam."""
        mm.on_action('Hero', 'bet', 100, 'FLOP', 200)
        mm.on_action('Villain', 'call', 100, 'FLOP', 300)

        t = _tendencies(mm, 'Hero', 'Villain')
        assert t._facing_bet_opportunities == 1
        assert t._all_ins_facing_bet == 0
        assert t.all_in_per_facing_bet == 0.0

    def test_villain_folds_facing_bet(self, mm):
        """Hero bets, Villain folds → facing-bet opp +1, no jam."""
        mm.on_action('Hero', 'bet', 100, 'FLOP', 200)
        mm.on_action('Villain', 'fold', 0, 'FLOP', 200)

        t = _tendencies(mm, 'Hero', 'Villain')
        assert t._facing_bet_opportunities == 1
        assert t._all_ins_facing_bet == 0


# ── Preflop exclusion ────────────────────────────────────────────────────


class TestPreflopExclusion:
    def test_preflop_jam_does_not_touch_postflop_counters(self, mm):
        """Villain shoves preflop → no postflop counter movement."""
        mm.on_action('Villain', 'all_in', 5000, 'PRE_FLOP', 5000)

        t = _tendencies(mm, 'Hero', 'Villain')
        assert t._postflop_jam_opens == 0
        assert t._all_ins_facing_bet == 0
        assert t._postflop_open_opportunities == 0
        assert t._facing_bet_opportunities == 0
        # But the LEGACY all_in_count IS incremented.
        assert t._all_in_count == 1

    def test_preflop_facing_3bet_jam_does_not_touch_postflop(self, mm):
        """Preflop response-jam (facing a 3-bet) also stays out of
        postflop counters."""
        mm.on_action('Hero', 'raise', 300, 'PRE_FLOP', 300)
        mm.on_action('Villain', 'all_in', 5000, 'PRE_FLOP', 5300)

        t = _tendencies(mm, 'Hero', 'Villain')
        assert t._postflop_jam_opens == 0
        assert t._all_ins_facing_bet == 0


# ── Street transition resets _recent_aggressor_name ──────────────────────


class TestStreetTransition:
    def test_flop_bet_does_not_carry_into_turn(self, mm):
        """Hero bets on flop, Villain calls. On turn, Villain checks
        first-to-act. The check is an OPEN opportunity for Villain,
        not a facing-bet."""
        mm.on_action('Hero', 'bet', 100, 'FLOP', 200)
        mm.on_action('Villain', 'call', 100, 'FLOP', 300)
        # Turn begins. First action is Villain checking.
        mm.on_action('Villain', 'check', 0, 'TURN', 300)

        t = _tendencies(mm, 'Hero', 'Villain')
        # Villain's flop call: facing-bet opp +1
        # Villain's turn check: open opp +1
        assert t._facing_bet_opportunities == 1
        assert t._postflop_open_opportunities == 1
        assert t._all_ins_facing_bet == 0
        assert t._postflop_jam_opens == 0

    def test_flop_to_river_skip_clears_aggressor(self, mm):
        """When phase jumps (e.g. all-in run-out) the aggressor state
        still resets at the new street."""
        mm.on_action('Hero', 'bet', 100, 'FLOP', 200)
        mm.on_action('Villain', 'call', 100, 'FLOP', 300)
        # Hero's first river action — turn was skipped or ran out.
        mm.on_action('Hero', 'bet', 200, 'RIVER', 500)

        # The transition from FLOP to RIVER cleared aggressor state.
        # Villain's river jam faces Hero's river bet → response jam +1.
        # Combined with Villain's flop call (also facing-bet) → 2 total
        # facing-bet opportunities; 1 of them was the jam.
        mm.on_action('Villain', 'all_in', 5000, 'RIVER', 5500)
        t = _tendencies(mm, 'Hero', 'Villain')
        assert t._all_ins_facing_bet == 1
        assert t._facing_bet_opportunities == 2  # flop call + river jam
        # And Hero's two bets (flop + river) should each be open opps
        # from Villain's-observer perspective on Hero.
        t_hero = _tendencies(mm, 'Villain', 'Hero')
        assert t_hero._postflop_open_opportunities == 2
        assert t_hero._postflop_jam_opens == 0  # neither was a jam


# ── Per-opponent isolation in MM ─────────────────────────────────────────


class TestPerOpponentIsolationViaMM:
    def test_aggression_does_not_leak_to_other_opponent_model(self):
        """Hero observes BOTH Villain and Third. Villain's jam should
        only update Villain's counters, not Third's."""
        mm = AIMemoryManager(game_id='test-game-multi')
        mm.initialize_for_player('Hero')
        mm.initialize_for_player('Villain')
        mm.initialize_for_player('Third')
        mm.on_hand_start(_gs('Hero', 'Villain', 'Third'), hand_number=1)

        mm.on_action('Villain', 'all_in', 5000, 'FLOP', 5000)

        t_villain = _tendencies(mm, 'Hero', 'Villain')
        t_third = _tendencies(mm, 'Hero', 'Third')

        # Villain has the jam.
        assert t_villain._postflop_jam_opens == 1
        # Third (different opponent) is untouched.
        assert t_third._postflop_jam_opens == 0
        assert t_third._postflop_open_opportunities == 0


# ── Hero's own actions also tracked from each observer's POV ─────────────


class TestSelfObservation:
    def test_hero_jam_visible_to_villain_as_observer(self, mm):
        """When Hero jams first on flop, the counter from Villain's
        observer perspective shows Hero as a first-in jammer."""
        mm.on_action('Hero', 'all_in', 5000, 'FLOP', 5000)
        t = _tendencies(mm, 'Villain', 'Hero')
        assert t._postflop_jam_opens == 1
        assert t._postflop_open_opportunities == 1
