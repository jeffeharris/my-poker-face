"""Helpers for hydrating decision-analysis rows for API responses.

The `player_decision_analysis` table stores `intervention_trace` and
`strategy_pipeline_snapshot` as JSON-encoded text columns. This module
parses those columns into structured objects so the API returns typed
data instead of forcing each client to `JSON.parse` strings.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _parse_json_field(
    row: Dict[str, Any],
    src_key: str,
    dest_key: str,
    expected_type: type,
) -> None:
    """Move `row[src_key]` (JSON string) into `row[dest_key]` (parsed object).

    Drops `src_key` on success to avoid double-payload. On malformed
    JSON or wrong shape, sets `dest_key` to None and logs at WARNING
    so the UI sees a consistent contract instead of a 500.
    """
    raw = row.get(src_key)
    if raw is None:
        row[dest_key] = None
        row.pop(src_key, None)
        return
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as e:
        logger.warning(
            "[decision_analysis] malformed %s in row id=%s: %s",
            src_key, row.get('id'), e,
        )
        row[dest_key] = None
        row.pop(src_key, None)
        return
    if not isinstance(parsed, expected_type):
        logger.warning(
            "[decision_analysis] expected %s for %s in row id=%s, got %s",
            expected_type.__name__, src_key, row.get('id'),
            type(parsed).__name__,
        )
        row[dest_key] = None
        row.pop(src_key, None)
        return
    row[dest_key] = parsed
    row.pop(src_key, None)


def hydrate_decision_analysis(
    row: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Parse the trace + snapshot JSON columns on a decision-analysis row.

    Mutates `row` in place: adds `intervention_trace` (list[dict] | None)
    and `strategy_pipeline_snapshot` (dict | None), dropping the raw
    `_json` columns. Returns the same row. No-op when `row` is None.

    Used by prompt-debug routes so the React DecisionAnalyzer can
    discriminate TieredBot decisions (intervention_trace is a non-empty
    list) from LLM decisions (intervention_trace is None) without
    parsing strings client-side.
    """
    if row is None:
        return None
    _parse_json_field(row, 'intervention_trace_json', 'intervention_trace', list)
    _parse_json_field(row, 'strategy_pipeline_snapshot_json', 'strategy_pipeline_snapshot', dict)
    return row
