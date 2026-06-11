"""Reusable scripted **table scenes** — the generalized Scene-0 machinery.

Scene 0 (the Lucky Stack tutorial) proved a pattern: a pinned table where the
deck is rigged hand-by-hand, an AI cast plays scripted lines, a mentor narrates,
and finishing the script fires a completion effect. This module lifts that out of
Scene-0-specific code so *other* table scenes (training drills, set-piece story
hands up the circuit) can reuse it by dropping in a `TableScene` descriptor.

What's reusable:
  - the engine's name-keyed deck rig (`PokerStateMachine.provide_hand_holes`) —
    immune to the per-hand button rotation;
  - the hand-boundary driver in `flask_app/handlers/game_handler.py`, which now
    operates on a resolved `TableScene` rather than hardcoded Scene-0 constants;
  - cold-load durability via `career_progress.scene_progress[scene_id]`.

This module stays Flask-free (pure data + lookup) so it can be imported anywhere;
the driver, narration emit, and completion dispatch live in the game handler.

A scene's hands are `career_scene.Scene0Hand` records (the `ScriptedHand` alias
below reads better for non-Scene-0 scripts; same shape). Narration fields on each
hand are role-oriented: `sal_*` = the mentor's lines, `fish_*` = the fish's. The
mentor's chat *display name* comes from the scene (`mentor_name`), not a constant,
so a different scene can have a different mentor.

Frontend note: the floating-portrait treatment (`SalFloater`) currently keys on
the sender name "Sal Monroe", so a scene with a different mentor narrates in plain
chat until the floater is generalized — a small follow-up, not a backend blocker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from cash_mode import career_scene
from cash_mode.career_progression import (
    SAL_ID,
    SAL_NAME,
    SCENE0_FISH_ID,
    SCENE0_TABLE_ID,
)

# Friendlier alias for the per-hand record when authoring non-Scene-0 scripts.
ScriptedHand = career_scene.Scene0Hand


@dataclass(frozen=True)
class TableScene:
    """A scripted scene pinned to one cash table.

    - `scene_id`: stable key for persisted progress (`scene_progress[scene_id]`).
    - `table_id`: the pinned table this scene plays at.
    - `cast`: role → persona_id for the AI cast (e.g. {'mentor': 'sal_moretti',
      'fish': 'loose_larry'}). The human is always the 'hero'.
    - `script`: ordered list of `ScriptedHand` (hand 0 normal, teaching + filler
      hands rigged).
    - `mentor_name`: chat display name the mentor's narration is sent under.
    - `on_complete`: dispatch key the game handler maps to a completion effect
      (e.g. 'career_first_vouch'). Kept as a string so this module stays pure.
    - `graduation_lines`: the mentor's closing sequence, played on completion.
    """

    scene_id: str
    table_id: str
    cast: Dict[str, str]
    script: List
    mentor_name: str
    on_complete: str
    graduation_lines: Tuple[str, ...] = field(default_factory=tuple)

    def hand_for_index(self, idx: int):
        """The scripted hand at `idx`, or None past the end of the script."""
        if 0 <= idx < len(self.script):
            return self.script[idx]
        return None

    @property
    def length(self) -> int:
        return len(self.script)


# --- registry ----------------------------------------------------------------
_SCENES_BY_TABLE: Dict[str, TableScene] = {}


def register(scene: TableScene) -> TableScene:
    """Register a scene under its table_id (idempotent — last write wins)."""
    _SCENES_BY_TABLE[scene.table_id] = scene
    return scene


def scene_for_table(table_id: Optional[str]) -> Optional[TableScene]:
    """The scene pinned to `table_id`, or None for an ordinary cash table."""
    if not table_id:
        return None
    return _SCENES_BY_TABLE.get(table_id)


def is_scene_table(table_id: Optional[str]) -> bool:
    return scene_for_table(table_id) is not None


# --- the Scene-0 registration (the first consumer) ---------------------------
SCENE0 = register(
    TableScene(
        scene_id="scene0",
        table_id=SCENE0_TABLE_ID,
        cast={"mentor": SAL_ID, "fish": SCENE0_FISH_ID},
        script=career_scene.SCENE0_SCRIPT,
        mentor_name=SAL_NAME,
        on_complete="career_first_vouch",
        graduation_lines=tuple(career_scene.SAL_GRADUATION_SEQUENCE),
    )
)
