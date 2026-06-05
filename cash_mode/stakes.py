"""Stake dataclass — one row per session's stake deal.

A **stake** is a deal struck at sit-down and settled at leave-table.
The staker puts up `principal` chips; the borrower plays them; at
session end, total chips are split per the agreed `cut`. If the
borrower busted without recovering the principal, the residual
becomes `carry_amount` (status='carry') — a static debt that sits
until the borrower works it down.

This module holds the in-memory dataclass shape. Persistence lives
in `poker/repositories/stake_repository.py` (schema v98). Settlement
math lives in `cash_mode/stake_settlement.py` (Commit 4).

Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# --- Enumerations as string literals ---
#
# These are typed as `str` rather than `Enum` because the values
# cross the DB boundary as TEXT columns and the audit / lobby paths
# read them as raw strings. An enum would be wrapper churn.

# `staker_kind` values
STAKER_KIND_HOUSE = "house"
STAKER_KIND_PERSONALITY = "personality"
STAKER_KIND_HUMAN = "human"

# `borrower_kind` values
BORROWER_KIND_HUMAN = "human"
BORROWER_KIND_PERSONALITY = "personality"

# `format` values
STAKE_FORMAT_PURE = "pure"
STAKE_FORMAT_MATCH_SHARE = "match_share"
STAKE_FORMAT_HOUSE = "house"

# `status` values
STAKE_STATUS_ACTIVE = "active"
STAKE_STATUS_SETTLED = "settled"
STAKE_STATUS_CARRY = "carry"
STAKE_STATUS_DEFAULTED = "defaulted"


@dataclass(frozen=True)
class Stake:
    """One row of the `stakes` table.

    Frozen so equality is value-based and stake objects can be hashed
    / used as dict keys in test fixtures. Mutations create new instances
    via `dataclasses.replace`.

    Field semantics:
      - `staker_id`: NULL for house stakes; personality_id or owner_id
        otherwise. The repository layer translates None ↔ NULL.
      - `staker_kind`: drives settlement routing (house → ledger;
        personality / human → pure bankroll transfer).
      - `borrower_kind`: 'human' in Phase 1; 'personality' added in
        Phase 4.
      - `format`: 'pure' (staker funds full principal, borrower pays
        origination_fee), 'match_share' (both contribute half, no fee,
        higher cut), 'house' (lender of last resort, forgive on bust).
      - `principal`: chips the staker put up.
      - `match_amount`: chips the borrower put up (match_share only;
        0 otherwise).
      - `origination_fee`: borrower → staker bankroll at sit-down
        (pure stakes only).
      - `cut`: staker's share of net winnings as a fraction [0.0, 1.0].
      - `status`: lifecycle marker. 'active' until leave-table;
        then one of 'settled' (clean), 'carry' (residual debt rolls
        forward), or 'defaulted' (explicit default action — Phase 2).
      - `carry_amount`: residual principal owed when status='carry'.
        Always 0 for other statuses.
      - `stake_tier`: STAKES_LADDER key (`$2`, `$10`, etc.) the stake
        was made at. Used by Phase 2 tier resolution and analytics on
        default rates by stake size.
      - `created_at`: stake-row creation timestamp.
      - `settled_at`: leave-table timestamp; None while active.
      - `forgiveness_last_asked`: timestamp of the most recent
        `/request-forgiveness` ask against this stake. None when
        the borrower has never asked. Phase 3 rate-limits asks at
        one per stake per 24h.
      - `pending_forgiveness_ask`: timestamp set when an AI borrower
        has surfaced a forgiveness request against a human staker
        and is waiting for the player's grant/refuse. None when no
        pending ask (default; cleared on resolution). Only meaningful
        for `staker_kind='human'` carries — AI-to-AI carries auto-
        resolve via `try_ai_forgiveness_ask` directly.
    """

    stake_id: str
    session_id: str
    staker_id: Optional[str]
    staker_kind: str
    borrower_id: str
    borrower_kind: str
    format: str
    principal: int
    match_amount: int
    origination_fee: int
    cut: float
    status: str
    carry_amount: int
    stake_tier: str
    created_at: datetime
    settled_at: Optional[datetime] = None
    forgiveness_last_asked: Optional[datetime] = None
    # v106 — settlement chip-flow capture for the Net Worth history.
    # `staker_payout`: chips returned to the staker at settle time
    # (principal + cut × upside on a clean settle; partial recovery on
    # a bust; 0 on a full bust). NULL on active rows (not yet settled)
    # and on legacy rows that settled pre-v106 (where we couldn't go
    # back and reconstruct the values). `borrower_payout` is the
    # mirror — chips returned to the borrower.
    staker_payout: Optional[int] = None
    borrower_payout: Optional[int] = None
    # v110 — pending forgiveness ask awaiting human-staker decision.
    pending_forgiveness_ask: Optional[datetime] = None
    # v111 — specific lobby table the stake was opened against. NULL on
    # AI-to-AI stakes (no table identity for synthetic ai_session_*
    # rows) and on legacy pre-v111 rows. Populated by sponsor_and_sit
    # for human-takes-sponsor sit-downs so per-table analytics ("which
    # $50 table generates the most carry?") become possible. Settlement
    # remains keyed on session_id, so this column is purely additive.
    table_id: Optional[str] = None
    # v150 — distinguishes HOW a closed stake resolved when the bare
    # `status` isn't specific enough. NULL for ordinary settles/defaults;
    # 'bankruptcy' when the borrower's carries were discharged by the
    # insolvency valve (status stays 'defaulted' so all the default
    # machinery — history inclusion, track-record counts, sponsor-offer
    # penalties — keeps treating it as a default; this is purely the
    # display label so the Net Worth history can read "bankruptcy" vs a
    # deliberate stiff). Additive; existing rows read NULL.
    resolution: Optional[str] = None
