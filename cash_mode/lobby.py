"""Lobby seeding and boot-time cleanup.

Two top-level entry points called from app startup:

  - `ensure_lobby_seeded(cash_table_repo, personality_repo,
      bankroll_repo, now, owner_id_for_eligibility)` — idempotent.
    Creates 5 lobby tables (one per stake) if missing, fills each
    with 4 baseline AI personalities, leaves 2 seats `"open"`.

  - `kill_all_cash_sessions(game_state_service, game_repo)` —
    one-shot boot cleanup. Deletes every in-flight cash game from
    memory and every persisted `cash-*` row. Subsumes the older
    `cleanup_orphan_cash_games`.

Both are pure-ish: they take repository instances as parameters
rather than importing from `flask_app.extensions`, so the test
harness can pass tempdb-backed repos.

Spec: `docs/plans/CASH_MODE_LOBBY_HANDOFF.md` §"Lobby maintenance" (a)
and §"Locked decisions" (3 — kill_all_cash_sessions).
"""

from __future__ import annotations

import itertools
import logging
import random
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from cash_mode.attractiveness import (
    CASINO_VENUE_APPEAL,
    DEFAULT_SEEK_RATE,
    FillableTable,
    SeatSeeker,
    assign_seats_greedy,
    seeker_buy_in,
)
from cash_mode.bankroll import (
    AIBankrollState,
    project_bankroll,
)
from cash_mode.full_sim import (
    DEFAULT_HAND_SIM_PROB,
    HandSimResult,
    hand_burst_count,
    play_one_hand,
)
from cash_mode.movement import (
    DEFAULT_LIVE_FILL_PROB,
    RESEAT_RECOVERY_FLOOR,
    RosterRefreshResult,
    is_in_cooldown,
    is_in_vice_cooldown,
    project_idle_energy,
    record_vice_cooldown,
    refresh_table_roster,
    reseat_readiness,
)
from cash_mode.seat_registry import SeatOccupancyRegistry
from cash_mode.staker_history import StakerHistoryStats
from cash_mode.stakes import BORROWER_KIND_PERSONALITY
from cash_mode.stakes_ladder import (
    STAKES_ORDER,
    table_buy_in_window,
)
from cash_mode.staking_tier import TIER_HOUSE_ONLY, resolve_tier
from cash_mode.tables import (
    BASELINE_AI_SEATS,
    TABLE_SEAT_COUNT,
    CashTableState,
    ai_slot,
    open_slot,
)

# Presence-machine dual-write shadow (cutover Phase 1). These mirror each
# authoritative `save_table` seat write into the dormant `entity_presence`
# table via `presence_shadow.shadow_transition`, which is a guarded no-op
# unless `economy_flags.PRESENCE_SHADOW_WRITE_ENABLED` is on and is wrapped
# in try/except so it can never break the real seat write it shadows. See
# `docs/plans/CASH_MODE_PRESENCE_MIGRATION.md` §Sequencing step 1.
from cash_mode import presence_shadow
from cash_mode.presence import PresenceEvent, ai_entity_id, player_entity_id

logger = logging.getLogger(__name__)


def _shadow_seat_state(table: CashTableState) -> Dict[str, Tuple[str, int]]:
    """Map of `entity_id -> (table_id, seat_index)` for the *occupied* seats
    of `table`, in the Presence ledger-entity convention
    (`player:<owner_id>` / `ai:<personality_id>`).

    The real lobby seat writers persist a whole `CashTableState` rather than
    moving one entity at a time, so the dual-write shadow derives "who is
    where" from the table that was just saved. Open / reserved seats are
    skipped (only `kind in {ai, human}` produce a Presence row).
    """
    out: Dict[str, Tuple[str, int]] = {}
    for idx, slot in enumerate(table.seats):
        kind = slot.get("kind")
        if kind == "ai":
            pid = slot.get("personality_id")
            if pid:
                out[ai_entity_id(pid)] = (table.table_id, idx)
        elif kind == "human":
            # `human_slot` (cash_mode/tables.py) stores the player owner_id in
            # `personality_id` (so the routing layer can treat human + AI seats
            # uniformly when checking occupancy). Check it LAST so the explicit
            # owner_id/player_id/user_id keys still win if a slot ever carries
            # them — but without `personality_id` the human is silently dropped
            # and never gets a Presence row (the merged Phase-1 reader's bug).
            owner = (
                slot.get("owner_id")
                or slot.get("player_id")
                or slot.get("user_id")
                or slot.get("personality_id")
            )
            if owner:
                out[player_entity_id(owner)] = (table.table_id, idx)
    return out


def _shadow_repo():
    """Resolve the `entity_presence` repository the same way
    `presence_shadow.shadow_transition` does, so the dual-write reconcile can
    *read* current presence to emit minimal, legal transitions. Returns None
    (silently) when shadow writes are off or the repo isn't wired (sim / cold
    boot) — the reconcile then degrades to no-ops, exactly like the helper."""
    if not presence_shadow.is_enabled():
        return None
    try:
        from flask_app import extensions

        return getattr(extensions, "entity_presence_repo", None)
    except Exception:  # noqa: BLE001 — never let shadow plumbing break the real path
        return None


def _shadow_reconcile_table(
    table: CashTableState,
    sandbox_id: Optional[str],
    *,
    repo=None,
) -> None:
    """Dual-write SHADOW: make `entity_presence` agree with the seat map of a
    table that was just authoritatively saved.

    Because the lobby persists a whole `CashTableState` (not per-entity
    seat/vacate ops), we derive the Presence transitions by diffing the saved
    seat map against current shadow state:

      - occupant not currently SEATED here    -> `SIT` (legal from
        OFFLINE/IDLE/POOL — a fresh seed, an idle re-seat, or a pool fish);
      - occupant currently SEATED *elsewhere* -> `LEAVE` then `SIT` (a move);
      - occupant already SEATED at this exact seat -> nothing (avoids the
        illegal `SEATED --sit--> SEATED` self-edge, which would otherwise spam
        the divergence log every refresh tick).

    Vacated seats ARE turned into `LEAVE`s here (§C dedup decision): an entity
    that left this table to the idle pool / off-grid / a bust appears only as
    its absence from the new seat map, so step (1) below LEAVE-clears any stale
    `SEATED` row this table still holds in the shadow. Without that, the stale
    row keeps occupying the seat in the partial-unique index and the next
    rightful `SIT` collides and is swallowed, stranding that entity unseated.
    A cross-table *move* is still handled per-entity in step (2) (LEAVE-then-SIT
    against the source table). The destination of a bare idle departure is left
    to the machine's IDLE state; the idle-pool repo is deliberately NOT also
    shadow-wired (that would double-drive — migration inventory §C).

    Flag-gated + best-effort throughout (`_shadow_repo` returns None when the
    switch is off; each transition goes through `presence_shadow`'s
    try/except). `sandbox_id` must be the real sandbox — never the
    `entity_presence` `'default'` fallback bucket (migration doc gotcha).
    """
    if sandbox_id is None:
        return
    # Under the AUTHORITY flip, `save_table` drives presence authoritatively
    # inside its own transaction — this call-site reconcile (a separate
    # connection, AFTER the commit) would be redundant and, in a TOCTOU window,
    # could emit a spurious LEAVE against an entity a later save just seated.
    # So skip entirely once authority is on; the chokepoint is the sole seat
    # writer. (Off-grid mirroring still runs via presence_shadow.)
    from cash_mode import economy_flags
    if getattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", False):
        return
    if repo is None:
        repo = _shadow_repo()
    if repo is None:
        return

    desired = _shadow_seat_state(table)  # entity_id -> (table_id, seat_index)

    # (1) Clear STALE occupants of this table first. The lobby persists a whole
    # `CashTableState`, so a seat an entity vacated (to the idle pool / off-grid
    # / a bust) shows up only as that entity's *absence* from the new seat map —
    # never as an event. If we don't emit its `LEAVE`, the shadow keeps a stale
    # `SEATED` row holding that seat in the partial-unique index, and the next
    # entity that legitimately takes the seat collides (`IntegrityError`, which
    # `shadow_transition` swallows) and is stranded unseated. This is the §C
    # dedup decision (CASH_MODE_PRESENCE_MIGRATION.md): the seat->IDLE `LEAVE` is
    # emitted HERE, by the reconcile that already sees the seat go empty — not at
    # the idle-pool repo layer. So: LEAVE everyone the shadow currently has
    # SEATED at THIS table who is not still in the new map at the same seat.
    try:
        seated_here = [
            s for s in repo.list_for_sandbox(sandbox_id)
            if s.is_seated and s.table_id == table.table_id
        ]
    except Exception:  # noqa: BLE001 — read failure must not break the real path
        seated_here = []
    for s in seated_here:
        if desired.get(s.entity_id) == (s.table_id, s.seat_index):
            continue  # still correctly seated here — leave it be
        presence_shadow.shadow_transition(
            entity_id=s.entity_id,
            sandbox_id=sandbox_id,
            event=PresenceEvent.LEAVE,
            repo=repo,
        )

    # (2) Seat the desired occupants. A `SIT` is legal from OFFLINE/IDLE/POOL;
    # an entity SEATED *elsewhere* (a cross-table move) is LEAVE-then-SIT'd here
    # (step 1 only clears stale rows on THIS table, not the source table of a
    # move — and SIT-from-SEATED is illegal by design).
    for entity_id, (table_id, seat_index) in desired.items():
        try:
            current = repo.load(entity_id, sandbox_id)
        except Exception:  # noqa: BLE001 — read failure must not break the real path
            current = None

        if current is not None and current.is_seated:
            if current.table_id == table_id and current.seat_index == seat_index:
                continue  # already correct in the shadow — no-op
            # Seated elsewhere (or a different seat): model the move as
            # LEAVE then SIT so the machine's one-seat-at-a-time invariant
            # holds (SIT-from-SEATED is illegal by design).
            presence_shadow.shadow_transition(
                entity_id=entity_id,
                sandbox_id=sandbox_id,
                event=PresenceEvent.LEAVE,
                repo=repo,
            )

        presence_shadow.shadow_transition(
            entity_id=entity_id,
            sandbox_id=sandbox_id,
            event=PresenceEvent.SIT,
            table_id=table_id,
            seat_index=seat_index,
            repo=repo,
        )


def _next_occupied_seat(
    seats: List[Dict[str, Any]],
    start_after: int,
) -> Optional[int]:
    """Find the next non-`open` seat clockwise from `start_after` (exclusive).

    Returns `None` when no seat is occupied. `start_after = -1` finds the
    first occupied seat starting at index 0.
    """
    n = len(seats)
    for offset in range(1, n + 1):
        idx = (start_after + offset) % n
        if seats[idx].get("kind") != "open":
            return idx
    return None


def get_dealer_index(table: CashTableState) -> Optional[int]:
    """Current dealer seat index for `table`, or `None` if all seats open.

    Reads `table.dealer_idx` (schema v96+) and self-heals when that
    points to a now-open seat (an AI left between refreshes — the
    button rolls forward to the next occupied seat). The in-memory
    mutation is a soft-correction for the read; the persistent value
    isn't rewritten here, so the heal stays read-only until the next
    refresh tick reseats the button via the engine path.
    """
    seats = table.seats
    idx = table.dealer_idx
    if 0 <= idx < len(seats) and seats[idx].get("kind") != "open":
        return idx
    return _next_occupied_seat(seats, start_after=-1)


def _table_id_for_stake(stake_label: str, suffix: str = "001") -> str:
    """Return the stable table_id for a lobby table at this stake.

    `cash-table-2-001` style — the dollar sign in stake_label isn't
    URL-safe, so we slugify to the bare numeric. `suffix` defaults to
    "001" (the canonical first table per stake) for back-compat with
    pre-v111 callers; multi-table seeding passes the suffix from
    `lobby_config.LOBBY_TABLES`.
    """
    if stake_label.startswith("$"):
        slug = stake_label[1:]
    else:
        slug = stake_label
    return f"cash-table-{slug}-{suffix}"


def ensure_ai_bankrolls_seeded(
    *,
    personality_repo,
    bankroll_repo,
    sandbox_id: str,
    now: Optional[datetime] = None,
    user_id: Optional[str] = None,
    chip_ledger_repo=None,
) -> Dict[str, str]:
    """Idempotent bankroll seed for every cash-eligible personality.

    For each personality returned by `list_eligible_for_cash_mode`:
      - No `ai_bankroll_state` row → write
        `chips=knobs.starting_bankroll, last_regen_tick=now`.
      - Row exists with `last_regen_tick IS NULL` → repair to the same
        seeded state. This is the placeholder pattern that
        `save_emotional_state_json` leaves behind when the controller
        flushes psychology before the AI has ever been credited a
        starting bankroll. Without the repair, those rows sit at
        `chips=0` forever with no regen clock.
      - Row exists with `last_regen_tick` set → leave alone (live state).

    Returns `{personality_id: action}` where action is one of
    `"created"`, `"repaired"`, `"skipped"`. Useful for boot logs.

    Why this exists: the live-fill path in `refresh_table_roster`
    treats "no row" as "0 chips" (via `load_ai_bankroll_current`
    returning None and movement.py's `or 0`). The seed path uses
    `knobs.starting_bankroll` as a fallback. Without this helper, only the
    handful of personalities seeded into table seats at boot ever got
    rows — every personality added later was permanently locked out
    of live-fill. Calling this alongside `ensure_lobby_seeded` keeps
    every eligible personality usable.
    """
    if now is None:
        now = datetime.utcnow()

    eligible = personality_repo.list_eligible_for_cash_mode(user_id=user_id)
    actions: Dict[str, str] = {}
    for cand in eligible:
        pid = cand.get("personality_id")
        if not pid:
            continue
        knobs = bankroll_repo.load_personality_knobs(pid)
        stored = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
        needs_write = stored is None or stored.last_regen_tick is None
        if not needs_write:
            actions[pid] = "skipped"
            continue
        new_state = AIBankrollState(
            personality_id=pid,
            chips=knobs.starting_bankroll,
            last_regen_tick=now,
        )
        bankroll_repo.save_ai_bankroll(
            new_state,
            sandbox_id=sandbox_id,
            chip_ledger_repo=chip_ledger_repo,
        )
        if stored is None:
            actions[pid] = "created"
        else:
            actions[pid] = "repaired"
            # save_ai_bankroll only emits `ai_seed` on first-write —
            # the repair case (placeholder row at chips=0) writes a
            # non-zero chip count without auditing the mint. Emit
            # manually so the chip ledger stays balanced. The mint is
            # documenting chips that should have been seeded at
            # placeholder-row creation time (when
            # `save_emotional_state_json` inserted chips=0).
            if chip_ledger_repo is not None and new_state.chips > stored.chips:
                from core.economy import ledger as chip_ledger

                chip_ledger.record_ai_seed(
                    chip_ledger_repo,
                    personality_id=pid,
                    amount=new_state.chips - stored.chips,
                    context={
                        'site': 'ensure_ai_bankrolls_seeded',
                        'sandbox_id': sandbox_id,
                        'reason': 'placeholder_repair',
                    },
                    sandbox_id=sandbox_id,
                )
    n_created = sum(1 for a in actions.values() if a == "created")
    n_repaired = sum(1 for a in actions.values() if a == "repaired")
    if n_created or n_repaired:
        logger.info(
            "[CASH][LOBBY] bankroll seed: %d created, %d repaired, %d skipped",
            n_created,
            n_repaired,
            len(actions) - n_created - n_repaired,
        )
    return actions


def ensure_lobby_seeded(
    *,
    cash_table_repo,
    personality_repo,
    bankroll_repo,
    now: Optional[datetime] = None,
    user_id: Optional[str] = None,
    sandbox_id: Optional[str] = None,
    chip_ledger_repo=None,
) -> List[CashTableState]:
    """Idempotent boot-time lobby seed.

    For each stake in `STAKES_ORDER`:
      1. If a `cash_tables` row exists for the canonical table_id,
         leave it alone.
      2. Otherwise create a new `CashTableState`, fill 4 AI seats with
         eligible personalities (affordable, not already seated at
         another freshly seeded table), leave 2 seats open.

    Each personality lands on at most one table across the whole lobby
    seed (global uniqueness invariant). Personalities are pulled from
    `list_eligible_for_cash_mode`, then filtered down to those whose
    projected bankroll covers the table's AI buy-in
    (`min_buy_in × buy_in_multiplier`).

    Returns the final list of `CashTableState`s in the lobby (newly
    seeded + previously existing rows). Used by tests + the boot hook
    to verify a successful seed.

    `now` defaults to `datetime.utcnow()`. Explicit `now` is useful in
    tests to pin the projection clock.

    AI bankrolls ARE debited at seed time — `debit_bankroll_for_seat`
    pulls `ai_buy_in` chips from each AI's bankroll into the seat,
    keeping the chip-ledger audit's `actual_outstanding` invariant
    intact (chips move from `ai_bankrolls_stored` to
    `cash_table_seats_ai`, total unchanged, no ledger entry needed
    because it's a pure transfer between two non-bank pools).

    Idempotent boot pass: this function only debits when seating a
    NEW AI into a freshly-created table. Existing tables are
    preserved unchanged, so re-running ensure_lobby_seeded on a
    second boot doesn't double-debit. Tables created in this pass
    are recorded in `out_tables` so the boot hook can log them.

    Symmetric credit: when an AI leaves a seat (movement decision
    via `refresh_table_roster`), the corresponding `BankrollChange`
    of direction `from_seat` returns those chips to the bankroll
    via `credit_ai_cash_out`, which handles cap-clamp + ledger
    entries for any overflow.
    """
    if now is None:
        now = datetime.utcnow()

    existing_tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
    by_id: Dict[str, CashTableState] = {t.table_id: t for t in existing_tables}
    # Global "already seated" set, used both for incremental seeding and
    # for preserving uniqueness across tables that already exist.
    seated_globally: Set[str] = set()
    for t in existing_tables:
        for slot in t.seats:
            if slot["kind"] == "ai":
                seated_globally.add(slot["personality_id"])

    eligible = personality_repo.list_eligible_for_cash_mode(user_id=user_id)

    out_tables: List[CashTableState] = []

    # Randomly distribute the 4 AI seats across the 6 positions so the
    # player picks a seat with positional meaning (3-handed UTG vs.
    # button) rather than always taking position 5 because that's where
    # the deterministic empty seat is. `seed_rng` is local — pure-ish
    # boot pass; tests can override by patching random.Random.
    seed_rng = random.Random()

    # v111: iterate the lobby_config dict (N tables per stake with
    # named entries) instead of STAKES_ORDER (single table per stake).
    # Existing tables (matched by full table_id) are preserved as-is;
    # only missing entries from the config get seeded. The outer-loop
    # ordering still respects the stake ladder because dict insertion
    # order in LOBBY_TABLES mirrors STAKES_ORDER.
    from cash_mode.lobby_config import LOBBY_TABLES

    for stake_label, entries in LOBBY_TABLES.items():
        if stake_label not in STAKES_ORDER:
            # Defensive: a lobby_config entry referencing a stake not in
            # the ladder would be a config typo. Skip with a warning
            # rather than crashing boot.
            logger.warning(
                "[CASH][LOBBY] seed: skipping unknown stake %r in lobby_config",
                stake_label,
            )
            continue
        for entry in entries:
            suffix = entry['id_suffix']
            display_name = entry['name']
            table_id = _table_id_for_stake(stake_label, suffix)
            existing = by_id.get(table_id)
            if existing is not None:
                # Already seeded; preserve. `name` backfill for legacy -001
                # rows is handled by the v111 migration, not the live seed.
                out_tables.append(existing)
                continue

            # Pick which 4 positions hold AI seats (the remaining 2 stay
            # open and become the player's choices). Distinct random sample
            # so no duplicates.
            ai_positions = sorted(seed_rng.sample(range(TABLE_SEAT_COUNT), BASELINE_AI_SEATS))

            # Shuffle the candidate pool per-table so seeding doesn't
            # always pick the alphabetically-first affordable
            # personalities. The repo returns `eligible` sorted by
            # personality_id for stable tests; we randomize a copy here
            # so cash-mode rotation actually sees the full roster across
            # reboots/sandboxes. `seated_globally` still enforces
            # one-table-per-personality across all lobby tables.
            shuffled_eligible = list(eligible)
            seed_rng.shuffle(shuffled_eligible)

            # Build a fresh row. Fill the chosen positions with AI seats.
            seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]
            _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
            position_iter = iter(ai_positions)
            filled = 0
            for cand in shuffled_eligible:
                if filled >= BASELINE_AI_SEATS:
                    break
                pid = cand.get("personality_id")
                if not pid or pid in seated_globally:
                    continue

                knobs = bankroll_repo.load_personality_knobs(pid)
                ai_threshold = round(min_buy_in * knobs.buy_in_multiplier)
                ai_buy_in = min(ai_threshold, max_buy_in)

                stored = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
                if stored is None:
                    # No bankroll row yet — use the personality's cap as a
                    # generous starting projection. Sit-down will write the
                    # row at debit time.
                    projected = knobs.starting_bankroll
                else:
                    projected = project_bankroll(
                        stored,
                        knobs.starting_bankroll,
                        knobs.bankroll_rate,
                        now,
                    )
                if projected < ai_threshold:
                    continue

                seat_position = next(position_iter)
                # Debit the AI's bankroll to fund their initial seat
                # chips BEFORE committing the seat in memory. Without this
                # debit the chip-ledger audit double-counts (the comment
                # above this loop explained the original placeholder
                # semantics). Pure transfer, no ledger entry —
                # `ai_bankrolls_stored` and `cash_table_seats_ai` move in
                # opposite directions by `ai_buy_in`, preserving
                # `actual_outstanding`.
                #
                # Window B (cold-start) atomicity: `debit_bankroll_for_seat`
                # can fail by *returning None* (row missing / projected <
                # buy-in — the audit-safe refusal) or by *raising*. Either
                # way the AI is NOT funded, so we must NOT place the seat:
                # doing so would write a seated-but-unfunded AI (seat chips
                # with no matching bankroll debit → minted chips once
                # `save_table` persists the row). Debit-first + drop-on-fail
                # makes a per-AI debit failure cleanly skip just that AI,
                # leaving the position open for the next candidate. The
                # success path (debit returns non-None) is unchanged.
                from cash_mode.bankroll import debit_bankroll_for_seat

                try:
                    debit_result = debit_bankroll_for_seat(
                        bankroll_repo,
                        pid,
                        ai_buy_in,
                        sandbox_id=sandbox_id,
                        chip_ledger_repo=chip_ledger_repo,
                        now=now,
                    )
                except Exception:
                    logger.exception(
                        "[CASH][LOBBY] seed %s/%s: debit raised for %r — "
                        "skipping seat (no chips moved)",
                        stake_label,
                        table_id,
                        pid,
                    )
                    debit_result = None

                if debit_result is None:
                    # Refused/failed debit: AI was not funded. Return the
                    # position to the pool (so a later candidate can use it)
                    # and leave the seat open. Nothing was committed for this
                    # AI — no seat write, no seated_globally entry, no
                    # filled++.
                    position_iter = itertools.chain([seat_position], position_iter)
                    logger.warning(
                        "[CASH][LOBBY] seed %s/%s: debit refused for %r "
                        "(seat %d) — leaving seat open, AI dropped",
                        stake_label,
                        table_id,
                        pid,
                        seat_position,
                    )
                    continue

                seats[seat_position] = ai_slot(pid, ai_buy_in)
                seated_globally.add(pid)
                # (If this AI was idle, the seated⇒not-idle invariant is
                # enforced by the save_table call after this loop — see
                # CashTableRepository.save_table.)
                filled += 1
                logger.info(
                    "[CASH][LOBBY] seed %s/%s: seated %r at seat %d chips=%d",
                    stake_label,
                    table_id,
                    pid,
                    seat_position,
                    ai_buy_in,
                )

            new_state = CashTableState(
                table_id=table_id,
                stake_label=stake_label,
                seats=seats,
                created_at=now,
                last_activity_at=now,
                name=display_name,
            )
            cash_table_repo.save_table(new_state, sandbox_id=sandbox_id, now=now)
            # SHADOW (Presence cutover Phase 1): mirror the freshly-seeded
            # AI seats into `entity_presence` (SEED→SIT, derived as SIT from
            # OFFLINE by the reconcile). Additive, flag-gated, best-effort.
            _shadow_reconcile_table(new_state, sandbox_id)
            out_tables.append(new_state)
            logger.info(
                "[CASH][LOBBY] seed %s: created table %r (%r) with %d AI seats",
                stake_label,
                table_id,
                display_name,
                filled,
            )

    return out_tables


def _global_seated_set(tables: List[CashTableState]) -> Set[str]:
    """Return personality_ids currently in any table's AI slot."""
    out: Set[str] = set()
    for t in tables:
        for slot in t.seats:
            if slot["kind"] == "ai":
                out.add(slot["personality_id"])
    return out


# Last-stand (predator-signal) dedup state. Maps sandbox_id -> the set of
# personality_ids currently announced as having their whole bankroll on a
# table. In-memory and best-effort: a process restart re-announces each
# committed AI once, which is harmless. Mirrors the ring buffer's
# "session-scoped is fine" stance (cash_mode/activity.py). Keyed by
# sandbox so two players' worlds don't suppress each other's signals.
_last_stand_announced: Dict[str, Set[str]] = {}


def _committed_seated_ais(
    table: CashTableState,
    *,
    reserve_lookup: Callable[[str], Optional[int]],
) -> Dict[str, int]:
    """Return `{personality_id: seat_chips}` for AI seats whose reserve
    bankroll is $0 while they still hold chips — i.e. their entire net
    worth is on this table.

    Strict $0 by design: that's the only state in which busting the seat
    stack fully crashes the AI out. Any reserve at all and they'd go idle
    + side-hustle back, so a "go finish them" signal would be a lie. $0
    reserve while seated is reachable whenever a low-reserve AI rebuys for
    more than they have left — `debit_bankroll_for_seat` clamps stored
    chips at 0. `reserve_lookup` returns the AI's off-table bankroll (None
    when no row exists — treated as "not committed" so a missing row never
    produces a false alarm).
    """
    out: Dict[str, int] = {}
    for slot in table.seats:
        if slot.get("kind") != "ai":
            continue
        pid = slot.get("personality_id")
        chips = int(slot.get("chips", 0))
        if not pid or chips <= 0:
            continue
        reserve = reserve_lookup(pid)
        if reserve is None:
            continue
        if reserve <= 0:
            out[pid] = chips
    return out


def _select_new_last_stands(
    sandbox_id: Optional[str],
    now_qualifying: Set[str],
) -> Set[str]:
    """Diff this refresh's committed AIs against the prior refresh's.

    Returns the personality_ids that are newly committed (so the ticker
    fires once, not every tick) and rolls the announced set forward to
    `now_qualifying`. An AI that recovered, left, or moved to a table not
    scanned this refresh drops out of the set and can re-trigger later.
    """
    key = sandbox_id or ""
    prev = _last_stand_announced.get(key, set())
    newly = now_qualifying - prev
    _last_stand_announced[key] = set(now_qualifying)
    return newly


def _persist_reseat_recovery(bankroll_repo, personality_id, sandbox_id, recovered_energy) -> None:
    """Write recovered idle energy back to emotional_state_json so the AI's
    rebuilt controller hydrates the rested value rather than the drained
    leave-time energy (otherwise it would re-seat and immediately generate
    tenure leave-pressure again). Best-effort — failures are swallowed.
    """
    if bankroll_repo is None:
        return
    try:
        import json as _json

        blob = bankroll_repo.load_emotional_state_json(personality_id, sandbox_id=sandbox_id)
        if not blob:
            return
        state = _json.loads(blob)
        axes = state.get("axes")
        if isinstance(axes, dict):
            axes["energy"] = round(float(recovered_energy), 4)
            bankroll_repo.save_emotional_state_json(
                personality_id, _json.dumps(state), sandbox_id=sandbox_id
            )
    except Exception as exc:  # noqa: BLE001 — recovery write-back is best-effort
        logger.debug(
            "[CASH][LOBBY] reseat energy write-back failed for %s: %s",
            personality_id,
            exc,
        )


def _process_global_greedy_fills(
    *,
    fill_ctx,
    idle_pool,
    eligible,
    seated_globally,
    fish_ids,
    bankroll_lookup,
    bankroll_repo,
    cash_table_repo,
    chip_ledger_repo,
    personality_repo,
    sandbox_id,
    now,
    rng,
    seek_rate: float = DEFAULT_SEEK_RATE,
    human_headroom: int = 0,
) -> None:
    """The loop inversion (CASH_MODE_TABLE_ATTRACTIVENESS.md §2).

    Runs once after the per-table movement loop (which ran Step 1 only,
    `enable_live_fill=False`). Seats idle / eligible-never-seated AIs across
    ALL tables at once: each seeker (most-desperate first) picks the single
    most attractive table it can afford and has an open seat at, with
    occupancy recomputed between picks so `W_CROWD` spreads sharks.

    Owns its OWN contained persistence for fills — the per-table loop's
    bankroll/idle/save_table passes have already run for Step-1 outcomes, so
    Step 1's `from_seat`/settlement path is untouched here. Per Codex's
    invariant: each new seat is debited inline (`debit_bankroll_for_seat`)
    and we do NOT also append a result-level `to_seat` change (which would
    double-debit). `seated_globally` is mutated in place as each AI is
    seated so the next pick can't reuse it.
    """
    if not fill_ctx:
        return

    from cash_mode.bankroll import debit_bankroll_for_seat

    # 1. Build a FillableTable + the usable open-seat indices per table.
    #    Usable = open now AND open at pre-burst start (excludes seats
    #    vacated during this refresh — defer-freshly-vacated, Invariant 6).
    tables_state: Dict[str, FillableTable] = {}
    fill_indices: Dict[str, List[int]] = {}
    for tid, (result, preburst_open) in fill_ctx.items():
        tbl = result.new_table
        try:
            _, min_bi, max_bi = table_buy_in_window(tbl.stake_label)
        except KeyError:
            continue
        usable = [
            i for i, s in enumerate(tbl.seats) if s.get("kind") == "open" and i in preburst_open
        ]
        # Reserve `human_headroom` of the open seats for a human who taps
        # Sit/Sponsor in the lobby — the fill leaves the highest-index
        # open seats untouched so the ticker can't saturate a table and
        # crowd the player out (the stale-snapshot 409 race). 0 = no
        # reservation (sims/tests fill to full). We keep the LOW-index
        # seats fillable so reserved seats are deterministic.
        if human_headroom > 0 and usable:
            reserve = min(len(usable), human_headroom)
            usable = usable[: len(usable) - reserve]
        if not usable:
            continue
        is_lobby = tbl.table_type == "lobby"
        grinders = fish_chips = whale_chips = 0
        for s in tbl.seats:
            if s.get("kind") != "ai":
                continue
            if s.get("archetype") == "fish":
                # A fish at a lobby table IS the whale (regular fish are
                # casino-only); weigh it as a whale.
                if is_lobby:
                    whale_chips += int(s.get("chips", 0))
                else:
                    fish_chips += int(s.get("chips", 0))
            else:
                grinders += 1
        tables_state[tid] = FillableTable(
            table_id=tid,
            stake_label=tbl.stake_label,
            min_buy_in=min_bi,
            max_buy_in=max_bi,
            open_count=len(usable),
            grinder_count=grinders,
            fish_chips=fish_chips,
            whale_chips=whale_chips,
            # Casino = the low-rent public grind room: less attractive
            # baseline, but the fish draw rides over it and it stays a valid
            # open-to-all fallback (CASINO_VENUE_APPEAL).
            venue_appeal=CASINO_VENUE_APPEAL if tbl.table_type == "casino" else 1.0,
        )
        fill_indices[tid] = usable
    if not tables_state:
        return

    _knobs_cache: Dict[str, Any] = {}

    def _knobs(pid: str):
        if pid not in _knobs_cache:
            _knobs_cache[pid] = bankroll_repo.load_personality_knobs(pid)
        return _knobs_cache[pid]

    def _recovery_fraction(pid: str, left_at) -> float:
        """Recovery toward baseline for an idle AI (1.0 = fully rested) —
        mirrors the retired per-table `_reseat_energy_lookup`."""
        import json as _json

        stored = baseline = 0.5
        if bankroll_repo is not None:
            try:
                blob = bankroll_repo.load_emotional_state_json(pid, sandbox_id=sandbox_id)
                if blob:
                    st = _json.loads(blob)
                    stored = float(st.get("axes", {}).get("energy", 0.5))
                    baseline = float(st.get("anchors", {}).get("baseline_energy", stored))
            except Exception:
                stored = baseline = 0.5
        if left_at is None:
            projected = stored
        else:
            try:
                idle_seconds = max(0.0, (now - left_at).total_seconds())
            except Exception:
                idle_seconds = 0.0
            projected = project_idle_energy(stored, baseline, idle_seconds)
        return 1.0 if baseline <= 0 else min(1.0, projected / baseline)

    def _can_afford_target(
        target_stake: str, projected: int, buy_in_multiplier: float
    ) -> bool:
        """Whether `projected` covers this AI's ACTUAL buy-in at `target_stake`.

        Must mirror the placement gate (`assign_seats_greedy` →
        `seeker_buy_in`): `round(min_buy_in × buy_in_multiplier)` capped at the
        tier max — NOT the raw min. Gating stickiness on the raw min while the
        greedy seats on the multiplied amount opens a dead band
        `[min, min × mult]`: an AI there is rich enough to refuse lower tables
        (this gate) yet too poor to ever be placed at its target (greedy), so
        it strands forever as "stale idle". Mirroring the formula here lets
        such an AI relax down to a tier it can actually sit at.
        """
        try:
            _, t_min, t_max = table_buy_in_window(target_stake)
        except Exception:
            return True  # unknown tier — don't gate
        required = min(round(t_min * buy_in_multiplier), t_max)
        return projected >= required

    # 2. Candidate pool: idle AIs first, then eligible-never-seated; exclude
    #    fish (casino-only) and the already-seated.
    candidates: List[Tuple[str, Any]] = []  # (pid, idle_entry | None)
    seen: Set[str] = set()
    for entry in idle_pool:
        pid = entry.personality_id
        if pid in fish_ids or pid in seated_globally or pid in seen:
            continue
        candidates.append((pid, entry))
        seen.add(pid)
    for cand in eligible:
        pid = cand.get("personality_id")
        if not pid or pid in fish_ids or pid in seated_globally or pid in seen:
            continue
        candidates.append((pid, None))
        seen.add(pid)

    # 3. Roll the seek-rate + apply the impure gates (recovery, cooldown,
    #    target-stake) into each seeker's allowed_table_ids.
    seekers: List[SeatSeeker] = []
    idle_sourced: Set[str] = set()
    recovery_frac: Dict[str, float] = {}
    for pid, entry in candidates:
        if rng.random() >= seek_rate:
            continue
        if entry is not None:
            frac = _recovery_fraction(pid, entry.left_at)
            if frac < RESEAT_RECOVERY_FLOOR or rng.random() >= reseat_readiness(frac):
                continue  # still resting
            recovery_frac[pid] = frac
        projected = bankroll_lookup(pid) or 0
        try:
            knobs = _knobs(pid)
        except Exception:
            continue
        allowed: Set[str] = set()
        for tid, ft in tables_state.items():
            if is_in_cooldown(tid, pid, now):
                continue  # per-table leave cooldown (not a global ban)
            if (
                entry is not None
                and entry.target_stake is not None
                and entry.target_stake != ft.stake_label
                and _can_afford_target(
                    entry.target_stake, projected, knobs.buy_in_multiplier
                )
            ):
                continue  # target-stake stickiness (relaxed if can't afford target)
            allowed.add(tid)
        if not allowed:
            continue
        seekers.append(
            SeatSeeker(
                personality_id=pid,
                projected_bankroll=projected,
                starting_bankroll=knobs.starting_bankroll,
                comfort_zone=knobs.stake_comfort_zone or STAKES_ORDER[0],
                allowed_table_ids=frozenset(allowed),
                buy_in_multiplier=knobs.buy_in_multiplier,
            )
        )
        if entry is not None:
            idle_sourced.add(pid)
    if not seekers:
        return

    # Most-desperate-first priority (mirrors the retired hungry-grinder
    # reorder): lower bankroll/starting ratio picks first; pid breaks ties.
    seekers.sort(
        key=lambda s: (s.projected_bankroll / max(1, s.starting_bankroll), s.personality_id)
    )

    assignments = assign_seats_greedy(seekers, tables_state)
    if not assignments:
        return

    # 4. Apply each assignment: fund (inline debit) → seat → idle-remove.
    affected: Dict[str, List[str]] = {}
    for pid, tid in assignments:
        result, _preburst = fill_ctx[tid]
        ft = tables_state[tid]
        idxs = fill_indices[tid]
        if not idxs:
            continue
        knobs = _knobs(pid)
        buy_in = seeker_buy_in(ft, knobs.buy_in_multiplier)
        # Inline debit (pure transfer bankroll → seat). Refuses + returns
        # None on insufficiency (regen race etc.); skip seating then so we
        # never mint chips. The greedy core already affordability-checked,
        # so this is the rare belt-and-suspenders path.
        if (
            debit_bankroll_for_seat(
                bankroll_repo,
                pid,
                buy_in,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
                now=now,
            )
            is None
        ):
            continue
        seat_idx = idxs.pop(0)
        result.new_table.seats[seat_idx] = ai_slot(pid, buy_in)
        # Keep the global occupancy set accurate (within-batch double-seating
        # is already prevented by the one-seeker-per-pid candidate dedup; this
        # keeps `seated_globally` correct for the returned state and any
        # post-fill consumer that reads it).
        seated_globally.add(pid)
        result.freshly_seated_personality_ids.append(pid)
        if pid in idle_sourced:
            cash_table_repo.delete_idle(pid, sandbox_id=sandbox_id)
            if pid in recovery_frac:
                _persist_reseat_recovery(bankroll_repo, pid, sandbox_id, recovery_frac[pid])
        affected.setdefault(tid, []).append(pid)

    # 5. Persist the mutated tables + emit arrival (JOIN) events for fills.
    for tid, pids in affected.items():
        result, _preburst = fill_ctx[tid]
        cash_table_repo.save_table(result.new_table, sandbox_id=sandbox_id, now=now)
        # SHADOW (Presence cutover Phase 1): mirror this greedy fill's seat
        # writes. The reconcile diffs the saved seat map vs current shadow
        # state, so the AIs just seated here (idle→SIT / eligible→SIT, or a
        # cross-table move as LEAVE+SIT) are recorded and unchanged
        # neighbours are left alone. Additive, flag-gated, best-effort.
        _shadow_reconcile_table(result.new_table, sandbox_id)
        _emit_activity_events(
            table=result.new_table,
            previous_table=result.new_table,  # unused by the emitter
            decisions={},
            freshly_seated_personality_ids=pids,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )


def refresh_unseated_tables(
    *,
    cash_table_repo,
    personality_repo,
    bankroll_repo,
    rng: Optional[random.Random] = None,
    now: Optional[datetime] = None,
    user_id: Optional[str] = None,
    sandbox_id: Optional[str] = None,
    live_fill_prob: float = DEFAULT_LIVE_FILL_PROB,
    # Per-refresh probability that an idle/eligible AI goes room-hunting in
    # the global greedy fill (replaces the per-seat live_fill_prob Bernoulli;
    # CASH_MODE_TABLE_ATTRACTIVENESS.md §2). Sim-tunable; a future refinement
    # could scale it with the catch-up gap so long-unwatched lobbies fill
    # faster. `live_fill_prob` is retained only for the (now fill-disabled)
    # refresh_table_roster signature / back-compat.
    seek_rate: float = DEFAULT_SEEK_RATE,
    hand_sim_prob: float = DEFAULT_HAND_SIM_PROB,
    chip_ledger_repo=None,
    # Phase 4: stake_repo + relationship_repo are required for the
    # take_stake interception. When either is None, take_stake never
    # fires and forced_leave behaves as it did pre-Phase-4 (preserves
    # behavior for the limited number of test callers that don't pass
    # them — tests for take_stake plumbing pass both).
    relationship_repo=None,
    stake_repo=None,
    # Vice spending repo. When None, the vice mechanic is disabled —
    # no expiry pass, no start pass. Optional so existing test callers
    # that don't care about vice can pass nothing and keep working.
    vice_repo=None,
    # Side-hustle repo (the mirror of vice — broke AIs earn off-grid).
    # When None, the side hustle is disabled. Optional for the same
    # back-compat reason as vice_repo. See CASH_MODE_SIDE_HUSTLE.md.
    side_hustle_repo=None,
    # Vice mode — which vice mechanism (if any) feeds the bank pool this
    # refresh: one of `economy_flags.VICE_MODES` ('real' | 'fake' | 'off'),
    # mutually exclusive by construction. None → use the live default
    # `economy_flags.VICE_MODE`. The sim passes 'fake' (real vice needs an
    # LLM call per fire). See economy_flags.VICE_MODE / CASH_MODE_SIDE_HUSTLE.md.
    vice_mode: Optional[str] = None,
    # Personas the human is actively playing in a live in-memory hand
    # (from `game_handler.live_cash_seated_pids`). The world sim's
    # `seated_globally` is derived only from the persisted `cash_tables`
    # snapshot, which can lag/omit the human's live table; treating these
    # as occupied is what stops the ticker seating — or busting — a live
    # opponent at another table mid-hand. None → no live games to honor
    # (the default for sim/test callers, preserving their behavior).
    live_seated_pids: Optional[Set[str]] = None,
    # Number of open seats the global greedy fill leaves untouched on each
    # table, reserving them for a human who taps Sit/Sponsor in the lobby.
    # 0 (default) = full saturation, which is what sims/tests want; the
    # LIVE lobby + ticker pass `economy_flags.LIVE_FILL_HUMAN_HEADROOM`.
    human_headroom: int = 0,
) -> Dict[str, RosterRefreshResult]:
    """Run a movement+live-fill refresh on every table without a human.

    Called from `GET /api/cash/lobby` (lazy cadence — see handoff
    §"Cadence"). For each table whose seats don't include a `"human"`
    slot, evaluates AI movement and rolls live-fill probability on
    open seats. Persists table + idle-pool changes through the repos.

    Tables with a human seated are skipped here: the hand-boundary
    refresh hook in commit 7 covers those. Two separate cadences keep
    the rolls cheap and avoid running movement twice per hand.

    Returns `{table_id: RosterRefreshResult}` for the refreshed tables
    so callers can log/inspect. Empty dict means nothing was refreshed.
    """
    if rng is None:
        rng = random.Random()
    if now is None:
        now = datetime.utcnow()
    # Resolve the mutually-exclusive vice mode (per-call override → live
    # default). 'real' / 'fake' / 'off'; anything else falls through both
    # gates below (no vice), which is the safe default for a bad value.
    if vice_mode is None:
        from cash_mode import economy_flags as _economy_flags

        vice_mode = _economy_flags.VICE_MODE

    tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)

    # Sponsorship seat-hold expiry — runs BEFORE seated_globally / the
    # per-table loop so a hold whose TTL lapsed this refresh frees its
    # seat back to "open" and becomes fillable in the same tick. The
    # frontend releases holds explicitly on SponsorModal-close; this is
    # the safety net for the abandoned-modal case (closed tab, dropped
    # network) so a stale hold can't strand a seat against live-fill.
    # Mutates the loaded `tables` in place so the rest of the refresh
    # sees the freed seats.
    from cash_mode.tables import is_reservation_expired, open_slot

    for table in tables:
        freed_any = False
        for idx, slot in enumerate(table.seats):
            if is_reservation_expired(slot, now):
                table.seats[idx] = open_slot()
                freed_any = True
        if freed_any:
            try:
                cash_table_repo.save_table(table, sandbox_id=sandbox_id, now=now)
                logger.info(
                    "[CASH][LOBBY] expired sponsorship seat-hold(s) on table=%r",
                    table.table_id,
                )
            except Exception as exc:
                logger.warning(
                    "[CASH][LOBBY] failed to free expired seat-hold on %r: %s",
                    table.table_id,
                    exc,
                )

    idle_pool = cash_table_repo.list_idle(sandbox_id=sandbox_id)
    seated_globally = SeatOccupancyRegistry(
        _global_seated_set(tables), label="refresh_unseated_tables"
    )
    eligible = personality_repo.list_eligible_for_cash_mode(user_id=user_id)

    # A persona the human is playing live counts as occupied even when the
    # persisted snapshot doesn't show it seated. Union into `seated_globally`
    # so movement/live-fill won't reuse it elsewhere; the idle/eligible
    # filter below drops it from the seating surfaces too.
    live_seated = set(live_seated_pids or ())
    if live_seated:
        seated_globally |= live_seated

    # Vice expiry pass — runs BEFORE the table loop so AIs whose vice
    # ended this refresh become immediately eligible for seating /
    # staking. Returns a list of ViceEndResults; Commit 2 will emit
    # ticker rows from them.
    vice_ends: list = []
    on_vice: Set[str] = set()
    if vice_repo is not None and sandbox_id is not None:
        try:
            from cash_mode.ai_vice_spending import tick_vice_expirations

            vice_ends = tick_vice_expirations(
                vice_repo=vice_repo,
                bankroll_repo=bankroll_repo,
                personality_repo=personality_repo,
                sandbox_id=sandbox_id,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] vice expiry pass failed: %s",
                exc,
            )
        try:
            on_vice = vice_repo.active_pids(sandbox_id=sandbox_id, now=now)
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] vice active_pids failed: %s",
                exc,
            )
            on_vice = set()
        # Post-vice refractory window: an AI that just finished a vice
        # can't be sent off to another for VICE_COOLDOWN_SECONDS, so it
        # returns and plays a while still rich instead of bouncing right
        # back out. Applies to idle- and leave-triggered vices alike.
        for _end in vice_ends:
            record_vice_cooldown(_end.personality_id, now)

    # Side-hustle expiry pass — the mirror of the vice expiry pass.
    # Runs BEFORE the table loop so an AI who finished hustling (and was
    # just credited a pool-funded payout) becomes immediately eligible
    # for seating this same refresh. Gated on SIDE_HUSTLE_ENABLED + the
    # repos needed to draw from / ledger the pool.
    from cash_mode import economy_flags

    hustle_ends: list = []
    on_hustle: Set[str] = set()
    if (
        economy_flags.SIDE_HUSTLE_ENABLED
        and side_hustle_repo is not None
        and sandbox_id is not None
        and chip_ledger_repo is not None
    ):
        try:
            from cash_mode.ai_side_hustle import tick_side_hustle_expirations

            hustle_ends = tick_side_hustle_expirations(
                side_hustle_repo=side_hustle_repo,
                bankroll_repo=bankroll_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] side-hustle expiry pass failed: %s",
                exc,
            )
        try:
            on_hustle = side_hustle_repo.active_pids(sandbox_id=sandbox_id, now=now)
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] side-hustle active_pids failed: %s",
                exc,
            )
            on_hustle = set()

    # Filter the idle pool and eligible-personality lists to exclude
    # AIs currently off-grid (on a vice OR a side hustle). Every
    # downstream gate (live-fill, staking-candidate selection,
    # cross-table pool) reads these variables, so a single filter at the
    # top covers all the seating / staking eligibility surfaces without
    # per-call-site changes.
    off_grid = on_vice | on_hustle
    unavailable = off_grid | live_seated
    if unavailable:
        idle_pool = [entry for entry in idle_pool if entry.personality_id not in unavailable]
        eligible = [cand for cand in eligible if cand.get("personality_id") not in unavailable]

    # Circulating gate (v123): an idle persona only walks back to a seat if
    # it's still in the eligible (circulating) set. `eligible` is already
    # circulating-only (list_eligible_for_cash_mode gates on it), so a persona
    # demoted to circulating=0 — a leaked sim/test zombie, or any deliberately
    # retired persona — DRAINS out of the lobby: its idle rows are skipped here
    # instead of cycling back in via the idle-first re-seat path, which keys on
    # personality_id and would otherwise bypass the gate (the re-seat candidate
    # pools take idle AIs before consulting `eligible`). One filter here covers
    # both seat-fill consumers (per-table refresh_table_roster + the global
    # greedy fill), mirroring the `unavailable` filter above. Legit AIs are
    # unaffected — list_eligible returns every circulating persona regardless of
    # bankroll, so the intersection only removes non-circulating idlers.
    _circulating_ids = {
        cand.get("personality_id") for cand in eligible if cand.get("personality_id")
    }
    idle_pool = [entry for entry in idle_pool if entry.personality_id in _circulating_ids]

    # Closed-economy: fish are a casino-only player class. The lobby
    # never live-fills a fish; this set is the defense-in-depth filter
    # for fish that may have entered `list_idle` after leaving a
    # casino seat. Boot-time seed seeding is already fish-free via
    # `list_eligible_for_cash_mode` (excludes archetype='fish').
    from cash_mode.closed_economy import load_fish_ids

    _fish_ids = load_fish_ids(bankroll_repo, sandbox_id=sandbox_id)

    def _bankroll_lookup(pid: str) -> Optional[int]:
        current = bankroll_repo.load_ai_bankroll_current(
            pid,
            sandbox_id=sandbox_id,
            now=now,
        )
        if current is not None:
            return current
        # No row yet — mirror the seed-path fallback so a personality
        # added between boot and the first lobby refresh isn't locked
        # out of live-fill. `ensure_ai_bankrolls_seeded` should have
        # written the row already; this is the defensive shim.
        return bankroll_repo.load_personality_knobs(pid).starting_bankroll

    # Phase 4 take_stake callbacks. Wired only when both stake_repo
    # and relationship_repo are provided. The borrower/lender profile
    # lookups always work — the gates inside find_ai_staker_for treat
    # missing config as defaults.
    _take_stake_enabled = relationship_repo is not None and stake_repo is not None

    # Phase 4 Commit 4: build the cross-table staker candidate pool
    # ONCE per refresh, indexed by stake_label of the target table.
    # Each stake's pool includes (a) AIs seated at OTHER tables whose
    # `stake_comfort_zone` is the target stake or an adjacent stake,
    # plus (b) idle / eligible-but-never-seated AIs filtered the same
    # way. Computed lazily inside the per-table loop because adjacency
    # depends on the target stake_label.
    def _adjacent_stakes(label: str) -> List[str]:
        if label not in STAKES_ORDER:
            return []
        idx = STAKES_ORDER.index(label)
        return [STAKES_ORDER[i] for i in (idx - 1, idx, idx + 1) if 0 <= i < len(STAKES_ORDER)]

    # Cache `stake_comfort_zone` per pid once per refresh. The knobs
    # are static for the duration of a lobby refresh, so loading them
    # per (pid, table) combo would do up to 5× the necessary work
    # across the per-table loop. Populated lazily on first access.
    _comfort_zone_cache: Dict[str, Optional[str]] = {}

    def _comfort_zone(pid: str) -> Optional[str]:
        if pid not in _comfort_zone_cache:
            try:
                knobs = bankroll_repo.load_personality_knobs(pid)
                _comfort_zone_cache[pid] = knobs.stake_comfort_zone
            except Exception:
                _comfort_zone_cache[pid] = None
        return _comfort_zone_cache[pid]

    def _cross_table_pool_for(target_stake_label: str, current_table_id: str) -> List[str]:
        if not _take_stake_enabled:
            return []
        adj = set(_adjacent_stakes(target_stake_label))
        if not adj:
            return []
        candidates: List[str] = []
        seen: set = set()
        # AIs at other tables.
        for other_table in tables:
            if other_table.table_id == current_table_id:
                continue
            for slot in other_table.seats:
                if slot.get("kind") != "ai":
                    continue
                pid = slot.get("personality_id")
                if not pid or pid in seen:
                    continue
                if _comfort_zone(pid) in adj:
                    candidates.append(pid)
                    seen.add(pid)
        # Idle pool AIs.
        for idle_entry in idle_pool:
            pid = idle_entry.personality_id
            if pid in seen:
                continue
            if _comfort_zone(pid) in adj:
                candidates.append(pid)
                seen.add(pid)
        # Eligible-never-seated personalities (already filtered to
        # cash-eligible upstream; we further filter by adjacency).
        for cand in eligible:
            pid = cand.get("personality_id")
            if not pid or pid in seen:
                continue
            if _comfort_zone(pid) in adj:
                candidates.append(pid)
                seen.add(pid)
        return candidates

    # In-memory set of pids that already received a `take_stake` this
    # lobby refresh. Stake rows aren't written until AFTER the burst
    # loop completes, so `load_active_for_borrower` returns stale
    # (None) within the burst. Without this guard, an AI that busts
    # twice across a multi-hand burst would get two stakes created in
    # `agg_stake_creations`, violating the one-active-stake invariant
    # and orphaning the earlier stake row.
    _burst_stake_creation_pids: set = set()

    def _borrower_profile_lookup(pid: str):
        # An AI already on an active stake can't take a new one — the
        # `one-active-stake-per-borrower` invariant would otherwise break
        # (orphaned active rows accumulate in the stakes table). Surface
        # this as "unwilling" to the take_stake interception so the AI
        # falls back to forced_leave + session-end settlement instead.
        profile = bankroll_repo.load_borrower_profile(pid)
        if not profile.willing:
            return profile
        from cash_mode.staker_profile import BorrowerProfile

        # Burst-local guard: was this pid already given a stake earlier
        # in the current refresh? (DB check below sees stale state.)
        if pid in _burst_stake_creation_pids:
            return BorrowerProfile(willing=False)
        if stake_repo is not None:
            existing = stake_repo.load_active_for_borrower(
                pid,
                "personality",
            )
            if existing is not None:
                return BorrowerProfile(willing=False)
        return profile

    def _staker_profile_lookup(pid: str):
        return bankroll_repo.load_staker_profile(pid)

    def _relationship_lookup(observer_id: str, opponent_id: str):
        if relationship_repo is None:
            return None
        rel = relationship_repo.load_relationship_state(
            observer_id=observer_id,
            opponent_id=opponent_id,
            now=now,
        )
        if rel is None:
            return None
        return (rel.likability, rel.respect, rel.heat)

    # Staker-incentives plan: per-refresh history cache so the
    # weighted-selection path in find_ai_staker_for does at most one
    # `aggregate_history_for_staker` query per candidate per refresh,
    # regardless of how many busts/burst-hands surface that candidate.
    # Lifetime is one refresh — disposed when this function returns.
    _history_cache: Dict[str, Dict[str, StakerHistoryStats]] = {}

    def _history_for(staker_id: str):
        if stake_repo is None:
            return {}
        if staker_id not in _history_cache:
            try:
                _history_cache[staker_id] = stake_repo.aggregate_history_for_staker(staker_id)
            except Exception as exc:
                logger.debug(
                    "[CASH][LOBBY] history aggregation failed staker=%r: %s",
                    staker_id,
                    exc,
                )
                _history_cache[staker_id] = {}
        return _history_cache[staker_id]

    # Parallel cache for starting_bankroll values used by the excess-
    # pressure score. Static for the refresh duration since knobs come
    # from `personalities.config_json` which doesn't mutate mid-refresh.
    _starting_bankroll_cache: Dict[str, Optional[int]] = {}

    def _starting_bankroll_for(pid: str) -> Optional[int]:
        if pid not in _starting_bankroll_cache:
            try:
                knobs = bankroll_repo.load_personality_knobs(pid)
                _starting_bankroll_cache[pid] = knobs.starting_bankroll
            except Exception:
                _starting_bankroll_cache[pid] = None
        return _starting_bankroll_cache[pid]

    def _carry_lookup(staker_id: str, borrower_id: str) -> int:
        """Phase 4.5 Commit 1 — total outstanding carry borrower → staker.

        Returns 0 when the borrower has no carries with this staker, or
        when no stake_repo is wired (early tests / standalone callers).
        Used by `refresh_table_roster`'s `take_stake` branch to garnish
        the new stake's cut against the candidate's prior unpaid debt.
        """
        if stake_repo is None:
            return 0
        try:
            carries = stake_repo.list_carries_for_staker(staker_id)
        except Exception as exc:
            logger.debug(
                "[CASH][LOBBY] carry_lookup failed staker=%r borrower=%r: %s",
                staker_id,
                borrower_id,
                exc,
            )
            return 0
        return sum(int(c.carry_amount) for c in carries if c.borrower_id == borrower_id)

    def _buy_in_lookup(pid: str) -> int:
        # Map back to a table buy-in: needs the stake_label of the
        # destination table. We close over the current iteration's
        # `table.stake_label` via the outer scope.
        return _current_table_buy_in[pid]

    # Last-stand detection: pid -> (table_id, stake_label) for every
    # seated AI at $0 reserve seen this refresh. Reconciled against the
    # prior refresh after the table loop so the ticker fires once per
    # episode rather than every tick.
    last_stand_qualifying: Dict[str, Tuple[str, str, Optional[str]]] = {}

    # Vice-on-leave plumbing. A discretionary leaver (take_break /
    # bored_move) who is rich enough rolls the existing wealth×psych vice
    # probability AT the leave and goes straight off-grid (see
    # refresh_table_roster's `go_vice`) — the fix for the post-loop
    # idle-only scan never catching a winner who re-seats before it runs.
    # Built once per refresh: the cast median is a slow aggregate, fine to
    # snapshot here and reuse for the leave commits below. None (no
    # interception) unless real vice is on and the cast is rich enough.
    _vice_cast_median = 0
    _vice_prob_lookup = None
    all_vice_bound: List[str] = []
    if (
        vice_mode == 'real'
        and vice_repo is not None
        and bankroll_repo is not None
        and sandbox_id is not None
    ):
        from cash_mode.ai_vice_spending import (
            MIN_CAST_MEDIAN_FOR_VICE,
            _load_psych_snapshot,
            compute_cast_median,
            compute_excess_ratio,
            compute_pressure,
            compute_vice_probability,
        )

        try:
            _vice_cast_median = compute_cast_median(
                bankroll_repo.list_all_ai_bankroll_chips(sandbox_id=sandbox_id)
            )
        except Exception as exc:
            logger.warning("[CASH][LOBBY] vice cast-median compute failed: %s", exc)
            _vice_cast_median = 0

        if _vice_cast_median >= MIN_CAST_MEDIAN_FOR_VICE:

            def _vice_prob_lookup(pid: str) -> float:
                # Refractory window: no urge to celebrate right after one.
                if is_in_vice_cooldown(pid, now):
                    return 0.0
                try:
                    current = bankroll_repo.load_ai_bankroll_current(
                        pid, sandbox_id=sandbox_id, now=now
                    )
                except Exception:
                    return 0.0
                if not current or current <= 0:
                    return 0.0
                excess = compute_excess_ratio(current, _vice_cast_median)
                if excess <= 0:
                    return 0.0
                psych = _load_psych_snapshot(
                    bankroll_repo=bankroll_repo,
                    personality_id=pid,
                    sandbox_id=sandbox_id,
                )
                if psych is None:
                    pressure = compute_pressure(0.7, 0.7, 0.7)
                else:
                    pressure = compute_pressure(
                        psych['confidence'], psych['composure'], psych['energy']
                    )
                return compute_vice_probability(excess, pressure)

    out: Dict[str, RosterRefreshResult] = {}
    # Per-table context for the post-loop GLOBAL greedy fill
    # (CASH_MODE_TABLE_ATTRACTIVENESS.md §2): the refresh result (to mutate
    # seats + append to_seat/idle changes) and the set of seat indices that
    # were OPEN at this table's pre-burst start — the fill may only use seats
    # open the whole refresh, not ones freshly vacated this tick.
    fill_ctx: Dict[str, Tuple[RosterRefreshResult, frozenset]] = {}
    for table in tables:
        if table.human_seat_index() is not None:
            # Active session table; the hand-boundary hook handles it.
            continue

        big_blind, table_min_buy_in, table_max_buy_in = table_buy_in_window(table.stake_label)
        try:
            stake_idx = STAKES_ORDER.index(table.stake_label)
        except ValueError:
            continue
        next_tier_min_buy_in: Optional[int] = None
        if stake_idx + 1 < len(STAKES_ORDER):
            _, nxt_min, _ = table_buy_in_window(STAKES_ORDER[stake_idx + 1])
            next_tier_min_buy_in = nxt_min

        # Build a per-table buy-in lookup that honors per-personality
        # `buy_in_multiplier`. Computed once per table; passed into the
        # pure helper.
        _current_table_buy_in: Dict[str, int] = {}

        def _buy_in_for(
            pid: str,
            _table_buy_in=_current_table_buy_in,
            _min_buy_in=table_min_buy_in,
            _max_buy_in=table_max_buy_in,
        ) -> int:
            if pid in _table_buy_in:
                return _table_buy_in[pid]
            knobs = bankroll_repo.load_personality_knobs(pid)
            threshold = round(_min_buy_in * knobs.buy_in_multiplier)
            value = min(threshold, _max_buy_in)
            _table_buy_in[pid] = value
            return value

        # Phase 4.5 Commit 2 — tier-gated take_stake. Wrap the borrower
        # lookup with a per-table tier check so an AI whose carry-load
        # has pushed them to `house_only` at this stake can't intercept
        # `forced_leave` with a peer-staked seat refill. Without this
        # gate, an over-leveraged AI keeps qualifying for peer stakes
        # purely because lender filters don't read the borrower's tier
        # — the runaway-debt failure mode. House-staker fallback is
        # NOT applied here (movement-time is the wrong layer for
        # sponsor-flow selection); over-tier AIs just `forced_leave`
        # normally and may try again at a lower-stake table later.
        _target_stake_label = table.stake_label

        def _borrower_lookup_for_table(
            pid: str,
            _stake=_target_stake_label,
        ):
            profile = _borrower_profile_lookup(pid)
            if not profile.willing:
                return profile
            if stake_repo is None:
                return profile
            try:
                tier = resolve_tier(
                    borrower_id=pid,
                    borrower_kind=BORROWER_KIND_PERSONALITY,
                    current_stake_label=_stake,
                    stake_repo=stake_repo,
                )
            except Exception as exc:
                logger.debug(
                    "[CASH][LOBBY] tier resolution failed pid=%r: %s",
                    pid,
                    exc,
                )
                return profile
            if tier == TIER_HOUSE_ONLY:
                from cash_mode.staker_profile import BorrowerProfile

                return BorrowerProfile(willing=False)
            return profile

        # Full sim with catch-up burst (Commits 3-5): if the table
        # was last refreshed recently, fire at most one probability-
        # gated hand (existing behavior). If the lobby was unwatched
        # for longer than the burst threshold, simulate "the world
        # advanced while you were away" by running multiple hands
        # before this refresh tick returns. Cap is enforced per
        # `hand_burst_count` to keep the lobby response under the
        # 500 ms budget Phase 0 measured.
        gap_seconds = 0.0
        if table.last_activity_at is not None:
            gap_seconds = max(0.0, (now - table.last_activity_at).total_seconds())
        burst_n = hand_burst_count(
            gap_seconds=gap_seconds,
            base_prob=hand_sim_prob,
            rng=rng,
        )

        # Per-hand movement + fill: each sim hand drives one movement
        # evaluation and one live-fill roll per open seat. Replaces the
        # prior "one refresh after the whole burst" cadence, which made
        # fills tied to poll frequency rather than table activity.
        # Aggregate the per-hand results so persistence + event emission
        # below sees the full burst's worth of changes.
        from cash_mode.full_sim import _get_default_controller_cache

        controller_cache = _get_default_controller_cache()

        def _psych_lookup_sim(pid: str, _controller_cache=controller_cache) -> Dict[str, Any]:
            ctrl = _controller_cache.get(pid)
            if ctrl is None:
                return {}
            psych = getattr(ctrl, 'psychology', None)
            if psych is None:
                return {}
            try:
                zone = getattr(psych, 'primary_zone', 'neutral')
            except Exception:
                zone = 'neutral'
            try:
                intensity = min(1.0, float(psych.zone_effects.total_penalty_strength))
            except Exception:
                intensity = 0.0
            return {
                'energy': float(getattr(psych, 'energy', 0.5)),
                'zone': zone,
                'hands_in_detached_zone': int(getattr(ctrl, '_detached_hands', 0)),
                'emotional_intensity': intensity,
            }

        # Snapshot pre-burst table for _emit_activity_events below.
        # `table` gets reassigned each iteration to the post-hand result,
        # so without this snapshot the diff-aware event helper would see
        # the wrong "previous" state.
        previous_table_snapshot = table

        sim_results: List[HandSimResult] = []
        agg_decisions: Dict[str, str] = {}
        agg_idle_changes = []
        agg_bankroll_changes = []
        agg_freshly_seated: List[str] = []
        agg_rebuy_changes = []
        agg_stake_creations = []
        agg_leave_signals: Dict[str, str] = {}
        agg_vice_bound: List[str] = []
        for _ in range(burst_n):
            # Rotate the dealer button to the next occupied seat for
            # this hand. Matters for seat-choice UX — when a player
            # opens the lobby, the visible button position tells them
            # what positional spots (UTG / CO / BTN / blinds) the open
            # seats correspond to. Without this, the in-engine dealer
            # would be seat 0 for every burst hand and the position
            # signal would be noise.
            current_dealer = get_dealer_index(table)
            next_dealer = _next_occupied_seat(
                table.seats,
                start_after=current_dealer if current_dealer is not None else -1,
            )

            r = play_one_hand(
                table.seats,
                big_blind=big_blind,
                rng=rng,
                sandbox_id=sandbox_id,
                name_for=_name_for_personality(personality_repo),
                starting_dealer_seat_idx=next_dealer,
                bankroll_repo=bankroll_repo,
                chip_ledger_repo=chip_ledger_repo,
                table_id=getattr(table, 'table_id', None),
            )
            if r.delta > 0:
                table.seats = r.new_seats
            # Persist the dealer position on the table state. The
            # subsequent `cash_table_repo.save_table` writes it to the
            # `cash_tables.dealer_idx` column (schema v96), so the
            # rotation survives backend restart. On a no-op hand
            # (table dropped below 2 AIs mid-burst), `dealer_seat_idx`
            # is None — leave the prior value in place so we don't
            # corrupt the position with a "no hand happened" marker.
            if r.dealer_seat_idx is not None:
                table.dealer_idx = r.dealer_seat_idx
            sim_results.append(r)

            # (The casino closing countdown is no longer decremented here.
            # A casino only enters closing once it's empty of fish, so it
            # plays no hands and this hook never fired. The countdown is now
            # ticked once per provisioning resolution instead — see
            # `resolve_casino_provisioning` Pass 2.)

            # Advance detached counters for AI seats now that the hand
            # has resolved (their psychology reflects this hand's events).
            for slot in table.seats:
                if slot.get('kind') != 'ai':
                    continue
                pid = slot.get('personality_id')
                ctrl = controller_cache.get(pid) if pid else None
                if ctrl is None:
                    continue
                psych = getattr(ctrl, 'psychology', None)
                if psych is None:
                    continue
                try:
                    zone = getattr(psych, 'primary_zone', 'neutral')
                except Exception:
                    zone = 'neutral'
                prior = getattr(ctrl, '_detached_hands', 0)
                ctrl._detached_hands = (prior + 1) if zone == 'detached' else 0

            # Per-hand MOVEMENT only (Step 1). Live-fill (Step 2) is now a
            # single GLOBAL greedy pass after this loop
            # (CASH_MODE_TABLE_ATTRACTIVENESS.md §2), so the old per-table
            # fill prep — fish-filtered idle pool, casino/whale ×2 boost +
            # hungry/predator reorders, and the idle-recovery memo — is gone:
            # `_process_global_greedy_fills` subsumes them via
            # `attractiveness` + `hunger` + `W_CROWD` + the seek-rate and its
            # own cooldown/recovery gates.
            per_hand = refresh_table_roster(
                table,
                # Fill is global now (enable_live_fill=False) — idle/eligible
                # pools feed the post-loop greedy pass, not Step 2 here.
                idle_pool=[],
                eligible_candidates=[],
                enable_live_fill=False,
                seated_globally=seated_globally,
                bankroll_lookup=_bankroll_lookup,
                buy_in_lookup=_buy_in_for,
                rng=rng,
                now=now,
                stake_idx=stake_idx,
                table_min_buy_in=table_min_buy_in,
                table_max_buy_in=table_max_buy_in,
                next_tier_min_buy_in=next_tier_min_buy_in,
                psych_lookup=_psych_lookup_sim,
                # Phase 4: intercept forced_leave with take_stake when
                # peer AIs are willing to fund the busting borrower.
                # Wired only when callers pass relationship_repo and
                # stake_repo — None inputs short-circuit the interception
                # back to plain forced_leave inside refresh_table_roster.
                borrower_profile_lookup=(
                    _borrower_lookup_for_table if _take_stake_enabled else None
                ),
                staker_profile_lookup=(_staker_profile_lookup if _take_stake_enabled else None),
                relationship_lookup=(_relationship_lookup if _take_stake_enabled else None),
                stake_label=table.stake_label,
                # Phase 4 Commit 4: cross-table candidate pool. The
                # per-table loop sees the pre-loop snapshot of other
                # tables / idle pool, which is good enough — a staker
                # picked here might have also moved this tick at their
                # own table, but the bankroll lookup re-checks capacity
                # so a now-broke AI wouldn't qualify even if they
                # appeared in this list.
                cross_table_staker_pids=_cross_table_pool_for(
                    table.stake_label,
                    table.table_id,
                ),
                # Phase 4.5 Commit 1: per-staker garnishment for AI
                # borrowers. Only meaningful when stake_repo is wired
                # (else the lookup returns 0 and the cut stays at
                # rate_anchor as before).
                carry_lookup=(_carry_lookup if _take_stake_enabled else None),
                # Staker-incentives plan: weighted candidate selection
                # in find_ai_staker_for. Wired only when stake_repo is
                # available; otherwise the matcher falls back to its
                # legacy uniform-random pick.
                history_lookup=(_history_for if _take_stake_enabled else None),
                starting_bankroll_lookup=(_starting_bankroll_for if _take_stake_enabled else None),
                # Vice-on-leave: roll vice for a discretionary leaver so a
                # winner goes off-grid instead of into the idle pool. None
                # when real vice is off / cast too poor (no interception).
                vice_prob_lookup=_vice_prob_lookup,
            )
            # Carry the post-hand table forward to the next iteration.
            table = per_hand.new_table
            # Refresh idle_pool snapshot from in-memory aggregates so the
            # next iteration's idle-candidate filter sees the latest.
            for ch in per_hand.idle_changes:
                if ch.kind == 'add' and ch.entry is not None:
                    idle_pool = [e for e in idle_pool if e.personality_id != ch.personality_id]
                    idle_pool.append(ch.entry)
                elif ch.kind == 'remove':
                    idle_pool = [e for e in idle_pool if e.personality_id != ch.personality_id]
            agg_decisions.update(per_hand.decisions)
            agg_idle_changes.extend(per_hand.idle_changes)
            agg_bankroll_changes.extend(per_hand.bankroll_changes)
            # Step 1 never live-fills (enable_live_fill=False), so
            # freshly_seated is empty here — the global greedy pass below
            # owns fills and the reseat-recovery persistence for them.
            agg_freshly_seated.extend(per_hand.freshly_seated_personality_ids)
            agg_rebuy_changes.extend(per_hand.rebuy_changes)
            agg_stake_creations.extend(per_hand.stake_creations)
            agg_leave_signals.update(per_hand.leave_signals)
            agg_vice_bound.extend(per_hand.vice_bound)
            # Update the burst-local set so subsequent hands within
            # this same burst can't double-stake the same borrower.
            for sc in per_hand.stake_creations:
                _burst_stake_creation_pids.add(sc.borrower_id)

        # Synthesize a result object that the existing post-loop
        # persistence + event-emission code can consume unchanged.
        result = RosterRefreshResult(
            new_table=table,
            idle_changes=agg_idle_changes,
            freshly_seated_personality_ids=agg_freshly_seated,
            bankroll_changes=agg_bankroll_changes,
            decisions=agg_decisions,
            rebuy_changes=agg_rebuy_changes,
            leave_signals=agg_leave_signals,
            stake_creations=agg_stake_creations,
            vice_bound=agg_vice_bound,
        )
        # Roll up this table's go_vice leavers for the refresh-level
        # commit below (their from_seat credits are applied in-loop, so
        # the bankroll is whole by the time we size the spend).
        all_vice_bound.extend(result.vice_bound)

        # Aspiration-ask: AIs seated at this table after the burst may
        # decide they want to climb a tier without busting. Mutates
        # `result` in place — vacates seats, appends bankroll changes,
        # appends idle-pool changes, creates stake rows via
        # `stake_repo`. The downstream persistence code (save_table,
        # idle_changes loop, from_seat credits) consumes these changes
        # exactly like the bust-stake flow's outputs.
        #
        # Gated on the take_stake plumbing being wired (we need
        # stake_repo + the find_ai_staker_for callbacks). Spec:
        # `docs/plans/CASH_MODE_AI_ASPIRATION_ASK.md` Commit 4.
        if _take_stake_enabled and stake_repo is not None:
            _process_aspiration_asks(
                result=result,
                bankroll_repo=bankroll_repo,
                stake_repo=stake_repo,
                relationship_repo=relationship_repo,
                personality_repo=personality_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                now=now,
                rng=rng,
                staker_profile_lookup=_staker_profile_lookup,
                bankroll_lookup=_bankroll_lookup,
                relationship_lookup=_relationship_lookup,
                history_lookup=_history_for,
                starting_bankroll_lookup=_starting_bankroll_for,
                all_tables=tables,
                idle_pool=idle_pool,
            )

        # Persist the table (always — last_activity_at bumps) and idle
        # pool changes. The dealer button was advanced in real engine-
        # order inside the burst loop above (one rotation per sim hand,
        # synchronized with `play_one_hand`'s starting dealer), so we
        # don't need a separate `advance_dealer` step here.
        cash_table_repo.save_table(result.new_table, sandbox_id=sandbox_id, now=now)
        # SHADOW (Presence cutover Phase 1): mirror the post-burst seat map
        # into `entity_presence`. The reconcile records anyone now seated who
        # wasn't already (e.g. a take_stake reseat) and is a no-op for the
        # already-seated cast. AIs that LEFT this table during the burst are
        # NOT turned into shadow LEAVEs here — that's owned by the idle-pool /
        # hustle / vice writers (migration inventory C–E, out of scope for
        # this lobby-only pass); their idle `save_idle` below is the
        # authoritative record of the departure for now. Additive,
        # flag-gated, best-effort.
        _shadow_reconcile_table(result.new_table, sandbox_id)

        for change in result.idle_changes:
            if change.kind == "add" and change.entry is not None:
                cash_table_repo.save_idle(change.entry, sandbox_id=sandbox_id)
            elif change.kind == "remove":
                cash_table_repo.delete_idle(change.personality_id, sandbox_id=sandbox_id)

        # Phase 4 Commit 3: settle AI-borrower stakes BEFORE the normal
        # from_seat credit fires. When an AI with an active stake row
        # leaves, the chips on their seat need to be split per the
        # stake's `cut` between staker and borrower — not credited
        # whole to the borrower. We compute the settlement flows here
        # and record which `from_seat` BankrollChange index was
        # consumed by the settlement so the from_seat loop below
        # skips ONLY that entry (not all from_seat entries for the
        # same pid — a take_stake earlier in the burst emits its
        # own from_seat for the bust chips, which must still credit).
        settled_from_seat_indices = _settle_table_stakes(
            result,
            stake_repo=stake_repo,
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=chip_ledger_repo,
            relationship_repo=relationship_repo,
            personality_repo=personality_repo,
            sandbox_id=sandbox_id,
            now=now,
        )

        _apply_bankroll_transfers(
            result,
            settled_from_seat_indices=settled_from_seat_indices,
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=chip_ledger_repo,
            sandbox_id=sandbox_id,
            now=now,
        )

        _apply_stake_creations(
            result,
            stake_repo=stake_repo,
            relationship_repo=relationship_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=chip_ledger_repo,
            sandbox_id=sandbox_id,
            now=now,
        )

        # Emit lobby activity events from the refresh result.
        # `decisions` covers AIs that were on the table at the start
        # of the refresh; non-`stay` decisions correspond to leaves.
        # `freshly_seated_personality_ids` covers joins from idle pool
        # or live-fill from the eligible pool.
        _emit_activity_events(
            table=result.new_table,
            previous_table=previous_table_snapshot,
            decisions=result.decisions,
            freshly_seated_personality_ids=result.freshly_seated_personality_ids,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )

        # Burst-aware event emission: pick at most one headline per
        # event type across the whole burst, then add a summary event
        # when hands were compressed. The per-type-per-burst cap is
        # the resolution recorded in the design doc's Q6.
        _emit_burst_events(
            table=result.new_table,
            sim_results=sim_results,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )

        # Predator signal: collect AI seats whose entire net worth is now
        # on this table ($0 reserve — one busted stack from a full crash
        # out). Reserve reflects every chip move applied above (rebuys,
        # leave-credits, stake settlements/creations), so the scan reads
        # the post-refresh truth. Emission is deferred to one dedup'd pass
        # after every table is processed.
        for _pid, _chips in _committed_seated_ais(
            result.new_table,
            reserve_lookup=_bankroll_lookup,
        ).items():
            last_stand_qualifying[_pid] = (
                result.new_table.table_id,
                result.new_table.stake_label,
                result.new_table.name,
            )

        # Refresh idle_pool snapshot so the next iteration sees the
        # updated state (we may have added or removed entries).
        idle_pool = cash_table_repo.list_idle(sandbox_id=sandbox_id)

        out[table.table_id] = result
        # Record pre-burst open seats so the global fill won't re-fill a
        # seat that was vacated during THIS refresh (defer-freshly-vacated).
        _preburst_open = frozenset(
            i for i, s in enumerate(previous_table_snapshot.seats) if s.get("kind") == "open"
        )
        fill_ctx[result.new_table.table_id] = (result, _preburst_open)

    # --- GLOBAL greedy fill (the loop inversion, spec §2) ---
    # Step 1 above ran movement only; now seat idle/eligible AIs across ALL
    # tables at once, each picking its most attractive affordable open table.
    _process_global_greedy_fills(
        fill_ctx=fill_ctx,
        idle_pool=idle_pool,
        eligible=eligible,
        seated_globally=seated_globally,
        fish_ids=_fish_ids,
        bankroll_lookup=_bankroll_lookup,
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        chip_ledger_repo=chip_ledger_repo,
        personality_repo=personality_repo,
        sandbox_id=sandbox_id,
        now=now,
        rng=rng,
        seek_rate=seek_rate,
        human_headroom=human_headroom,
    )

    # Emit the last-stand predator signal for AIs newly committed since
    # the previous refresh. Dedup keeps a steadily-committed seat from
    # re-flooding the ticker every tick; recovered / departed AIs drop
    # out of the announced set and can re-trigger on a future episode.
    newly_committed = _select_new_last_stands(
        sandbox_id,
        set(last_stand_qualifying),
    )
    if newly_committed:
        _emit_last_stand_events(
            candidates={pid: last_stand_qualifying[pid] for pid in newly_committed},
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )

    # Phase 4.5 Commits 3-5: AI-initiated carry resolution. Runs once
    # per lobby refresh, after every table has been processed. Iterates
    # AIs with outstanding carries (single bulk query) and rolls
    # voluntary payoff / forgiveness ask / explicit default per the
    # handoff spec. Best-effort — failures here don't affect the
    # table-refresh side effects above.
    if stake_repo is not None:
        try:
            from cash_mode.ai_carry_resolution import resolve_ai_carries

            def _energy_lookup(pid: str) -> float:
                """Resolve an AI's energy for the explicit-default
                pressure formula. Cache lookup first (active sessions);
                fall back to persisted emotional_state_json for idle
                AIs; neutral 0.5 default for never-played AIs.

                Best-effort — any failure returns 0.5 so the pressure
                math still proceeds with neutral energy. The carry
                resolution surface tolerates this gracefully (other
                pressure signals dominate when energy is unknown).
                """
                from cash_mode.full_sim import _get_default_controller_cache

                cache = _get_default_controller_cache()
                ctrl = cache.get(pid)
                if ctrl is not None:
                    psych = getattr(ctrl, 'psychology', None)
                    if psych is not None:
                        try:
                            return float(getattr(psych, 'energy', 0.5))
                        except Exception:
                            return 0.5
                try:
                    blob = bankroll_repo.load_emotional_state_json(
                        pid,
                        sandbox_id=sandbox_id,
                    )
                except Exception:
                    return 0.5
                if not blob:
                    return 0.5
                try:
                    import json as _json

                    state_dict = _json.loads(blob)
                    return float(state_dict.get('energy', 0.5))
                except Exception:
                    return 0.5

            batch = resolve_ai_carries(
                bankroll_repo=bankroll_repo,
                stake_repo=stake_repo,
                relationship_repo=relationship_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                energy_lookup=_energy_lookup,
                rng=rng,
                now=now,
            )
            _emit_carry_resolution_events(
                batch=batch,
                personality_repo=personality_repo,
                stake_repo=stake_repo,
                now=now,
                sandbox_id=sandbox_id,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] AI carry resolution failed: %s",
                exc,
            )

    # AI vice spending — start pass. Runs after carry resolution so a
    # carry-settling AI in the same refresh isn't immediately whisked
    # off-grid. Candidate set is idle-pool AIs minus already-vicing
    # AIs (the filter above already removed them from idle_pool).
    # Each fire is a sync narration call + ledger entry + state row.
    # Runs BEFORE closed-economy resolution so real-vice deposits land
    # in the bank pool on the same tick they're available for tourist
    # injection / casino seeding (vice_spending is in
    # BANK_POOL_DEPOSIT_REASONS).
    # Sandbox-wide seated pids — the off-grid (vice / side-hustle) passes
    # below draw from `list_idle`, but a seating-path bug could leave a
    # seated AI with a stale idle row (the `seated_and_idle` split-brain).
    # Never send a *seated* AI off-grid: that would compound it into
    # `seated_and_offgrid`. Defense-in-depth — the seat-write paths now
    # clear idle rows, but this guard keeps the invariant locally.
    _seated_pids: Set[str] = {
        slot.get("personality_id")
        for tbl in cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
        for slot in tbl.seats
        if slot.get("kind") == "ai" and slot.get("personality_id")
    }

    vice_starts: list = []
    if (
        vice_mode == 'real'
        and vice_repo is not None
        and sandbox_id is not None
        and chip_ledger_repo is not None
    ):
        try:
            from cash_mode.ai_vice_spending import resolve_ai_vice_spending
            from cash_mode.vice_narration import narrate_vice

            # Idle-only candidates per the design's "sim-seated AIs
            # are deferred" decision (see CASH_MODE_AI_VICE_SPENDING.md).
            # idle_pool was already filtered to exclude on_vice AIs at
            # the top of this function. Refresh from the current idle
            # snapshot so any AIs who entered idle during the table
            # loop are eligible too. Exclude seated AIs (split-brain
            # guard) and fish (a casino-only, pool-funded class that
            # never goes off-grid — mirrors the side-hustle pass).
            current_idle = cash_table_repo.list_idle(sandbox_id=sandbox_id)
            candidates = {
                e.personality_id
                for e in current_idle
                if e.personality_id not in on_vice
                and e.personality_id not in _seated_pids
                and e.personality_id not in _fish_ids
                # Respect the post-vice refractory window here too, so the
                # idle-path and the leave-path share one cooldown.
                and not is_in_vice_cooldown(e.personality_id, now)
            }

            def _vice_narrate(pid, amount, snapshot):
                # Bind the personality_repo so the LLM prompt can
                # include style + anchors + verbal tics. Fail-soft
                # internal to narrate_vice — never raises.
                return narrate_vice(
                    pid,
                    amount,
                    snapshot,
                    personality_repo=personality_repo,
                )

            vice_starts = resolve_ai_vice_spending(
                candidates=candidates,
                vice_repo=vice_repo,
                bankroll_repo=bankroll_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                rng=rng,
                now=now,
                narrate_fn=_vice_narrate,
            )

            # Leave-vice commits: AIs whose discretionary leave a vice roll
            # intercepted (go_vice) went off-grid this refresh. The roll
            # already happened, so this only sizes + commits the spend
            # (debit bankroll → bank pool), bypassing the idle-only scan
            # and its per-refresh cap. from_seat credits were applied in
            # the table loop, so the bankroll is whole. Reuses the same
            # cast-median snapshot built before the loop.
            if all_vice_bound and _vice_cast_median > 0:
                from cash_mode.ai_vice_spending import commit_leave_vice

                for _pid in all_vice_bound:
                    committed = commit_leave_vice(
                        personality_id=_pid,
                        cast_median=_vice_cast_median,
                        vice_repo=vice_repo,
                        bankroll_repo=bankroll_repo,
                        chip_ledger_repo=chip_ledger_repo,
                        sandbox_id=sandbox_id,
                        rng=rng,
                        now=now,
                        narrate_fn=_vice_narrate,
                    )
                    if committed is not None:
                        vice_starts.append(committed)
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] AI vice spending failed: %s",
                exc,
            )

    # Emit vice ticker events for both starts (this refresh) and ends
    # (from the expiry pass at the top). Best-effort — already wrapped
    # in try/except inside the helper.
    if vice_starts or vice_ends:
        try:
            _emit_vice_spending_events(
                starts=vice_starts,
                ends=vice_ends,
                personality_repo=personality_repo,
                now=now,
                sandbox_id=sandbox_id,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] vice event emission failed: %s",
                exc,
            )

    # AI side hustle — start pass (mirror of the vice start pass). Sends
    # broke idle AIs off-grid to earn. Candidate set = idle AIs who can't
    # afford the cheapest buy-in (so they can't sit *anywhere* — casino
    # tables stay the preferred place for anyone who CAN sit), minus any
    # AI already off-grid (on a vice or hustle) and minus fish (a
    # casino-only class that never hustles). The payout lands at expiry,
    # so no chips move here — only the state row + narration.
    hustle_starts: list = []
    if (
        economy_flags.SIDE_HUSTLE_ENABLED
        and side_hustle_repo is not None
        and sandbox_id is not None
    ):
        try:
            from cash_mode.ai_side_hustle import resolve_ai_side_hustle
            from cash_mode.side_hustle_narration import narrate_side_hustle

            # Cheapest buy-in in the lobby = the lowest stake tier's min.
            # An AI projected below this (scaled by its buy-in multiplier)
            # can't sit at any table and is a hustle candidate.
            cheapest_min_buy_in = table_buy_in_window(STAKES_ORDER[0])[1]
            current_idle = cash_table_repo.list_idle(sandbox_id=sandbox_id)
            candidates: Set[str] = set()
            for e in current_idle:
                pid = e.personality_id
                # Same split-brain guard as the vice pass: never send a
                # seated AI off-grid on a hustle (seated_and_offgrid).
                if pid in on_vice or pid in on_hustle or pid in _fish_ids or pid in _seated_pids:
                    continue
                try:
                    projected = bankroll_repo.load_ai_bankroll_current(
                        pid,
                        sandbox_id=sandbox_id,
                        now=now,
                    )
                    knobs = bankroll_repo.load_personality_knobs(pid)
                except Exception:
                    continue
                if projected is None:
                    continue
                threshold = round(cheapest_min_buy_in * knobs.buy_in_multiplier)
                if projected < threshold:
                    candidates.add(pid)

            def _hustle_narrate(pid, amount):
                # Bind personality_repo so the LLM prompt can include
                # style + anchors + verbal tics. Fail-soft internally.
                return narrate_side_hustle(
                    pid,
                    amount,
                    personality_repo=personality_repo,
                )

            if candidates:
                hustle_starts = resolve_ai_side_hustle(
                    candidates=candidates,
                    side_hustle_repo=side_hustle_repo,
                    bankroll_repo=bankroll_repo,
                    sandbox_id=sandbox_id,
                    rng=rng,
                    now=now,
                    narrate_fn=_hustle_narrate,
                )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] AI side hustle failed: %s",
                exc,
            )

    # Emit side-hustle ticker events for both starts (this refresh) and
    # ends (from the expiry pass at the top). Best-effort.
    if hustle_starts or hustle_ends:
        try:
            _emit_side_hustle_events(
                starts=hustle_starts,
                ends=hustle_ends,
                personality_repo=personality_repo,
                now=now,
                sandbox_id=sandbox_id,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] side-hustle event emission failed: %s",
                exc,
            )

    # Closed-economy testbed: fake-vice deposits. Runs only when
    # `vice_mode == 'fake'` (and the pool ledger is present) — the stub
    # vice is sim-only and mutually exclusive with the real vice above, so
    # they can never both drain rich AIs (the `bank_pool_deposit` overlap
    # we removed). Best-effort: a failure doesn't tank the lobby refresh.
    # Spec: `docs/plans/CASH_MODE_CLOSED_ECONOMY.md`.
    if chip_ledger_repo is not None and vice_mode == 'fake':
        try:
            from cash_mode.closed_economy import resolve_closed_economy

            resolve_closed_economy(
                bankroll_repo=bankroll_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                rng=rng,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] closed-economy resolution failed: %s",
                exc,
            )

    # Casino provisioning: spawn `table_type='casino'` tables when the bank
    # pool is fat enough, refill fish, tear them down when fish are busted +
    # pool empty. Gated ONLY on `chip_ledger_repo` (it needs the bank-pool
    # ledger) — NOT on vice_mode. Fish seating is a live-game feature, not a
    # sim testbed one; it was previously nested inside the `vice_mode ==
    # 'fake'` block above and so silently stopped running in production
    # (vice_mode='real'), starving casinos of fish while grinders live-filled
    # every seat. Runs after closed-economy so fresh vice deposits show up in
    # the pool-depth check on the same tick.
    if chip_ledger_repo is not None:
        try:
            from cash_mode.casino_provisioning import resolve_casino_provisioning

            resolve_casino_provisioning(
                cash_table_repo=cash_table_repo,
                bankroll_repo=bankroll_repo,
                personality_repo=personality_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                rng=rng,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] casino provisioning failed: %s",
                exc,
            )

    # Whale provisioning: the $200+ relief gate. A rare, deep pool-funded
    # high roller seated at a real cardroom (lobby) table — the top of the
    # bank-pool dam, replacing the retired $200 casino. Runs after casino
    # provisioning (so the drain-on-exit sweep there has already zeroed any
    # just-departed whale's bankroll and the pool-depth check sees fresh
    # reserves) and surfaces its spawn / wind-down on the ticker. Gated on
    # `chip_ledger_repo` like the casino pass. Best-effort.
    if chip_ledger_repo is not None:
        try:
            from cash_mode.casino_provisioning import resolve_whale_provisioning

            whale_batch = resolve_whale_provisioning(
                cash_table_repo=cash_table_repo,
                bankroll_repo=bankroll_repo,
                personality_repo=personality_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                rng=rng,
                now=now,
            )
            _emit_whale_events(whale_batch, sandbox_id=sandbox_id, now=now)
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] whale provisioning failed: %s",
                exc,
            )

    return out


def _settle_table_stakes(
    result,
    *,
    stake_repo,
    bankroll_repo,
    chip_ledger_repo,
    relationship_repo,
    personality_repo,
    sandbox_id,
    now,
) -> set:
    """Settle active AI-borrower stakes for a table before from_seat credits.

    Extracted verbatim from `refresh_unseated_tables`' per-table loop. For each
    leaving AI with an active stake, splits the seat chips per the stake `cut`
    between staker and borrower, fires repaid/defaulted relationship + ticker
    events, and returns the set of `result.bankroll_changes` `from_seat` indices
    the settlement consumed so the caller's transfer pass skips exactly those
    (and only those) entries. Returns an empty set when `stake_repo` is None,
    preserving the pre-extraction behavior for callers without staking wired.
    """
    settled_from_seat_indices: set = set()
    if stake_repo is None:
        return settled_from_seat_indices
    from cash_mode.activity import AI_STAKE_TICKER_THRESHOLD
    from cash_mode.stake_chip_flow import (
        DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL,
        DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL,
        build_stake_settlement_flows,
    )
    from cash_mode.stake_settlement import settle_stake_on_leave
    from cash_mode.stakes import (
        BORROWER_KIND_PERSONALITY,
        STAKE_STATUS_CARRY,
        STAKER_KIND_HUMAN,
    )

    # Find the LAST from_seat per pid — that's the session-end
    # leave amount (any earlier from_seat for the same pid is
    # the take_stake bust-chips return, which must still
    # credit the bankroll normally).
    last_from_seat_index: Dict[str, int] = {}
    for i, bc in enumerate(result.bankroll_changes):
        if bc.direction == "from_seat":
            last_from_seat_index[bc.personality_id] = i

    for pid, idx in last_from_seat_index.items():
        chips_at_leave = result.bankroll_changes[idx].amount
        # Was this AI a stake borrower? Look up active stake.
        active_stake = stake_repo.load_active_for_borrower(
            pid,
            BORROWER_KIND_PERSONALITY,
        )
        if active_stake is None:
            continue
        settlement = settle_stake_on_leave(
            active_stake.stake_id,
            chips_at_leave,
            stake_repo=stake_repo,
            chip_ledger_repo=chip_ledger_repo,
            ledger_context={
                "site": "ai_session_end",
                "table_id": result.new_table.table_id,
            },
            sandbox_id=sandbox_id,
            now=now,
        )
        if settlement is None:
            continue
        # Apply the settlement flows. For AI-staker / AI-borrower
        # personality stakes the flows are pure bankroll→bankroll
        # transfers — no ledger entry, mirror the route's leave
        # path.
        flows = build_stake_settlement_flows(settlement)
        for flow in flows:
            if flow.direction == DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL:
                # Phase 5 Commit 3 — human-staker branch. When
                # the staker is the player, credit their
                # player_bankroll_state row instead of
                # credit_ai_cash_out (which would mis-route the
                # chips into an AI bankroll). Read-modify-write
                # so concurrent leaves don't lose the credit.
                if flow.staker_kind == STAKER_KIND_HUMAN:
                    from cash_mode.bankroll import PlayerBankrollState

                    existing = bankroll_repo.load_player_bankroll(
                        flow.staker_id,
                    )
                    if existing is not None:
                        bankroll_repo.save_player_bankroll(
                            PlayerBankrollState(
                                player_id=existing.player_id,
                                chips=existing.chips + flow.amount,
                                starting_bankroll=existing.starting_bankroll,
                            ),
                        )
                    else:
                        logger.warning(
                            "[CASH][LOBBY] human staker bankroll "
                            "missing for credit staker=%r stake=%r",
                            flow.staker_id,
                            active_stake.stake_id,
                        )
                else:
                    from cash_mode.bankroll import credit_ai_cash_out

                    credit_ai_cash_out(
                        bankroll_repo,
                        flow.staker_id,
                        flow.amount,
                        sandbox_id=sandbox_id,
                        now=now,
                        chip_ledger_repo=chip_ledger_repo,
                        ledger_context={
                            "stake_id": active_stake.stake_id,
                            "site": "ai_stake_settle_staker",
                        },
                    )
            elif flow.direction == DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL:
                from cash_mode.bankroll import credit_ai_cash_out

                credit_ai_cash_out(
                    bankroll_repo,
                    flow.borrower_id,
                    flow.amount,
                    sandbox_id=sandbox_id,
                    now=now,
                    chip_ledger_repo=chip_ledger_repo,
                    ledger_context={
                        "stake_id": active_stake.stake_id,
                        "site": "ai_stake_settle_borrower",
                    },
                )
        # Fire repaid/defaulted event. Carry rolls forward
        # silently on the relationship axes — only the
        # explicit STAKE_DEFAULTED action would fire the
        # sharper hit; natural carry is just a status='carry'
        # row. Phase 4 Commit 5: when status is carry, emit
        # an EVENT_AI_DEFAULT to the lobby ticker so the
        # player sees the moment-of-default drama even though
        # no axis-shift event fires.
        if (
            relationship_repo is not None
            and settlement.new_status != STAKE_STATUS_CARRY
            and settlement.staker_id is not None
            and settlement.forgiven_amount == 0
        ):
            try:
                from poker.memory import OpponentModelManager
                from poker.memory.relationship_events import (
                    RelationshipEvent,
                )

                mgr = OpponentModelManager(
                    relationship_repo=relationship_repo,
                )
                mgr.record_event(
                    actor_id=settlement.staker_id,
                    target_id=settlement.borrower_id,
                    event=RelationshipEvent.STAKE_REPAID,
                )
            except Exception as exc:
                logger.warning(
                    "[CASH][LOBBY] STAKE_REPAID event failed " "stake=%r: %s",
                    active_stake.stake_id,
                    exc,
                )
        if (
            settlement.new_status == STAKE_STATUS_CARRY
            and settlement.carry_amount >= AI_STAKE_TICKER_THRESHOLD
            and settlement.staker_id is not None
        ):
            try:
                from cash_mode.activity import (
                    EVENT_AI_DEFAULT,
                    LobbyEvent,
                    format_ai_default_message,
                    record_event,
                )

                staker_name = _ticker_name_for(
                    settlement.staker_id,
                    personality_repo,
                )
                borrower_name = _ticker_name_for(
                    settlement.borrower_id,
                    personality_repo,
                )
                if staker_name and borrower_name:
                    record_event(
                        LobbyEvent(
                            type=EVENT_AI_DEFAULT,
                            table_id=result.new_table.table_id,
                            stake_label=active_stake.stake_tier,
                            personality_id=settlement.borrower_id,
                            name=borrower_name,
                            reason=settlement.staker_id,
                            message=format_ai_default_message(
                                borrower_name,
                                staker_name,
                                active_stake.stake_tier,
                                settlement.carry_amount,
                            ),
                            created_at=now.isoformat(),
                            sandbox_id=sandbox_id,
                        )
                    )
            except Exception as exc:
                logger.warning(
                    "[CASH][LOBBY] EVENT_AI_DEFAULT emit failed: %s",
                    exc,
                )
        settled_from_seat_indices.add(idx)
    return settled_from_seat_indices


def _apply_stake_creations(
    result,
    *,
    stake_repo,
    relationship_repo,
    personality_repo,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id,
    now,
) -> None:
    """Apply AI-borrow stake creations recorded on `result`.

    Extracted verbatim from `refresh_unseated_tables`' per-table loop:
    debits each staker's bankroll, persists the Stake row, fires the
    STAKE_OFFERED relationship event, and emits EVENT_AI_STAKE for stakes
    above the ticker threshold. No-op when `result.stake_creations` is empty.
    """
    # Phase 4: apply AI-borrow stake creations. The seat refill
    # was already baked into result.new_table by refresh_table_roster
    # (the borrower's chips moved from chips_at_bust → principal,
    # and the from_seat above credited the borrower's bankroll
    # with chips_at_bust). What remains:
    #   - Debit the staker's bankroll by principal.
    #   - Persist a Stake row (status=active, both kinds personality).
    #   - Fire STAKE_OFFERED so the staker's relationship axes
    #     toward the borrower reflect the new tie.
    #   - Emit EVENT_AI_STAKE on the lobby ticker (Commit 5).
    from cash_mode.activity import AI_STAKE_TICKER_THRESHOLD
    from cash_mode.bankroll import debit_bankroll_for_seat

    if result.stake_creations:
        import uuid

        from cash_mode.stakes import (
            BORROWER_KIND_PERSONALITY,
            STAKE_FORMAT_PURE,
            STAKE_STATUS_ACTIVE,
            STAKER_KIND_PERSONALITY,
            Stake,
        )

        for sc in result.stake_creations:
            debit_bankroll_for_seat(
                bankroll_repo,
                sc.staker_id,
                sc.principal,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
                now=now,
            )
            stake = Stake(
                stake_id=f"ai_stake_{uuid.uuid4().hex[:12]}",
                session_id=f"ai_session_{sc.borrower_id}_{int(now.timestamp())}",
                staker_id=sc.staker_id,
                staker_kind=STAKER_KIND_PERSONALITY,
                borrower_id=sc.borrower_id,
                borrower_kind=BORROWER_KIND_PERSONALITY,
                format=STAKE_FORMAT_PURE,
                principal=sc.principal,
                match_amount=0,
                origination_fee=0,
                cut=sc.cut,
                status=STAKE_STATUS_ACTIVE,
                carry_amount=0,
                stake_tier=sc.stake_label,
                created_at=now,
            )
            if stake_repo is not None:
                stake_repo.create_stake(stake)
            if relationship_repo is not None:
                try:
                    from poker.memory import OpponentModelManager
                    from poker.memory.relationship_events import (
                        RelationshipEvent,
                    )

                    mgr = OpponentModelManager(
                        relationship_repo=relationship_repo,
                    )
                    mgr.record_event(
                        actor_id=sc.staker_id,
                        target_id=sc.borrower_id,
                        event=RelationshipEvent.STAKE_OFFERED,
                    )
                except Exception as exc:
                    logger.warning(
                        "[CASH][LOBBY] STAKE_OFFERED event failed " "staker=%r borrower=%r: %s",
                        sc.staker_id,
                        sc.borrower_id,
                        exc,
                    )
            # Phase 4 Commit 5: emit ticker event for stakes above
            # the threshold so the player sees the AI economy
            # moving. Smaller stakes (at $2/$10) fire silently —
            # state mutates, but the ticker stays focused on
            # higher-stakes drama.
            if sc.principal >= AI_STAKE_TICKER_THRESHOLD:
                try:
                    from cash_mode.activity import (
                        EVENT_AI_STAKE,
                        LobbyEvent,
                        format_ai_stake_message,
                        record_event,
                    )

                    staker_name = _ticker_name_for(
                        sc.staker_id,
                        personality_repo,
                    )
                    borrower_name = _ticker_name_for(
                        sc.borrower_id,
                        personality_repo,
                    )
                    if staker_name and borrower_name:
                        record_event(
                            LobbyEvent(
                                type=EVENT_AI_STAKE,
                                table_id=result.new_table.table_id,
                                stake_label=sc.stake_label,
                                personality_id=sc.staker_id,
                                name=staker_name,
                                reason=sc.borrower_id,
                                message=format_ai_stake_message(
                                    staker_name,
                                    borrower_name,
                                    sc.stake_label,
                                    sc.principal,
                                ),
                                created_at=now.isoformat(),
                                sandbox_id=sandbox_id,
                            )
                        )
                except Exception as exc:
                    logger.warning(
                        "[CASH][LOBBY] EVENT_AI_STAKE emit failed: %s",
                        exc,
                    )


def _apply_bankroll_transfers(
    result,
    *,
    settled_from_seat_indices,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id,
    now,
) -> None:
    """Apply bankroll <-> seat transfers for one table's refresh result.

    Extracted verbatim from `refresh_unseated_tables`' per-table loop.
    `to_seat` changes debit the AI's bankroll (pure transfer); `from_seat`
    changes credit it via credit_ai_cash_out, EXCEPT the indices in
    `settled_from_seat_indices`, which the stake-settlement pass already
    consumed.
    """
    # Apply bankroll ↔ seat transfers (closes the v1.5 lobby-seed
    # leak: live-fill used to mint chips on new seats without
    # deducting from the AI's bankroll). `to_seat` is a pure
    # transfer (no ledger entry); `from_seat` goes through
    # `credit_ai_cash_out` so regen commits and cap-clamp overflow
    # fires a ledger entry.
    from cash_mode.bankroll import (
        credit_ai_cash_out,
        debit_bankroll_for_seat,
    )

    for i, bc in enumerate(result.bankroll_changes):
        if bc.direction == "to_seat":
            debit_bankroll_for_seat(
                bankroll_repo,
                bc.personality_id,
                bc.amount,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
                now=now,
            )
        elif bc.direction == "from_seat":
            # Skip ONLY the specific from_seat entry the
            # settlement consumed; earlier from_seat entries for
            # the same pid (take_stake bust chips) still credit.
            if i in settled_from_seat_indices:
                continue
            credit_ai_cash_out(
                bankroll_repo,
                bc.personality_id,
                bc.amount,
                sandbox_id=sandbox_id,
                now=now,
                chip_ledger_repo=chip_ledger_repo,
                ledger_context={
                    "site": "refresh_table_roster_vacate",
                    "table_id": result.new_table.table_id,
                },
            )


def _ticker_name_for(pid: str, personality_repo) -> Optional[str]:
    """Resolve a personality's display name for lobby ticker events.

    Best-effort: returns None on any lookup failure or missing personality so
    callers can skip emission rather than crash. Hoisted out of
    `refresh_unseated_tables`' per-table loop (it was redefined every iteration)
    so the extracted settlement / stake-creation stage helpers can share it.
    """
    try:
        personality = personality_repo.load_personality_by_id(pid)
    except Exception:
        return None
    if not personality:
        return None
    return personality.get("name") or pid


def _emit_whale_events(whale_batch, *, sandbox_id: Optional[str], now: datetime) -> None:
    """Surface whale spawn / wind-down on the lobby ticker.

    Kept separate from the provisioner (which stays free of the activity
    ring) so emission lives with the rest of lobby.py's ticker hooks.
    Best-effort: a buffer hiccup must not tank the refresh.
    """
    if whale_batch is None:
        return
    try:
        from cash_mode.activity import (
            EVENT_WHALE_ARRIVAL,
            EVENT_WHALE_DEPARTURE,
            LobbyEvent,
            record_event,
        )

        spawn = whale_batch.spawn
        if spawn is not None:
            record_event(
                LobbyEvent(
                    type=EVENT_WHALE_ARRIVAL,
                    table_id=spawn.table_id,
                    stake_label=spawn.stake_label,
                    personality_id=spawn.whale_id,
                    name=spawn.name,
                    reason='',
                    message=f"🐋 {spawn.name} just sat down at {spawn.stake_label}",
                    created_at=now.isoformat(),
                    sandbox_id=sandbox_id,
                )
            )
        teardown = whale_batch.teardown
        if teardown is not None:
            record_event(
                LobbyEvent(
                    type=EVENT_WHALE_DEPARTURE,
                    table_id=teardown.table_id,
                    stake_label=teardown.stake_label,
                    personality_id=teardown.whale_id,
                    name=teardown.name,
                    reason=teardown.reason,
                    message=f"🐋 {teardown.name} cashed out and left {teardown.stake_label}",
                    created_at=now.isoformat(),
                    sandbox_id=sandbox_id,
                )
            )
    except Exception as exc:
        logger.warning("[CASH][LOBBY] whale ticker emit failed: %s", exc)


def _emit_activity_events(
    *,
    table,
    previous_table,
    decisions,
    freshly_seated_personality_ids,
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Push lobby activity events to the in-memory ring buffer.

    Pulls display names from the personality repo. Wrapped in a
    broad except — the ticker is a UX nicety, not a correctness
    surface; if it fails for one event the lobby refresh shouldn't
    abort. Same defensive style as `try/except` around the relationship
    hint lookup in the lobby route.
    """
    from cash_mode.activity import (
        EVENT_JOIN,
        EVENT_LEAVE,
        LobbyEvent,
        format_join_message,
        format_leave_message,
        record_event,
    )

    def _name_for(pid: str) -> Optional[str]:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            return None
        if not personality:
            return None
        return personality.get("name") or pid

    stake = table.stake_label
    table_name = table.name
    ts = now.isoformat()

    for pid, decision in decisions.items():
        if decision == "stay":
            continue
        name = _name_for(pid)
        if not name:
            continue
        try:
            record_event(
                LobbyEvent(
                    type=EVENT_LEAVE,
                    table_id=table.table_id,
                    stake_label=stake,
                    personality_id=pid,
                    name=name,
                    reason=decision,
                    message=format_leave_message(name, stake, decision, table_name),
                    created_at=ts,
                    sandbox_id=sandbox_id,
                )
            )
        except Exception:
            # Buffer is best-effort. Don't let it break the refresh.
            pass

    for pid in freshly_seated_personality_ids:
        name = _name_for(pid)
        if not name:
            continue
        try:
            record_event(
                LobbyEvent(
                    type=EVENT_JOIN,
                    table_id=table.table_id,
                    stake_label=stake,
                    personality_id=pid,
                    name=name,
                    reason="",
                    message=format_join_message(name, stake, table_name),
                    created_at=ts,
                    sandbox_id=sandbox_id,
                )
            )
        except Exception:
            pass


def _emit_last_stand_events(
    *,
    candidates: Dict[str, Tuple[str, str, Optional[str]]],
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Push last-stand (predator-signal) events to the ring buffer.

    `candidates` maps `personality_id -> (table_id, stake_label, table_name)`
    for the AIs newly committed this refresh (already dedup'd by the caller).
    Best-effort, same defensive stance as the other emitters — the
    ticker is UX, never a correctness surface.
    """
    if not candidates:
        return
    from cash_mode.activity import (
        EVENT_LAST_STAND,
        LobbyEvent,
        format_last_stand_message,
        record_event,
    )

    ts = now.isoformat()
    for pid, (table_id, stake_label, table_name) in candidates.items():
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            personality = None
        name = (personality or {}).get("name") if personality else None
        if not name:
            continue
        try:
            record_event(
                LobbyEvent(
                    type=EVENT_LAST_STAND,
                    table_id=table_id,
                    stake_label=stake_label,
                    personality_id=pid,
                    name=name,
                    reason="",
                    message=format_last_stand_message(name, stake_label, table_name),
                    created_at=ts,
                    sandbox_id=sandbox_id,
                )
            )
        except Exception:
            pass


def _name_for_personality(personality_repo) -> Callable[[str], str]:
    """Return a `pid -> display_name` resolver backed by the repo.

    Used by `play_one_hand` so the engine builds controllers with the
    right personality name (the TieredBotController looks up its
    config by name). Falls back to the personality_id on any miss so
    the engine still runs — controllers without a config get the
    default psychology, which is fine for sim purposes.
    """

    def _resolve(pid: str) -> str:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            return pid
        if not personality:
            return pid
        return personality.get("name") or pid

    return _resolve


def _emit_sim_events(
    *,
    table,
    sim_result,
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
    hand_id: Optional[str] = None,
    primary: bool = True,
) -> None:
    """Push paired big_win + big_loss events for a sim hand.

    Both sides are recorded so future filtering (per-personality
    feeds, "show me losses only") works without re-deriving the
    pair. The lobby ticker today shows the most recent N events,
    so the user sees both rows next to each other ("Napoleon won
    $X off Bezos" / "Bezos dropped $X to Napoleon"). Same shape
    as the predecessor fake-sim emission so the event contract on
    the wire is unchanged across the swap.
    """
    from cash_mode.activity import (
        EVENT_BIG_LOSS,
        EVENT_BIG_WIN,
        LobbyEvent,
        format_big_loss_message,
        format_big_win_message,
        record_event,
    )

    winner_pid = sim_result.winner_pid
    loser_pid = sim_result.loser_pid
    if not winner_pid or not loser_pid:
        return

    def _name_for(pid: str) -> Optional[str]:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            return None
        if not personality:
            return None
        return personality.get("name") or pid

    winner_name = _name_for(winner_pid)
    loser_name = _name_for(loser_pid)
    if not winner_name or not loser_name:
        return

    stake = table.stake_label
    ts = now.isoformat()
    delta = int(sim_result.delta)

    try:
        record_event(
            LobbyEvent(
                type=EVENT_BIG_WIN,
                table_id=table.table_id,
                stake_label=stake,
                personality_id=winner_pid,
                name=winner_name,
                reason=loser_pid,  # opponent id for frontend grouping
                message=format_big_win_message(winner_name, loser_name, stake, delta, table.name),
                created_at=ts,
                sandbox_id=sandbox_id,
                hand_id=hand_id,
                primary=primary,
            )
        )
        record_event(
            LobbyEvent(
                type=EVENT_BIG_LOSS,
                table_id=table.table_id,
                stake_label=stake,
                personality_id=loser_pid,
                name=loser_name,
                reason=winner_pid,
                message=format_big_loss_message(loser_name, winner_name, stake, delta, table.name),
                created_at=ts,
                sandbox_id=sandbox_id,
                hand_id=hand_id,
                primary=primary,
            )
        )
    except Exception:
        # Buffer is best-effort.
        pass


def _emit_burst_events(
    *,
    table,
    sim_results: List[HandSimResult],
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Emit at most one event per type across a catch-up burst.

    The lobby ticker shows a small window; bursting 25 hands could
    flood it with 25 big_win events from one table and bury every
    other movement signal. Design Q6 (doc 2026-05-19) picked the
    per-burst per-table cap: at most one big_win/big_loss, one
    all_in, and one bust per refresh per table, plus an aggregate
    summary event when hands were compressed.

    Selection: the headline big_win across the burst is the hand
    with the largest delta. The headline all_in / bust are the first
    such events in the burst (chronological order — the user-facing
    framing reads "X shoved" once, not "X shoved 4 times").
    """
    if not sim_results:
        return

    # Single live hand (the common case): collapse the hand's beats into
    # ONE composed primary line so the feed reads as a coherent sentence
    # ("X shoved all-in and won $Y, busting Z") instead of a mis-ordered
    # win/all-in/bust cluster. The atomic events are still recorded
    # (primary=False) for per-AI filtering. The multi-hand burst path below
    # stays compressed — its events span different hands and can't honestly
    # be joined into one sentence.
    if len(sim_results) == 1:
        r = sim_results[0]
        hand_id = f"{table.table_id}:{now.isoformat()}"
        if r.big_event:
            _emit_sim_events(
                table=table,
                sim_result=r,
                personality_repo=personality_repo,
                now=now,
                sandbox_id=sandbox_id,
                hand_id=hand_id,
                primary=False,
            )
        if r.hand_events:
            _emit_hand_events(
                table=table,
                sim_result=r,
                personality_repo=personality_repo,
                now=now,
                sandbox_id=sandbox_id,
                hand_id=hand_id,
                primary=False,
            )
        _emit_hand_summary(
            table=table,
            sim_result=r,
            personality_repo=personality_repo,
            now=now,
            hand_id=hand_id,
            sandbox_id=sandbox_id,
        )
        return

    # Pick the biggest big_event hand for the headline win/loss
    # emission. None when no hand in the burst crossed threshold.
    headline_big: Optional[HandSimResult] = None
    for r in sim_results:
        if not r.big_event:
            continue
        if headline_big is None or r.delta > headline_big.delta:
            headline_big = r

    if headline_big is not None:
        _emit_sim_events(
            table=table,
            sim_result=headline_big,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )

    # Aggregate hand-level events across the burst, dedup'd by type.
    # `_emit_hand_events` already caps to one per type per call; we
    # just pass it the union of every burst hand's events.
    from cash_mode.full_sim import HandSimResult as _HSR

    aggregated_events = []
    for r in sim_results:
        aggregated_events.extend(r.hand_events)
    if aggregated_events:
        synthetic = _HSR(
            new_seats=sim_results[-1].new_seats,
            hand_events=aggregated_events,
        )
        _emit_hand_events(
            table=table,
            sim_result=synthetic,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )

    # Summary event when more than one hand fired. Drops a single
    # "...and N more hands" line so the user knows the world ticked.
    if len(sim_results) > 1:
        _emit_burst_summary(
            table=table,
            sim_results=sim_results,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )


def _emit_burst_summary(
    *,
    table,
    sim_results: List[HandSimResult],
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Emit a single summary event for a multi-hand burst.

    "Top leader" is the personality with the largest cumulative net
    delta across the burst. When the burst was chip-neutral for
    everyone (rare — would need every hand to be near-zero), the
    summary degenerates to a plain hand-count phrase.
    """
    from cash_mode.activity import (
        EVENT_BURST_SUMMARY,
        LobbyEvent,
        format_burst_summary_message,
        record_event,
    )

    net_by_pid: Dict[str, int] = {}
    for r in sim_results:
        if not r.winner_pid or not r.loser_pid:
            continue
        net_by_pid[r.winner_pid] = net_by_pid.get(r.winner_pid, 0) + r.delta
        net_by_pid[r.loser_pid] = net_by_pid.get(r.loser_pid, 0) - r.delta

    top_pid: Optional[str] = None
    top_delta = 0
    for pid, net in net_by_pid.items():
        if abs(net) > abs(top_delta):
            top_pid = pid
            top_delta = net

    top_name: Optional[str] = None
    if top_pid:
        try:
            personality = personality_repo.load_personality_by_id(top_pid)
            if personality:
                top_name = personality.get("name") or top_pid
        except Exception:
            top_name = top_pid

    try:
        record_event(
            LobbyEvent(
                type=EVENT_BURST_SUMMARY,
                table_id=table.table_id,
                stake_label=table.stake_label,
                personality_id=top_pid or "",
                name=top_name or "",
                reason="",
                message=format_burst_summary_message(
                    stake_label=table.stake_label,
                    hands=len(sim_results),
                    top_name=top_name,
                    top_net_delta=top_delta,
                    table_name=table.name,
                ),
                created_at=now.isoformat(),
                sandbox_id=sandbox_id,
            )
        )
    except Exception:
        pass


def _emit_hand_events(
    *,
    table,
    sim_result,
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
    hand_id: Optional[str] = None,
    primary: bool = True,
) -> None:
    """Translate `HandSimResult.hand_events` into `LobbyEvent`s.

    Per the design doc's resolved Q6, hand-level events use a
    per-burst per-table cap so a catch-up burst (Commit 5) of 25
    hands can't blow past 25 events for one table. v1 here emits
    AT MOST one event per type per table per refresh — `seen_types`
    enforces the cap. Commit 5 will replace this single-call cap
    with a burst-aware cap that operates across the whole hand
    sequence; today the per-tick cap is already in effect because
    only one hand fires per refresh.
    """
    from cash_mode.activity import (
        EVENT_ALL_IN,
        EVENT_BUST,
        LobbyEvent,
        format_all_in_message,
        format_bust_message,
        record_event,
    )
    from cash_mode.full_sim import HAND_EVENT_ALL_IN, HAND_EVENT_BUST

    def _name_for(pid: str) -> Optional[str]:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            return None
        if not personality:
            return None
        return personality.get("name") or pid

    stake = table.stake_label
    ts = now.isoformat()
    seen_types: Set[str] = set()

    for evt in sim_result.hand_events:
        if evt.type in seen_types:
            continue
        name = _name_for(evt.personality_id)
        if not name:
            continue

        if evt.type == HAND_EVENT_ALL_IN:
            opponent_name = _name_for(evt.opponent_pid) if evt.opponent_pid else None
            try:
                record_event(
                    LobbyEvent(
                        type=EVENT_ALL_IN,
                        table_id=table.table_id,
                        stake_label=stake,
                        personality_id=evt.personality_id,
                        name=name,
                        reason=evt.opponent_pid or "",
                        message=format_all_in_message(name, stake, opponent_name, table.name),
                        created_at=ts,
                        sandbox_id=sandbox_id,
                        hand_id=hand_id,
                        primary=primary,
                    )
                )
                seen_types.add(evt.type)
            except Exception:
                pass

        elif evt.type == HAND_EVENT_BUST:
            try:
                record_event(
                    LobbyEvent(
                        type=EVENT_BUST,
                        table_id=table.table_id,
                        stake_label=stake,
                        personality_id=evt.personality_id,
                        name=name,
                        reason=evt.opponent_pid or "",
                        message=format_bust_message(name, stake, table.name),
                        created_at=ts,
                        sandbox_id=sandbox_id,
                        hand_id=hand_id,
                        primary=primary,
                    )
                )
                seen_types.add(evt.type)
            except Exception:
                pass


def _emit_hand_summary(
    *,
    table,
    sim_result,
    personality_repo,
    now: datetime,
    hand_id: str,
    sandbox_id: Optional[str] = None,
) -> None:
    """Emit the ONE composed primary line summarizing a single sim hand.

    Headline priority: a big-pot win (folding in the winner's shove and the
    loser's bust) → a bust → a lone all-in shove. Reuses the atomic event
    `type` (big_win / all_in / bust) so the ticker picks the right icon; the
    `primary=False` atomic copies emitted alongside carry the structured
    data for filtering. Emits nothing when the hand had no notable beat
    (matching the prior behavior where no atomic event would have fired).
    """
    from cash_mode.activity import (
        EVENT_ALL_IN,
        EVENT_BIG_WIN,
        EVENT_BUST,
        LobbyEvent,
        format_all_in_message,
        format_hand_summary_message,
        record_event,
    )
    from cash_mode.full_sim import HAND_EVENT_ALL_IN, HAND_EVENT_BUST

    def _name_for(pid: Optional[str]) -> Optional[str]:
        if not pid:
            return None
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            return None
        if not personality:
            return None
        return personality.get("name") or pid

    stake = table.stake_label
    winner_pid = sim_result.winner_pid
    loser_pid = sim_result.loser_pid
    busted_pids = [e.personality_id for e in sim_result.hand_events if e.type == HAND_EVENT_BUST]
    allin_pids = {e.personality_id for e in sim_result.hand_events if e.type == HAND_EVENT_ALL_IN}

    winner_name = _name_for(winner_pid)
    loser_name = _name_for(loser_pid)
    busted_names = [n for n in (_name_for(p) for p in busted_pids) if n]
    delta = int(sim_result.delta)

    etype: Optional[str] = None
    subject_pid: Optional[str] = None
    opponent_pid = ""
    message = ""

    if sim_result.big_event and winner_name and winner_pid:
        winner_shoved = winner_pid in allin_pids
        etype = EVENT_ALL_IN if winner_shoved else EVENT_BIG_WIN
        subject_pid = winner_pid
        opponent_pid = loser_pid or ""
        message = format_hand_summary_message(
            winner=winner_name,
            loser=loser_name,
            amount=delta,
            stake_label=stake,
            winner_shoved=winner_shoved,
            busted_names=busted_names,
            table_name=table.name,
        )
    elif busted_names:
        etype = EVENT_BUST
        subject_pid = busted_pids[0]
        opponent_pid = winner_pid or ""
        message = format_hand_summary_message(
            winner=winner_name,
            loser=None,
            amount=0,
            stake_label=stake,
            winner_shoved=False,
            busted_names=busted_names,
            table_name=table.name,
        )
    elif allin_pids:
        shover_pid = next(iter(allin_pids))
        shover_name = _name_for(shover_pid)
        if shover_name:
            etype = EVENT_ALL_IN
            subject_pid = shover_pid
            opponent = loser_pid if shover_pid == winner_pid else winner_pid
            opponent_pid = opponent or ""
            message = format_all_in_message(shover_name, stake, _name_for(opponent), table.name)

    if not (etype and subject_pid and message):
        return

    try:
        record_event(
            LobbyEvent(
                type=etype,
                table_id=table.table_id,
                stake_label=stake,
                personality_id=subject_pid,
                name=_name_for(subject_pid) or subject_pid,
                reason=opponent_pid,
                message=message,
                created_at=now.isoformat(),
                sandbox_id=sandbox_id,
                hand_id=hand_id,
                primary=True,
            )
        )
    except Exception:
        pass


def _emit_carry_resolution_events(
    *,
    batch,
    personality_repo,
    stake_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Translate a CarryResolutionBatch into LobbyEvents.

    Phase 4.5 Commits 3-5 — surfaces payoff / forgiveness / default
    outcomes to the lobby ticker. Threshold-gated by
    `AI_CARRY_TICKER_THRESHOLD` (mirror of `AI_STAKE_TICKER_THRESHOLD`)
    so small-stake resolutions stay invisible. Refused forgiveness
    asks are intentionally silent — the axis shift is enough drama;
    every refusal in the ticker would be noise.

    Best-effort: ring-buffer failures don't propagate.
    """
    if not batch or not batch.results:
        return

    from cash_mode.activity import (
        AI_CARRY_TICKER_THRESHOLD,
        EVENT_AI_DEFAULT,
        EVENT_AI_FORGIVEN,
        EVENT_AI_PAYOFF,
        EVENT_AI_REQUESTS_FORGIVENESS,
        LobbyEvent,
        format_ai_explicit_default_message,
        format_ai_forgiven_message,
        format_ai_payoff_message,
        format_ai_requests_forgiveness_message,
        record_event,
    )

    def _name_for(pid: str) -> Optional[str]:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            return None
        if not personality:
            return None
        return personality.get("name") or pid

    ts = now.isoformat()

    for result in batch.results:
        if result.amount < AI_CARRY_TICKER_THRESHOLD:
            continue
        borrower_name = _name_for(result.borrower_id)
        staker_name = _name_for(result.staker_id)
        if not borrower_name or not staker_name:
            continue

        if result.kind == 'payoff':
            event_type = EVENT_AI_PAYOFF
            message = format_ai_payoff_message(
                borrower_name,
                staker_name,
                result.stake_tier,
                result.amount,
            )
            actor_pid = result.borrower_id
            counterparty_pid = result.staker_id
            actor_name = borrower_name
        elif result.kind == 'forgiven':
            event_type = EVENT_AI_FORGIVEN
            message = format_ai_forgiven_message(
                staker_name,
                borrower_name,
                result.stake_tier,
                result.amount,
            )
            # The staker is the actor in a grant (they chose to forgive),
            # so the event indexes by staker_id. Mirrors how `ai_stake`
            # uses the staker as the actor and the borrower as `reason`.
            actor_pid = result.staker_id
            counterparty_pid = result.borrower_id
            actor_name = staker_name
        elif result.kind == 'default':
            event_type = EVENT_AI_DEFAULT
            message = format_ai_explicit_default_message(
                borrower_name,
                staker_name,
                result.stake_tier,
                result.amount,
            )
            actor_pid = result.borrower_id
            counterparty_pid = result.staker_id
            actor_name = borrower_name
        elif result.kind == 'forgiveness_pending':
            # v110 — AI is asking the human staker for forgiveness.
            # Surfaces alongside the wallet badge so the player sees
            # the ask landed even if they're not watching the drawer.
            event_type = EVENT_AI_REQUESTS_FORGIVENESS
            message = format_ai_requests_forgiveness_message(
                borrower_name,
                result.stake_tier,
                result.amount,
            )
            actor_pid = result.borrower_id
            counterparty_pid = result.staker_id  # the human owner_id
            actor_name = borrower_name
        else:
            # forgiveness_refused — silent on the ticker by design.
            continue

        try:
            record_event(
                LobbyEvent(
                    type=event_type,
                    table_id="",  # carry resolutions aren't table-scoped
                    stake_label=result.stake_tier,
                    personality_id=actor_pid,
                    name=actor_name,
                    reason=counterparty_pid,
                    message=message,
                    created_at=ts,
                    sandbox_id=sandbox_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] carry resolution event emit failed (%s): %s",
                result.kind,
                exc,
            )


def _emit_vice_spending_events(
    *,
    starts,
    ends,
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Translate vice-start / vice-end results into LobbyEvents.

    Vice events don't pivot on a table — vice is a between-tables
    activity — so `table_id` and `stake_label` are empty. The
    narration is the message for `vice_start`; `vice_end` uses a
    short return phrase. `reason` carries the duration bucket
    ('short' / 'medium' / 'long') so the frontend can render
    bucket-specific accents if it wants.

    Per the design's `VICE_STARTS_PER_REFRESH` cap, `starts` is
    already bounded at the dispatcher layer — we emit every entry
    in it. Ends are unbounded but cheap (no LLM); we emit them all.

    Best-effort: ring-buffer failures don't propagate.
    """
    if not starts and not ends:
        return

    from cash_mode.activity import (
        EVENT_VICE_END,
        EVENT_VICE_START,
        LobbyEvent,
        format_vice_end_message,
        format_vice_start_message,
        record_event,
    )

    def _name_for(pid: str) -> str:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            personality = None
        if personality and personality.get("name"):
            return personality["name"]
        return pid

    ts = now.isoformat()

    for s in starts:
        name = _name_for(s.personality_id)
        try:
            record_event(
                LobbyEvent(
                    type=EVENT_VICE_START,
                    table_id="",
                    stake_label="",
                    personality_id=s.personality_id,
                    name=name,
                    reason=s.duration_bucket,
                    message=format_vice_start_message(name, s.narration),
                    created_at=ts,
                    sandbox_id=sandbox_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] vice start event emit failed pid=%r: %s",
                s.personality_id,
                exc,
            )

    for e in ends:
        name = _name_for(e.personality_id)
        try:
            record_event(
                LobbyEvent(
                    type=EVENT_VICE_END,
                    table_id="",
                    stake_label="",
                    personality_id=e.personality_id,
                    name=name,
                    reason=e.duration_bucket,
                    message=format_vice_end_message(name),
                    created_at=ts,
                    sandbox_id=sandbox_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] vice end event emit failed pid=%r: %s",
                e.personality_id,
                exc,
            )


def _emit_side_hustle_events(
    *,
    starts,
    ends,
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Translate side-hustle start / end results into LobbyEvents.

    Mirror of `_emit_vice_spending_events`. Like vice, the hustle is a
    between-tables activity, so `table_id` / `stake_label` are empty and
    `reason` carries the duration bucket. The start message is the
    narration; the end message surfaces the pool-funded payout
    ("{name} is back with $X") or the terse phrase when the pool was dry.

    `starts` is already bounded at the dispatcher (HUSTLE_STARTS_PER_REFRESH);
    ends are unbounded but cheap. Best-effort: ring-buffer failures don't
    propagate.
    """
    if not starts and not ends:
        return

    from cash_mode.activity import (
        EVENT_HUSTLE_END,
        EVENT_HUSTLE_START,
        LobbyEvent,
        format_hustle_end_message,
        format_hustle_start_message,
        record_event,
    )

    def _name_for(pid: str) -> str:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            personality = None
        if personality and personality.get("name"):
            return personality["name"]
        return pid

    ts = now.isoformat()

    for s in starts:
        name = _name_for(s.personality_id)
        try:
            record_event(
                LobbyEvent(
                    type=EVENT_HUSTLE_START,
                    table_id="",
                    stake_label="",
                    personality_id=s.personality_id,
                    name=name,
                    reason=s.duration_bucket,
                    message=format_hustle_start_message(name, s.narration),
                    created_at=ts,
                    sandbox_id=sandbox_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] hustle start event emit failed pid=%r: %s",
                s.personality_id,
                exc,
            )

    for e in ends:
        name = _name_for(e.personality_id)
        try:
            record_event(
                LobbyEvent(
                    type=EVENT_HUSTLE_END,
                    table_id="",
                    stake_label="",
                    personality_id=e.personality_id,
                    name=name,
                    reason=e.duration_bucket,
                    message=format_hustle_end_message(name, e.paid_amount),
                    created_at=ts,
                    sandbox_id=sandbox_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] hustle end event emit failed pid=%r: %s",
                e.personality_id,
                exc,
            )


def _emit_session_event(cash_session_repo, session_id, event, **kwargs) -> None:
    """Best-effort wrapper over `cash_session_repo.record_event` (Tier 3).

    Tolerates a repo without `record_event` (older test fakes) and never
    raises — lifecycle telemetry must not break a sweep.
    """
    record = getattr(cash_session_repo, "record_event", None)
    if record is None:
        return
    try:
        record(session_id, event, **kwargs)
    except Exception:
        logger.debug("[CASH][LOBBY] session event %r/%r emit failed", session_id, event)


# Default "abandoned" threshold for the boot/watchdog sweep. 4h, not
# 30m: a cash row is only reaped (settled at chips=0) once it's gone
# untouched this long, so a player who steps away for lunch — or whose
# session is frozen across a reboot ("resume on reboot is by design") —
# doesn't get their table stack burned. Codex review #1.
DEFAULT_STALE_TTL_SECONDS = 14400


def _settle_orphan_seat_to_bankroll(
    *,
    game_id: str,
    owner_id: Optional[str],
    sandbox_id: Optional[str],
    bankroll_repo,
    chip_ledger_repo,
    now: datetime,
) -> int:
    """Settle a non-empty human seat balance back to the owner's bankroll
    BEFORE its games row is deleted — the structural chip-custody guarantee
    (CASH_MODE_CHIP_CUSTODY, Phase 3 / D2).

    A reaped row is normally a closed/busted/orphan session whose seat is empty
    (the cash-out already returned the chips). But a sit that committed a buy-in
    (`player_buy_in`: player→seat) and then errored before its session row
    landed leaves chips recorded in `seat:<game_id>` with no cash-out. Deleting
    that row would strand those chips — the silent-forfeiture bug class. So we
    settle the seat balance back to the bankroll (a `player_cash_out` transfer +
    a bankroll credit) instead of zeroing it. A seat balance can leave ONLY via
    a settlement transfer; nothing may zero a non-empty seat.

    Returns the chips recovered (0 when custody is off, the seat is empty, or
    there's no bankroll row to settle into — in the last case the balance is
    LEFT in the ledger, never forfeited, and flagged for an operator).
    """
    from cash_mode import economy_flags

    if (
        not economy_flags.CHIP_CUSTODY_ENABLED
        or chip_ledger_repo is None
        or bankroll_repo is None
        or not owner_id
    ):
        return 0
    from core.economy import ledger as chip_ledger

    # game_id is globally unique, so the seat account's rows are all one
    # sandbox — sum across (None) is safe and avoids a sandbox mismatch.
    bal = chip_ledger_repo.balance_of(chip_ledger.seat(game_id), sandbox_id=None)
    if bal <= 0:
        return 0
    state = bankroll_repo.load_player_bankroll(owner_id)
    if state is None:
        logger.warning(
            "[CASH LIFECYCLE] settle-before-delete: seat %r holds %d chips but "
            "owner %r has no bankroll row — LEAVING the balance in the ledger "
            "(not forfeiting); needs operator attention",
            game_id, bal, owner_id,
        )
        return 0
    from cash_mode.bankroll import PlayerBankrollState

    bankroll_repo.save_player_bankroll(
        PlayerBankrollState(
            player_id=state.player_id,
            chips=state.chips + bal,
            starting_bankroll=state.starting_bankroll,
        )
    )
    chip_ledger.record_player_cash_out(
        chip_ledger_repo,
        owner_id=owner_id,
        game_id=game_id,
        amount=bal,
        context={'site': 'boot_sweep_settle', 'game_id': game_id},
        sandbox_id=sandbox_id,
    )
    logger.info(
        "[CASH][LOBBY] settle-before-delete recovered %d chips for owner %r "
        "from seat %r (chip forfeiture prevented)",
        bal, owner_id, game_id,
    )
    return bal


def _boot_sweep_stale_cash_rows(
    *,
    game_repo,
    cash_session_repo,
    stake_repo=None,
    chip_ledger_repo=None,
    bankroll_repo=None,
    cash_table_repo=None,
    entity_presence_repo=None,
    stale_ttl_seconds: int,
    now: datetime,
    skip_game_ids: Optional[Set[str]] = None,
    source: str = "boot",
    game_state_service=None,
) -> int:
    """Sweep abandoned `cash-*` rows whose `updated_at` is past the TTL.

    `source` ("boot" | "watchdog") tags the close reason + lifecycle
    event so ops can tell a reboot reconcile from a between-reboots
    watchdog reap.

    Resume-on-reboot is by design: a *fresh* cash row (touched within
    `stale_ttl_seconds`) is left intact so the player reconnects to
    their frozen table. But a row nobody has touched in a long time is
    an abandoned orphan — leaving it makes `_find_active_cash_game_id`
    surface it forever and the sit guard 409s every new sit. This is
    the convergence backstop (T2.2): boot is where partial/abandoned
    state gets resolved instead of lingering.

    For each stale row we ghost-clean (the chips at the table are
    notional at boot — last hand-boundary sync — and the bankroll
    already reflects the buy-in debit):

      1. Settle any *active* stake at chips=0 (full-bust path:
         personality stakes carry the principal, house stakes forgive)
         so the staker's debited principal doesn't dangle un-resolved.
         Idempotent — `settle_stake_on_leave` no-ops on a non-active
         stake. Skipped when `stake_repo` isn't provided.
      2. Finalise the `cash_sessions` row as `closed_status='boot_swept'`
         (the repo's `ended_at IS NULL` guard makes this idempotent).
      3. Delete the `games` row so the sit guard stops 409-ing.

    Returns the number of rows swept. Best-effort per row — one bad row
    doesn't abort the rest of the sweep.
    """
    from cash_mode.cash_sessions import (
        CLOSED_STATUS_BOOT_SWEPT,
        CLOSED_STATUS_STALE_SWEPT,
        SESSION_STATE_BROKEN,
        SESSION_STATES_BLOCKING,
    )

    closed_status = CLOSED_STATUS_STALE_SWEPT if source == "watchdog" else CLOSED_STATUS_BOOT_SWEPT

    swept = 0
    try:
        rows = game_repo.list_games(owner_id=None, limit=10000, offset=0)
    except Exception as e:
        logger.warning("[CASH][LOBBY] boot sweep list_games failed: %s", e)
        return 0

    skip_game_ids = skip_game_ids or set()
    for row in rows:
        if not row.game_id.startswith("cash-"):
            continue
        if row.game_id in skip_game_ids:
            # In-memory / actively-played game — never sweep it. Deleting
            # the DB row out from under a live in-memory copy just gets
            # re-saved on the next tick (the resurrection race), and the
            # player may still be at the table. The watchdog passes the
            # live cash-game ids here.
            continue
        try:
            age = (now - row.updated_at).total_seconds()
        except Exception:
            # Unparseable timestamp — treat as stale (safer than
            # leaving a row we can't reason about wedged forever).
            age = stale_ttl_seconds + 1
        if age <= stale_ttl_seconds:
            continue  # fresh → resumable, leave it alone

        # Close the resurrection race (Codex review #2): a stale row can be
        # warm-loaded / resumed into memory between the skip-set snapshot
        # (built by the watchdog) and this delete. Acquire the SAME
        # per-game lock the leave + cold-load paths use, then re-check
        # in-memory presence under it; if the game is now live, skip it —
        # deleting its row out from under a live in-memory copy is the
        # split-brain the whole hardening effort fights. On boot the map
        # is empty so the re-check is a no-op (and game_state_service may
        # be None for older callers → nullcontext, no lock).
        lock_ctx = (
            game_state_service.get_game_lock(row.game_id)
            if game_state_service is not None
            else nullcontext()
        )
        try:
            with lock_ctx:
                if (
                    game_state_service is not None
                    and game_state_service.get_game(row.game_id) is not None
                ):
                    continue  # resumed into memory since the snapshot — don't sweep
                session = cash_session_repo.load(row.game_id) if cash_session_repo else None
                # FREEZE-FOREVER GUARD (CASH_MODE_STATE_MODEL.md §5.4, §10 Cut 1).
                # Never sweep a session the player can still resume. A frozen
                # (active/paused/abandoning) session IS the player's sacred table —
                # zeroing its chips (final_chips_at_table=0, player_take_home=0) and
                # deleting its games row is the silent-forfeiture bug that destroyed
                # real buy-ins. Leave it wholly intact: the sit guard already treats
                # a blocking session as the one resumable table per owner (by
                # design), and the games row is durable hand history (kept forever).
                # Only genuinely-dead rows — closed/broken sessions, or sessionless
                # orphans from a sit that errored before its row landed — fall
                # through to the cleanup below, and those carry no live chips.
                if session is not None and session.session_state in SESSION_STATES_BLOCKING:
                    logger.debug(
                        "[CASH][LOBBY] %s sweep skipped resumable session %r "
                        "(state=%s, age=%.0fs) — frozen table preserved",
                        source,
                        row.game_id,
                        session.session_state,
                        age,
                    )
                    continue
                # 1. Resolve any active stake so its principal doesn't dangle.
                if stake_repo is not None:
                    active_stake = stake_repo.load_active_for_session(row.game_id)
                    if active_stake is not None:
                        from cash_mode.stake_settlement import settle_stake_on_leave

                        settle_stake_on_leave(
                            active_stake.stake_id,
                            0,  # notional: treat an abandoned table as a bust
                            stake_repo=stake_repo,
                            chip_ledger_repo=chip_ledger_repo,
                            ledger_context={'game_id': row.game_id, 'site': 'boot_sweep'},
                            sandbox_id=session.sandbox_id if session else None,
                            now=now,
                        )
                # 1b. STRUCTURAL settle-before-delete (chip-custody Phase 3):
                # if the human seat account still holds chips (an orphan sit
                # that committed a buy-in but never cashed out), settle them
                # back to the bankroll rather than zeroing them on delete.
                recovered = _settle_orphan_seat_to_bankroll(
                    game_id=row.game_id,
                    owner_id=row.owner_id,
                    sandbox_id=session.sandbox_id if session else None,
                    bankroll_repo=bankroll_repo,
                    chip_ledger_repo=chip_ledger_repo,
                    now=now,
                )
                # 1c. Presence half (R3a): open the human's persisted seat so the
                # deleted row can't leave a ghost seat (save_table drives
                # GO_OFFLINE under authority). Makes _free_ghost_human_seats'
                # orphan unrepresentable at this — the documented — source.
                _sweep_sb = session.sandbox_id if session else None
                if cash_table_repo is not None and row.owner_id and _sweep_sb:
                    from cash_mode.presence_sweep import free_human_seat_on_delete

                    free_human_seat_on_delete(
                        owner_id=row.owner_id,
                        sandbox_id=_sweep_sb,
                        repos={
                            "cash_table_repo": cash_table_repo,
                            "entity_presence_repo": entity_presence_repo,
                        },
                    )
                # 2. Mark the session closed (idempotent via ended_at guard).
                # `player_take_home`/`final_chips_at_table` reflect any chips the
                # settle just returned — the record is no longer a flat zero when
                # chips were actually recovered.
                if session is not None and session.ended_at is None:
                    cash_session_repo.finalise(
                        session.session_id,
                        ended_at=now,
                        final_chips_at_table=recovered,
                        sponsor_repaid=0,
                        player_take_home=recovered,
                        hands_played=session.hands_played or 0,
                        hands_won=session.hands_won or 0,
                        biggest_pot_won=session.biggest_pot_won or 0,
                        duration_seconds=0,
                        closed_status=closed_status,
                    )
                # 3. Drop the games row so the sit guard stops blocking sits.
                game_repo.delete_game(row.game_id)
                swept += 1
            # Tier 3: lifecycle telemetry.
            _emit_session_event(
                cash_session_repo,
                row.game_id,
                "swept",
                owner_id=row.owner_id,
                sandbox_id=session.sandbox_id if session else None,
                detail={"source": source, "age_seconds": int(age)},
                now=now,
            )
            logger.info(
                "[CASH][LOBBY] %s-swept stale orphan cash row %r "
                "(age=%.0fs > ttl=%ds, owner=%r)",
                source,
                row.game_id,
                age,
                stale_ttl_seconds,
                row.owner_id,
            )
        except Exception as e:
            logger.warning(
                "[CASH][LOBBY] %s sweep failed for %r: %s",
                source,
                row.game_id,
                e,
            )
            # Convergence: a row we couldn't sweep is marked `broken` so
            # the sit guard stops treating it as an active session (it
            # won't wedge new sits) even though its games row lingers.
            try:
                cash_session_repo.set_session_state(row.game_id, SESSION_STATE_BROKEN)
                # Alertable signal (PRH-28): the `[CASH LIFECYCLE]` prefix is
                # in alerting._PREFIXES, so a marked-broken session pages the
                # webhook. `broken` shouldn't normally happen; a steady drip
                # means orphans aren't self-healing — operator attention.
                logger.warning(
                    "[CASH LIFECYCLE] cash session %r marked BROKEN — %s sweep "
                    "could not converge; sit guard will skip it (no player wedge), "
                    "but it needs operator attention",
                    row.game_id,
                    source,
                )
                _emit_session_event(
                    cash_session_repo,
                    row.game_id,
                    "broken",
                    owner_id=row.owner_id,
                    detail={"source": source, "error": str(e)},
                    now=now,
                )
            except Exception:
                logger.exception(
                    "[CASH][LOBBY] failed to mark %r broken after sweep failure",
                    row.game_id,
                )

    if swept:
        logger.info("[CASH][LOBBY] boot sweep removed %d stale orphan cash row(s)", swept)
    return swept


def kill_all_cash_sessions(
    *,
    game_state_service,
    game_repo,
    cash_table_repo=None,
    bankroll_repo=None,
    sandbox_id: Optional[str] = None,
    cash_session_repo=None,
    stake_repo=None,
    chip_ledger_repo=None,
    entity_presence_repo=None,
    stale_ttl_seconds: int = DEFAULT_STALE_TTL_SECONDS,
    now: Optional[datetime] = None,
) -> int:
    """Boot reconcile: drop stale in-memory cash games; reset orphan seats.

    Cash sessions are now expected to *survive* a reboot — `progress_game`
    auto-saves cash rows on every step, the cold-load path in
    `/api/game-state/<id>` rehydrates them with cash-mode flags + AI
    controllers, and the player reconnects to their frozen table just
    like a tournament. This function used to wipe every `cash-*` row at
    boot (a v1.5-deploy hygiene step from before resume worked); that
    purge has been removed.

    What this still does:

      1. Drop every in-memory `cash_mode=True` game from
         `game_state_service`. A fresh process has no in-memory games,
         so this is effectively a no-op at startup, but it stays here
         so callers (e.g. tests) can use it to force-clear runtime
         state.

      2. Reconcile orphan `"human"` seats on persistent `cash_tables`.
         A seat is orphan when its `personality_id` (the owner) has no
         surviving `cash-*` row — typically because some other process
         deleted it. For each orphan: refund the seat's chips to the
         owner's bankroll and revert the slot to `open_slot()`. Without
         this, the lobby would render the player as still seated at a
         vanished table. Skipped when `cash_table_repo` and
         `bankroll_repo` are not provided (older test harnesses).

    Returns the count of in-memory cash sessions dropped (item 1) so
    the boot logger can report it. Orphan-seat resets are logged at
    INFO level individually.
    """
    dropped = 0

    # In-memory.
    in_memory_to_delete = []
    for gid, gdata in list(game_state_service.games.items()):
        if gdata.get("cash_mode"):
            in_memory_to_delete.append(gid)
    for gid in in_memory_to_delete:
        game_state_service.delete_game(gid)
        dropped += 1
        logger.info("[CASH][LOBBY] kill_all_cash_sessions: dropped in-memory %r", gid)

    # Stale-orphan row sweep (T2.2). Runs before the seat reconcile so
    # that owners whose row we just swept fall into the "no surviving
    # cash-* row" bucket below and get their lobby seat freed too.
    if cash_session_repo is not None:
        if now is None:
            now = datetime.utcnow()
        _boot_sweep_stale_cash_rows(
            game_repo=game_repo,
            cash_session_repo=cash_session_repo,
            stake_repo=stake_repo,
            chip_ledger_repo=chip_ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            entity_presence_repo=entity_presence_repo,
            stale_ttl_seconds=stale_ttl_seconds,
            now=now,
            # Lock + re-check guard against the resurrection race (Codex #2).
            # Harmless at boot (memory is empty) but correct if a request
            # warm-loads a game mid-boot-reconcile.
            game_state_service=game_state_service,
        )

    # Reconcile orphan human seats. A seat is orphan when its owner
    # has no surviving `cash-*` row — the lobby would otherwise render
    # the player as still seated at a vanished table.
    if cash_table_repo is not None and bankroll_repo is not None and sandbox_id is not None:
        from dataclasses import replace as _dc_replace

        try:
            rows = game_repo.list_games(owner_id=None, limit=10000, offset=0)
        except Exception as e:
            logger.warning("[CASH][LOBBY] list_games failed during reconcile: %s", e)
            rows = []
        owners_with_cash_row: Set[str] = {
            (row.owner_id or "") for row in rows if row.game_id.startswith("cash-") and row.owner_id
        }

        try:
            tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
        except Exception as e:
            logger.warning("[CASH][LOBBY] list_all_tables failed during reconcile: %s", e)
            tables = []

        for table in tables:
            for idx, slot in enumerate(table.seats):
                if slot.get("kind") != "human":
                    continue
                owner_id = slot.get("personality_id")
                if owner_id and owner_id in owners_with_cash_row:
                    # Seat backed by a real cash row — leave it intact
                    # so the player can resume on reconnect.
                    continue
                refund_chips = int(slot.get("chips", 0))
                try:
                    if owner_id and refund_chips > 0:
                        br = bankroll_repo.load_player_bankroll(owner_id)
                        if br is not None:
                            bankroll_repo.save_player_bankroll(
                                _dc_replace(br, chips=br.chips + refund_chips)
                            )
                    cash_table_repo.save_table(
                        table.with_seat(idx, open_slot()),
                        sandbox_id=sandbox_id,
                    )
                    logger.info(
                        "[CASH][LOBBY] kill_all_cash_sessions: reset orphan human seat "
                        "table=%r seat=%d owner=%r refunded=%d",
                        table.table_id,
                        idx,
                        owner_id,
                        refund_chips,
                    )
                except Exception as e:
                    logger.warning(
                        "[CASH][LOBBY] failed to reset human seat " "table=%r seat=%d: %s",
                        table.table_id,
                        idx,
                        e,
                    )

    if dropped:
        logger.info(
            "[CASH][LOBBY] kill_all_cash_sessions: dropped %d cash session(s) total",
            dropped,
        )
    return dropped


# --- Aspiration-ask integration --------------------------------------------


# Per-AI cooldown applied to every triggered aspiration roll (success or
# failure). 60 simulated seconds — at the production lobby cadence
# (~8s/tick) that's ~7-8 ticks between successive attempts per AI.
ASPIRATION_COOLDOWN_SECONDS = 60


def _process_aspiration_asks(
    *,
    result: RosterRefreshResult,
    bankroll_repo,
    stake_repo,
    relationship_repo,
    personality_repo,
    chip_ledger_repo=None,
    sandbox_id: Optional[str],
    now: datetime,
    rng: random.Random,
    staker_profile_lookup,
    bankroll_lookup,
    relationship_lookup,
    history_lookup,
    starting_bankroll_lookup,
    all_tables,
    idle_pool,
) -> None:
    """Mutate `result` in place with aspiration-ask outcomes.

    For each AI seated at `result.new_table` after the burst:

      1. Skip if cooldown is still active.
      2. Skip if AI already has an active stake (one-active-stake).
      3. Roll aspiration probability against the AI's
         `borrower_profile.aspiration_bias` × `wealth_gap_factor`.
      4. If the roll succeeds, attempt `find_ai_staker_for` over the
         cross-table candidate pool at the *target* tier.
      5. If a staker is found, commit: create stake row, vacate
         current seat, issue bankroll changes (asker's seat → asker's
         bankroll PLUS principal, staker's bankroll − principal), and
         add an idle-pool entry with `target_stake = target_tier`.
      6. Stamp the cooldown regardless of success — failed asks still
         consume the rate limit so no single AI spams.

    All chip movements flow through the same `BankrollChange` /
    `IdlePoolChange` / `stake_repo.create_stake` surfaces the
    bust-stake path uses, so the chip-ledger audit invariant is
    preserved unchanged.

    Spec: `docs/plans/CASH_MODE_AI_ASPIRATION_ASK.md` Commit 4.
    """
    from cash_mode.aspiration import compute_aspiration_probability
    from cash_mode.movement import (
        BankrollChange,
        IdlePoolChange,
        IdlePoolEntry,
        find_ai_staker_for,
    )
    from cash_mode.stakes import (
        STAKE_FORMAT_PURE,
        STAKE_STATUS_ACTIVE,
        STAKER_KIND_PERSONALITY,
        Stake,
    )
    from cash_mode.stakes_ladder import (
        STAKES_ORDER,
        table_buy_in_window,
    )
    from cash_mode.tables import open_slot
    from poker.memory import OpponentModelManager
    from poker.memory.relationship_events import RelationshipEvent

    table = result.new_table
    current_tier = table.stake_label
    if current_tier not in STAKES_ORDER:
        return  # Defensive — unknown tier label can't have a "next".
    current_idx = STAKES_ORDER.index(current_tier)
    if current_idx + 1 >= len(STAKES_ORDER):
        return  # Top-tier seats have nowhere to climb to.
    target_tier = STAKES_ORDER[current_idx + 1]
    try:
        _, target_min_buy_in, _ = table_buy_in_window(target_tier)
    except Exception:
        return
    if target_min_buy_in <= 0:
        return

    cooldown_until_new = now + timedelta(seconds=ASPIRATION_COOLDOWN_SECONDS)
    # Snapshot the seats list so we iterate by index — the loop body
    # may vacate seats, but we want to evaluate every AI that was
    # seated when we started, not race against our own mutations.
    seats_snapshot = list(enumerate(table.seats))

    for seat_idx, slot in seats_snapshot:
        if slot.get("kind") != "ai":
            continue
        asker_pid = slot.get("personality_id")
        if not asker_pid:
            continue
        # One-active-stake invariant: an AI already a borrower can't
        # take a second stake on top.
        try:
            existing = stake_repo.load_active_for_borrower(
                asker_pid,
                BORROWER_KIND_PERSONALITY,
            )
        except Exception as exc:
            logger.debug(
                "[CASH][LOBBY] aspiration: stake lookup failed pid=%r: %s",
                asker_pid,
                exc,
            )
            existing = None
        if existing is not None:
            continue

        # Per-AI cooldown gate.
        try:
            cooldown_until = bankroll_repo.load_aspiration_cooldown_until(
                asker_pid,
                sandbox_id=sandbox_id,
            )
        except Exception as exc:
            logger.debug(
                "[CASH][LOBBY] aspiration: cooldown read failed pid=%r: %s",
                asker_pid,
                exc,
            )
            cooldown_until = None
        if cooldown_until is not None and cooldown_until > now:
            continue

        try:
            profile = bankroll_repo.load_borrower_profile(asker_pid)
        except Exception:
            continue
        if not profile.willing or profile.aspiration_bias <= 0:
            continue

        bankroll = bankroll_lookup(asker_pid) or 0
        if bankroll <= 0:
            continue

        prob = compute_aspiration_probability(
            aspiration_bias=profile.aspiration_bias,
            bankroll=bankroll,
            target_min_buy_in=target_min_buy_in,
        )
        if prob <= 0 or rng.random() >= prob:
            continue

        # Probability roll succeeded. Stamp cooldown up front so even
        # a failed staker-find doesn't immediately re-roll on the
        # next tick.
        try:
            bankroll_repo.save_aspiration_cooldown_until(
                asker_pid,
                sandbox_id=sandbox_id,
                until=cooldown_until_new,
            )
        except Exception as exc:
            logger.debug(
                "[CASH][LOBBY] aspiration: cooldown write failed pid=%r: %s",
                asker_pid,
                exc,
            )

        # Carry-reckoning trigger: about to climb? Settle the books
        # first. Loads this AI's outstanding carries; if any exist,
        # call try_ai_voluntary_payoff at event base_rate so the
        # personality's payoff_eagerness drives the decision against
        # the wealth/aspiration pull. Conscientious AIs will pay (and
        # skip the climb this tick — they spent their chips); gamblers
        # will skip the payoff (score collapses to 0) and proceed to
        # the climb attempt with carries unresolved (which the matcher
        # penalty will discount).
        try:
            outstanding = stake_repo.list_carries_for_borrower(
                asker_pid,
                BORROWER_KIND_PERSONALITY,
            )
        except Exception as exc:
            logger.debug(
                "[CASH][LOBBY] aspiration: carry lookup failed pid=%r: %s",
                asker_pid,
                exc,
            )
            outstanding = []
        if outstanding:
            from cash_mode.ai_carry_resolution import (
                PAYOFF_EVENT_BASE_RATE,
                try_ai_voluntary_payoff,
            )

            try:
                payoff_result = try_ai_voluntary_payoff(
                    personality_id=asker_pid,
                    carries=outstanding,
                    bankroll_repo=bankroll_repo,
                    stake_repo=stake_repo,
                    relationship_repo=relationship_repo,
                    chip_ledger_repo=None,  # AI-AI carry → pure transfer
                    sandbox_id=sandbox_id,
                    rng=rng,
                    now=now,
                    base_rate=PAYOFF_EVENT_BASE_RATE,
                )
            except Exception as exc:
                logger.warning(
                    "[CASH][LOBBY] aspiration: pre-climb payoff failed " "pid=%r: %s",
                    asker_pid,
                    exc,
                )
                payoff_result = None
            if payoff_result is not None:
                # Settled at least one carry; the AI used their chips
                # on debt rather than climbing. Skip the climb this
                # tick — next tick re-evaluates from the post-payoff
                # bankroll state.
                logger.info(
                    "[CASH][LOBBY] aspiration: pre-climb payoff fired "
                    "pid=%r stake=%r amount=%d — skipping climb",
                    asker_pid,
                    payoff_result.stake_id,
                    payoff_result.amount,
                )
                continue

        # Matcher penalty: borrowers with outstanding carries are
        # harder to back. The `0.5^N` gate (N = outstanding carry
        # count) closes the strategic loop — gamblers who skip the
        # pre-climb payoff above still face a steep matcher hit, so
        # repeated unpaid carries effectively block new stakes until
        # something resolves (payoff via per-tick fallback, default,
        # or — for AI-AI carries — auto-forgiveness).
        if outstanding:
            from cash_mode.ai_carry_resolution import (
                carry_penalty_probability,
            )

            penalty_prob = carry_penalty_probability(len(outstanding))
            if rng.random() >= penalty_prob:
                logger.info(
                    "[CASH][LOBBY] aspiration: matcher carry-penalty blocked "
                    "pid=%r carries=%d penalty_prob=%.3f",
                    asker_pid,
                    len(outstanding),
                    penalty_prob,
                )
                continue

        principal = target_min_buy_in
        # Aspiration asks accept backers from ANYWHERE in the lobby,
        # not just adjacent tiers. The bust-stake adjacency
        # constraint (`_cross_table_pool_for`) was designed for
        # peer-bailout where the borrower needs a staker who
        # "knows" their tier. Aspiration is the opposite — a wealthy
        # patron from $1000 backing a $10 climber is perfectly
        # plausible and we want to allow it. `find_ai_staker_for`'s
        # capacity gate naturally filters out under-capitalized
        # candidates; relationship gates handle the rest. Pool =
        # every AI on another table + every AI in the idle pool,
        # deduped.
        seen_in_pool: set = set()
        candidate_pool: List[str] = []
        for other_table in all_tables:
            if other_table.table_id == table.table_id:
                continue
            for other_slot in other_table.seats:
                if other_slot.get("kind") != "ai":
                    continue
                other_pid = other_slot.get("personality_id")
                if not other_pid or other_pid == asker_pid:
                    continue
                if other_pid in seen_in_pool:
                    continue
                seen_in_pool.add(other_pid)
                candidate_pool.append(other_pid)
        for idle_entry in idle_pool:
            other_pid = idle_entry.personality_id
            if not other_pid or other_pid == asker_pid:
                continue
            if other_pid in seen_in_pool:
                continue
            seen_in_pool.add(other_pid)
            candidate_pool.append(other_pid)
        if not candidate_pool:
            continue

        try:
            picked = find_ai_staker_for(
                borrower_id=asker_pid,
                principal=principal,
                candidate_pids=candidate_pool,
                staker_profile_lookup=staker_profile_lookup,
                bankroll_lookup=bankroll_lookup,
                relationship_lookup=relationship_lookup,
                rng=rng,
                history_lookup=history_lookup,
                starting_bankroll_lookup=starting_bankroll_lookup,
            )
        except Exception as exc:
            logger.debug(
                "[CASH][LOBBY] aspiration: find_ai_staker_for raised " "pid=%r: %s",
                asker_pid,
                exc,
            )
            continue
        if picked is None:
            continue
        staker_id, staker_profile = picked

        # Commit — financial operations FIRST, structural mutations LAST
        # (Window A atomicity, CASH_SEAT_INVARIANT_HARDENING §3).
        #
        # Order: (1) debit the staker; (2) create the Stake row, refunding
        # the staker if that write fails; (3) only after BOTH financial
        # ops have committed, vacate the seat + queue the chip-return.
        #
        # Rationale: the seat vacate and `from_seat` chip-return credit
        # the asker `seat_chips + principal`. If they ran before a failed
        # staker debit (the historical order), nobody was debited the
        # `principal` → minted chips. Reordering makes the structural
        # mutations pure in-memory writes on `result` that only run on the
        # fully-committed financial path — they have no independent
        # failure of their own. The success end-state is identical: the
        # same debit, the same stake row, the same seat vacate, the same
        # single `from_seat` of `seat_chips + principal`.
        seat_chips = int(slot.get("chips", 0) or 0)

        # (1) Debit the staker inline (matches Phase 4's stake_creations
        # post-loop debit pattern). `debit_bankroll_for_seat` signals
        # failure two ways: it RAISES, or it returns None (insufficient
        # funds / missing row — the audit-safe refusal). BOTH must skip
        # the ask cleanly without touching the seat, or chips mint.
        try:
            from cash_mode.bankroll import debit_bankroll_for_seat

            debited = debit_bankroll_for_seat(
                bankroll_repo,
                staker_id,
                principal,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] aspiration: staker debit failed "
                "staker=%r pid=%r principal=%d: %s",
                staker_id,
                asker_pid,
                principal,
                exc,
            )
            continue
        if debited is None:
            logger.warning(
                "[CASH][LOBBY] aspiration: staker debit refused "
                "staker=%r pid=%r principal=%d — skipping climb",
                staker_id,
                asker_pid,
                principal,
            )
            continue

        # (2) Create the stake row. If the write fails AFTER the staker
        # was debited, refund the staker the `principal` (reversing only
        # the transfer portion — any regen the debit committed is real
        # and stays) and skip the ask. The seat has NOT been touched yet.
        import uuid

        stake = Stake(
            stake_id=f"ai_stake_aspire_{uuid.uuid4().hex[:12]}",
            session_id=f"ai_aspire_{asker_pid}_{int(now.timestamp())}",
            staker_id=staker_id,
            staker_kind=STAKER_KIND_PERSONALITY,
            borrower_id=asker_pid,
            borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE,
            principal=principal,
            match_amount=0,
            origination_fee=0,
            cut=staker_profile.rate_anchor,
            status=STAKE_STATUS_ACTIVE,
            carry_amount=0,
            stake_tier=target_tier,
            created_at=now,
        )
        try:
            stake_repo.create_stake(stake)
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] aspiration: create_stake failed " "asker=%r staker=%r: %s",
                asker_pid,
                staker_id,
                exc,
            )
            # Refund the staker so the committed debit isn't a silent
            # loss. `debited` is the post-debit AIBankrollState; add the
            # principal back to it (the transfer is reversed; the regen
            # commit, if any, is preserved).
            try:
                from cash_mode.bankroll import AIBankrollState

                bankroll_repo.save_ai_bankroll(
                    AIBankrollState(
                        personality_id=staker_id,
                        chips=debited.chips + principal,
                        last_regen_tick=debited.last_regen_tick,
                    ),
                    sandbox_id=sandbox_id,
                )
            except TypeError as te:
                if "sandbox_id" not in str(te):
                    logger.warning(
                        "[CASH][LOBBY] aspiration: staker refund failed "
                        "staker=%r principal=%d: %s",
                        staker_id,
                        principal,
                        te,
                    )
                else:
                    bankroll_repo.save_ai_bankroll(
                        AIBankrollState(
                            personality_id=staker_id,
                            chips=debited.chips + principal,
                            last_regen_tick=debited.last_regen_tick,
                        )
                    )
            except Exception as refund_exc:
                logger.warning(
                    "[CASH][LOBBY] aspiration: staker refund failed "
                    "staker=%r principal=%d: %s",
                    staker_id,
                    principal,
                    refund_exc,
                )
            continue

        # (3) Both financial ops committed — now vacate the seat and queue
        # the chip-return. Pure in-memory mutations on `result`; no
        # independent failure path.
        table.seats[seat_idx] = open_slot()

        # Single BankrollChange combining the seat-chip return and the
        # stake-principal credit. The post-burst code applies
        # from_seat changes via credit_ai_cash_out, which writes one
        # row + handles regen — combining the two amounts is a
        # single write, cleaner than two separate calls.
        result.bankroll_changes.append(
            BankrollChange(
                direction="from_seat",
                personality_id=asker_pid,
                amount=seat_chips + principal,
            )
        )

        # Relationship event — same shape as bust-stake.
        if relationship_repo is not None:
            try:
                OpponentModelManager(
                    relationship_repo=relationship_repo,
                ).record_event(
                    actor_id=staker_id,
                    target_id=asker_pid,
                    event=RelationshipEvent.STAKE_OFFERED,
                )
            except Exception as exc:
                logger.debug(
                    "[CASH][LOBBY] aspiration: STAKE_OFFERED event "
                    "failed staker=%r asker=%r: %s",
                    staker_id,
                    asker_pid,
                    exc,
                )

        # Idle pool: target_stake = next tier so live-fill picks up
        # this AI at the new table. Reuse `stake_up_queued` — the
        # idle-pool consumer doesn't care whether the queue came from
        # overflow `stake_up` movement or from this aspiration_ask
        # path; both want the same "wait for a seat at this label"
        # semantics.
        idle_entry = IdlePoolEntry(
            personality_id=asker_pid,
            left_at=now,
            reason="stake_up_queued",
            target_stake=target_tier,
        )
        result.idle_changes.append(
            IdlePoolChange(
                kind="add",
                personality_id=asker_pid,
                entry=idle_entry,
            )
        )

        # Decision tag for sim observability (replaces any prior
        # value the burst loop wrote — aspiration_ask overrides the
        # final movement decision for this AI in this refresh).
        result.decisions[asker_pid] = "aspiration_climb"

        logger.info(
            "[CASH][LOBBY] aspiration_climb: %r ($%s) → %r at %s, " "principal=%d cut=%.2f",
            asker_pid,
            current_tier,
            staker_id,
            target_tier,
            principal,
            staker_profile.rate_anchor,
        )
