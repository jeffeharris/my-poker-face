"""Admin "Player Holdings" view — current snapshot + time-series history.

Powers the Chip Economy → Player Holdings section. Two payloads:

  * `compute_holdings_snapshot` — per-player table of current chip
    counts. AI personalities scoped to the requested sandbox (or
    cross-sandbox when `sandbox_id=None`); human players come from the
    global `player_bankroll_state` (humans aren't sandbox-scoped in v1).

  * `compute_holdings_history` — per-player cumulative chip flow into
    and out of the central bank, time-ordered. Derived from
    `chip_ledger_entries` — only counts events that touch the bank
    (seed, regen, rake, stake settlement). Intra-table P&L is NOT
    captured because the ledger doesn't observe seat-to-seat flows;
    the UI labels the chart accordingly so it isn't read as a true
    balance curve.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def compute_holdings_snapshot(
    *,
    bankroll_repo,
    personality_repo,
    user_repo,
    relationship_repo,
    db_path: str,
    now: Optional[datetime] = None,
    sandbox_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the per-player holdings table payload.

    AI rows project regen at read time so the value matches what a
    live cash-mode read would return. Human rows expose `chips`
    verbatim — no regen, no projection.

    Each row also carries cash-mode `chips_won` / `chips_lost`
    from `cash_pair_stats`. When `sandbox_id` is set, the aggregate
    is scoped to that sandbox (matches what the dropdown shows);
    when `None` (admin "All sandboxes" view), it's the lifetime
    cross-sandbox total. v109 added the sandbox_id column so the
    per-sandbox filter actually narrows the data.

    Rows are sorted by `projected_chips` descending so the largest
    holders appear first.
    """
    if now is None:
        now = datetime.utcnow()

    # Cash PnL aggregate. Scoped when a sandbox is selected so the
    # Won/Lost/Net columns match the dropdown; unscoped (lifetime
    # cross-sandbox) in the admin "All sandboxes" view. Keyed on
    # the observer_id that the relationship detector wrote — historically
    # sometimes the personality slug, sometimes the display name. Look
    # up each row's PnL by trying every plausible key.
    if relationship_repo is not None:
        try:
            cash_pnl_by_observer = relationship_repo.aggregate_cash_pnl_by_entity(
                sandbox_id=sandbox_id,
            )
        except Exception as e:
            logger.warning("holdings: aggregate_cash_pnl_by_entity failed: %s", e)
            cash_pnl_by_observer = {}
    else:
        cash_pnl_by_observer = {}

    ai_rows = _collect_ai_rows(
        bankroll_repo=bankroll_repo,
        personality_repo=personality_repo,
        cash_pnl_by_observer=cash_pnl_by_observer,
        now=now,
        sandbox_id=sandbox_id,
    )
    player_rows = _collect_player_rows(
        user_repo=user_repo,
        cash_pnl_by_observer=cash_pnl_by_observer,
        db_path=db_path,
    )

    rows = ai_rows + player_rows
    rows.sort(key=lambda r: r['projected_chips'], reverse=True)
    return {
        'rows': rows,
        'as_of': now.isoformat(),
        'sandbox_id': sandbox_id,
    }


MAX_SERIES = 12  # plotted-line cap; rest still appear in the holdings table
MAX_POINTS_PER_SERIES = 400


def compute_holdings_history(
    *,
    ledger_repo,
    bankroll_repo,
    personality_repo,
    user_repo,
    db_path: str,
    days: int = 30,
    now: Optional[datetime] = None,
    sandbox_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return per-entity cumulative chip flow into/out of the central bank.

    Walks `chip_ledger_entries` since `now - days` and accumulates a
    running signed total per non-bank entity. Each entity's series ends
    with a synthetic point at `now` so the chart flattens cleanly to
    the current cumulative value rather than dropping off at the last
    event.

    The series value is NOT the entity's balance — it's "net chips
    received from the central bank to date." Seed + regen + stake
    issue increase it; rake + stake settlement decrease it. The frontend
    titles the chart accordingly.
    """
    if now is None:
        now = datetime.utcnow()
    days = max(1, min(int(days), 365))
    since = now - timedelta(days=days)
    since_iso = since.isoformat()

    entries = ledger_repo.non_bank_entries_since(
        since_iso,
        sandbox_id=sandbox_id,
    )

    series_by_entity: Dict[str, List[Dict[str, Any]]] = {}
    running: Dict[str, int] = {}

    for entry in entries:
        source = entry['source']
        sink = entry['sink']
        amount = entry['amount']
        # SQLite's `CURRENT_TIMESTAMP` default writes `YYYY-MM-DD HH:MM:SS`
        # (space separator, no timezone). Browsers disagree on parsing
        # that string — Safari returns NaN, Chrome treats it as local
        # time. Normalize to ISO-8601 with explicit UTC so the React
        # chart can `new Date(t).getTime()` it reliably.
        created_at = _normalize_to_utc_iso(entry['created_at'])
        # The bank is on exactly one side (the repo query enforces that
        # at least one side is non-bank; pure non-bank transfers
        # don't currently exist, but if they did this would skip them
        # rather than miscount).
        if sink.startswith('player:') or sink.startswith('ai:'):
            entity = sink
            signed = amount
        elif source.startswith('player:') or source.startswith('ai:'):
            entity = source
            signed = -amount
        else:
            continue

        new_total = running.get(entity, 0) + signed
        running[entity] = new_total
        series_by_entity.setdefault(entity, []).append(
            {
                't': created_at,
                'value': new_total,
                'reason': entry['reason'],
            }
        )

    # Flatten each series to `now` so the chart doesn't end at the
    # last event — feels broken otherwise when the latest activity was
    # hours ago.
    end_iso = _normalize_to_utc_iso(now.isoformat())
    for entity, points in series_by_entity.items():
        if not points or points[-1]['t'] != end_iso:
            points.append(
                {
                    't': end_iso,
                    'value': running[entity],
                    'reason': 'now',
                }
            )

    # Resolve labels: AI personalities → display name, players → user
    # name / email. Done after the walk so we only look up entities
    # that actually appear in the series.
    labels = _resolve_entity_labels(
        entity_ids=list(series_by_entity.keys()),
        personality_repo=personality_repo,
        user_repo=user_repo,
    )

    # Rank by absolute net flow so both top gainers and biggest
    # net-losers make the cut (a personality whose bank credits got
    # raked back is just as interesting as one accumulating chips).
    ranked = sorted(
        series_by_entity.items(),
        key=lambda kv: abs(running[kv[0]]),
        reverse=True,
    )
    truncated = ranked[:MAX_SERIES]
    series = [
        {
            'entity_id': entity_id,
            'label': labels.get(entity_id, entity_id),
            'kind': 'ai' if entity_id.startswith('ai:') else 'player',
            'total_net_flow': running[entity_id],
            'points': _downsample(points, MAX_POINTS_PER_SERIES),
        }
        for entity_id, points in truncated
    ]
    # Present the chart in net-flow descending order (gainers first)
    # rather than abs order; the ranking above was just for the
    # truncation cut, not the display order.
    series.sort(key=lambda s: s['total_net_flow'], reverse=True)

    return {
        'series': series,
        'series_total': len(series_by_entity),
        'series_truncated_to': len(series),
        'since': _normalize_to_utc_iso(since_iso),
        'as_of': end_iso,
        'sandbox_id': sandbox_id,
        'days': days,
    }


# --- internals ---


def _normalize_to_utc_iso(value: Optional[str]) -> Optional[str]:
    """Return `value` in `YYYY-MM-DDTHH:MM:SS...Z` form for browser parsing.

    Accepts:
      - `YYYY-MM-DD HH:MM:SS[.fff]` — SQLite CURRENT_TIMESTAMP default.
        Space → `T`, trailing `Z` appended.
      - `YYYY-MM-DDTHH:MM:SS[.fff]` — Python `datetime.isoformat()` (naive).
        Trailing `Z` appended.
      - Anything already ending with `Z` or `+/-NN:NN` offset: returned
        verbatim (already unambiguous).
      - `None`: returned verbatim.
    """
    if value is None:
        return None
    s = value
    if ' ' in s and 'T' not in s:
        s = s.replace(' ', 'T', 1)
    if s.endswith('Z') or '+' in s[10:] or s.count('-') > 2:
        # Already carries explicit timezone info; trust it.
        return s
    return s + 'Z'


def _downsample(
    points: List[Dict[str, Any]],
    target: int,
) -> List[Dict[str, Any]]:
    """Cap a time-ordered point list to `target` points, preserving shape.

    Uses uniform-stride sampling that always retains the first and
    last points. For cumulative-flow series this preserves the curve's
    shape well — values are step-monotonic-ish, so a stride-skipped
    point lands close to its neighbors anyway. We keep last-in-bucket
    semantics by selecting indices, not averaging, so reasons stay
    truthful.
    """
    n = len(points)
    if n <= target:
        return points
    # Always keep first and last; sample (target - 2) interior points
    # at uniform stride.
    if target <= 2:
        return [points[0], points[-1]]
    interior_count = target - 2
    stride = (n - 1) / (interior_count + 1)
    indices = {0, n - 1}
    for i in range(1, interior_count + 1):
        indices.add(min(n - 1, max(0, round(i * stride))))
    return [points[i] for i in sorted(indices)]


def _lookup_cash_pnl(
    cash_pnl_by_observer: Dict[str, Dict[str, int]],
    *keys: Optional[str],
) -> Dict[str, int]:
    """Find the cash-PnL aggregate for a row by trying each key in order.

    `cash_pair_stats.observer_id` is mixed in legacy data: sometimes
    the personality slug, sometimes the display name. The caller passes
    every plausible key (slug, name) and we return the first match —
    or all-zero if nothing hits.
    """
    for key in keys:
        if key and key in cash_pnl_by_observer:
            return cash_pnl_by_observer[key]
    return {'chips_won': 0, 'chips_lost': 0, 'net_pnl': 0, 'hands_played_cash': 0}


def _collect_ai_rows(
    *,
    bankroll_repo,
    personality_repo,
    cash_pnl_by_observer: Dict[str, Dict[str, int]],
    now: datetime,
    sandbox_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Build one row per AI personality bankroll in scope.

    Cross-sandbox (`sandbox_id=None`) walks every (personality_id,
    sandbox_id) pair — the same surface the chip-ledger audit uses for
    its projected sum — so a personality with bankrolls in multiple
    sandboxes shows up once per sandbox. A specific sandbox returns
    one row per personality in that sandbox.
    """
    pairs: List[Tuple[str, str]] = []
    if sandbox_id is None:
        try:
            pairs = list(bankroll_repo.iter_personality_ids_with_bankrolls_by_sandbox())
        except AttributeError:
            # Degraded fallback: list unique personality_ids without a
            # sandbox column; show them as cross-sandbox blanks.
            pids = bankroll_repo.iter_personality_ids_with_bankrolls(
                sandbox_id=None,
            )
            pairs = [(pid, '') for pid in pids]
    else:
        pids = bankroll_repo.iter_personality_ids_with_bankrolls(
            sandbox_id=sandbox_id,
        )
        pairs = [(pid, sandbox_id) for pid in pids]

    rows: List[Dict[str, Any]] = []
    for pid, sid in pairs:
        # `sid` may be the empty string only on the degraded
        # `AttributeError` fallback path — that's the "sandbox unknown"
        # sentinel. Treat it the same as None here so the row at least
        # surfaces with stored=0 instead of crashing the load call.
        loadable_sid = sid if sid else None
        state = None
        stored = 0
        if loadable_sid is not None:
            try:
                state = bankroll_repo.load_ai_bankroll(pid, sandbox_id=loadable_sid)
                stored = int(state.chips) if state else 0
            except Exception as e:
                logger.warning(
                    "holdings: load_ai_bankroll(%r, %r) failed: %s",
                    pid,
                    loadable_sid,
                    e,
                )
        projected = stored
        if loadable_sid is not None:
            try:
                projected = int(
                    bankroll_repo.load_ai_bankroll_current(
                        pid,
                        sandbox_id=loadable_sid,
                        now=now,
                    )
                    or stored
                )
            except Exception as e:
                logger.warning(
                    "holdings: load_ai_bankroll_current(%r, %r) failed: %s",
                    pid,
                    loadable_sid,
                    e,
                )

        name = _resolve_personality_name(personality_repo, pid)
        pnl = _lookup_cash_pnl(cash_pnl_by_observer, pid, name)
        rows.append(
            {
                'entity_id': f'ai:{pid}',
                'kind': 'ai',
                'id': pid,
                'name': name,
                'sandbox_id': sid or None,
                'stored_chips': stored,
                'projected_chips': projected,
                'uncommitted_regen': projected - stored,
                'last_regen_tick': (
                    state.last_regen_tick.isoformat() if state and state.last_regen_tick else None
                ),
                'chips_won': pnl['chips_won'],
                'chips_lost': pnl['chips_lost'],
                'net_pnl': pnl['net_pnl'],
            }
        )
    return rows


def _collect_player_rows(
    *,
    user_repo,
    cash_pnl_by_observer: Dict[str, Dict[str, int]],
    db_path: str,
) -> List[Dict[str, Any]]:
    """Build one row per human player bankroll.

    `player_bankroll_state` is global (not sandbox-scoped in v1) so the
    same player rows appear regardless of the admin sandbox filter.
    The UI flags this so admins don't try to read per-sandbox player
    holdings into the table.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT player_id, chips, starting_bankroll FROM player_bankroll_state"
        ).fetchall()

    # Guests have no `users` row, so `users.name` lookups miss. The
    # relationship detector historically wrote `observer_id` as the seat
    # display name (e.g. "Jeff") rather than the owner_id, so we also
    # fall back to the player's most-recent `games.owner_name` to bridge
    # that gap. Bulk-fetched once to avoid an N+1 per player row.
    player_ids = [row['player_id'] for row in rows]
    owner_names = _fetch_recent_owner_names(db_path, player_ids)

    out: List[Dict[str, Any]] = []
    for row in rows:
        player_id = row['player_id']
        chips = int(row['chips'] or 0)
        user_name = _resolve_player_name(user_repo, player_id)
        owner_name = owner_names.get(player_id)
        name = user_name or owner_name or player_id
        pnl = _lookup_cash_pnl(
            cash_pnl_by_observer,
            player_id,
            user_name,
            owner_name,
        )
        out.append(
            {
                'entity_id': f'player:{player_id}',
                'kind': 'player',
                'id': player_id,
                'name': name,
                'sandbox_id': None,
                'stored_chips': chips,
                'projected_chips': chips,
                'uncommitted_regen': 0,
                'last_regen_tick': None,
                'chips_won': pnl['chips_won'],
                'chips_lost': pnl['chips_lost'],
                'net_pnl': pnl['net_pnl'],
            }
        )
    return out


def _fetch_recent_owner_names(
    db_path: str,
    player_ids: List[str],
) -> Dict[str, str]:
    """Return `{owner_id: most_recent_owner_name}` for each id in scope.

    Bulk-fetches the most recent non-empty `games.owner_name` per
    `owner_id` in a single query. Players with no game rows (or only
    NULL/empty `owner_name`s) are simply absent from the result map —
    the caller treats that as "no fallback key available."
    """
    if not player_ids:
        return {}
    placeholders = ','.join('?' for _ in player_ids)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT owner_id, owner_name
                FROM games
                WHERE owner_id IN ({placeholders})
                  AND owner_name IS NOT NULL
                  AND owner_name != ''
                  AND (owner_id, created_at) IN (
                      SELECT owner_id, MAX(created_at)
                      FROM games
                      WHERE owner_id IN ({placeholders})
                        AND owner_name IS NOT NULL
                        AND owner_name != ''
                      GROUP BY owner_id
                  )
                """,
                list(player_ids) + list(player_ids),
            ).fetchall()
    except sqlite3.Error as e:
        logger.warning("holdings: owner_name bulk lookup failed: %s", e)
        return {}
    return {row['owner_id']: row['owner_name'] for row in rows}


def _resolve_personality_name(personality_repo, personality_id: str) -> str:
    """Look up a personality's display name; fall back to its id."""
    if personality_repo is None:
        return personality_id
    try:
        data = personality_repo.load_personality_by_id(personality_id)
    except Exception:
        data = None
    if not data:
        return personality_id
    return data.get('name') or personality_id


def _resolve_player_name(user_repo, player_id: str) -> Optional[str]:
    """Look up a human player's display name. Falls back to None.

    `player_id` is the user / owner id used in `player_bankroll_state`.
    Guest ids may not have a `users` row — those callers fall back to
    the id string in the calling row builder.
    """
    if user_repo is None:
        return None
    try:
        user = user_repo.get_user_by_id(player_id)
    except Exception:
        user = None
    if not user:
        return None
    return user.get('name') or user.get('email')


def _resolve_entity_labels(
    *,
    entity_ids: List[str],
    personality_repo,
    user_repo,
) -> Dict[str, str]:
    """Build {entity_id: display_label} for every entity in the series.

    AI entities use the personality's display name; player entities use
    the user record's name/email. Unknown ids fall back to the id
    portion (after the `ai:`/`player:` prefix) so the legend always
    has *something* readable.
    """
    labels: Dict[str, str] = {}
    for entity_id in entity_ids:
        if entity_id.startswith('ai:'):
            pid = entity_id[len('ai:') :]
            labels[entity_id] = _resolve_personality_name(personality_repo, pid)
        elif entity_id.startswith('player:'):
            pid = entity_id[len('player:') :]
            labels[entity_id] = _resolve_player_name(user_repo, pid) or pid
        else:
            labels[entity_id] = entity_id
    return labels
