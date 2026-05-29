"""Loads authored scripted-spot drills from config/training_scenarios/*.json.

Drills are authored content (version-controlled JSON, diff-readable, no
migration), so they live as files rather than DB rows or code constants. Each
file is one `TrainingScenario` wrapping a `ScriptedSpot`.

Validation is strict and happens at load time: every scenario is actually built
through the factory, so a malformed spot is dropped (logged) at startup rather
than 500-ing a live game. Loading is lazy + cached — the first `get`/`list`
populates the registry.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .scenario import ScriptedSpot, TrainingScenario

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "config" / "training_scenarios"

_registry: dict[str, TrainingScenario] = {}
_loaded = False


def _build_scenario(raw: dict) -> TrainingScenario:
    spot = ScriptedSpot(**raw["spot"])
    return TrainingScenario(
        id=raw["id"],
        name=raw["name"],
        description=raw.get("description", ""),
        config=spot,
        tags=raw.get("tags", []),
        coach_focus_skills=raw.get("coach_focus_skills", []),
    )


def _validate(scenario: TrainingScenario) -> None:
    """Build the scenario through the factory so bad data fails at load time."""
    from .state_builder import build_scripted_spot_state_machine

    n = len(scenario.config.villain_stacks_bb)
    build_scripted_spot_state_machine(
        scenario.config,
        "ValidationHero",
        [f"Villain{i}" for i in range(n)],
        seed=0,
    )


def load_library(directory: Optional[str] = None) -> None:
    """(Re)load the scenario registry from disk. Idempotent."""
    global _registry, _loaded
    d = Path(directory) if directory else _DEFAULT_DIR
    reg: dict[str, TrainingScenario] = {}
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try:
                scenario = _build_scenario(json.loads(f.read_text()))
                _validate(scenario)
                if scenario.id in reg:
                    logger.warning("training scenario id %r duplicated in %s", scenario.id, f.name)
                reg[scenario.id] = scenario
            except Exception as e:
                logger.error("training scenario %s failed to load: %s", f.name, e)
    _registry = reg
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        load_library()


def get_scenario(scenario_id: str) -> Optional[TrainingScenario]:
    _ensure_loaded()
    return _registry.get(scenario_id)


def list_scenarios() -> list[TrainingScenario]:
    _ensure_loaded()
    return list(_registry.values())
