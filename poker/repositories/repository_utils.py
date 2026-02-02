"""Shared utility functions for repository modules."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def parse_json_fields(row_dict: dict, fields: list, context: str = ""):
    """Parse JSON string fields in a row dict, logging failures at debug level."""
    for field in fields:
        if row_dict.get(field):
            try:
                row_dict[field] = json.loads(row_dict[field])
            except json.JSONDecodeError:
                logger.debug(f"Failed to parse JSON for field '{field}'{f' in {context}' if context else ''}")


def build_where_clause(conditions: list) -> str:
    """Build a WHERE clause from a list of conditions."""
    return f"WHERE {' AND '.join(conditions)}" if conditions else ""
