"""Tests for the WTA-SNG eval runner (EVAL_HARNESS_PLAN §P1).

Pure-function tests (seat specs, work split, Wilson CI) are fast. The
end-to-end SNG tests use a small field + aggressive blind ramp so a tournament
finishes in a handful of hands; the headline assertion is **whole-tournament
chip conservation** — under winner-take-all with no rake, the lone survivor must
hold every chip dealt (N × starting_stack). If that fails, the runner (or the
engine's stack carry-over / elimination) is leaking chips.
"""

import pytest

from experiments.sng_runner import (
    TERMINAL_SINGLE,
    Accounting,
    CCBlock,
    _bootstrap_ci_blocks,
    _cc_seat_specs,
    _field_seat_specs,
    _split,
    _wilson,
    play_sng,
)
from poker.strategy.strategy_table import load_strategy_table

# Aggressive ramp + shallow stacks → SNGs end in ~10-25 hands (fast tests).
_FAST_BLINDS = {'growth': 2.0, 'hands_per_level': 4, 'max_blind': 0}
_START_STACK = 2000  # 20bb at bb=100
_BIG_BLIND = 100


class TestWorkSplit:
    def test_split_covers_all_sngs(self):
        chunks = _split(400, base_seed=42)
        assert sum(c for _, c in chunks) == 400

    def test_split_seeds_contiguous_no_overlap(self):
        chunks = _split(10, base_seed=100)
        seeds = []
        for start, count in chunks:
            seeds.extend(range(start, start + count))
        assert seeds == list(range(100, 110))  # contiguous, no gaps/overlap

    def test_split_handles_fewer_sngs_than_workers(self):
        chunks = _split(1, base_seed=5)
        assert sum(c for _, c in chunks) == 1


class TestSeatSpecs:
    def test_field_specs_unique_names_even_with_dupes(self):
        table = load_strategy_table()
        specs = _field_seat_specs(['Baseline', 'Baseline', 'TAG'], table, rotation=0)
        names = [s[0] for s in specs]
        assert len(names) == len(set(names))  # unique despite repeated archetype

    def test_field_rotation_changes_seat_order(self):
        table = load_strategy_table()
        a = [s[0] for s in _field_seat_specs(['Baseline', 'TAG', 'LAG'], table, 0)]
        b = [s[0] for s in _field_seat_specs(['Baseline', 'TAG', 'LAG'], table, 1)]
        assert a != b and set(a) == set(b)

    def test_cc_specs_split_is_correct(self):
        full = load_strategy_table()
        specs, challenger_names = _cc_seat_specs(
            'multistreet',
            n_seats=6,
            challenger_idx={0, 2, 4},
            champion_table=full,
            challenger_table=full,
            archetype='Baseline',
        )
        assert len(specs) == 6
        assert len(challenger_names) == 3
        champion_names = {s[0] for s in specs} - challenger_names
        assert len(champion_names) == 3

    def test_cc_specs_role_swap_complement(self):
        # The role-swapped run (challenger in the complement seats) must seat the
        # challenger group in exactly the seats the base run left to champion.
        full = load_strategy_table()
        _, base_names = _cc_seat_specs(
            'multistreet',
            n_seats=6,
            challenger_idx={0, 2, 4},
            champion_table=full,
            challenger_table=full,
            archetype='Baseline',
        )
        _, comp_names = _cc_seat_specs(
            'multistreet',
            n_seats=6,
            challenger_idx={1, 3, 5},
            champion_table=full,
            challenger_table=full,
            archetype='Baseline',
        )
        # Seat indices are disjoint and cover the table.
        base_idx = {int(n.rsplit('_', 1)[1]) for n in base_names}
        comp_idx = {int(n.rsplit('_', 1)[1]) for n in comp_names}
        assert base_idx == {0, 2, 4}
        assert comp_idx == {1, 3, 5}
        assert not (base_idx & comp_idx)

    def test_cc_specs_with_backdrop(self):
        # A backdrop fills the non-A/B seats with fixed opponents: n_ab = seats −
        # len(backdrop) A/B seats first (CHAL_/CHMP_), then the backdrop seats
        # (Arch#k, never CHAL_/CHMP_, so the winner's name reveals which won).
        full = load_strategy_table()
        specs, challenger_names = _cc_seat_specs(
            'exploitation',
            n_seats=6,
            challenger_idx={0},
            champion_table=full,
            challenger_table=full,
            archetype='TAG',
            backdrop=('CallStation', 'FoldyBot', 'CallStation', 'FoldyBot'),
        )
        names = [s[0] for s in specs]
        assert names[:2] == ['CHAL_0', 'CHMP_1']  # 2 A/B seats, challenger at 0
        assert challenger_names == {'CHAL_0'}
        assert names[2:] == ['CallStation#1', 'FoldyBot#1', 'CallStation#2', 'FoldyBot#2']
        assert all(not n.startswith(('CHAL_', 'CHMP_')) for n in names[2:])


class TestWilson:
    def test_brackets_point_estimate(self):
        p, lo, hi = _wilson(50, 100)
        assert p == 0.5 and lo < 0.5 < hi

    def test_empty(self):
        assert _wilson(0, 0) == (0.0, 0.0, 0.0)


class TestPlaySng:
    def _small_field_specs(self):
        table = load_strategy_table()
        return _field_seat_specs(['Baseline', 'TAG', 'GTO-Lite'], table, rotation=0)

    def test_ends_with_winner_holding_every_chip(self):
        specs = self._small_field_specs()
        total = len(specs) * _START_STACK
        res = play_sng(specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=7, max_hands=500)
        assert res.winner is not None
        assert res.terminal_reason == TERMINAL_SINGLE
        # WTA, no rake: chips are conserved across the whole tournament, so the
        # survivors hold exactly what was dealt — and at a clean finish that's
        # one player with all of it.
        assert sum(res.final_stacks.values()) == total
        assert res.final_stacks.get(res.winner) == total
        assert len(res.final_stacks) == 1

    def test_terminates_well_under_max_hands(self):
        specs = self._small_field_specs()
        res = play_sng(specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=7)
        assert 0 < res.hands_played < 200  # escalating blinds force a finish

    def test_escalating_blinds_lift_the_end_ante(self):
        # P1/P6: a finished SNG must have ramped past the opening blind level —
        # proof the depth progression actually fired (else the "exercises the
        # depth ramp" claim is hollow).
        specs = self._small_field_specs()
        res = play_sng(specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=7)
        assert res.final_ante > _BIG_BLIND

    def test_deterministic_for_a_seed(self):
        specs = self._small_field_specs()
        r1 = play_sng(specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=99)
        specs2 = self._small_field_specs()
        r2 = play_sng(specs2, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=99)
        assert r1 == r2

    def test_deck_sequence_is_a_deterministic_function_of_seed(self):
        # P0: the whole-tournament deck progression must reproduce from sng_seed
        # alone (later hands' decks come from the SM's chained hand-seed, not a
        # fixed per-hand seed — so "independent SNG" only holds if this is
        # deterministic), and the decks must actually advance hand-to-hand.
        def capture():
            decks = []
            specs = self._small_field_specs()
            play_sng(
                specs,
                _FAST_BLINDS,
                _START_STACK,
                _BIG_BLIND,
                sng_seed=99,
                on_hand_start=lambda i, gs: decks.append(tuple(gs.deck)),
            )
            return decks

        decks_a = capture()
        decks_b = capture()
        assert decks_a == decks_b  # reproduces exactly from the seed
        assert len(decks_a) >= 2  # multi-hand tournament
        # The deck advances each hand — not the same shuffle reused. (Card is
        # unhashable, so compare against the first deck rather than set-dedup.)
        assert any(d != decks_a[0] for d in decks_a[1:])

    def test_different_seeds_can_differ(self):
        # Not strictly guaranteed, but across several seeds the winner should
        # vary at least once — confirms the seed actually drives the SNG.
        specs_fn = self._small_field_specs
        winners = {
            play_sng(specs_fn(), _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=s).winner
            for s in range(20, 32)
        }
        assert len(winners) >= 2


class TestAccounting:
    def test_clean_finish_counts_as_decisive(self):
        acct = Accounting()
        table = load_strategy_table()
        specs = _field_seat_specs(['Baseline', 'TAG', 'GTO-Lite'], table, rotation=0)
        res = play_sng(specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=7)
        clean = acct.record(res)
        assert clean is True
        assert acct.attempted == 1
        assert acct.decisive == 1
        assert acct.none == 0 and acct.cap == 0

    def test_merge_sums_buckets(self):
        a = Accounting(attempted=3, decisive=2, none=1)
        b = Accounting(attempted=2, decisive=2)
        a.merge(b)
        assert a.attempted == 5 and a.decisive == 4 and a.none == 1


class TestBootstrapCI:
    def test_null_blocks_bracket_half(self):
        # A null with realistic block-to-block variance: half the blocks go 2-0
        # to the challenger, half 0-2. Mean is exactly 0.5 and — because the
        # blocks vary — the bootstrap CI is a real interval bracketing it.
        blocks = [CCBlock(seed=s, chal_wins=2 if s % 2 else 0, decisive=2) for s in range(200)]
        point, lo, hi = _bootstrap_ci_blocks(blocks, iters=1000)
        assert point == 0.5
        assert lo < 0.5 < hi

    def test_identical_blocks_give_degenerate_point_ci(self):
        # Zero between-block variance (every block a perfect 1-of-2 split) →
        # the bootstrap correctly collapses to a point at the null.
        blocks = [CCBlock(seed=s, chal_wins=1, decisive=2) for s in range(50)]
        point, lo, hi = _bootstrap_ci_blocks(blocks, iters=500)
        assert point == lo == hi == 0.5

    def test_all_challenger_wins_pins_high(self):
        blocks = [CCBlock(seed=s, chal_wins=2, decisive=2) for s in range(100)]
        point, lo, hi = _bootstrap_ci_blocks(blocks, iters=1000)
        assert point == 1.0
        assert lo > 0.9


class TestOpponentModelFeed:
    """The opt-in opponent-model feed is what makes the exploitation layer's
    inputs real — without a populated manager `_apply_exploitation` no-ops, so
    these guard that the feed both populates reads and preserves game flow."""

    def _tag_vs_stations(self):
        from experiments.simulate_bb100 import ARCHETYPES, make_controller, make_game_state
        from poker.poker_state_machine import PokerStateMachine

        table = load_strategy_table()
        names = ['TAG_hero', 'CallStation#1', 'CallStation#2']
        cfgs = [ARCHETYPES['TAG'], ARCHETYPES['CallStation'], ARCHETYPES['CallStation']]
        sm0 = PokerStateMachine(make_game_state(names, _BIG_BLIND, 10000, 0, seed=1))
        ctrls = [
            make_controller(nm, cfg, table, sm0, rng_seed=1000 * i)
            for i, (nm, cfg) in enumerate(zip(names, cfgs, strict=False))
        ]
        return names, ctrls, table

    def test_feed_accumulates_station_read(self):
        import random

        from experiments.champion_challenger import OpponentFeed, run_cc_hand
        from experiments.simulate_bb100 import make_game_state
        from poker.memory.cbet_detector import CbetDetector
        from poker.memory.opponent_model import OpponentModelManager
        from poker.poker_state_machine import PokerStateMachine

        names, ctrls, _ = self._tag_vs_stations()
        mgr = OpponentModelManager()
        feed = OpponentFeed(mgr, CbetDetector(), hero_names=('TAG_hero',))
        for c in ctrls:
            c.opponent_model_manager = mgr  # so the layer could read it in-game
        for h in range(20):
            random.seed(50 * h + 3)
            gs = make_game_state(names, _BIG_BLIND, 10000, h % 3, seed=50 * h + 3)
            sm = PokerStateMachine(gs)
            sm.current_hand_seed = 50 * h + 3
            for c in ctrls:
                c.state_machine = sm
            run_cc_hand(sm, ctrls, _BIG_BLIND, feed=feed, hand_number=h)
        model = mgr.get_model_if_exists('TAG_hero', 'CallStation#1')
        assert model is not None
        assert model.tendencies.hands_observed >= 1
        # CallStation calls everything → a high-VPIP, low-aggression station read,
        # which is exactly what the exploitation detectors key on.
        assert model.tendencies.vpip > 0.5
        assert model.tendencies.aggression_factor <= 1.0

    def test_feed_is_observation_only(self):
        # The feed runs per-action but only observes — with offsets zeroed
        # (exploitation_strength=0 on every tiered seat) a populated model must
        # change NO decision, so the SNG plays out byte-identically with the
        # feed on vs off. (This isolates "the feed is side-effect-free" from the
        # layer's intended effect, which only appears when strength > 0.)
        from experiments.simulate_bb100 import ARCHETYPES

        table = load_strategy_table()
        specs = [
            ('CHAL_0', ARCHETYPES['TAG'], table, {'exploitation_strength': 0.0}),
            ('CHMP_1', ARCHETYPES['TAG'], table, {'exploitation_strength': 0.0}),
            ('CallStation#1', ARCHETYPES['CallStation'], table, {}),
            ('CallStation#2', ARCHETYPES['CallStation'], table, {}),
        ]
        off = play_sng(specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=7)
        on = play_sng(
            specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=7, opponent_model=True
        )
        assert on.winner is not None
        assert on == off  # populated model + zero strength ⇒ no perturbation
