"""Tests for the circuit Main Event invite lifecycle (P3.5 — backend).

offer → accept (human tournament + buy-in) / decline / expire (autonomous),
one-at-a-time guard, and affordability on accept.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from cash_mode.bankroll import PlayerBankrollState
from core.economy.ledger import player, tournament
from flask_app.services import tournament_economy_service as econ, tournament_invites as inv
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_invite_repository import TournamentInviteRepository
from poker.repositories.tournament_session_repository import TournamentSessionRepository

SB = 'sb-inv'
OWNER = 'owner-inv'


class FakePersonalityRepo:
    def __init__(self, ids):
        self._ids = ids

    def list_eligible_for_cash_mode(self, *, user_id=None):
        return [{'personality_id': pid, 'name': pid} for pid in self._ids]


@pytest.fixture
def kit():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "econ.db")
        SchemaManager(path).ensure_schema()
        repos = dict(
            ledger_repo=ChipLedgerRepository(path),
            bankroll_repo=BankrollRepository(path),
            session_repo=TournamentSessionRepository(path),
            invite_repo=TournamentInviteRepository(path),
            personality_repo=FakePersonalityRepo([f'persona_{i}' for i in range(8)]),
        )
        yield repos
        for r in (
            repos['ledger_repo'],
            repos['bankroll_repo'],
            repos['session_repo'],
            repos['invite_repo'],
        ):
            r.close()


def _offer(kit, **kw):
    return inv.offer(
        invite_repo=kit['invite_repo'],
        session_repo=kit['session_repo'],
        owner_id=OWNER,
        sandbox_id=SB,
        field_size=6,
        table_size=3,
        starting_stack=10_000,
        seed=4,
        **kw,
    )


class TestOffer:
    def test_offer_creates_open_invite(self, kit):
        invite = _offer(kit, buy_in=500)
        assert invite is not None
        assert invite['status'] == 'offered'
        assert invite['buy_in'] == 500
        assert inv.active_invite(kit['invite_repo'], OWNER)['invite_id'] == invite['invite_id']

    def test_only_one_open_invite(self, kit):
        assert _offer(kit, buy_in=0) is not None
        assert _offer(kit, buy_in=0) is None  # second suppressed


class TestAccept:
    def test_accept_builds_human_tournament_and_charges(self, kit):
        kit['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=OWNER, chips=5_000, starting_bankroll=10_000)
        )
        _offer(kit, buy_in=500)
        result = inv.accept(
            invite_repo=kit['invite_repo'],
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'],
            owner_id=OWNER,
        )
        assert result is not None
        tid = result['tournament_id']
        assert result['human_id'] == f'human:{OWNER}'
        # Human charged into the escrow.
        assert kit['bankroll_repo'].load_player_bankroll(OWNER).chips == 4_500
        assert kit['ledger_repo'].balance_of(player(OWNER), sandbox_id=SB) == -500
        assert kit['ledger_repo'].balance_of(tournament(tid), sandbox_id=SB) >= 500
        # Invite consumed.
        assert inv.active_invite(kit['invite_repo'], OWNER) is None
        assert kit['invite_repo'].load(result_invite_id(kit))['status'] == 'accepted'

    def test_accept_insufficient_funds_keeps_invite_open(self, kit):
        kit['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=OWNER, chips=100, starting_bankroll=10_000)
        )
        _offer(kit, buy_in=500)
        with pytest.raises(econ.InsufficientFundsError):
            inv.accept(
                invite_repo=kit['invite_repo'],
                personality_repo=kit['personality_repo'],
                bankroll_repo=kit['bankroll_repo'],
                ledger_repo=kit['ledger_repo'],
                session_repo=kit['session_repo'],
                owner_id=OWNER,
            )
        # Nothing consumed; invite still open; no debit.
        assert inv.active_invite(kit['invite_repo'], OWNER) is not None
        assert kit['bankroll_repo'].load_player_bankroll(OWNER).chips == 100

    def test_accept_with_no_open_invite_is_none(self, kit):
        assert (
            inv.accept(
                invite_repo=kit['invite_repo'],
                personality_repo=kit['personality_repo'],
                bankroll_repo=kit['bankroll_repo'],
                ledger_repo=kit['ledger_repo'],
                session_repo=kit['session_repo'],
                owner_id=OWNER,
            )
            is None
        )


class TestDeclineExpire:
    def test_decline_spawns_autonomous(self, kit):
        _offer(kit, buy_in=0)
        spawned = inv.decline(
            invite_repo=kit['invite_repo'],
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'],
            owner_id=OWNER,
        )
        assert spawned is not None
        assert inv.active_invite(kit['invite_repo'], OWNER) is None  # consumed
        # Invite linked to the autonomous tournament + marked declined.
        row = kit['invite_repo'].load(result_invite_id(kit))
        assert row['status'] == 'declined'
        assert row['tournament_id'] == spawned['tournament_id']

    def test_expire_due_spawns_autonomous(self, kit):
        # Offer with a past expiry → the sweep expires it autonomously.
        _offer(kit, buy_in=0, expires_at='2000-01-01T00:00:00')
        spawned = inv.expire_due(
            invite_repo=kit['invite_repo'],
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'],
            now_iso='2026-06-01T00:00:00',
        )
        assert len(spawned) == 1
        assert inv.active_invite(kit['invite_repo'], OWNER) is None
        assert kit['invite_repo'].load(result_invite_id(kit))['status'] == 'expired'

    def test_future_expiry_not_swept(self, kit):
        _offer(kit, buy_in=0, expires_at='2099-01-01T00:00:00')
        spawned = inv.expire_due(
            invite_repo=kit['invite_repo'],
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'],
            now_iso='2026-06-01T00:00:00',
        )
        assert spawned == []
        assert inv.active_invite(kit['invite_repo'], OWNER) is not None  # still open

    def test_aware_expires_at_orders_correctly_vs_naive_now(self, kit):
        """Production stamps `expires_at` UTC-aware (`+00:00`) to fix the browser
        countdown, but the sweep's `now_iso` is naive utcnow and the expiry query
        is a lexicographic SQL string compare. Prove the mixed formats still order
        chronologically across the second AND sub-second boundary — a regression
        here would skip due invites or expire future ones early."""
        from datetime import datetime, timedelta, timezone

        def stamp(dt):  # exactly how maybe_offer_main_event builds expires_at
            return dt.replace(tzinfo=timezone.utc).isoformat()

        repo = kit['invite_repo']
        base = datetime(2026, 6, 1, 12, 0, 0, 500000)  # naive utcnow stand-in
        now_iso = base.isoformat()
        # One open invite per owner (partial unique index), each a different owner.
        common = dict(sandbox_id=SB, buy_in=0, field_size=6, table_size=3, starting_stack=10_000)
        repo.create(
            invite_id='past',
            owner_id='o-past',
            expires_at=stamp(base - timedelta(seconds=1)),
            **common,
        )
        repo.create(
            invite_id='sub-second-future',
            owner_id='o-sub',
            expires_at=stamp(base + timedelta(milliseconds=1)),
            **common,
        )
        repo.create(
            invite_id='future',
            owner_id='o-future',
            expires_at=stamp(base + timedelta(seconds=1)),
            **common,
        )

        due = {row['invite_id'] for row in repo.list_open_due(now_iso=now_iso)}
        assert 'past' in due  # aware, just past → swept
        assert 'future' not in due  # aware, just future → not swept
        assert 'sub-second-future' not in due  # aware, sub-second future → not swept


def result_invite_id(kit):
    """The single invite row's id (these tests create exactly one)."""
    import sqlite3

    conn = sqlite3.connect(kit['invite_repo'].db_path)
    try:
        return conn.execute("SELECT invite_id FROM tournament_invites LIMIT 1").fetchone()[0]
    finally:
        conn.close()


class TestInviteClaimCAS:
    """The cross-worker compare-and-swap that makes accept/decline/expire safe
    when the in-memory sandbox lock can't (it doesn't span gunicorn workers)."""

    def test_claim_only_one_winner(self, kit):
        _offer(kit, buy_in=0)
        iid = result_invite_id(kit)
        repo = kit['invite_repo']
        assert repo.claim(iid, to_status='accepted', owner_id=OWNER) is True
        # A second claim loses — the row is no longer 'offered'.
        assert repo.claim(iid, to_status='accepted', owner_id=OWNER) is False

    def test_claim_owner_guard(self, kit):
        _offer(kit, buy_in=0)
        iid = result_invite_id(kit)
        assert kit['invite_repo'].claim(iid, to_status='accepted', owner_id='someone-else') is False

    def test_revert_reopens_unlinked_claim(self, kit):
        _offer(kit, buy_in=0)
        iid = result_invite_id(kit)
        repo = kit['invite_repo']
        assert repo.claim(iid, to_status='accepted', owner_id=OWNER) is True
        assert repo.revert_to_offered(iid) is True
        assert repo.load(iid)['status'] == 'offered'
        # Re-openable → a fresh claim succeeds again.
        assert repo.claim(iid, to_status='accepted', owner_id=OWNER) is True

    def test_revert_refuses_linked_invite(self, kit):
        _offer(kit, buy_in=0)
        iid = result_invite_id(kit)
        repo = kit['invite_repo']
        repo.claim(iid, to_status='accepted', owner_id=OWNER)
        repo.resolve(iid, status='accepted', tournament_id='tourney_x')  # link it
        # A linked (real) acceptance must never be reverted out from under play.
        assert repo.revert_to_offered(iid) is False
        assert repo.load(iid)['status'] == 'accepted'

    def test_accept_loser_bails_no_double_charge(self, kit):
        """A worker whose invite was already claimed gets None and charges nothing."""
        kit['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=OWNER, chips=100_000, starting_bankroll=100_000)
        )
        _offer(kit, buy_in=500)
        iid = result_invite_id(kit)
        # Pre-claim to simulate the winning worker.
        assert kit['invite_repo'].claim(iid, to_status='accepted', owner_id=OWNER) is True
        before = kit['bankroll_repo'].load_player_bankroll(OWNER).chips
        result = inv.accept(
            invite_repo=kit['invite_repo'],
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'],
            owner_id=OWNER,
        )
        assert result is None  # loser bails
        assert kit['bankroll_repo'].load_player_bankroll(OWNER).chips == before  # no charge


class TestExpireScoping:
    def test_expire_due_scoped_to_sandbox(self, kit):
        # Owner A in SB (past-due) + owner B in another sandbox (past-due).
        _offer(kit, buy_in=0, expires_at='2000-01-01T00:00:00')
        inv.offer(
            invite_repo=kit['invite_repo'],
            session_repo=kit['session_repo'],
            owner_id='owner-B',
            sandbox_id='sb-B',
            field_size=6,
            table_size=3,
            starting_stack=10_000,
            seed=4,
            buy_in=0,
            expires_at='2000-01-01T00:00:00',
        )
        # Sweep ONLY sandbox SB — owner B's invite (foreign sandbox) is untouched.
        spawned = inv.expire_due(
            invite_repo=kit['invite_repo'],
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'],
            now_iso='2026-06-01T00:00:00',
            sandbox_id=SB,
        )
        assert len(spawned) == 1
        assert inv.active_invite(kit['invite_repo'], OWNER) is None  # A expired
        assert inv.active_invite(kit['invite_repo'], 'owner-B') is not None  # B untouched

    def test_list_open_due_sandbox_filter(self, kit):
        _offer(kit, buy_in=0, expires_at='2000-01-01T00:00:00')
        inv.offer(
            invite_repo=kit['invite_repo'],
            session_repo=kit['session_repo'],
            owner_id='owner-B',
            sandbox_id='sb-B',
            field_size=6,
            table_size=3,
            starting_stack=10_000,
            seed=4,
            buy_in=0,
            expires_at='2000-01-01T00:00:00',
        )
        repo = kit['invite_repo']
        assert len(repo.list_open_due(now_iso='2026-06-01T00:00:00')) == 2  # global
        scoped = repo.list_open_due(now_iso='2026-06-01T00:00:00', sandbox_id='sb-B')
        assert [r['owner_id'] for r in scoped] == ['owner-B']


class TestOneOpenInviteIndex:
    """v136 partial unique index — one open invite per owner, enforced in the DB."""

    def test_second_open_invite_rejected_at_db(self, kit):
        import sqlite3

        repo = kit['invite_repo']
        repo.create(
            invite_id='inv_a',
            owner_id=OWNER,
            sandbox_id=SB,
            buy_in=0,
            field_size=6,
            table_size=3,
            starting_stack=10_000,
        )
        with pytest.raises(sqlite3.IntegrityError):
            repo.create(
                invite_id='inv_b',
                owner_id=OWNER,
                sandbox_id=SB,
                buy_in=0,
                field_size=6,
                table_size=3,
                starting_stack=10_000,
            )

    def test_resolving_frees_the_slot(self, kit):
        repo = kit['invite_repo']
        repo.create(
            invite_id='inv_a',
            owner_id=OWNER,
            sandbox_id=SB,
            buy_in=0,
            field_size=6,
            table_size=3,
            starting_stack=10_000,
        )
        repo.resolve('inv_a', status='expired')
        repo.create(
            invite_id='inv_b',
            owner_id=OWNER,
            sandbox_id=SB,
            buy_in=0,
            field_size=6,
            table_size=3,
            starting_stack=10_000,
        )
        assert repo.active_for_owner(OWNER)['invite_id'] == 'inv_b'

    def test_offer_handles_lost_race_gracefully(self, kit):
        """offer() turns a constraint violation (concurrent winner) into the open
        invite, not a 500."""
        repo = kit['invite_repo']
        # Insert an open invite directly (a concurrent worker's), bypassing offer()'s
        # active_for_owner guard to force the insert race.
        repo.create(
            invite_id='other',
            owner_id=OWNER,
            sandbox_id=SB,
            buy_in=0,
            field_size=6,
            table_size=3,
            starting_stack=10_000,
        )
        # offer() sees it via active_for_owner and returns None (already open).
        assert _offer(kit, buy_in=0) is None


class TestDraftFailClosed:
    def test_spawn_aborts_on_exclusion_scan_failure(self, kit):
        """A seat-scan error aborts the spawn (fail closed) rather than fielding
        from a partial exclusion set and risking a seated persona — #5."""
        from flask_app.services.tournament_spawn import spawn_autonomous_tournament

        class BrokenCashTableRepo:
            def list_all_tables(self, *, sandbox_id=None):
                raise RuntimeError("scan boom")

        # Fund a flush bank so funding isn't the reason for a None return.
        kit['ledger_repo'].record(
            'central_bank', 'player:u', 1_000_000, 'player_seed', sandbox_id=SB
        )
        kit['ledger_repo'].record(
            'ai:d', 'central_bank', 300_000, 'bank_pool_deposit', sandbox_id=SB
        )
        result = spawn_autonomous_tournament(
            owner_id=OWNER,
            sandbox_id=SB,
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'],
            cash_table_repo=BrokenCashTableRepo(),
            field_size=6,
            table_size=3,
            starting_stack=10_000,
            seed=1,
            rng_seed=1,
        )
        assert result is None  # aborted, no tournament created


class TestDeclineSpawnFailure:
    def test_decline_consumes_even_when_spawn_unfieldable(self, kit):
        """Too few personas to field the autonomous run → the invite is still
        consumed (declined) and decline reports success, not a false 404 — #7."""
        kit['personality_repo'] = FakePersonalityRepo(['only_one'])  # < MIN_FIELD
        _offer(kit, buy_in=0)
        result = inv.decline(
            invite_repo=kit['invite_repo'],
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'],
            owner_id=OWNER,
        )
        assert result is not None  # not a misleading "no open invite"
        assert result['tournament_id'] is None
        assert kit['invite_repo'].load(result_invite_id(kit))['status'] == 'declined'


class TestDrawReserve:
    """tournaments-as-a-draw (Phase B3): offer() stores the draw-ranked field as
    reserved_pids when wired + flag on, and is fully inert otherwise."""

    def _draw_ctx(self, kit):
        return inv.draw_context(
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            prestige_repo=None,
            cash_table_repo=None,
            ledger_repo=kit['ledger_repo'],
        )

    def test_offer_reserves_top_field_when_flag_on(self, kit, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', True)
        invite = _offer(kit, buy_in=0, draw_ctx=self._draw_ctx(kit))
        reserved = kit['invite_repo'].load(invite['invite_id'])['reserved_pids']
        assert len(reserved) == 6  # top field_size of the 8-persona pool
        assert set(reserved) <= {f'persona_{i}' for i in range(8)}

    def test_offer_inert_when_flag_off(self, kit, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', False)
        invite = _offer(kit, buy_in=0, draw_ctx=self._draw_ctx(kit))
        assert kit['invite_repo'].load(invite['invite_id'])['reserved_pids'] == []

    def test_offer_inert_without_draw_ctx(self, kit, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', True)
        invite = _offer(kit, buy_in=0)  # no draw_ctx passed
        assert kit['invite_repo'].load(invite['invite_id'])['reserved_pids'] == []

    def test_reserved_field_flows_through_decline_spawn(self, kit, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', True)
        _offer(kit, buy_in=0, draw_ctx=self._draw_ctx(kit))
        # decline threads invite['reserved_pids'] → spawn → select_persona_field;
        # it must still field the autonomous run without error.
        spawned = inv.decline(
            invite_repo=kit['invite_repo'],
            personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'],
            owner_id=OWNER,
        )
        assert spawned is not None and spawned['tournament_id'] is not None


class TestBoundPidsGating:
    """`bound_pids` / `open_invite_for_gather` — the trickle-vacate gate (Phase C):
    only gather an open invite that has reserved_pids AND an expires_at AND the
    flag on, so a vacated persona is never stranded without a guaranteed spawn."""

    def _make_open_invite(self, kit, *, reserved, expires_at):
        kit['invite_repo'].create(
            invite_id='iv',
            owner_id=OWNER,
            sandbox_id=SB,
            buy_in=0,
            field_size=6,
            table_size=3,
            starting_stack=10_000,
            expires_at=expires_at,
            reserved_pids=reserved,
        )

    def test_gather_when_flag_on_and_has_expiry(self, kit, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', True)
        self._make_open_invite(
            kit, reserved=['persona_1', 'persona_2'], expires_at='2099-01-01T00:00:00'
        )
        assert inv.bound_pids(kit['invite_repo'], OWNER) == {'persona_1', 'persona_2'}

    def test_no_gather_when_flag_off(self, kit, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', False)
        self._make_open_invite(kit, reserved=['persona_1'], expires_at='2099-01-01T00:00:00')
        assert inv.bound_pids(kit['invite_repo'], OWNER) == set()

    def test_no_gather_without_expiry(self, kit, monkeypatch):
        # An invite kept open indefinitely (no expires_at) is never gathered —
        # the no-stranding guarantee (no guaranteed spawn to absorb vacated AIs).
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', True)
        self._make_open_invite(kit, reserved=['persona_1'], expires_at=None)
        assert inv.bound_pids(kit['invite_repo'], OWNER) == set()

    def test_no_invite_is_empty(self, kit, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', True)
        assert inv.bound_pids(kit['invite_repo'], OWNER) == set()
        assert inv.open_invite_for_gather(kit['invite_repo'], OWNER) is None


class TestDraftExclusionsReserved:
    """draft_exclusions unions the owner's open-invite reserved field (B3.3)."""

    def test_unions_reserved_pids_for_owner(self, kit):
        from flask_app.services.tournament_spawn import draft_exclusions

        kit['invite_repo'].create(
            invite_id='iv',
            owner_id=OWNER,
            sandbox_id=SB,
            buy_in=0,
            field_size=6,
            table_size=3,
            starting_stack=10_000,
            reserved_pids=['persona_1', 'persona_2'],
        )
        excl = draft_exclusions(
            cash_table_repo=None,
            session_repo=None,
            owner_id=OWNER,
            sandbox_id=SB,
            invite_repo=kit['invite_repo'],
        )
        assert {'persona_1', 'persona_2'} <= excl

    def test_inert_without_invite_repo(self, kit):
        from flask_app.services.tournament_spawn import draft_exclusions

        excl = draft_exclusions(
            cash_table_repo=None, session_repo=None, owner_id=OWNER, sandbox_id=SB
        )
        assert excl == set()


class TestAcceptCannotField:
    def test_accept_raises_cannot_field_when_no_personas(self, kit):
        """Accept with an empty draft pool re-opens the invite and raises
        CannotFieldTournamentError — distinct from 'no open invite' so the UI can
        say "not enough players right now" instead of a misleading not-found."""
        kit['personality_repo'] = FakePersonalityRepo([])  # 0 eligible personas
        _offer(kit, buy_in=0)
        with pytest.raises(inv.CannotFieldTournamentError):
            inv.accept(
                invite_repo=kit['invite_repo'],
                personality_repo=kit['personality_repo'],
                bankroll_repo=kit['bankroll_repo'],
                ledger_repo=kit['ledger_repo'],
                session_repo=kit['session_repo'],
                owner_id=OWNER,
            )
        # The invite is still open (re-opened by the revert) for a later retry.
        assert inv.active_invite(kit['invite_repo'], OWNER) is not None


def _field_json(entries: list[str], stacks: dict[str, int]) -> str:
    """Minimal session_json matching the winner-stamp extraction contract
    (TournamentField.to_dict: entries + stacks)."""
    return json.dumps({'field': {'entries': {e: 0 for e in entries}, 'stacks': stacks}})


class TestWinnerStamp:
    """`save()` denormalizes the champion + field size off the session — the
    Champions Roll reads these without deserializing session_json per row."""

    def test_stamps_winner_and_field_size_when_field_collapses(self, kit):
        repo = kit['session_repo']
        repo.save(
            tournament_id='t-done',
            owner_id=OWNER,
            status='complete',
            resolver_kind='fake',
            session_json=_field_json(['alice', 'bob', 'cara'], {'cara': 30_000}),
            created_at='2026-06-01T00:00:00',
        )
        row = repo.load('t-done')
        assert row['winner_pid'] == 'cara'  # sole remaining stack
        assert row['field_size'] == 3

    def test_no_winner_while_field_live_but_field_size_known(self, kit):
        repo = kit['session_repo']
        repo.save(
            tournament_id='t-live',
            owner_id=OWNER,
            status='active',
            resolver_kind='fake',
            session_json=_field_json(['a', 'b'], {'a': 1, 'b': 2}),
            created_at='2026-06-01T00:00:00',
        )
        row = repo.load('t-live')
        assert row['winner_pid'] is None  # two stacks left → undecided
        assert row['field_size'] == 2

    def test_winner_not_nulled_by_a_later_blank_save(self, kit):
        repo = kit['session_repo']
        common = dict(
            tournament_id='t-coalesce',
            owner_id=OWNER,
            status='complete',
            resolver_kind='fake',
            created_at='2026-06-01T00:00:00',
        )
        repo.save(session_json=_field_json(['a', 'b'], {'b': 2}), **common)
        repo.save(session_json='{}', **common)  # COALESCE keeps the champion
        assert repo.load('t-coalesce')['winner_pid'] == 'b'


def _session_with_human(human_id, entries, stacks, eliminations=None) -> str:
    """session_json carrying a human seat + eliminations, for human_finish tests."""
    return json.dumps(
        {
            'human_id': human_id,
            'field': {
                'entries': {e: 0 for e in entries},
                'stacks': stacks,
                'eliminations': eliminations or [],
            },
        }
    )


class TestHumanFinish:
    """`save()` also stamps the human seat's finishing position — the Champions
    Roll's "you finished Nth" on events the player actually sat in."""

    HUMAN = f'human:{OWNER}'

    def test_human_wins_finishes_first(self, kit):
        repo = kit['session_repo']
        repo.save(
            tournament_id='t-win',
            owner_id=OWNER,
            status='complete',
            resolver_kind='single',
            session_json=_session_with_human(
                self.HUMAN, [self.HUMAN, 'mervin'], {self.HUMAN: 18_000}
            ),
            created_at='2026-06-01T00:00:00',
        )
        assert repo.load('t-win')['human_finish'] == 1

    def test_human_busts_uses_elimination_position(self, kit):
        repo = kit['session_repo']
        repo.save(
            tournament_id='t-bust',
            owner_id=OWNER,
            status='complete',
            resolver_kind='single',
            session_json=_session_with_human(
                self.HUMAN,
                [self.HUMAN, 'mervin', 'sun_tzu'],
                {'mervin': 27_000},  # mervin is champion
                eliminations=[
                    {'player_id': 'sun_tzu', 'finishing_position': 3, 'eliminator': 'mervin'},
                    {'player_id': self.HUMAN, 'finishing_position': 2, 'eliminator': 'mervin'},
                ],
            ),
            created_at='2026-06-01T00:00:00',
        )
        row = repo.load('t-bust')
        assert row['winner_pid'] == 'mervin'
        assert row['human_finish'] == 2  # human's elimination position

    def test_autonomous_event_has_no_human_finish(self, kit):
        # The human seat isn't in the field (declined/expired → ran without them).
        repo = kit['session_repo']
        repo.save(
            tournament_id='t-auto',
            owner_id=OWNER,
            status='complete',
            resolver_kind='fake',
            session_json=_session_with_human(self.HUMAN, ['mervin', 'sun_tzu'], {'mervin': 18_000}),
            created_at='2026-06-01T00:00:00',
        )
        assert repo.load('t-auto')['human_finish'] is None  # not a participant


class TestCircuitHistory:
    """`list_circuit_history_for_owner` = the Champions Roll: completed
    invite-linked events (incl. ones the player passed on), newest first."""

    def _complete(self, kit, tid: str, winner: str, when: str) -> None:
        kit['session_repo'].save(
            tournament_id=tid,
            owner_id=OWNER,
            status='complete',
            resolver_kind='fake',
            session_json=_field_json([winner, 'filler'], {winner: 9}),
            created_at=when,
        )

    def _link_invite(self, kit, invite_id: str, tid: str, status: str) -> None:
        kit['invite_repo'].create(
            invite_id=invite_id,
            owner_id=OWNER,
            sandbox_id=SB,
            buy_in=0,
            field_size=2,
            table_size=2,
            starting_stack=9,
        )
        kit['invite_repo'].resolve(invite_id, status=status, tournament_id=tid)

    def test_lists_circuit_events_with_disposition_excludes_adhoc(self, kit):
        # Two circuit events: one accepted (played), one declined (passed).
        self._complete(kit, 't-played', f'human:{OWNER}', '2026-06-01T00:00:00')
        self._link_invite(kit, 'i1', 't-played', 'accepted')
        self._complete(kit, 't-passed', 'mervin', '2026-06-02T00:00:00')
        self._link_invite(kit, 'i2', 't-passed', 'declined')
        # An ad-hoc tournament (no invite) must NOT appear in the circuit roll.
        self._complete(kit, 't-adhoc', 'stranger', '2026-06-03T00:00:00')

        roll = kit['session_repo'].list_circuit_history_for_owner(OWNER)
        assert [e['tournament_id'] for e in roll] == ['t-passed', 't-played']  # newest first
        by_id = {e['tournament_id']: e for e in roll}
        assert by_id['t-played']['played'] is True
        assert by_id['t-passed']['played'] is False  # ran without the player
        assert by_id['t-passed']['winner_pid'] == 'mervin'
        assert by_id['t-played']['field_size'] == 2
        assert 'human_finish' in by_id['t-played']  # the query plumbs the finish through

    def test_dedups_a_tournament_with_two_linked_invites(self, kit):
        # The invite→tournament link isn't unique at the schema level. A stray
        # second invite pointing at the same tournament must not fan out the JOIN
        # into duplicate roll rows (which LIMIT could then truncate).
        self._complete(kit, 't-dup', 'mervin', '2026-06-01T00:00:00')
        self._link_invite(kit, 'i-acc', 't-dup', 'accepted')
        self._link_invite(kit, 'i-dec', 't-dup', 'declined')

        roll = kit['session_repo'].list_circuit_history_for_owner(OWNER)
        dup = [e for e in roll if e['tournament_id'] == 't-dup']
        assert len(dup) == 1  # collapsed to a single row
        assert dup[0]['played'] is True  # MIN(status) prefers 'accepted'
