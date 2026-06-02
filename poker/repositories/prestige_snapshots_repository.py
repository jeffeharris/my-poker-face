"""Repository for the v121 `prestige_snapshots` surface.

Per-(sandbox, owner) human-reputation points captured by the realtime
background ticker. Powers the cash lobby's reputation scoreboard and a
renown trajectory over time.

Two axes (see `cash_mode/prestige.py` for the formula):
  - `renown` ratchets — the recorder always stores the running peak, so a
    downswing can't erase the career record.
  - `regard` swings with behaviour and partially decays with heat.

Component columns (`renown_*`, `regard_*`) store the formula's
contributions so the panel and debugging can show WHY without recomputing.
Rows are append-only history; `prune` enforces retention.

Schema is created by `SchemaManager.ensure_schema()` (v121 migration);
this class only touches data. See
`docs/plans/CASH_MODE_PLAYER_PRESTIGE.md`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 60  # reputation moves slowly; keep a longer tail than holdings


class PrestigeSnapshotsRepository(BaseRepository):
    """CRUD for `prestige_snapshots`."""

    def record(
        self,
        *,
        captured_at: str,
        sandbox_id: str,
        owner_id: str,
        score: Any,  # cash_mode.prestige.ReputationScore (duck-typed to avoid an import cycle)
        entity_kind: str = "player",
        formula_version: str = "v1",
        renown_v2: Optional[float] = None,
        victim_percentile: Optional[float] = None,
        high_cut: Optional[float] = None,
        renown_v2_components: Optional[Dict[str, float]] = None,
        field_size: Optional[int] = None,
    ) -> None:
        """Insert one prestige capture for (sandbox, owner).

        `score` is a `ReputationScore`; its `renown` is expected to already
        be the ratcheted value (the recorder reads `load_renown_peak` and
        passes the peak into `compute_prestige`, which takes the max). This
        method just persists — it does not enforce the ratchet itself.

        The v2 fields (``formula_version`` … ``field_size``) are the v133
        ADDITIVE columns. They default to a v1 row (``formula_version='v1'``,
        the rest NULL) so every existing caller is unchanged. When the ticker
        computes the field-relative v2 layer (behind ``RENOWN_V2_ENABLED``) it
        passes ``formula_version='v2'`` plus the uncapped ``renown_v2``, the
        field ``high_cut``, the human's ``victim_percentile``, the v2 component
        breakdown (JSON-serialised here), and the ``field_size``. The CONSUMED
        ``score.quadrant`` is whichever formula's quadrant the caller chose —
        ``formula_version`` only records which one, for the panel's gauge.

        ``entity_kind`` (v139) is 'player' for the human (the default — every
        existing caller is unchanged) or 'ai' for an AI entity. For AI rows
        ``owner_id`` carries the raw ``personality_id`` (the universal subject
        id). Use :meth:`record_ai_many` for the AI fan-out; this single-row
        path is the human's.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO prestige_snapshots (
                    captured_at, sandbox_id, owner_id, entity_kind,
                    renown, regard, quadrant,
                    renown_breadth, renown_tenure, renown_stake_tier,
                    renown_beat_respected, renown_high_stakes,
                    regard_likability, regard_respect, regard_heat,
                    opponent_count,
                    formula_version, renown_v2, victim_percentile,
                    high_cut, renown_v2_components, field_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_at,
                    sandbox_id,
                    owner_id,
                    entity_kind,
                    float(score.renown),
                    float(score.regard),
                    score.quadrant,
                    float(score.renown_breadth),
                    float(score.renown_tenure),
                    float(score.renown_stake_tier),
                    float(score.renown_beat_respected),
                    float(score.renown_high_stakes),
                    float(score.regard_likability),
                    float(score.regard_respect),
                    float(score.regard_heat),
                    int(score.opponent_count),
                    formula_version,
                    None if renown_v2 is None else float(renown_v2),
                    None if victim_percentile is None else float(victim_percentile),
                    None if high_cut is None else float(high_cut),
                    None if renown_v2_components is None
                    else json.dumps(renown_v2_components, sort_keys=True),
                    None if field_size is None else int(field_size),
                ),
            )

    def record_ai_many(
        self,
        *,
        sandbox_id: str,
        captured_at: str,
        rows: List[Dict[str, Any]],
    ) -> int:
        """Batch-insert v2-native renown rows for AI entities. Returns count.

        The AI fan-out (`ticker_service`) scores the whole field every cycle and
        already throws every AI row away; this persists them in ONE transaction
        (the only added write per recompute, vs N single inserts).

        Each ``row`` is a plain dict — NOT a ``ReputationScore`` — with:
          ``owner_id`` (raw personality_id), ``renown_v2`` (already ratcheted),
          ``regard``, ``quadrant`` (the field-relative label), ``victim_percentile``,
          ``high_cut``, ``components`` (dict → JSON), ``field_size``.

        AI rows are **v2-native**: ``entity_kind='ai'``, ``formula_version='v2'``,
        and the v1 columns (``renown`` + the ``renown_*``/``regard_*`` component
        breakdown) are 0 — they don't apply to the capped v1 scale. The v2
        consumers read ``quadrant`` + ``renown_v2``. ``owner_id`` is the subject
        (the personality_id), never the sandbox owner — that invariant keeps the
        human's ``load_latest`` from ever matching an AI row.
        """
        if not rows:
            return 0
        params = [
            (
                captured_at,
                sandbox_id,
                row["owner_id"],
                "ai",
                0.0,  # v1 renown — n/a for AI rows
                float(row.get("regard", 0.0)),
                row["quadrant"],
                0.0, 0.0, 0.0, 0.0, 0.0,  # renown_* components — n/a
                0.0, 0.0, 0.0,            # regard_* components — n/a
                0,                        # opponent_count — n/a
                "v2",
                float(row["renown_v2"]),
                None if row.get("victim_percentile") is None
                else float(row["victim_percentile"]),
                None if row.get("high_cut") is None else float(row["high_cut"]),
                None if row.get("components") is None
                else json.dumps(row["components"], sort_keys=True),
                None if row.get("field_size") is None else int(row["field_size"]),
            )
            for row in rows
        ]
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO prestige_snapshots (
                    captured_at, sandbox_id, owner_id, entity_kind,
                    renown, regard, quadrant,
                    renown_breadth, renown_tenure, renown_stake_tier,
                    renown_beat_respected, renown_high_stakes,
                    regard_likability, regard_respect, regard_heat,
                    opponent_count,
                    formula_version, renown_v2, victim_percentile,
                    high_cut, renown_v2_components, field_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
        return len(params)

    def load_latest(
        self,
        sandbox_id: str,
        owner_id: str,
        entity_kind: str = "player",
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent capture for (sandbox, owner, kind), or None.

        The lobby route reads this for the current scoreboard. Returns every
        column so the route can expose the component breakdown without a
        second query. None when no capture exists yet (a brand-new sandbox
        before the first ticker fire) — the lobby renders no panel.

        ``entity_kind`` defaults to 'player', so every existing human caller is
        unchanged and never matches an AI row (AI rows carry 'ai'). Pass
        ``entity_kind='ai'`` to read a persisted AI entity's latest renown.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM prestige_snapshots
                WHERE sandbox_id = ? AND owner_id = ? AND entity_kind = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (sandbox_id, owner_id, entity_kind),
            ).fetchone()
        return dict(row) if row is not None else None

    def load_renown_peak(
        self,
        sandbox_id: str,
        owner_id: str,
        entity_kind: str = "player",
    ) -> float:
        """Return the historical max renown for (sandbox, owner, kind), or 0.0.

        The recorder reads this and passes it to `compute_prestige` so
        renown ratchets — a recompute can never drop it below the peak.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT MAX(renown) AS peak
                FROM prestige_snapshots
                WHERE sandbox_id = ? AND owner_id = ? AND entity_kind = ?
                """,
                (sandbox_id, owner_id, entity_kind),
            ).fetchone()
        return float(row["peak"]) if row and row["peak"] is not None else 0.0

    def load_renown_v2_peak(
        self,
        sandbox_id: str,
        owner_id: str,
        entity_kind: str = "player",
    ) -> float:
        """Return the historical max **v2** renown for (sandbox, owner, kind), or 0.0.

        The v2 layer is uncapped and on a different scale than v1, so it keeps
        its OWN ratchet — the recorder reads this and passes it into the v2
        compute, which takes the max, so a downswing (or a field that inflated
        around the human) can't erase the v2 career record. Rows written before
        v133 (or any v1-only row) have NULL `renown_v2` and are ignored by the
        MAX. Independent of `load_renown_peak` (the v1 ratchet); the two never
        mix scales.

        For the per-AI fan-out, prefer the batched :meth:`load_renown_v2_peaks`
        (one GROUP BY) over calling this once per AI.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT MAX(renown_v2) AS peak
                FROM prestige_snapshots
                WHERE sandbox_id = ? AND owner_id = ? AND entity_kind = ?
                """,
                (sandbox_id, owner_id, entity_kind),
            ).fetchone()
        return float(row["peak"]) if row and row["peak"] is not None else 0.0

    def load_renown_v2_peaks(
        self,
        sandbox_id: str,
        entity_kind: str = "ai",
    ) -> Dict[str, float]:
        """Return ``{owner_id: max(renown_v2)}`` for every entity of ``kind``.

        One GROUP-BY query for the whole field, so the per-AI ratchet doesn't
        cost N round-trips per recompute. Defaults to 'ai' (the fan-out's use);
        entities with only NULL `renown_v2` rows are omitted. Hits
        ``idx_prestige_snap_kind``.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT owner_id, MAX(renown_v2) AS peak
                FROM prestige_snapshots
                WHERE sandbox_id = ? AND entity_kind = ? AND renown_v2 IS NOT NULL
                GROUP BY owner_id
                """,
                (sandbox_id, entity_kind),
            ).fetchall()
        return {r["owner_id"]: float(r["peak"]) for r in rows}

    def load_latest_field_percentiles(
        self,
        sandbox_id: str,
    ) -> Dict[str, float]:
        """Return ``{owner_id: victim_percentile}`` for the latest captured cycle.

        The field-renown percentile (in [0,1]) of every entity — AI and human
        alike — from the most recent ticker recompute. Powers the B4 marquee
        pull (`cash_mode.attractiveness.occ_prestige` + `status_appetite`): one
        batched read per fill instead of a per-entity lookup.

        The ticker writes the whole field in one recompute, so all rows share a
        `captured_at`; selecting the sandbox's MAX(captured_at) yields exactly
        the latest cycle's percentiles. Rows with NULL `victim_percentile`
        (v1-only) are skipped. Empty dict when nothing's been scored yet.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT owner_id, victim_percentile
                FROM prestige_snapshots
                WHERE sandbox_id = ?
                  AND captured_at = (
                      SELECT MAX(captured_at) FROM prestige_snapshots
                      WHERE sandbox_id = ?
                  )
                  AND victim_percentile IS NOT NULL
                """,
                (sandbox_id, sandbox_id),
            ).fetchall()
        return {r["owner_id"]: float(r["victim_percentile"]) for r in rows}

    def series_since(
        self,
        since_iso: str,
        *,
        sandbox_id: str,
        owner_id: str,
        entity_kind: str = "player",
    ) -> List[Dict[str, Any]]:
        """Return (captured_at, renown, regard) points since `since_iso`, oldest → newest.

        For a future renown/regard trajectory sparkline. Hits
        `idx_prestige_snap_scope`. Lexical `captured_at >= since_iso`
        comparison (the recorder writes explicit ISO-8601 UTC). Defaults to the
        human ('player') so AI rows never bleed into the human's trajectory.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT captured_at, renown, regard, quadrant
                FROM prestige_snapshots
                WHERE sandbox_id = ? AND owner_id = ? AND entity_kind = ?
                  AND captured_at >= ?
                ORDER BY captured_at ASC
                """,
                (sandbox_id, owner_id, entity_kind, since_iso),
            ).fetchall()
        return [
            {
                "captured_at": r["captured_at"],
                "renown": float(r["renown"]),
                "regard": float(r["regard"]),
                "quadrant": r["quadrant"],
            }
            for r in rows
        ]

    def prune(self, older_than_iso: str) -> int:
        """Delete captures older than `older_than_iso`. Returns row count.

        Enforces retention so the table can't grow unbounded over long
        uptime. Lexical comparison on the ISO `captured_at`.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM prestige_snapshots WHERE captured_at < ?",
                (older_than_iso,),
            )
            return cursor.rowcount
