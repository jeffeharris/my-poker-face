"""Admin "Player Holdings" view — net-worth snapshot + over-time history.

Powers the Chip Economy → Player Holdings section. Three entry points:

  * `compute_holdings_snapshot` — per-entity table of current net worth
    and its components (chips, stakes receivable, stakes outstanding)
    plus vice spent / side-hustle earned. Net worth requires a sandbox
    (stakes are global per entity but chips are per-sandbox); in the
    cross-sandbox "All sandboxes" view the table degrades to chips only
    (`net_worth_scoped=False`).

  * `compute_holdings_history` — per-entity net worth over time, read
    from the `holdings_snapshots` table the background ticker records.
    Net worth can't be reconstructed from the chip ledger (seat-to-seat
    chip flows never hit it), so the curve accrues forward from the
    first recorded snapshot. Requires a sandbox.

  * `record_holdings_snapshot` — compute the scoped net-worth rows and
    persist one capture column. Called by the world ticker (rate-limited)
    and as a first-view seed so the chart is never blank.

net worth = liquid chips + stakes receivable − stakes outstanding
(mirrors `GET /api/cash/net-worth`). See
`docs/plans/CASH_MODE_NET_WORTH_HOLDINGS.md`.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

VICE_REASON = 'vice_spending'
SIDE_HUSTLE_REASON = 'side_hustle_earning'

MAX_SERIES = 12          # plotted-line cap; rest still appear in the holdings table
MAX_POINTS_PER_SERIES = 400
DEFAULT_RETENTION_DAYS = 30


def compute_holdings_snapshot(
    *,
    bankroll_repo,
    personality_repo,
    user_repo,
    stake_repo,
    db_path: str,
    now: Optional[datetime] = None,
    sandbox_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the per-entity net-worth table payload.

    AI rows project regen at read time so the chip count matches what a
    live cash-mode read would return. Human rows expose `chips` verbatim.

    When `sandbox_id` is set, each row carries `net_worth`, `receivable`,
    `outstanding`, `vice_spent`, and `side_hustle_earned`. When `None`
    (the deprecated cross-sandbox view) net worth is omitted — stakes are
    global per entity, so attributing them across per-sandbox chip rows
    isn't meaningful — and the rows carry chips only.
    """
    if now is None:
        now = datetime.utcnow()
    scoped = sandbox_id is not None

    # Net-worth inputs. Stakes are global (the `stakes` table has no
    # sandbox_id), so receivable/outstanding are entity totals; vice and
    # side-hustle come from the per-entity chip ledger scoped to this
    # sandbox. All only computed in the scoped view.
    receivables: Dict[str, int] = {}
    outstanding: Dict[str, int] = {}
    vice: Dict[str, int] = {}
    side_hustle: Dict[str, int] = {}
    if scoped:
        if stake_repo is not None:
            try:
                receivables = stake_repo.aggregate_receivables_by_staker()
                outstanding = stake_repo.aggregate_outstanding_by_borrower()
            except Exception as e:
                logger.warning("holdings: stake aggregate failed: %s", e)
        vice = _aggregate_ledger_by_entity(db_path, VICE_REASON, 'source', sandbox_id)
        side_hustle = _aggregate_ledger_by_entity(
            db_path, SIDE_HUSTLE_REASON, 'sink', sandbox_id,
        )

    ai_rows = _collect_ai_rows(
        bankroll_repo=bankroll_repo,
        personality_repo=personality_repo,
        receivables=receivables,
        outstanding=outstanding,
        vice=vice,
        side_hustle=side_hustle,
        now=now,
        sandbox_id=sandbox_id,
        scoped=scoped,
    )
    player_rows = _collect_player_rows(
        user_repo=user_repo,
        receivables=receivables,
        outstanding=outstanding,
        vice=vice,
        side_hustle=side_hustle,
        db_path=db_path,
        scoped=scoped,
    )

    rows = ai_rows + player_rows
    sort_key = 'net_worth' if scoped else 'chips'
    rows.sort(key=lambda r: (r.get(sort_key) or 0), reverse=True)
    return {
        'rows': rows,
        'as_of': now.isoformat(),
        'sandbox_id': sandbox_id,
        'net_worth_scoped': scoped,
    }


def record_holdings_snapshot(
    *,
    snapshots_repo,
    bankroll_repo,
    personality_repo,
    user_repo,
    stake_repo,
    db_path: str,
    sandbox_id: str,
    now: Optional[datetime] = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> int:
    """Capture one net-worth column for `sandbox_id` into holdings_snapshots.

    Reuses `compute_holdings_snapshot` (scoped) so the recorded curve and
    the live table agree by construction. Writes only the net-worth
    components; vice/side-hustle are table-only (current totals, not a
    time series). Prunes rows past the retention window each call (cheap
    delete on an indexed column). Returns the number of points written.
    A `None` sandbox is a no-op — net worth requires a sandbox.
    """
    if sandbox_id is None:
        return 0
    if now is None:
        now = datetime.utcnow()
    snap = compute_holdings_snapshot(
        bankroll_repo=bankroll_repo,
        personality_repo=personality_repo,
        user_repo=user_repo,
        stake_repo=stake_repo,
        db_path=db_path,
        now=now,
        sandbox_id=sandbox_id,
    )
    captured_at = _normalize_to_utc_iso(now.isoformat())
    rows = [
        {
            'sandbox_id': sandbox_id,
            'entity_id': r['entity_id'],
            'kind': r['kind'],
            'net_worth': r['net_worth'],
            'chips': r['projected_chips'],
            'receivable': r['receivable'],
            'outstanding': r['outstanding'],
        }
        for r in snap['rows']
        if r.get('net_worth') is not None
    ]
    written = snapshots_repo.record(rows, captured_at=captured_at)
    try:
        cutoff = _normalize_to_utc_iso(
            (now - timedelta(days=retention_days)).isoformat()
        )
        snapshots_repo.prune(cutoff)
    except Exception as e:
        logger.warning("holdings: snapshot prune failed: %s", e)
    return written


def compute_holdings_history(
    *,
    snapshots_repo,
    personality_repo,
    user_repo,
    days: int = 30,
    now: Optional[datetime] = None,
    sandbox_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return per-entity net worth over time for a sandbox.

    Reads recorded `holdings_snapshots` points (not the chip ledger). The
    `since` returned is auto-fit to the earliest available point inside
    the window so a young economy renders as real movement rather than a
    flat line pinned to the window's left edge.

    `sandbox_id=None` returns an empty payload with `requires_sandbox`
    set — net worth is only meaningful within a sandbox (decision: the
    cross-sandbox net-worth view is deprecated).
    """
    if now is None:
        now = datetime.utcnow()
    days = max(1, min(int(days), 365))
    since = now - timedelta(days=days)
    since_iso = _normalize_to_utc_iso(since.isoformat())
    end_iso = _normalize_to_utc_iso(now.isoformat())

    if sandbox_id is None:
        return {
            'series': [],
            'series_total': 0,
            'series_truncated_to': 0,
            'since': since_iso,
            'as_of': end_iso,
            'sandbox_id': None,
            'days': days,
            'requires_sandbox': True,
        }

    points = snapshots_repo.series_since(since_iso, sandbox_id=sandbox_id)

    series_by_entity: Dict[str, List[Dict[str, Any]]] = {}
    for p in points:
        series_by_entity.setdefault(p['entity_id'], []).append({
            't': _normalize_to_utc_iso(p['captured_at']),
            'value': p['net_worth'],
        })

    # Extend each series flat to `now` with its latest value. Two reasons:
    # the curve shouldn't visually end at the last capture (feels stale when
    # the ticker last fired minutes ago), and a single recorded point would
    # otherwise be one SVG move-to that draws nothing — the synthetic
    # endpoint guarantees a visible (≥2-point) line on first view.
    for pts in series_by_entity.values():
        if pts and pts[-1]['t'] != end_iso:
            pts.append({'t': end_iso, 'value': pts[-1]['value']})

    # Auto-fit: start the x-domain at the earliest recorded point (when it
    # falls inside the window) so the curve fills the chart instead of a
    # long flat stub back to `since`.
    earliest = min(
        (pts[0]['t'] for pts in series_by_entity.values() if pts),
        default=None,
    )
    effective_since = (
        earliest if (earliest and earliest > since_iso) else since_iso
    )

    labels = _resolve_entity_labels(
        entity_ids=list(series_by_entity.keys()),
        personality_repo=personality_repo,
        user_repo=user_repo,
    )

    # Rank by current (latest) net worth descending — richest first.
    ranked = sorted(
        series_by_entity.items(),
        key=lambda kv: kv[1][-1]['value'] if kv[1] else 0,
        reverse=True,
    )
    truncated = ranked[:MAX_SERIES]
    series = [
        {
            'entity_id': entity_id,
            'label': labels.get(entity_id, entity_id),
            'kind': 'ai' if entity_id.startswith('ai:') else 'player',
            'current_net_worth': pts[-1]['value'] if pts else 0,
            'points': _downsample(pts, MAX_POINTS_PER_SERIES),
        }
        for entity_id, pts in truncated
    ]

    return {
        'series': series,
        'series_total': len(series_by_entity),
        'series_truncated_to': len(series),
        'since': effective_since,
        'as_of': end_iso,
        'sandbox_id': sandbox_id,
        'days': days,
        'requires_sandbox': False,
    }


# --- internals ---


def _aggregate_ledger_by_entity(
    db_path: str, reason: str, side: str, sandbox_id: str,
) -> Dict[str, int]:
    """Sum chip-ledger `amount` per entity for one reason, scoped to a sandbox.

    `side` is `'source'` (entity paid, e.g. vice spending) or `'sink'`
    (entity received, e.g. side-hustle earning). Returns
    `{entity_id: total}` keyed by the raw ledger vocabulary
    (`ai:<slug>` / `player:<id>`), so callers look up by the row's
    `entity_id` directly. Missing / unreadable → empty map.
    """
    col = 'source' if side == 'source' else 'sink'
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT {col} AS entity, SUM(amount) AS total
                FROM chip_ledger_entries
                WHERE reason = ? AND sandbox_id = ?
                GROUP BY {col}
                """,
                (reason, sandbox_id),
            ).fetchall()
    except sqlite3.Error as e:
        logger.warning("holdings: ledger aggregate (%s) failed: %s", reason, e)
        return {}
    return {row['entity']: int(row['total'] or 0) for row in rows}


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
    points: List[Dict[str, Any]], target: int,
) -> List[Dict[str, Any]]:
    """Cap a time-ordered point list to `target` points, preserving shape.

    Uniform-stride sampling that always retains the first and last points.
    Selects indices (no averaging) so timestamps stay truthful.
    """
    n = len(points)
    if n <= target:
        return points
    if target <= 2:
        return [points[0], points[-1]]
    interior_count = target - 2
    stride = (n - 1) / (interior_count + 1)
    indices = {0, n - 1}
    for i in range(1, interior_count + 1):
        indices.add(min(n - 1, max(0, round(i * stride))))
    return [points[i] for i in sorted(indices)]


def _net_worth_for(
    entity_id: str,
    bare_id: str,
    chips: int,
    *,
    receivables: Dict[str, int],
    outstanding: Dict[str, int],
    vice: Dict[str, int],
    side_hustle: Dict[str, int],
) -> Dict[str, int]:
    """Build the net-worth column block for one row.

    `bare_id` keys the (global) stake aggregates (`staker_id` /
    `borrower_id` are bare slugs / player ids); `entity_id` keys the chip
    ledger aggregates (`ai:<slug>` / `player:<id>`).
    """
    receivable = int(receivables.get(bare_id, 0))
    owed = int(outstanding.get(bare_id, 0))
    return {
        'receivable': receivable,
        'outstanding': owed,
        'net_worth': chips + receivable - owed,
        'vice_spent': int(vice.get(entity_id, 0)),
        'side_hustle_earned': int(side_hustle.get(entity_id, 0)),
    }


def _collect_ai_rows(
    *,
    bankroll_repo,
    personality_repo,
    receivables: Dict[str, int],
    outstanding: Dict[str, int],
    vice: Dict[str, int],
    side_hustle: Dict[str, int],
    now: datetime,
    sandbox_id: Optional[str],
    scoped: bool,
) -> List[Dict[str, Any]]:
    """Build one row per AI personality bankroll in scope.

    Cross-sandbox (`sandbox_id=None`) walks every (personality_id,
    sandbox_id) pair so a personality with bankrolls in multiple sandboxes
    shows up once per sandbox (chips only — no net worth in that view). A
    specific sandbox returns one row per personality with the full
    net-worth block.
    """
    pairs: List[Tuple[str, str]] = []
    if sandbox_id is None:
        try:
            pairs = list(
                bankroll_repo.iter_personality_ids_with_bankrolls_by_sandbox()
            )
        except AttributeError:
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
        loadable_sid = sid if sid else None
        state = None
        stored = 0
        if loadable_sid is not None:
            try:
                state = bankroll_repo.load_ai_bankroll(pid, sandbox_id=loadable_sid)
                stored = int(state.chips) if state else 0
            except Exception as e:
                logger.warning(
                    "holdings: load_ai_bankroll(%r, %r) failed: %s", pid, loadable_sid, e,
                )
        projected = stored
        if loadable_sid is not None:
            try:
                projected = int(
                    bankroll_repo.load_ai_bankroll_current(
                        pid, sandbox_id=loadable_sid, now=now,
                    ) or stored
                )
            except Exception as e:
                logger.warning(
                    "holdings: load_ai_bankroll_current(%r, %r) failed: %s",
                    pid, loadable_sid, e,
                )

        name = _resolve_personality_name(personality_repo, pid)
        entity_id = f'ai:{pid}'
        row = {
            'entity_id': entity_id,
            'kind': 'ai',
            'id': pid,
            'name': name,
            'sandbox_id': sid or None,
            'stored_chips': stored,
            'projected_chips': projected,
            'uncommitted_regen': projected - stored,
            'last_regen_tick': (
                state.last_regen_tick.isoformat()
                if state and state.last_regen_tick else None
            ),
        }
        if scoped:
            row.update(_net_worth_for(
                entity_id, pid, projected,
                receivables=receivables, outstanding=outstanding,
                vice=vice, side_hustle=side_hustle,
            ))
        rows.append(row)
    return rows


def _collect_player_rows(
    *,
    user_repo,
    receivables: Dict[str, int],
    outstanding: Dict[str, int],
    vice: Dict[str, int],
    side_hustle: Dict[str, int],
    db_path: str,
    scoped: bool,
) -> List[Dict[str, Any]]:
    """Build one row per human player bankroll.

    `player_bankroll_state` is global (not sandbox-scoped in v1) so the
    same player rows appear regardless of the admin sandbox filter. In the
    scoped view they carry the net-worth block (stakes are global; vice /
    side-hustle are looked up by the player's `player:<id>` ledger key).
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT player_id, chips, starting_bankroll FROM player_bankroll_state"
        ).fetchall()

    out: List[Dict[str, Any]] = []
    for row in rows:
        player_id = row['player_id']
        chips = int(row['chips'] or 0)
        user_name = _resolve_player_name(user_repo, player_id)
        name = user_name or player_id
        entity_id = f'player:{player_id}'
        record = {
            'entity_id': entity_id,
            'kind': 'player',
            'id': player_id,
            'name': name,
            'sandbox_id': None,
            'stored_chips': chips,
            'projected_chips': chips,
            'uncommitted_regen': 0,
            'last_regen_tick': None,
        }
        if scoped:
            record.update(_net_worth_for(
                entity_id, player_id, chips,
                receivables=receivables, outstanding=outstanding,
                vice=vice, side_hustle=side_hustle,
            ))
        out.append(record)
    return out


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
    """Look up a human player's display name. Falls back to None."""
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
    """Build {entity_id: display_label} for every entity in the series."""
    labels: Dict[str, str] = {}
    for entity_id in entity_ids:
        if entity_id.startswith('ai:'):
            pid = entity_id[len('ai:'):]
            labels[entity_id] = _resolve_personality_name(personality_repo, pid)
        elif entity_id.startswith('player:'):
            pid = entity_id[len('player:'):]
            labels[entity_id] = (
                _resolve_player_name(user_repo, pid) or pid
            )
        else:
            labels[entity_id] = entity_id
    return labels
