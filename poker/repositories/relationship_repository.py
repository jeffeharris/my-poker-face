"""Repository for relationship_states, cash_pair_stats, and ai_table_hand_counts.

Cross-session/cross-game tables owned by this repository:

- `relationship_states` (v87): per-(observer, opponent) affinity axes
  (heat, respect, likability). Heat decays via `project_heat`; the
  stored value is the "heat as of last_decay_tick" snapshot. Default
  read methods apply projection; raw reads are admin-only and
  explicitly named.

- `cash_pair_stats` (v87): cash-mode-only cumulative PnL between pairs.
  Distinct from relationship_states because PnL is meaningless in
  tournaments.

- `ai_table_hand_counts` (v153/v154): per-(sandbox, ai, table) hand +
  cumulative-net counter. Incremented once per AI per hand (not bilateral,
  unlike cash_pair_stats). `net_chips` feeds the success-weighted
  table-affinity attractiveness lever (an AI drifts back to rooms it wins
  at); `hands` is a general per-room activity tally.

All persistence APIs use **stable personality_ids** for keys, not
display names. The relationship layer's read paths consume
`RelationshipState` instances; the projection-on-read pattern means
callers can't accidentally observe stale "heat as of last event"
values when they want "live heat now" — they're forced to go
through `load_*` rather than reading the column directly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

from poker.memory.opponent_model import (
    CashPairStats,
    RelationshipState,
    project_heat,
)
from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


def _parse_timestamp(value) -> Optional[datetime]:
    """SQLite returns timestamps as strings; coerce back to datetime.

    Returns None for NULL rows (no event ever recorded), the most
    common state for newly observed pairs.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # SQLite default format: "YYYY-MM-DD HH:MM:SS[.ffffff]"
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _state_from_row(row, *, project_to: Optional[datetime] = None) -> RelationshipState:
    """Build a `RelationshipState` from a `relationship_states` row.

    When `project_to` is set, the returned state's `heat` field is
    the projected value (live heat now); the underlying stored
    snapshot remains accessible via `load_raw_relationship_state`
    for admin/analytics callers.
    """
    state = RelationshipState(
        heat=row['heat'],
        respect=row['respect'],
        likability=row['likability'],
        last_seen=_parse_timestamp(row['last_seen']),
        last_decay_tick=_parse_timestamp(row['last_decay_tick']),
    )
    if project_to is not None:
        state.heat = project_heat(state, project_to)
    return state


class RelationshipRepository(BaseRepository):
    """CRUD for relationship_states, cash_pair_stats, and ai_table_hand_counts.

    Schema is created by `SchemaManager.ensure_schema()`; this class
    only touches data. Callers go through this rather than emitting
    raw SQL so the projection-on-read invariant stays enforced at one
    location.
    """

    # --- relationship_states ---

    def save_relationship_state(
        self,
        observer_id: str,
        opponent_id: str,
        state: RelationshipState,
    ) -> None:
        """Upsert the (observer_id, opponent_id) row.

        Writes the stored `heat` snapshot verbatim — `record_event`
        is responsible for projecting heat through decay *before*
        applying event shifts, so the snapshot in the DB always
        represents "heat after most recent event."

        Uses `ON CONFLICT DO UPDATE` (not `INSERT OR REPLACE`) so the
        affinity write only touches the columns it owns. `notes` (v95)
        and `nickname_override` (v101) are written by separate paths
        (`save_note`, `save_nickname_override`) against the same
        `(observer_id, opponent_id)` key; a DELETE+INSERT replace would
        NULL them on every social event. See those siblings, which
        upsert the same way.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO relationship_states
                    (observer_id, opponent_id, heat, respect, likability,
                     last_seen, last_decay_tick)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observer_id, opponent_id) DO UPDATE SET
                    heat = excluded.heat,
                    respect = excluded.respect,
                    likability = excluded.likability,
                    last_seen = excluded.last_seen,
                    last_decay_tick = excluded.last_decay_tick
                """,
                (
                    observer_id,
                    opponent_id,
                    state.heat,
                    state.respect,
                    state.likability,
                    state.last_seen.isoformat() if state.last_seen else None,
                    state.last_decay_tick.isoformat() if state.last_decay_tick else None,
                ),
            )

    def load_relationship_state(
        self,
        observer_id: str,
        opponent_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[RelationshipState]:
        """Load with projection applied to the heat axis.

        Returns None when no row exists for the pair (the no-event-
        ever state). Callers should treat None as "no relationship
        modifier applies" — equivalent to a default `RelationshipState`
        with no event history.

        `now` defaults to `datetime.utcnow()` if not supplied.
        Explicit `now` lets callers pin the projection point for
        replay/test stability.
        """
        if now is None:
            now = datetime.utcnow()
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT heat, respect, likability, last_seen, last_decay_tick
                FROM relationship_states
                WHERE observer_id = ? AND opponent_id = ?
                """,
                (observer_id, opponent_id),
            ).fetchone()
            return _state_from_row(row, project_to=now) if row else None

    def load_raw_relationship_state(
        self,
        observer_id: str,
        opponent_id: str,
    ) -> Optional[RelationshipState]:
        """Load without projection — "heat as of last_decay_tick."

        Admin / analytics only. Production read paths should use
        `load_relationship_state`. The name is intentionally
        verbose to discourage accidental use; the default
        `load_relationship_state` is the right hammer for almost
        every caller.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT heat, respect, likability, last_seen, last_decay_tick
                FROM relationship_states
                WHERE observer_id = ? AND opponent_id = ?
                """,
                (observer_id, opponent_id),
            ).fetchone()
            return _state_from_row(row, project_to=None) if row else None

    def load_all_relationships(
        self,
        observer_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, RelationshipState]:
        """Load every (observer, *) row, projection applied.

        Returns `{opponent_id: RelationshipState}`. Useful at game
        startup when a controller wants its full affinity surface in
        one read rather than N round-trips during the hand.
        """
        if now is None:
            now = datetime.utcnow()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT opponent_id, heat, respect, likability,
                       last_seen, last_decay_tick
                FROM relationship_states
                WHERE observer_id = ?
                """,
                (observer_id,),
            ).fetchall()
            return {row['opponent_id']: _state_from_row(row, project_to=now) for row in rows}

    def load_inbound_relationships(
        self,
        opponent_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, RelationshipState]:
        """Load every (*, opponent_id) row — the INBOUND edges, heat projected.

        The mirror of `load_all_relationships`: that scans by `observer_id`
        (this entity's view of everyone); this scans by `opponent_id`
        (everyone's view OF this entity). Returns
        `{observer_id: RelationshipState}`.

        Used by the prestige aggregator to read the room's sentiment toward
        the human (keyed by their `owner_id`) in one query when computing
        `regard`. Note: `relationship_states` is not sandbox-scoped (v87), so
        this returns the global inbound graph — which is correct, since the
        edges accumulate globally as events fire. Hits
        `idx_relationship_states_opponent` (v121).
        """
        if now is None:
            now = datetime.utcnow()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT observer_id, heat, respect, likability,
                       last_seen, last_decay_tick
                FROM relationship_states
                WHERE opponent_id = ?
                """,
                (opponent_id,),
            ).fetchall()
            return {row['observer_id']: _state_from_row(row, project_to=now) for row in rows}

    # --- cash_pair_stats ---

    def save_cash_pair_stats(
        self,
        observer_id: str,
        opponent_id: str,
        stats: CashPairStats,
        *,
        sandbox_id: str,
    ) -> None:
        """Upsert the (sandbox_id, observer_id, opponent_id) cumulative row.

        Callers writing pair updates at hand resolution must write
        BOTH the (winner, loser) row AND the mirror (loser, winner)
        row with negated PnL in a single transaction, so the two
        views can't drift. This method writes one row; transaction
        management is the caller's responsibility.

        `sandbox_id` is the v109 scoping field — every row is keyed
        per sandbox so the admin Chip Economy view can filter. There
        is no NULL bucket; callers that genuinely lack a sandbox
        context (legacy tests, tournament adapters) should pass a
        sentinel like `''` and accept that the aggregate's
        per-sandbox filter won't surface those rows.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cash_pair_stats
                    (sandbox_id, observer_id, opponent_id,
                     cumulative_pnl, hands_played_cash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    sandbox_id,
                    observer_id,
                    opponent_id,
                    stats.cumulative_pnl,
                    stats.hands_played_cash,
                ),
            )

    def apply_cash_pair_pnl(
        self,
        winner_id: str,
        loser_id: str,
        chips: int,
        *,
        sandbox_id: str,
        hand_delta: int = 1,
    ) -> None:
        """Bilateral cash_pair_stats update for one chip-flow tuple.

        Loads both `(winner, loser)` and `(loser, winner)` rows (or
        defaults), applies `+chips` to the winner-perspective row and
        `-chips` to the loser-perspective row, increments
        `hands_played_cash` on both by `hand_delta`, and persists in
        a single transaction so the views can't drift on partial
        failure.

        `chips` is the winner-POV magnitude (always positive when
        called from the chip-flow allocator's `ChipFlow.chips`); the
        method handles sign-flipping for the mirror row internally.

        `sandbox_id` (v109) scopes the row — pair PnL accumulates
        independently per sandbox so the admin Chip Economy panel can
        filter Won/Lost/Net by the sandbox dropdown.

        Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1
        §"Cash pair stats" — "Write transactions update both rows so
        the views can't drift."
        """
        with self._get_connection() as conn:
            # Load existing rows (raw row reads within the same conn
            # so the transaction sees consistent state).
            winner_row = conn.execute(
                """
                SELECT cumulative_pnl, hands_played_cash
                FROM cash_pair_stats
                WHERE sandbox_id = ? AND observer_id = ? AND opponent_id = ?
                """,
                (sandbox_id, winner_id, loser_id),
            ).fetchone()
            loser_row = conn.execute(
                """
                SELECT cumulative_pnl, hands_played_cash
                FROM cash_pair_stats
                WHERE sandbox_id = ? AND observer_id = ? AND opponent_id = ?
                """,
                (sandbox_id, loser_id, winner_id),
            ).fetchone()

            winner_pnl = (winner_row['cumulative_pnl'] if winner_row else 0) + chips
            winner_hands = (winner_row['hands_played_cash'] if winner_row else 0) + hand_delta
            loser_pnl = (loser_row['cumulative_pnl'] if loser_row else 0) - chips
            loser_hands = (loser_row['hands_played_cash'] if loser_row else 0) + hand_delta

            conn.execute(
                """
                INSERT OR REPLACE INTO cash_pair_stats
                    (sandbox_id, observer_id, opponent_id,
                     cumulative_pnl, hands_played_cash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sandbox_id, winner_id, loser_id, winner_pnl, winner_hands),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO cash_pair_stats
                    (sandbox_id, observer_id, opponent_id,
                     cumulative_pnl, hands_played_cash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sandbox_id, loser_id, winner_id, loser_pnl, loser_hands),
            )

    def aggregate_cash_pnl_by_entity(
        self,
        *,
        sandbox_id: Optional[str] = None,
    ) -> dict:
        """Per-entity cash PnL totals, summed across all opponents.

        For each `observer_id` in `cash_pair_stats`, returns
        `{chips_won, chips_lost, net_pnl, hands_played_cash}`:

          * `chips_won` = sum of positive `cumulative_pnl` contributions
            (chips taken from opponents the observer is up on).
          * `chips_lost` = sum of `abs(cumulative_pnl)` for negative
            contributions (chips given to opponents who are up on the
            observer).
          * `net_pnl` = chips_won − chips_lost.
          * `hands_played_cash` = SUM of hand counts across all pairs.
            **NOTE**: this overcounts — every hand involving N players
            writes N×(N−1) pair rows, so a 6-handed hand contributes 5
            to each seat's sum here. The number is useful for relative
            comparison between entities but isn't a literal hand count.

        `sandbox_id=None` (default) aggregates lifetime PnL across
        every sandbox — the cross-sandbox view that drives the
        CharacterDetailCard "Track Record" section. Passing an explicit
        `sandbox_id` filters to one sandbox, which is what the admin
        Chip Economy panel uses when scoped by its dropdown.
        """
        params: list = []
        where = ""
        if sandbox_id is not None:
            where = "WHERE sandbox_id = ?"
            params.append(sandbox_id)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    observer_id,
                    SUM(CASE WHEN cumulative_pnl > 0
                        THEN cumulative_pnl ELSE 0 END) AS chips_won,
                    SUM(CASE WHEN cumulative_pnl < 0
                        THEN -cumulative_pnl ELSE 0 END) AS chips_lost,
                    SUM(cumulative_pnl) AS net_pnl,
                    SUM(hands_played_cash) AS hands_played_cash
                FROM cash_pair_stats
                {where}
                GROUP BY observer_id
                """,
                params,
            ).fetchall()
        return {
            row['observer_id']: {
                'chips_won': int(row['chips_won'] or 0),
                'chips_lost': int(row['chips_lost'] or 0),
                'net_pnl': int(row['net_pnl'] or 0),
                'hands_played_cash': int(row['hands_played_cash'] or 0),
            }
            for row in rows
        }

    def load_cash_pair_stats(
        self,
        observer_id: str,
        opponent_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> Optional[CashPairStats]:
        """Load the cumulative cash-mode stats for a pair.

        Returns None when no row exists (pair has never played in
        cash mode). Callers should treat None as "PnL = 0, hands = 0"
        — equivalent to a default-constructed CashPairStats.

        `sandbox_id=None` (default) sums across every sandbox the pair
        has played in — the cross-sandbox lifetime view used by the
        CharacterDetailCard dossier. Passing an explicit `sandbox_id`
        returns just that sandbox's row.
        """
        with self._get_connection() as conn:
            if sandbox_id is None:
                row = conn.execute(
                    """
                    SELECT
                        SUM(cumulative_pnl) AS cumulative_pnl,
                        SUM(hands_played_cash) AS hands_played_cash
                    FROM cash_pair_stats
                    WHERE observer_id = ? AND opponent_id = ?
                    """,
                    (observer_id, opponent_id),
                ).fetchone()
                if not row or row['cumulative_pnl'] is None:
                    return None
            else:
                row = conn.execute(
                    """
                    SELECT cumulative_pnl, hands_played_cash
                    FROM cash_pair_stats
                    WHERE sandbox_id = ?
                      AND observer_id = ? AND opponent_id = ?
                    """,
                    (sandbox_id, observer_id, opponent_id),
                ).fetchone()
                if not row:
                    return None
            return CashPairStats(
                observer_id=observer_id,
                opponent_id=opponent_id,
                cumulative_pnl=int(row['cumulative_pnl']),
                hands_played_cash=int(row['hands_played_cash'] or 0),
            )

    def list_cash_pair_stats_for_observer(
        self,
        observer_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> list[CashPairStats]:
        """Return every opponent this observer has tangled with in cash.

        "Tangled with" = at least one confrontation hand where chips
        flowed (`hands_played_cash > 0`) — the durable proxy for "have I
        met this persona." Used by the whereabouts feature to scope the
        player-facing view to opponents they've actually played, and to
        annotate each with the player's lifetime PnL against them.

        `sandbox_id=None` sums across every sandbox (the cross-sandbox
        lifetime view); an explicit `sandbox_id` returns just that
        sandbox's rows. Pairs with `load_cash_pair_stats`' scoping.
        """
        with self._get_connection() as conn:
            if sandbox_id is None:
                rows = conn.execute(
                    """
                    SELECT
                        opponent_id,
                        SUM(cumulative_pnl) AS cumulative_pnl,
                        SUM(hands_played_cash) AS hands_played_cash
                    FROM cash_pair_stats
                    WHERE observer_id = ?
                    GROUP BY opponent_id
                    HAVING SUM(hands_played_cash) > 0
                    """,
                    (observer_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT opponent_id, cumulative_pnl, hands_played_cash
                    FROM cash_pair_stats
                    WHERE observer_id = ? AND sandbox_id = ?
                      AND hands_played_cash > 0
                    """,
                    (observer_id, sandbox_id),
                ).fetchall()
            return [
                CashPairStats(
                    observer_id=observer_id,
                    opponent_id=row["opponent_id"],
                    cumulative_pnl=int(row["cumulative_pnl"] or 0),
                    hands_played_cash=int(row["hands_played_cash"] or 0),
                )
                for row in rows
            ]

    # --- ai table hand counts (v153/v154) — per-room hand+net, feeds table affinity ---

    def increment_ai_table_hands(
        self,
        ai_id: str,
        table_id: str,
        *,
        sandbox_id: str,
        net_delta: int = 0,
        now: Optional[str] = None,
    ) -> None:
        """Record one hand played by `ai_id` at `table_id` in `sandbox_id`.

        Call ONCE per AI per hand — NOT bilateral. (Contrast
        `apply_cash_pair_pnl`, which writes N×(N−1) pair rows per hand;
        this writes one row per seated AI.) The first hand inserts
        `hands = 1`; later hands bump it. `net_delta` is this AI's signed
        chip result for the hand (won − lost), accumulated into `net_chips`
        for the success-weighted table-affinity term. `now` is an ISO-8601
        string stamped as `last_hand_at` for auditability.

        Via `net_chips`, feeds the table-affinity attractiveness lever.
        Best-effort by convention — callers wrap this so a counter write
        never breaks hand resolution.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO ai_table_hand_counts
                    (sandbox_id, ai_id, table_id, hands, net_chips, last_hand_at)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(sandbox_id, ai_id, table_id)
                DO UPDATE SET
                    hands = hands + 1,
                    net_chips = net_chips + excluded.net_chips,
                    last_hand_at = COALESCE(excluded.last_hand_at, ai_table_hand_counts.last_hand_at)
                """,
                (sandbox_id, ai_id, table_id, int(net_delta), now),
            )

    def load_ai_table_net(
        self,
        ai_id: str,
        *,
        sandbox_id: str,
    ) -> dict[str, int]:
        """Load all `{table_id: net_chips}` rows for one AI in one sandbox.

        The read behind the table-affinity attractiveness term — the seating
        path uses it to bias an AI toward rooms it wins at and away from rooms
        it loses at. Empty dict when the AI has no recorded hands.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT table_id, net_chips
                FROM ai_table_hand_counts
                WHERE sandbox_id = ? AND ai_id = ?
                """,
                (sandbox_id, ai_id),
            ).fetchall()
        return {row["table_id"]: int(row["net_chips"]) for row in rows}

    def load_ai_table_hands(
        self,
        ai_id: str,
        *,
        sandbox_id: str,
    ) -> dict[str, int]:
        """Return `{table_id: hands}` for one AI in one sandbox.

        Per-room activity tally — exposed for tests and admin/debug surfaces.
        Empty dict when the AI has no recorded hands.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT table_id, hands
                FROM ai_table_hand_counts
                WHERE sandbox_id = ? AND ai_id = ?
                """,
                (sandbox_id, ai_id),
            ).fetchall()
        return {row["table_id"]: int(row["hands"]) for row in rows}

    # --- notes (v95) ---

    def load_note(self, observer_id: str, opponent_id: str) -> Optional[str]:
        """Return the player-authored note for this pair, or None.

        None covers both "no row yet" and "row exists but notes is NULL"
        — the dossier treats those identically (no note to display, empty
        textarea to edit).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT notes FROM relationship_states
                WHERE observer_id = ? AND opponent_id = ?
                """,
                (observer_id, opponent_id),
            ).fetchone()
            if not row:
                return None
            return row['notes']

    def save_note(
        self,
        observer_id: str,
        opponent_id: str,
        note: Optional[str],
    ) -> None:
        """Upsert the note for this pair.

        Empty / whitespace-only notes are stored as NULL so the
        "has a note" predicate stays meaningful. Uses an UPSERT so
        we don't have to touch the affinity axes — a freshly-noted
        pair gets a row with default heat/respect/likability and the
        note attached.
        """
        clean = (note or '').strip() or None
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO relationship_states
                    (observer_id, opponent_id, notes)
                VALUES (?, ?, ?)
                ON CONFLICT(observer_id, opponent_id)
                DO UPDATE SET notes = excluded.notes
                """,
                (observer_id, opponent_id, clean),
            )

    # --- nickname_override (v101) ---

    def load_nickname_override(
        self,
        observer_id: str,
        opponent_id: str,
    ) -> Optional[str]:
        """Return the player-authored nickname override, or None.

        None covers both "no row yet" and "row exists but
        nickname_override is NULL" — the dossier treats those
        identically (fall back to the personality's canonical
        nickname).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT nickname_override FROM relationship_states
                WHERE observer_id = ? AND opponent_id = ?
                """,
                (observer_id, opponent_id),
            ).fetchone()
            if not row:
                return None
            return row['nickname_override']

    def load_all_nickname_overrides(
        self,
        observer_id: str,
    ) -> Dict[str, str]:
        """Return every nickname override this observer has set.

        Keyed on opponent personality_id. NULL / empty overrides are
        excluded — callers want only the rows where the viewer
        actually chose an alias, not the (default) "no override"
        state. Used by the client to apply per-viewer aliases to
        every opponent label without N round-trips.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT opponent_id, nickname_override
                FROM relationship_states
                WHERE observer_id = ?
                  AND nickname_override IS NOT NULL
                  AND nickname_override != ''
                """,
                (observer_id,),
            ).fetchall()
            return {row['opponent_id']: row['nickname_override'] for row in rows}

    def save_nickname_override(
        self,
        observer_id: str,
        opponent_id: str,
        nickname: Optional[str],
    ) -> None:
        """Upsert the nickname override for this pair.

        Empty / whitespace-only input is stored as NULL so "has an
        override" stays a meaningful predicate — clearing the field
        in the UI should fully revert to the canonical nickname.
        Mirrors `save_note`: UPSERT keeps the affinity axes at their
        defaults if no row exists yet.
        """
        clean = (nickname or '').strip() or None
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO relationship_states
                    (observer_id, opponent_id, nickname_override)
                VALUES (?, ?, ?)
                ON CONFLICT(observer_id, opponent_id)
                DO UPDATE SET nickname_override = excluded.nickname_override
                """,
                (observer_id, opponent_id, clean),
            )
