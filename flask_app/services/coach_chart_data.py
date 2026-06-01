"""DB adapter for chart-graded preflop leaks — load + reconstruct context.

Impure companion to the pure ``coach_chart_leaks`` core. Reads an owner's human
preflop decisions and reconstructs the (position, scenario, depth, players)
context each one needs to be graded against the solver charts.

Two provenance paths (see the scope):
  - **Exact (capture-forward):** if ``preflop_node_key`` was stored at decision
    time, use it verbatim — exact scenario + opener, incl. vs_3bet.
  - **Backfill (approximate):** reconstruct from ``player_position`` +
    ``cost_to_call`` vs the big blind + ``opponent_positions``. Opener is left
    unknown (the grader averages across openers). Clean for rfi / vs_open.

The big blind comes from the game's ``current_ante`` (this engine's term for
the BB level), read out of ``game_state_json`` via SQLite ``json_extract``.
"""

from __future__ import annotations

import logging
from typing import Optional

from poker.strategy.nodes import PreflopNode

logger = logging.getLogger(__name__)

# Engine position keys → 6-max chart labels. Short-handed tables collapse to a
# subset of these (the engine drops the unused middle seats), so a direct map
# is correct at 2-6 players.
_POSITION_LABEL = {
    'button': 'BTN',
    'small_blind_player': 'SB',
    'big_blind_player': 'BB',
    'under_the_gun': 'UTG',
    'middle_position_1': 'HJ',
    'cutoff': 'CO',
}

# A raise-to of this many big blinds or less reads as a single open; above it,
# as a 3-bet. Backfill heuristic only — capture-forward stores the exact node.
_OPEN_MAX_BB = 4.5


def position_label(engine_position: Optional[str]) -> Optional[str]:
    return _POSITION_LABEL.get(engine_position) if engine_position else None


def infer_scenario(pos_label: str, cost_to_call: float, bb: float) -> Optional[str]:
    """Reconstruct the preflop scenario from the cost-to-call vs the BB.

    Returns 'rfi' | 'vs_open' | 'vs_3bet', or None when there's no gradeable
    decision (BB checking its free option in an unopened pot).
    """
    if not bb or bb <= 0:
        return None
    sb = bb / 2.0
    # Highest live bet hero faces = what hero must add + what hero already posted.
    if pos_label == 'BB':
        highest = cost_to_call + bb
    elif pos_label == 'SB':
        highest = cost_to_call + sb
    else:
        highest = cost_to_call
    ratio = highest / bb
    if ratio <= 1.01:
        # Unopened: an open opportunity for everyone but the BB (who just checks).
        return None if pos_label == 'BB' else 'rfi'
    if ratio <= _OPEN_MAX_BB:
        return 'vs_open'
    return 'vs_3bet'


def _context_from_node_key(node_key: str) -> Optional[dict]:
    """Parse a stored PreflopNode.key into scenario/position/opener/hand."""
    try:
        scenario, position, opener, hand = node_key.split('|')
    except ValueError:
        return None
    return {
        'hand': hand,
        'position': position,
        'scenario': scenario,
        'opener': opener or None,
    }


def reconstruct_context(row: dict, bb: Optional[float]) -> Optional[dict]:
    """Turn one stored decision row into a chart-gradeable decision dict.

    Prefers the exact stored node when present; otherwise reconstructs from the
    cost-to-call. Returns None when the row isn't gradeable (unmappable
    position, BB free check, missing hand/blind).
    """
    hand = row.get('canon') or row.get('player_hand_canonical')
    if not hand:
        return None

    num_players = (row.get('num_opponents') or 0) + 1
    eff_bb = (row['player_stack'] / bb) if (bb and row.get('player_stack')) else 0.0

    node_key = row.get('preflop_node_key')
    if node_key:
        ctx = _context_from_node_key(node_key)
        if ctx is None:
            return None
    else:
        pos = position_label(row.get('position') or row.get('player_position'))
        if not pos:
            return None
        scenario = infer_scenario(pos, row.get('cost_to_call') or 0, bb)
        if scenario is None:
            return None
        ctx = {'hand': hand, 'position': pos, 'scenario': scenario, 'opener': None}

    ctx.update(
        {
            'effective_stack_bb': eff_bb,
            'num_players': num_players,
            'action': row.get('action') or row.get('action_taken'),
            # Carried for recency slicing; harmless to the pure grader.
            'created_at': row.get('created_at'),
            'hand_number': row.get('hand_number'),
        }
    )
    return ctx


def load_owner_chart_decisions(db_path: str, owner_id: str) -> list[dict]:
    """Load + reconstruct an owner's human preflop decisions for chart grading.

    Read-only. Scopes to games owned by ``owner_id`` and the human seat
    (player_name == owner_name), like ``coach_leaks.load_owner_preflop_decisions``.
    """
    import sqlite3

    out: list[dict] = []
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        # preflop_node_key may not exist yet (pre-capture-forward); select it
        # defensively so this works on either schema.
        has_node_key = any(
            r['name'] == 'preflop_node_key'
            for r in conn.execute("PRAGMA table_info(player_decision_analysis)")
        )
        node_col = 'pda.preflop_node_key AS preflop_node_key,' if has_node_key else ''
        # Scope by owner_id + join on game_id (both indexed); keep the human seat
        # (player_name == that game's owner_name) in Python. The `player_name =
        # g.owner_name` clause must stay OUT of the WHERE — it triggers an
        # O(games × name-matched-rows) nested loop (see load_owner_preflop_decisions).
        cur = conn.execute(
            f"""
            SELECT pda.player_hand_canonical AS canon,
                   pda.player_position       AS position,
                   pda.action_taken          AS action,
                   pda.cost_to_call          AS cost_to_call,
                   pda.player_stack          AS player_stack,
                   pda.num_opponents         AS num_opponents,
                   pda.player_name           AS player_name,
                   pda.created_at            AS created_at,
                   pda.hand_number           AS hand_number,
                   g.owner_name              AS owner_name,
                   {node_col}
                   CAST(json_extract(g.game_state_json, '$.current_ante') AS REAL) AS bb
            FROM player_decision_analysis pda
            JOIN games g ON g.game_id = pda.game_id
            WHERE g.owner_id = ?
              AND pda.phase = 'PRE_FLOP'
              AND pda.player_hand_canonical IS NOT NULL
            """,
            (owner_id,),
        )
        rows = [dict(r) for r in cur.fetchall() if r['player_name'] == r['owner_name']]
        conn.close()
    except Exception as e:
        logger.warning("load_owner_chart_decisions failed for %s: %s", owner_id, e)
        return []

    for r in rows:
        ctx = reconstruct_context(r, r.get('bb'))
        if ctx is not None:
            out.append(ctx)
    return out


def get_owner_chart_leak_set(
    db_path: str, owner_id: str, *, confirmed_only: bool = True, recent_hands: int = 500
) -> dict:
    """Build the live-recall lookup of an owner's chart leaks.

    Returns ``{'by_spot': {(scenario, position): info}, 'by_hand':
    {(scenario, position, hand): info}}`` where info is
    ``{kind, status, your_freq, chart_freq, gap}``. The two tiers let the
    in-game coach prefer a specific-hand nudge when one exists and fall back to
    the spot-tendency nudge otherwise.

    ``confirmed_only`` (default) keeps live nudges to leaks we're sure of;
    watching-tier items stay in the review surface.

    ``recent_hands`` scopes the recall to the player's last N hands so the
    in-game nudge tracks CURRENT form — a leak you've fixed stops nudging, and a
    leak you've started making shows up — rather than an all-time aggregate that
    never forgets. Pass ``recent_hands=None`` (or 0) for the all-time set.
    """
    from poker.strategy.preflop_reference import reference_strategy

    from .coach_chart_leaks import compute_chart_leaks, recent_slice

    decisions = load_owner_chart_decisions(db_path, owner_id)
    if recent_hands:
        decisions = recent_slice(decisions, n_hands=recent_hands)

    def info(lk):
        return {
            'kind': lk.kind,
            'status': lk.status,
            'your_freq': lk.your_freq,
            'chart_freq': lk.chart_freq,
            'gap': lk.gap,
        }

    def keep(lk):
        return (not confirmed_only) or lk.status == 'confirmed'

    spot = compute_chart_leaks(decisions, reference_strategy, group_by='position')
    hand = compute_chart_leaks(decisions, reference_strategy, group_by='hand')
    return {
        'by_spot': {
            (lk.scenario, lk.position): info(lk) for lk in spot.leaks if keep(lk)
        },
        'by_hand': {
            (lk.scenario, lk.position, lk.hand): info(lk) for lk in hand.leaks if keep(lk)
        },
    }
