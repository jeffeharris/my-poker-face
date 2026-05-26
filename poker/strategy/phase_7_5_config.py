"""Loads phase_7_5_config.yaml into typed dataclasses.

Single source of truth for Phase 7.5 thresholds. Values are loaded
at import time; tests override via reload_for_testing() with a
custom dict.

See docs/plans/PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md for semantics.
"""

import os
from dataclasses import dataclass
from typing import Tuple

import yaml

_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__),
    'data',
    'phase_7_5_config.yaml',
)


@dataclass(frozen=True)
class ExploitationClamps:
    default_max_total_shift: float
    medium_max_total_shift: float
    extreme_max_total_shift: float


@dataclass(frozen=True)
class SampleThresholds:
    medium_min_opportunities: int
    extreme_min_opportunities: int


@dataclass(frozen=True)
class SignalThresholds:
    medium_af_postflop: float
    extreme_af_postflop: float
    medium_all_in_per_facing_bet: float
    extreme_all_in_per_facing_bet: float
    medium_postflop_jam_open_rate: float
    extreme_postflop_jam_open_rate: float


@dataclass(frozen=True)
class TierDecay:
    window_size: int
    require_recent_window_full: int


@dataclass(frozen=True)
class BenchmarkPrior:
    enabled: bool
    confirmed_extreme_archetypes: Tuple[str, ...]


@dataclass(frozen=True)
class BluffCatchSizing:
    """Hand-class × bet/pot base call probabilities."""

    medium_made_le_50_pct: float
    medium_made_le_100_pct: float
    medium_made_le_200_pct: float
    medium_made_gt_200_pct: float
    weak_made_le_33_pct: float
    weak_made_le_67_pct: float
    weak_made_gt_67_pct: float


@dataclass(frozen=True)
class BluffCatchDampener:
    """Multipliers applied to base call prob."""

    street_river: float
    street_turn: float
    street_flop: float
    dangerous_texture_mult: float
    weak_made_on_paired_mult: float


@dataclass(frozen=True)
class BluffCatch:
    sizing: BluffCatchSizing
    dampener: BluffCatchDampener


@dataclass(frozen=True)
class Phase75Config:
    exploitation_clamps: ExploitationClamps
    sample_thresholds: SampleThresholds
    signal_thresholds: SignalThresholds
    tier_decay: TierDecay
    benchmark_prior: BenchmarkPrior
    bluff_catch: BluffCatch


def _from_dict(raw: dict) -> Phase75Config:
    """Parse the raw YAML dict into a typed Phase75Config."""
    return Phase75Config(
        exploitation_clamps=ExploitationClamps(**raw['exploitation_clamps']),
        sample_thresholds=SampleThresholds(**raw['sample_thresholds']),
        signal_thresholds=SignalThresholds(**raw['signal_thresholds']),
        tier_decay=TierDecay(**raw['tier_decay']),
        benchmark_prior=BenchmarkPrior(
            enabled=raw['benchmark_prior']['enabled'],
            confirmed_extreme_archetypes=tuple(
                raw['benchmark_prior']['confirmed_extreme_archetypes']
            ),
        ),
        bluff_catch=BluffCatch(
            sizing=BluffCatchSizing(
                medium_made_le_50_pct=raw['bluff_catch']['medium_made']['cap_le_50_pct'],
                medium_made_le_100_pct=raw['bluff_catch']['medium_made']['cap_le_100_pct'],
                medium_made_le_200_pct=raw['bluff_catch']['medium_made']['cap_le_200_pct'],
                medium_made_gt_200_pct=raw['bluff_catch']['medium_made']['cap_gt_200_pct'],
                weak_made_le_33_pct=raw['bluff_catch']['weak_made']['cap_le_33_pct'],
                weak_made_le_67_pct=raw['bluff_catch']['weak_made']['cap_le_67_pct'],
                weak_made_gt_67_pct=raw['bluff_catch']['weak_made']['cap_gt_67_pct'],
            ),
            dampener=BluffCatchDampener(**raw['bluff_catch']['dampener']),
        ),
    )


def load_config(path: str = _CONFIG_PATH) -> Phase75Config:
    """Load the config from disk. No caching at this level — the
    module-level CONFIG variable below is the cached singleton."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return _from_dict(raw)


# Module-level singleton — production paths read from this.
CONFIG: Phase75Config = load_config()


def reload_for_testing(raw: dict) -> None:
    """Replace the module-level CONFIG with values from a dict.

    For test fixtures only. Production code should not call this.
    """
    global CONFIG
    CONFIG = _from_dict(raw)


def reset_for_testing() -> None:
    """Restore CONFIG to the on-disk YAML."""
    global CONFIG
    CONFIG = load_config()
