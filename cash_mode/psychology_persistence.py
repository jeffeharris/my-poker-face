"""Shared persona-psychology persistence.

Hydrate / flush a controller's `PlayerPsychology` to and from the per-persona
`ai_bankroll_state.emotional_state_json` column (schema v97). This is the unit
of *emotional continuity in the cash world*: a persona carries its mood across
cash tables and cash-world (Circuit) tournaments, recovering toward baseline
while idle.

Promoted verbatim from `cash_mode.full_sim` (which still imports these under
their old private names) so the off-screen sim, the live cash seat build, and
the cash-world tournament builder all share ONE implementation. Behaviour is
identical to the original sim functions — only the log prefix changed.

The hook is best-effort everywhere: a missing repo, a NULL column, or a parse
failure leaves the controller at its freshly-built baseline rather than blocking
a hand on a column the next flush can rewrite. Keyed on
`(personality_id, sandbox_id)`; callers MUST pass the cash sandbox they resolved
(never None — that would cross-contaminate sandboxes).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def hydrate_persona_psychology(
    controller,
    personality_id: str,
    bankroll_repo,
    sandbox_id: str,
) -> None:
    """Apply persisted emotional state to a freshly-built controller.

    Reads `ai_bankroll_state.emotional_state_json` (schema v97) and
    deserializes via `PlayerPsychology.from_dict`. No-op when:
      - `bankroll_repo` is None (test paths that don't care)
      - the column is NULL (persona never touched the cash world before)
      - the JSON fails to parse (logged + skipped; controller stays at fresh
        defaults — surfacing the error would block hands on a column we can
        rewrite from the next flush)

    The controller's `psychology` attribute is replaced in place, not its
    underlying class. Anchors carry over via the `personality_config` arg to
    `from_dict`.

    Call ONLY on a fresh seat build, never on cold-load — cold-load restores the
    per-game `psychology_json`, and re-hydrating from the persona blob there
    would clobber the evolved in-game mood with a staler value.
    """
    if bankroll_repo is None:
        return
    try:
        blob = bankroll_repo.load_emotional_state_json(
            personality_id,
            sandbox_id=sandbox_id,
        )
    except Exception as exc:  # noqa: BLE001 — repo is best-effort here
        logger.debug("[PSYCH] %s: load_emotional_state_json failed: %s", personality_id, exc)
        return
    if not blob:
        return
    try:
        state_dict = json.loads(blob)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(
            "[PSYCH] %s: emotional_state_json malformed (%s); using fresh defaults",
            personality_id,
            exc,
        )
        return
    if controller.psychology is None:
        return
    try:
        from poker.player_psychology import PlayerPsychology

        personality_config = getattr(controller.ai_player, "personality_config", {})
        controller.psychology = PlayerPsychology.from_dict(
            state_dict,
            personality_config,
        )
    except Exception as exc:  # noqa: BLE001 — psychology is best-effort
        logger.warning(
            "[PSYCH] %s: PlayerPsychology.from_dict failed (%s); using fresh defaults",
            personality_id,
            exc,
        )
        return

    _apply_idle_energy_recovery(controller.psychology, state_dict, personality_id)


def _apply_idle_energy_recovery(psychology, state_dict, personality_id: str) -> None:
    """Decay-on-read: spring the `energy` axis toward baseline for the wall-clock
    the persona rested since its mood was last written.

    A persisted mood is a frozen snapshot — no hands fire while a persona is
    idle, so without this it would sit back down just as drained (or just as
    wired) as it left, hours later. Mirrors the lobby's projection-on-read
    (`cash_mode.movement.project_idle_energy`) and the energy-only model there:
    only `energy` recovers on idle; confidence / composure / tilt carry as the
    last snapshot until live hands move them again. Best-effort — a missing or
    unparseable `last_updated` just skips recovery (trust the stored value).
    """
    last_updated = state_dict.get("last_updated")
    anchors = getattr(psychology, "anchors", None)
    axes = getattr(psychology, "axes", None)
    if not last_updated or anchors is None or axes is None:
        return
    try:
        from datetime import datetime

        from cash_mode.movement import project_idle_energy

        idle_seconds = (datetime.utcnow() - datetime.fromisoformat(last_updated)).total_seconds()
        if idle_seconds <= 0:
            return
        recovered = project_idle_energy(axes.energy, anchors.baseline_energy, idle_seconds)
        psychology.axes = axes.update(energy=recovered)
    except Exception as exc:  # noqa: BLE001 — recovery is best-effort
        logger.debug("[PSYCH] %s: idle energy recovery skipped (%s)", personality_id, exc)


def serialize_persona_psychology(controller) -> Optional[str]:
    """Return the controller's psychology as a JSON blob, or None.

    Returns None if the controller has no psychology attached (some test stubs
    or partial builds). Wrapped `to_dict` / `json.dumps` so a serialization
    quirk on one field doesn't poison the rest.
    """
    psych = getattr(controller, "psychology", None)
    if psych is None:
        return None
    try:
        return json.dumps(psych.to_dict())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PSYCH] serialize_persona_psychology failed: %s", exc)
        return None


def flush_persona_psychology(
    controller,
    personality_id: str,
    bankroll_repo,
    sandbox_id: str,
) -> None:
    """Write the controller's current emotional state back to the persona blob.

    Best-effort: a repo error logs at debug and returns. Call at a session
    boundary (cash leave/settle, cash-world tournament completion) rather than
    per-hand, to avoid racing the off-screen sim's writes for the same
    `(personality_id, sandbox_id)`.
    """
    if bankroll_repo is None:
        return
    blob = serialize_persona_psychology(controller)
    if blob is None:
        return
    try:
        bankroll_repo.save_emotional_state_json(
            personality_id,
            blob,
            sandbox_id=sandbox_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[PSYCH] %s: save_emotional_state_json failed: %s", personality_id, exc)
