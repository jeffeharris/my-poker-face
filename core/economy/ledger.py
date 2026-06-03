"""Canonical call surface for chip-ledger instrumentation.

Call sites do `from core.economy.ledger import record, bank, player, ai`
rather than reaching into `ChipLedgerRepository` directly. Two reasons:

  1. **Vocabulary stability.** The ledger reason strings are kept in
     `LEDGER_REASONS`; this module rejects writes with unknown reasons
     so typos turn into test failures, not silent drift.
  2. **Swap point.** Central bank v1 (if it ships) will replace the
     write path with one that consults a `reserves` value before
     allowing the creation. Call sites won't change — this module's
     signature does.

`record()` takes the repository explicitly. That keeps the module
side-effect-free and testable; flask routes / handlers pull the repo
from `flask_app.extensions` and pass it through.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from poker.repositories.chip_ledger_repository import (
    CENTRAL_BANK,
    ChipLedgerRepository,
)

logger = logging.getLogger(__name__)


# The full vocabulary. Adding a reason requires editing this set so
# anyone grepping for chip-flow categories sees them in one place.
LEDGER_REASONS = frozenset(
    {
        # Creations: central_bank → X
        'player_seed',  # first-time player entry into cash mode
        'ai_seed',  # first AI bankroll write in a given sandbox
        'ai_regen',  # AI bankroll write where projected > stored
        'house_stake_issue',  # house-archetype stake principal issued to borrower
        'pre_ledger_universe',  # one-shot seed at migration so day-1 drift is 0
        'tourist_injection',  # bank pool → fish bankroll (closed-economy refill)
        'side_hustle_earning',  # bank pool → broke AI bankroll. The side-hustle
        # faucet that replaces passive `ai_regen`: a broke
        # AI goes off-grid to earn, drawing a lump from the
        # recyclable pool (caller clamps to pool depth).
        # See CASH_MODE_SIDE_HUSTLE.md.
        'casino_seat_seed',  # bank pool → fish seat chips at casino spawn
        # (atomic seed event — chips land at the seat,
        # not the bankroll; same pool draw semantics
        # as tourist_injection just routed differently)
        'tournament_overlay',  # bank pool → tournament:<id> escrow: the house
        # contribution that funds an AI-only / flush-bank
        # prize pool. A pool DRAW (depletes reserves — the
        # thermostat's "distribute" lever; see
        # economy_signal.tournament_funding). Counts in drift
        # (it really moves reserves into circulation), which is
        # why the overlay-vs-buy-in distinction is made by
        # REASON here, not by the tournament:<id> counterparty.
        'bank_pool_sim_seed',  # sim-only: central_bank → synthetic donor as
        # the creation half of a paired (creation +
        # bank_pool_deposit) seed flow. Paired form
        # keeps drift at 0 while inflating the pool
        # at sandbox start.
        # Destructions: X → central_bank
        'cap_clamp',  # DEPRECATED — historical entries only. Was emitted
        # when AI winnings would push bankroll above
        # `bankroll_cap`; that cap concept was retired when
        # `starting_bankroll` became a regen target rather
        # than a ceiling. Kept in the vocabulary so the
        # audit can still query historical entries.
        'house_stake_settle',  # leave-time settlement of a house-archetype stake
        'table_rake',  # per-hand pot rake skimmed at award time. Feeds
        # the recyclable bank pool (see
        # BANK_POOL_DEPOSIT_REASONS) — the chips are still
        # removed from circulation, but become drawable by
        # the side hustle / tourist injection rather than
        # evaporating. See CASH_MODE_SIDE_HUSTLE.md.
        'bank_pool_deposit',  # stub vice (and other operator-driven deposits)
        # → bank pool; the recyclable subset of central_bank
        # chips that fund `tourist_injection` /
        # `casino_seat_seed`.
        'vice_spending',  # AI voluntary spend-down (real vice mechanic).
        # Fires from the lobby refresh when a flush AI
        # rolls a vice. Per CASH_MODE_CLOSED_ECONOMY.md
        # this also feeds the bank pool — see
        # BANK_POOL_DEPOSIT_REASONS below.
        'tournament_return',  # tournament:<id> → bank pool: escrow chips that
        # found no real recipient at distribute time (a
        # synthetic-AI finisher's share, or any undistributed
        # overlay) returning to the recyclable pool. Keeps the
        # escrow at exactly 0 and is the v1 counterpart to a
        # real ai:<pid> payout (which lands when real-persona
        # tournament fields ship). Recyclable (a deposit), so
        # the overlay it cancels is restored to reserves.
        'casino_seat_return',  # ai → bank pool: residual seat chips returned
        # when a casino tears down (or a tourist leaves
        # mid-life). Mirror of `casino_seat_seed` —
        # ephemeral-tourist chips were never on a
        # bankroll, so the seat balance returns
        # straight to the pool to preserve drift==0.
        # Annotation (amount=0, audit reconciliation only)
        'forgive_balance',  # borrower left short of principal on a house stake
        'informant_unlock',  # player → bank pool: chips spent buying a dossier
        # section from the informant (the scouting meta-game
        # chip sink). Recyclable (see BANK_POOL_DEPOSIT_REASONS)
        # so scouting fees refill the AI-funding pool. See
        # OPPONENT_DOSSIER_PROGRESSION.md.
        # Transfers (NO central_bank side) — pure movement between two
        # non-bank surfaces. These DO NOT change the size of the
        # universe, so they are invisible to the creation/destruction
        # drift math (`sum_creations/destructions_by_reason` filter on
        # `central_bank`). They exist solely as a human-readable
        # transaction history / statement (Cut 2 of CASH_MODE_STATE_MODEL.md):
        # the audit-trail the silent-forfeiture bug exposed as missing.
        # Written via `record_transfer` (NOT `record`, which rejects
        # bank-less rows by design).
        'player_buy_in',  # player:<id> → seat:<game_id>: chips committed to a
        # cash seat at sit-down. Conservation-neutral (both
        # player_bankrolls and the live human seat stack are
        # already counted by the audit).
        'player_cash_out',  # seat:<game_id> → player:<id>: chips returned to
        # bankroll at leave/cash-out (the take-home).
        'ai_buy_in',  # ai:<pid> → seat:ai:<sandbox>:<pid>: the AI analog of
        # player_buy_in — chips committed to a cash seat at
        # sit-down. The custody-machine transfer that makes an
        # AI's at-table chips a derivable ledger balance.
        # Conservation-neutral (ai_bankroll_state drops, the
        # live AI seat stack rises; both already audit-counted).
        'ai_cash_out',  # seat:ai:<sandbox>:<pid> → ai:<pid>: the AI analog of
        # player_cash_out — the AI's full table stack folded
        # back into bankroll at leave/bust. Net of winnings:
        # the per-hand P&L nets inside the seat balance and
        # settles here, so the table conserves internally
        # between seat accounts (winners' seats go negative,
        # losers' positive; they sum to ~0 across a table).
        'stake_payoff',  # <staker-funded source> → ai:<staker>: a stake/carry
        # payoff routed through `credit_ai_cash_out`'s second
        # (non-seat) use. Source is the borrower (ai:<borrower>)
        # or the funding player (player:<owner>). A single
        # transfer reconciles both the borrower debit and the
        # staker credit; no seat is involved.
        'tournament_buy_in',  # player:<id>/ai:<pid> → tournament:<id>: an
        # entrant's buy-in committed to the tournament escrow
        # at registration. A drift-invisible TRANSFER (the
        # chips were already counted on the bankroll; they are
        # now earmarked at the escrow). Sibling of
        # player_buy_in — the escrow is the tournament analog
        # of the seat. Overlay (bank-funded) is NOT this; it is
        # `tournament_overlay`, a real pool draw.
        'tournament_payout',  # tournament:<id> → player:<id>/ai:<pid>: a prize
        # paid out of the escrow at completion. The TRANSFER
        # mirror of tournament_buy_in. After every payout +
        # rake the escrow nets to 0 (the escrow-balance
        # invariant).
    }
)

# Transfer reasons — entries with NO central_bank side (see the
# "Transfers" note in LEDGER_REASONS). `record()` rejects these; they
# must go through `record_transfer`, and the audit's creation/destruction
# sums ignore them. Kept as a set so a consumer can cleanly exclude
# transfer rows from any bank-oriented view.
TRANSFER_REASONS = frozenset(
    {
        'player_buy_in',
        'player_cash_out',
        'ai_buy_in',
        'ai_cash_out',
        'stake_payoff',
        'tournament_buy_in',
        'tournament_payout',
    }
)

# Pool of reasons that fund tourist injections / casino seat seeds —
# chips destroyed under any of these reasons are considered "recyclable"
# and may be drawn down by `BANK_POOL_DRAW_REASONS`. Closed-economy
# bank-pool depth is `Σ(deposit_reasons) − Σ(draw_reasons)`.
#
# `vice_spending` (real AI vice) and `bank_pool_deposit` (stub vice + sim
# seed) both deposit here, so the closed-economy loop is agnostic to
# which vice implementation is live.
#
# `table_rake` joined this set per CASH_MODE_SIDE_HUSTLE.md: rake used to
# be pure destruction (chips left the universe), but redirecting it into
# the recyclable pool is what funds the side hustle / tourist injection.
# The ledger entry direction is unchanged (winner → central_bank) — only
# its pool-depth classification moved.
BANK_POOL_DEPOSIT_REASONS = frozenset(
    {
        'bank_pool_deposit',
        'vice_spending',
        'casino_seat_return',
        'table_rake',
        'informant_unlock',
        'tournament_return',
    }
)

# Pool draws — creations that pull from the recyclable pool. Adding a
# new draw reason (e.g. a per-hand fish subsidy) just appends to this
# set; depth math automatically subtracts it.
BANK_POOL_DRAW_REASONS = frozenset(
    {
        'tourist_injection',
        'casino_seat_seed',
        'side_hustle_earning',
        'tournament_overlay',
    }
)


# Convenience constructors for source/sink strings. Keeps the format
# (e.g. 'player:<owner_id>') in one place — and the type system catches
# `player(None)` mistakes that the f-string equivalent would let
# through silently.


def bank() -> str:
    """The central bank as a source/sink."""
    return CENTRAL_BANK


def player(owner_id: str) -> str:
    """Format `owner_id` into the canonical `player:<owner_id>` form."""
    if not owner_id:
        raise ValueError("player() requires a non-empty owner_id")
    return f"player:{owner_id}"


def ai(personality_id: str) -> str:
    """Format `personality_id` into the canonical `ai:<personality_id>` form."""
    if not personality_id:
        raise ValueError("ai() requires a non-empty personality_id")
    return f"ai:{personality_id}"


def seat(game_id: str) -> str:
    """Format `game_id` into the canonical `seat:<game_id>` form.

    A `seat:` entity is the chips physically at a player's cash seat for
    one session. It is a *transfer* counterparty only (player_buy_in /
    player_cash_out) — never a creation/destruction side — so it never
    enters the conservation drift math. It exists so the human chip
    statement reads as a balanced pair (bankroll → seat at sit-down,
    seat → bankroll at leave).
    """
    if not game_id:
        raise ValueError("seat() requires a non-empty game_id")
    return f"seat:{game_id}"


def tournament(tournament_id: str) -> str:
    """Format `tournament_id` into the canonical `tournament:<id>` form.

    A `tournament:` entity is the escrow holding one tournament's whole purse:
    buy-ins flow IN (`player/ai → tournament:<id>`, transfers), the bank overlay
    flows IN (`bank → tournament:<id>`, a creation/draw), and payouts + rake flow
    OUT (`tournament:<id> → player/ai`, transfers; `tournament:<id> → bank`, a
    rake destruction). Its `balance_of` IS the at-escrow amount — the sibling of
    `seat(game_id)`. The escrow-balance invariant: after escrow-in it holds
    `Σ buy_ins + Σ overlays`; after distribute it nets to 0.
    """
    if not tournament_id:
        raise ValueError("tournament() requires a non-empty tournament_id")
    return f"tournament:{tournament_id}"


def ai_seat(sandbox_id: str, personality_id: str) -> str:
    """Format the canonical AI seat-account string.

    The AI analog of `seat(game_id)`. Humans key their seat account by
    `game_id` (one live game per human session); world/sim AIs churn
    `cash_tables` with no per-AI live game_id, so the AI seat account is
    keyed by `(sandbox_id, personality_id)` instead —
    `seat:ai:<sandbox_id>:<personality_id>`. Because an AI is seated at most
    one cash seat per sandbox (single-presence; cash mode is single-player),
    this balance is exactly one AI's at-table chips, not a pooled table.

    The buy-in/cash-out pair for one session uses this same account, so the
    AI's at-table chips become a derivable ledger balance — the custody
    substrate the seats-as-view phase (D1) and derived bankroll (D2) build on.
    """
    if not sandbox_id:
        raise ValueError("ai_seat() requires a non-empty sandbox_id")
    if not personality_id:
        raise ValueError("ai_seat() requires a non-empty personality_id")
    return f"seat:ai:{sandbox_id}:{personality_id}"


# --- D2: ledger-derived bankroll (the int becomes a cache of these) ---------
#
# `balance_of` (on the repo) is the substrate; these name the two entity
# scopes so call sites read intention-first. The storage asymmetry is
# resolved here, in ONE place: AI bankroll is per-sandbox, player bankroll
# is global (summed across sandboxes). See CASH_MODE_CHIP_CUSTODY_SCOPE.md.


def derive_ai_balance(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    sandbox_id: str,
) -> Optional[int]:
    """Ledger-derived AI bankroll for one (pid, sandbox), or None if no repo.

    The chips an AI holds in ONE save-file: `balance_of(ai(pid))` scoped to the
    sandbox. After chip-custody Phase 1 this equals the stored
    `ai_bankroll_state.chips` (at-table chips live in the seat account, not
    here) — so it is the authoritative value the int caches.
    """
    if repo is None:
        return None
    return repo.balance_of(ai(personality_id), sandbox_id=sandbox_id)


def derive_player_balance(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
) -> Optional[int]:
    """Ledger-derived player bankroll, summed ACROSS sandboxes, or None.

    `player_bankroll_state` is GLOBAL (no sandbox_id) — a human's bankroll
    roams with them across save-files (D6: one human per sandbox). So the
    derivation sums `player:<id>` rows over every sandbox (`sandbox_id=None`).
    """
    if repo is None:
        return None
    return repo.balance_of(player(owner_id), sandbox_id=None)


def record(
    repo: ChipLedgerRepository,
    *,
    source: str,
    sink: str,
    amount: int,
    reason: str,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """Write one ledger entry. Returns the row id, or None on failure.

    Validation rules:
      - `reason` must be in `LEDGER_REASONS` (unknown reasons would
        leak into the audit endpoint's `by_reason` bucket and confuse
        the categorisation).
      - `amount` must be a non-negative int. Negative amounts are
        almost always a sign-error at the call site — flip the
        source/sink direction instead.
      - The entry must touch the central bank (source OR sink ==
        `central_bank`). Pure transfers between non-bank entities
        don't change the size of the universe and are out of scope
        for v0.

    `sandbox_id` is the Phase 2.5 v103 per-sandbox audit scope. When
    omitted the row writes `sandbox_id=NULL` (legacy / pre-v103
    bucket). Production callers should always pass it so per-sandbox
    audits can filter; one-shot migration helpers
    (`_migrate_v94_seed_pre_ledger_universe`) leave it NULL on purpose.

    Failures log a warning and return None — we never want a ledger
    bug to take down a chip-moving code path. The audit-side drift
    will flag the missed entry.
    """
    if reason not in LEDGER_REASONS:
        logger.warning(
            "chip ledger: rejecting record() with unknown reason=%r "
            "(amount=%s source=%s sink=%s); add to LEDGER_REASONS first",
            reason,
            amount,
            source,
            sink,
        )
        return None

    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        logger.warning(
            "chip ledger: rejecting record() with non-int amount=%r (reason=%s)",
            amount,
            reason,
        )
        return None

    if amount_int < 0:
        logger.warning(
            "chip ledger: rejecting record() with negative amount=%d "
            "(reason=%s source=%s sink=%s); flip source/sink instead",
            amount_int,
            reason,
            source,
            sink,
        )
        return None

    if source != CENTRAL_BANK and sink != CENTRAL_BANK:
        logger.warning(
            "chip ledger: rejecting record() with no central_bank side "
            "(source=%s sink=%s reason=%s); v0 tracks only creations/destructions",
            source,
            sink,
            reason,
        )
        return None

    try:
        return repo.record(
            source=source,
            sink=sink,
            amount=amount_int,
            reason=reason,
            context=context,
            sandbox_id=sandbox_id,
        )
    except Exception as e:
        # ERROR, not warning (PRH-11): validation has already passed, so this
        # is a real DB-write failure on a row a chip-moving caller expected to
        # land. Callers write the bankroll first, then this best-effort ledger
        # row — so a failure here means the chip move likely committed without
        # a ledger entry = conservation drift. Surface it loudly for alerting;
        # the audit's `drift` is the reconciliation backstop.
        logger.error(
            "[LEDGER] DRIFT RISK: record() DB write failed "
            "(reason=%s amount=%d source=%s sink=%s): %s",
            reason,
            amount_int,
            source,
            sink,
            e,
        )
        return None


def record_transfer(
    repo: ChipLedgerRepository,
    *,
    source: str,
    sink: str,
    amount: int,
    reason: str,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """Write one TRANSFER ledger entry — a move between two non-bank
    surfaces that does NOT change the size of the universe.

    Distinct from `record()`, which rejects rows with no `central_bank`
    side (its job is creations/destructions only). Transfers are the
    human transaction-history rows (`player_buy_in` / `player_cash_out`):
    conservation-neutral (both surfaces are already counted by the
    audit), so they are deliberately invisible to the drift math. This
    helper exists so that invariant is explicit at the one write point
    rather than smuggled through `record()`.

    Validation: `reason` must be in `TRANSFER_REASONS`; `amount` a
    non-negative int; NEITHER side may be the central bank (a transfer
    that touched the bank would be a real creation/destruction and
    belongs in `record()`). Failures log and return None — never take
    down a chip-moving path for a best-effort history row.
    """
    if reason not in TRANSFER_REASONS:
        logger.warning(
            "chip ledger: rejecting record_transfer() with non-transfer "
            "reason=%r (use record() for creations/destructions)",
            reason,
        )
        return None
    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        logger.warning(
            "chip ledger: rejecting record_transfer() with non-int amount=%r " "(reason=%s)",
            amount,
            reason,
        )
        return None
    if amount_int < 0:
        logger.warning(
            "chip ledger: rejecting record_transfer() with negative amount=%d "
            "(reason=%s); flip source/sink instead",
            amount_int,
            reason,
        )
        return None
    if source == CENTRAL_BANK or sink == CENTRAL_BANK:
        logger.warning(
            "chip ledger: rejecting record_transfer() that touches central_bank "
            "(source=%s sink=%s reason=%s); use record() for bank-side flows",
            source,
            sink,
            reason,
        )
        return None
    try:
        return repo.record(
            source=source,
            sink=sink,
            amount=amount_int,
            reason=reason,
            context=context,
            sandbox_id=sandbox_id,
        )
    except Exception as e:
        # Best-effort: a missing history row is a forensics gap, not a
        # conservation problem (the move is bank-neutral). Log, don't raise.
        logger.error(
            "[LEDGER] transfer record failed (reason=%s amount=%d source=%s " "sink=%s): %s",
            reason,
            amount_int,
            source,
            sink,
            e,
        )
        return None


def record_player_buy_in(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    game_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """player:<owner_id> → seat:<game_id> — chips committed at sit-down.

    The human-statement counterpart of the bankroll debit. Conservation-
    neutral (player_bankrolls drops, the live human seat stack rises;
    both already counted by the audit). No-op when `repo` is None or
    `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record_transfer(
        repo,
        source=player(owner_id),
        sink=seat(game_id),
        amount=amount,
        reason='player_buy_in',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_player_cash_out(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    game_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """seat:<game_id> → player:<owner_id> — take-home chips at leave.

    The human-statement counterpart of the bankroll credit on leave /
    cash-out. Conservation-neutral. No-op when `repo` is None or
    `amount <= 0` (a bust-out leave with 0 take-home writes no row —
    the absence of a cash_out paired with a buy_in IS the record that
    the seat busted).
    """
    if repo is None or amount <= 0:
        return None
    return record_transfer(
        repo,
        source=seat(game_id),
        sink=player(owner_id),
        amount=amount,
        reason='player_cash_out',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_ai_buy_in(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    sandbox_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """ai:<pid> → seat:ai:<sandbox>:<pid> — chips committed at AI sit-down.

    The AI counterpart of `record_player_buy_in` (the chip-custody machine's
    parity wiring). Conservation-neutral (ai_bankroll_state drops, the live
    AI seat stack rises; both already counted by the audit). Makes the AI's
    at-table chips a derivable ledger balance. No-op when `repo` is None or
    `amount <= 0`. `sandbox_id` is required (it keys the seat account).
    """
    if repo is None or amount <= 0:
        return None
    return record_transfer(
        repo,
        source=ai(personality_id),
        sink=ai_seat(sandbox_id, personality_id),
        amount=amount,
        reason='ai_buy_in',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_ai_cash_out(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    sandbox_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """seat:ai:<sandbox>:<pid> → ai:<pid> — the AI's table stack at leave/bust.

    The AI counterpart of `record_player_cash_out`. `amount` is the AI's full
    table stack folded back into bankroll (net of winnings — the per-hand P&L
    nets inside the seat balance and settles here). Conservation-neutral. No-op
    when `repo` is None or `amount <= 0` (a bust with 0 take-home writes no row
    — the absent cash_out paired with a buy_in IS the bust record, same
    convention as humans). `sandbox_id` is required (it keys the seat account).
    """
    if repo is None or amount <= 0:
        return None
    return record_transfer(
        repo,
        source=ai_seat(sandbox_id, personality_id),
        sink=ai(personality_id),
        amount=amount,
        reason='ai_cash_out',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_stake_payoff(
    repo: Optional[ChipLedgerRepository],
    *,
    source: str,
    sink: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """<borrower/funding source> → <staker sink> — a stake/carry payoff (non-seat).

    `credit_ai_cash_out` is overloaded: besides real seat cash-outs it also
    credits a STAKER's bankroll when a borrower pays off a carry. That credit
    has no seat behind it — the chips come from the borrower (ai:<borrower>) or
    a funding player (player:<owner>), and land on the staker (ai:<staker> or
    player:<staker>). A single `source → sink` transfer reconciles both the
    debit and the credit so neither side drifts from its stored bankroll.
    Both `source` and `sink` are canonical entity strings the caller builds
    (use `ai()` / `player()`). No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record_transfer(
        repo,
        source=source,
        sink=sink,
        amount=amount,
        reason='stake_payoff',
        context=context,
        sandbox_id=sandbox_id,
    )


# --- Tournament escrow helpers ---
#
# The tournament economy is the seat/buy-in pattern with one net-new account,
# `tournament(id)`. Buy-in and payout are TRANSFERS (drift-invisible, earmarked
# at the escrow); the overlay is a bank-pool DRAW (real reserve movement). Rake
# reuses `record_table_rake` (source = the escrow) — already a deposit reason.


def record_tournament_buy_in(
    repo: Optional[ChipLedgerRepository],
    *,
    source: str,
    tournament_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """<entrant> → tournament:<id> — an entrant's buy-in committed to escrow.

    `source` is the canonical entity string the buy-in is debited from — build
    it with `player(owner_id)` (human) or `ai(personality_id)` (AI tourist).
    A drift-invisible transfer: the chips were already counted on the bankroll
    and are now earmarked at the escrow. No-op when `repo` is None or
    `amount <= 0` (a freeroll seat writes no buy-in row). Stamps the
    `tournament_id` into context so the post-event audit can scope by tournament.
    """
    if repo is None or amount <= 0:
        return None
    ctx = dict(context or {})
    ctx.setdefault('tournament_id', tournament_id)
    return record_transfer(
        repo,
        source=source,
        sink=tournament(tournament_id),
        amount=amount,
        reason='tournament_buy_in',
        context=ctx,
        sandbox_id=sandbox_id,
    )


def record_tournament_payout(
    repo: Optional[ChipLedgerRepository],
    *,
    sink: str,
    tournament_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """tournament:<id> → <finisher> — a prize paid out of the escrow.

    `sink` is the canonical entity string the prize lands on — `player(owner_id)`
    or `ai(personality_id)`. The transfer mirror of `record_tournament_buy_in`;
    after every payout + rake the escrow nets to 0. No-op when `repo` is None or
    `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    ctx = dict(context or {})
    ctx.setdefault('tournament_id', tournament_id)
    return record_transfer(
        repo,
        source=tournament(tournament_id),
        sink=sink,
        amount=amount,
        reason='tournament_payout',
        context=ctx,
        sandbox_id=sandbox_id,
    )


def record_tournament_overlay(
    repo: Optional[ChipLedgerRepository],
    *,
    tournament_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """central_bank → tournament:<id> — the house overlay funding the prize pool.

    A pool DRAW (`tournament_overlay` is in `BANK_POOL_DRAW_REASONS`): it depletes
    reserves and counts in drift, which is precisely how it differs from a
    drift-invisible buy-in even though both land at the same escrow. The caller
    (the funding policy) decides the amount from the live `EconomyState`; this
    helper just writes the row. No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    ctx = dict(context or {})
    ctx.setdefault('tournament_id', tournament_id)
    return record(
        repo,
        source=bank(),
        sink=tournament(tournament_id),
        amount=int(amount),
        reason='tournament_overlay',
        context=ctx,
        sandbox_id=sandbox_id,
    )


def record_tournament_return(
    repo: Optional[ChipLedgerRepository],
    *,
    tournament_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """tournament:<id> → central_bank — escrow chips with no real recipient.

    At distribute, a synthetic-AI finisher's share (and any undistributed
    overlay) is swept back to the recyclable bank pool so the escrow nets to 0.
    `tournament_return` is a `BANK_POOL_DEPOSIT_REASON`, so the overlay draw it
    cancels is restored to reserves. The swap point when real-persona fields
    ship: credit `ai:<pid>` via `record_tournament_payout` instead of sweeping.
    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    ctx = dict(context or {})
    ctx.setdefault('tournament_id', tournament_id)
    return record(
        repo,
        source=tournament(tournament_id),
        sink=bank(),
        amount=int(amount),
        reason='tournament_return',
        context=ctx,
        sandbox_id=sandbox_id,
    )


# --- Reason-specific helpers ---
#
# Thin sugar over `record()`. They exist so call sites read as
# `ledger.record_ai_regen(...)` rather than re-stating the reason
# string and source/sink direction. If any of these grow real logic
# (e.g. central bank v1 reserves check), it lives here once.


def record_player_seed(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """First-time entry: central_bank → player. Accepts repo=None (no-op).

    `sandbox_id` is the Phase 2.5 per-sandbox audit scope; omit to
    write NULL (pre-v103 legacy bucket).
    """
    if repo is None:
        return None
    return record(
        repo,
        source=bank(),
        sink=player(owner_id),
        amount=amount,
        reason='player_seed',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_informant_unlock(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """Dossier informant purchase: player → bank pool (a recyclable sink).

    The scouting meta-game's chip sink — the player pays to reveal a dossier
    section. Accepts repo=None (no-op). Mirrors the vice-spending direction
    (destruction into the recyclable pool), just sourced from the player.
    """
    if repo is None:
        return None
    return record(
        repo,
        source=player(owner_id),
        sink=bank(),
        amount=amount,
        reason='informant_unlock',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_ai_seed(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """First AI bankroll write in a sandbox: central_bank → ai.

    Closes the chip-ledger gap from `CASH_MODE_ECONOMY.md` Known
    Issues §2. Per-sandbox scoping (v102) makes this fire on every
    new sandbox's first write of each personality.

    No-op when `repo` is None or `amount <= 0`. Called from
    `BankrollRepository.save_ai_bankroll` when the existence check
    fires (first write per `(personality_id, sandbox_id)`).
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=int(amount),
        reason='ai_seed',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_ai_regen(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    stored_chips: int,
    projected_chips: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """central_bank → ai for the positive delta between stored and projected.

    No-op when `repo` is None or `projected_chips <= stored_chips`. Use at
    every `save_ai_bankroll` call site immediately after computing
    `projected_chips`.
    """
    if repo is None:
        return None
    delta = int(projected_chips) - int(stored_chips)
    if delta <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=delta,
        reason='ai_regen',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_house_stake_issue(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """House-archetype stake principal: central_bank → borrower.

    Personality and human stake principals are pure transfers between
    non-bank entities (staker's bankroll → borrower's table stack) and
    aren't routed through here. Only the house archetype path creates
    chips out of the central bank.
    """
    if repo is None:
        return None
    return record(
        repo,
        source=bank(),
        sink=player(owner_id),
        amount=amount,
        reason='house_stake_issue',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_table_rake(
    repo: Optional[ChipLedgerRepository],
    *,
    source: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """winner → central_bank for the per-hand rake skim.

    `source` is the canonical entity string the pot was drawn from —
    typically `ai(personality_id)` for sim hands or `player(owner_id)`
    for player-table hands. Constructed by the caller because rake
    targets a specific winner, which the caller already knows.

    No-op when `repo` is None or `amount <= 0` so call sites don't have
    to guard before invoking.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=source,
        sink=bank(),
        amount=amount,
        reason='table_rake',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_house_stake_settle(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """borrower → central_bank for a house-archetype stake settle.

    The staker share (principal recovered + cut on upside) goes back
    to the bank on leave-time settlement. Personality and human stakes
    don't route here — the staker share credits the staker's persistent
    bankroll instead, a pure non-bank transfer.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=player(owner_id),
        sink=bank(),
        amount=amount,
        reason='house_stake_settle',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_forgive_balance(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    forgiven_principal: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """Annotation row (amount=0) — house stake principal not recovered.

    Fired when the borrower leaves a house-stake session short of the
    principal. The unrecovered principal already exists in the universe
    (it flowed into other AIs' table stacks during play and gets caught
    at credit_ai_cash_out). This annotation only exists so the audit
    endpoint can reconcile: `sum(house_stake_issue) -
    sum(house_stake_settle) - sum(forgive_balance.context.forgiven_principal)`
    equals outstanding house-stake principal.

    Always source=player, sink=bank to keep the central-bank-side
    rule simple. Amount is 0 by construction.

    Skips the write when `forgiven_principal <= 0` — the annotation
    is meaningful only when chips were actually forgiven. Without
    this guard, every successful stake settlement would generate a
    noise-row with amount=0 and forgiven_principal=0 that adds
    audit clutter for no signal.
    """
    if repo is None or forgiven_principal <= 0:
        return None
    ctx = dict(context or {})
    ctx['forgiven_principal'] = int(forgiven_principal)
    return record(
        repo,
        source=player(owner_id),
        sink=bank(),
        amount=0,
        reason='forgive_balance',
        context=ctx,
        sandbox_id=sandbox_id,
    )


def record_bank_pool_deposit(
    repo: Optional[ChipLedgerRepository],
    *,
    source: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """source → central_bank for chips deposited into the closed-economy pool.

    `source` is the canonical entity string the chips came from —
    `ai(personality_id)` for stub vice (sim testbed) and
    `player(owner_id)` for future player vice. The deposit lands in
    the recyclable subset of central_bank reserves that funds
    `tourist_injection` / `casino_seat_seed`. Bank pool depth (per
    sandbox) is `Σ(BANK_POOL_DEPOSIT_REASONS) − Σ(BANK_POOL_DRAW_REASONS)`.

    Real AI vice writes via `record_vice_spending`; both feed the
    same pool (both reasons are in `BANK_POOL_DEPOSIT_REASONS`).

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=source,
        sink=bank(),
        amount=int(amount),
        reason='bank_pool_deposit',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_vice_spending(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """ai → central_bank for a vice spend (real AI vice mechanic).

    Fired by `resolve_ai_vice_spending` when a flush AI rolls a vice.
    The chips move from the AI's bankroll to the central bank as part
    of the standard destruction pattern; the AI then sits off-grid for
    the vice duration before returning. No-op when `amount <= 0`.

    Per `CASH_MODE_CLOSED_ECONOMY.md` the destination is the recyclable
    bank pool (not pure destruction) — `vice_spending` is in
    `BANK_POOL_DEPOSIT_REASONS` so the pool depth accounting picks
    these up the same way it picks up `bank_pool_deposit`.

    A single-personality destruction (ai → central_bank).
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=ai(personality_id),
        sink=bank(),
        amount=int(amount),
        reason='vice_spending',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_tourist_injection(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """central_bank → ai for a fish bankroll refill from the bank pool.

    Caller is responsible for verifying that the bank pool has enough
    reserves before drawing — `record_tourist_injection` itself just
    writes the ledger row (the pool is virtual; depth is computed,
    not gated by a row count).

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=int(amount),
        reason='tourist_injection',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_side_hustle_earning(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """central_bank → ai for a side-hustle payout drawn from the bank pool.

    The faucet that replaces passive `ai_regen` (see
    CASH_MODE_SIDE_HUSTLE.md): a broke AI goes off-grid to earn and
    returns with a lump credited to its bankroll. `side_hustle_earning`
    is in `BANK_POOL_DRAW_REASONS`, so it draws down pool depth the same
    way `tourist_injection` does.

    Caller is responsible for clamping `amount` to available pool
    reserves before drawing — this helper just writes the row (the pool
    is virtual; depth is computed, not gated by a row count). Mirror of
    `record_tourist_injection`.

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=int(amount),
        reason='side_hustle_earning',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_bank_pool_sim_seed_pair(
    repo: Optional[ChipLedgerRepository],
    *,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """Sim-only: inflate the bank pool by `amount` without touching real holders.

    Writes a paired creation + destruction so the audit's `drift == 0`
    invariant survives. Both rows reference a synthetic donor entity
    (`ai:bank_pool_sim_donor`) that has no bankroll row, so neither
    `actual_outstanding` changes.

    Result: bank pool depth gains `amount` chips. ledger_outstanding
    unchanged (creation cancels destruction). drift unchanged.

    Returns the entry id of the deposit (the second row), or None on
    skip. Intended for `SimConfig.initial_bank_pool_seed` and tests
    that want a pre-loaded pool.
    """
    if repo is None or amount <= 0:
        return None
    donor = ai('bank_pool_sim_donor')
    record(
        repo,
        source=bank(),
        sink=donor,
        amount=int(amount),
        reason='bank_pool_sim_seed',
        context=context,
        sandbox_id=sandbox_id,
    )
    return record(
        repo,
        source=donor,
        sink=bank(),
        amount=int(amount),
        reason='bank_pool_deposit',
        context=dict(context or {}, site='bank_pool_sim_seed'),
        sandbox_id=sandbox_id,
    )


def record_casino_seat_seed(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """central_bank → ai for a fish seat buy-in at casino spawn.

    The casino-provisioning resolver pays out the buy-in for each fish
    seat directly from the bank pool. The chips land in the AI entity's
    accounting (`ai:<personality_id>`); the caller is responsible for
    physically placing them in the seat (vs the bankroll) — this row
    only ledgers the chip creation.

    Same pool-draw semantics as `tourist_injection`; separate reason
    so the audit / trajectory can distinguish 'casino spawned' from
    'fish bankroll topped up.'

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=int(amount),
        reason='casino_seat_seed',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_casino_seat_return(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """ai → central_bank for residual seat chips returned to the pool.

    Mirror of `record_casino_seat_seed`. Fires at casino teardown for any
    seat with chips > 0, and at any other point where a tourist leaves
    with residual chips on the seat. Ephemeral tourists have no bankroll,
    so chips that were `casino_seat_seed`'d to the seat must return
    directly to the bank pool — never to a bankroll — to keep the
    conservation invariant (`drift == 0`).

    `casino_seat_return` is a `BANK_POOL_DEPOSIT_REASON`, so audit math
    correctly absorbs the chips back into pool depth.

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=ai(personality_id),
        sink=bank(),
        amount=int(amount),
        reason='casino_seat_return',
        context=context,
        sandbox_id=sandbox_id,
    )
