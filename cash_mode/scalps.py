"""Scalp attribution — the single, pure rule for "who busted whom" in cash mode.

A *scalp* is one elimination: an entity (the **eliminator**) busting another
(the **victim**) out of a cash hand. This module is the one place the
attribution rule lives, so the multiway heuristic can be changed in exactly one
spot later. It is **pure** — no DB, no side effects — so the durable counter
(`cash_scalps` / `CashScalpsRepository`, schema work) and the wiring into the
world-sim and human-hand paths can be built and tested independently.

The rule (v1 — the **headline-winner heuristic**, matching what the lobby
ticker already does in `activity.format_hand_summary_message`):

    The eliminator of a busted player is the hand's headline pot winner.

Two capture paths, same rule:
  - **AI-vs-AI** (world sim, `full_sim.play_one_hand`): `eliminations_from_sim`
    reads each `HAND_EVENT_BUST` and credits `result.winner_pid`.
  - **Human's real hand** (`game_handler.handle_evaluating_hand_phase`):
    `eliminations_from_human_hand` credits the human for each AI busted in a
    pot they won.

Both paths are **AI-symmetric** (the eliminator may be an AI) and route through
this module so attribution stays consistent.

Accepted v1 caveats (documented in CASH_MODE_SCALP_TRACKER.md §3): multiway
over-attribution (the headline winner gets credit for a side-pot bust they
didn't cover — rare, sims are near-heads-up in practice); self-busts and
no-eliminator hands earn no scalp; the human-as-victim case is out of scope
(the human leaves, they don't bust out).

Spec: docs/plans/CASH_MODE_SCALP_TRACKER.md.
"""

from __future__ import annotations

from typing import Iterable, List, Tuple

# Mirror of ``cash_mode.full_sim.HAND_EVENT_BUST`` (the source of truth). Kept
# as a local literal so this module stays PURE — importing full_sim drags in
# the whole poker/LLM engine, which would make every scalp unit test require
# the full runtime. A drift-guard test (test_scalps.py, integration-marked)
# asserts this stays equal to full_sim's constant.
HAND_EVENT_BUST = "bust"

# (eliminator_id, victim_id) — raw ids: owner_id for the human, personality_id
# for AIs (no `player:`/`ai:` prefix), mirroring cash_pair_stats conventions.
Scalp = Tuple[str, str]


def eliminations_from_sim(result) -> List[Scalp]:
    """Derive ``(eliminator, victim)`` scalps from one sim hand (the AI-vs-AI
    world-sim path, §3a).

    ``result`` is a ``HandSimResult`` (duck-typed: ``.winner_pid`` and
    ``.hand_events`` with ``.type`` / ``.personality_id``). The headline winner
    (``result.winner_pid``) is credited as the eliminator of every player with
    a ``HAND_EVENT_BUST`` event this hand.

    Skips the whole hand if there is no headline winner (``winner_pid`` falsy),
    and skips any self-bust (a player who hit zero on blinds with no one
    covering — ``victim == winner``). Returns one scalp per busted victim; in a
    multiway pot the winner is credited for each (accepted over-attribution).
    Pure — no DB, no mutation.
    """
    winner = getattr(result, "winner_pid", None)
    if not winner:
        return []
    scalps: List[Scalp] = []
    for ev in getattr(result, "hand_events", None) or []:
        if getattr(ev, "type", None) != HAND_EVENT_BUST:
            continue
        victim = getattr(ev, "personality_id", None)
        if not victim or victim == winner:
            continue
        scalps.append((winner, victim))
    return scalps


def eliminations_from_human_hand(human_id: str, busted_pids: Iterable[str]) -> List[Scalp]:
    """Derive scalps for the human's real cash hand (§3b).

    The human (``human_id`` = ``owner_id``) is the eliminator of each AI in
    ``busted_pids`` (the non-human players who hit zero in a pot the human won —
    the caller already derives this list for the achievements path). Skips the
    human themselves and de-duplicates within the hand. Pure — no DB, no
    mutation.
    """
    if not human_id:
        return []
    seen = set()
    scalps: List[Scalp] = []
    for victim in busted_pids:
        if not victim or victim == human_id or victim in seen:
            continue
        seen.add(victim)
        scalps.append((human_id, victim))
    return scalps
