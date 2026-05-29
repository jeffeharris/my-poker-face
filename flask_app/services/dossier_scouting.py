"""Dossier scouting gate (Phase 2 — the grind).

Pure, Flask-free helpers that turn "hands observed against an opponent" into
which dossier reads are unlocked, and apply that gate to a dossier response.

The model (see `docs/plans/OPPONENT_DOSSIER_PROGRESSION.md`):

- A dossier's *earnable* reads stay locked until you've observed an opponent
  for a **floor** number of hands; below that the file is "classified".
- Past the floor, individual reads ("bits") unlock one at a time as the
  observed-hand count crosses each bit's threshold — the grind.
- Identity (PROFILE), your own STANDING with them, and your FIELD NOTES are
  never gated — only what you'd genuinely have to *scout* is.

Unlock state is **derived on read** (here), never stored: it's a pure
function of the observed-hand count, so a threshold can cross mid-hand and
the next dossier open reflects it. Thresholds are tuning, not design — edit
`SCOUTING_SCHEDULE` freely.

Item-level granularity matches the hybrid decision (grind drips items; the
informant — Phase 3 — will unlock a whole section at once).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# The floor: earnable reads are fully classified below this many observed
# hands. Equals the lowest threshold in the schedule by construction.
FLOOR_HANDS = 25

# Ordered drip schedule: (item_id, display_label, hands_observed threshold).
# Each item controls one or more response fields (see `_ITEM_FIELDS`). Order
# is cosmetic — gating compares the count to each threshold independently.
SCOUTING_SCHEDULE: List[Tuple[str, str, int]] = [
    ('play_style', 'Play style', 25),
    ('vpip', 'VPIP', 25),
    ('pfr', 'PFR', 40),
    ('aggression_factor', 'Aggression', 60),
    ('behavioral_index', 'Behavioral index', 80),
    ('track_record', 'Track record', 100),
    ('pressure', 'Pressure profile', 100),
    ('memorable', 'Memorable hands', 140),
    ('table_posture', 'Table posture', 180),
]

_LABELS = {item_id: label for item_id, label, _ in SCOUTING_SCHEDULE}


def compute_scouting(hands_observed: int) -> Dict[str, Any]:
    """Derive the unlock state for a given observed-hand count.

    Returns the descriptor the dossier surfaces to the client:
      - `hands_observed`, `floor`, `floor_met`
      - `unlocked`: list of unlocked item_ids
      - `locked`: list of {id, label, unlocks_at} still to earn
    """
    hands = max(0, int(hands_observed or 0))
    unlocked: List[str] = []
    locked: List[Dict[str, Any]] = []
    for item_id, label, threshold in SCOUTING_SCHEDULE:
        if hands >= threshold:
            unlocked.append(item_id)
        else:
            locked.append({'id': item_id, 'label': label, 'unlocks_at': threshold})
    return {
        'hands_observed': hands,
        'floor': FLOOR_HANDS,
        'floor_met': hands >= FLOOR_HANDS,
        'unlocked': unlocked,
        'locked': locked,
    }


def _strip_observation_field(response: Dict[str, Any], field: str) -> None:
    obs = response.get('observation')
    if isinstance(obs, dict) and obs.get(field) is not None:
        obs[field] = None


def _redact_item(response: Dict[str, Any], item_id: str) -> None:
    """Null out the response field(s) a locked item controls."""
    if item_id in ('play_style', 'vpip', 'pfr', 'aggression_factor'):
        _strip_observation_field(response, item_id)
    elif item_id == 'behavioral_index':
        personality = response.get('personality')
        if isinstance(personality, dict) and isinstance(
            personality.get('anchors'), dict
        ):
            personality['anchors'] = {k: None for k in personality['anchors']}
    elif item_id == 'track_record':
        response['cash_pair_stats'] = None
    elif item_id == 'pressure':
        response['pressure_summary'] = None
    elif item_id == 'memorable':
        response['memorable_hands'] = []
    elif item_id == 'table_posture':
        response['ai_bankroll'] = None
        response['stake_summary'] = {
            'as_borrower': {'carry_count': 0, 'total_carried': 0},
            'as_staker': {'carry_count': 0, 'total_owed_to_them': 0},
        }


def apply_scouting_gate(
    response: Dict[str, Any], hands_observed: Optional[int]
) -> Dict[str, Any]:
    """Gate a dossier `response` in place and return the scouting descriptor.

    Strips the values of every still-locked earnable read so locked intel is
    never sent to the client, then returns the descriptor (also attached as
    `response['scouting']`). When the floor isn't met, every earnable read is
    redacted. Always-free sections (PROFILE, STANDING, FIELD NOTES, emotion)
    are untouched.
    """
    scouting = compute_scouting(hands_observed or 0)
    locked_ids = {entry['id'] for entry in scouting['locked']}
    for item_id in locked_ids:
        _redact_item(response, item_id)
    response['scouting'] = scouting
    return scouting
