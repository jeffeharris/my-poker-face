"""Tests for the heads-up preflop strategy chart (preflop_100bb_hu.json).

Phase 7: validates the HU chart file produced by
``poker/strategy/data/generate_hu_chart.py``. Per-row probability sums,
coverage of all 169 canonical hands, and chart-level aggregate range
metrics from ``hu_preflop_chart_README.md`` are the binding gates.
"""

from __future__ import annotations

from typing import Dict, List

import pytest

# Canonical-hand utilities reuse the generator's expander; importing is fine
# (no side effects) and keeps the hand-set authoritative across producer and
# tests.
from poker.strategy.data.generate_hu_chart import CANONICAL_HANDS
from poker.strategy.nodes import PreflopNode
from poker.strategy.strategy_table import load_hu_strategy_table

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope='module')
def hu_table():
    table = load_hu_strategy_table()
    if table is None:
        pytest.fail(
            "load_hu_strategy_table() returned None -- "
            "preflop_100bb_hu.json is missing. Run "
            "`python -m poker.strategy.data.generate_hu_chart` to produce it."
        )
    return table


def _row_for(
    table, *, hand: str, position: str, scenario: str, opener_position: str
) -> Dict[str, float]:
    node = PreflopNode(
        hand=hand,
        position=position,
        scenario=scenario,
        opener_position=opener_position,
    )
    profile = table.lookup_preflop(node)
    assert profile is not None, f"missing node: {node.key}"
    return profile.action_probabilities


SCENARIOS = [
    ('rfi', 'SB', ''),
    ('vs_open', 'BB', 'SB'),
    ('vs_3bet', 'SB', 'BB'),
    ('vs_4bet', 'BB', 'SB'),
]


# ---------------------------------------------------------------------------
# Loading + coverage
# ---------------------------------------------------------------------------


class TestLoadingAndCoverage:
    def test_table_loads(self, hu_table):
        assert hu_table is not None

    def test_total_entry_count(self, hu_table):
        # 169 hands * 4 scenarios = 676 entries.
        assert hu_table.size == 676

    @pytest.mark.parametrize("scenario,position,opener", SCENARIOS)
    def test_all_169_hands_present(self, hu_table, scenario, position, opener):
        missing = []
        for hand in CANONICAL_HANDS:
            node = PreflopNode(
                hand=hand,
                position=position,
                scenario=scenario,
                opener_position=opener,
            )
            if hu_table.lookup_preflop(node) is None:
                missing.append(hand)
        assert not missing, f"{scenario}.{position}_vs_{opener or 'NONE'}: missing hands={missing}"

    def test_canonical_hand_count(self):
        # Sanity: generator's hand list is exactly the 169 canonical hands.
        assert len(CANONICAL_HANDS) == 169
        assert len(set(CANONICAL_HANDS)) == 169


# ---------------------------------------------------------------------------
# Per-row probability sums (strict gate)
# ---------------------------------------------------------------------------


class TestRowsSumToOne:
    @pytest.mark.parametrize("scenario,position,opener", SCENARIOS)
    def test_every_row_sums_to_one(self, hu_table, scenario, position, opener):
        offenders: List[str] = []
        for hand in CANONICAL_HANDS:
            row = _row_for(
                hu_table,
                hand=hand,
                position=position,
                scenario=scenario,
                opener_position=opener,
            )
            total = sum(row.values())
            if abs(total - 1.0) > 1e-9:
                offenders.append(f"{hand}: sum={total} row={row}")
        assert not offenders, (
            f"{scenario}.{position}_vs_{opener or 'NONE'} row sums != 1.0:\n  "
            + "\n  ".join(offenders[:10])
        )


# ---------------------------------------------------------------------------
# Specific hand assertions
# ---------------------------------------------------------------------------


class TestPremiumsAndTrash:
    @pytest.mark.parametrize("hand", ["AA", "KK"])
    def test_premium_pairs_open_from_sb(self, hu_table, hand):
        row = _row_for(
            hu_table,
            hand=hand,
            position='SB',
            scenario='rfi',
            opener_position='',
        )
        # Sum of any raise/jam variant for the open
        open_prob = row.get('raise_3bb', 0.0) + row.get('jam', 0.0)
        assert open_prob >= 0.95, f"{hand} SB open prob = {open_prob} (expected >= 0.95); row={row}"

    @pytest.mark.parametrize("hand", ["72o", "82o", "32o", "92o", "42o"])
    def test_trash_offsuit_folds_from_sb(self, hu_table, hand):
        row = _row_for(
            hu_table,
            hand=hand,
            position='SB',
            scenario='rfi',
            opener_position='',
        )
        fold_prob = row.get('fold', 0.0)
        assert fold_prob >= 0.95, f"{hand} SB fold prob = {fold_prob} (expected >= 0.95); row={row}"


# ---------------------------------------------------------------------------
# Chart-level aggregate range metrics
# ---------------------------------------------------------------------------


def _sum_action(hu_table, action: str, scenario: str, position: str, opener: str) -> float:
    total = 0.0
    for hand in CANONICAL_HANDS:
        row = _row_for(
            hu_table,
            hand=hand,
            position=position,
            scenario=scenario,
            opener_position=opener,
        )
        total += row.get(action, 0.0)
    return total


class TestAggregateRangeBands:
    def test_sb_open_rate(self, hu_table):
        rate = _sum_action(hu_table, 'raise_3bb', 'rfi', 'SB', '') / 169
        assert 0.60 <= rate <= 0.72, f"SB open rate = {rate:.4f}, expected 0.60-0.72"

    def test_bb_defense_rate(self, hu_table):
        calls = _sum_action(hu_table, 'call', 'vs_open', 'BB', 'SB')
        threebets = _sum_action(hu_table, 'raise_3x', 'vs_open', 'BB', 'SB')
        rate = (calls + threebets) / 169
        assert 0.52 <= rate <= 0.62, f"BB defense rate = {rate:.4f}, expected 0.52-0.62"

    def test_bb_3bet_rate(self, hu_table):
        rate = _sum_action(hu_table, 'raise_3x', 'vs_open', 'BB', 'SB') / 169
        assert 0.12 <= rate <= 0.18, f"BB 3-bet rate = {rate:.4f}, expected 0.12-0.18"

    def test_sb_4bet_jam_rate_vs_3bet(self, hu_table):
        fourbets = _sum_action(hu_table, 'raise_4x', 'vs_3bet', 'SB', 'BB')
        jams = _sum_action(hu_table, 'jam', 'vs_3bet', 'SB', 'BB')
        rate = (fourbets + jams) / 169
        assert 0.06 <= rate <= 0.10, f"SB 4-bet+jam rate vs 3-bet = {rate:.4f}, expected 0.06-0.10"
