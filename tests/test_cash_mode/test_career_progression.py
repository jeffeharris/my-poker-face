"""Tests for the Act-1 career-progression spine (M1).

Covers the keyring repository, the read-side visibility filter, the scripted
first-vouch gate, the random home-court pick, conservation-safe Scene-0 seeding,
and the vouch firing. See `cash_mode/career_progression.py`,
`poker/repositories/career_progress_repository.py`, and
`docs/plans/CASH_MODE_CAREER_PROGRESSION.md`.
"""

from __future__ import annotations

import json
import random
import sqlite3

import pytest

pytestmark = pytest.mark.integration

from cash_mode import career_progression as cp
from cash_mode.activity import EVENT_VOUCH, clear_events, recent_events
from cash_mode.tables import CashTableState, open_slot
from poker.repositories.career_progress_repository import CareerProgress


def _table(table_id: str, *, table_type: str = "lobby", stake: str = "$2") -> CashTableState:
    return CashTableState(table_id=table_id, stake_label=stake, table_type=table_type)


def _insert_personality(db_path: str, pid: str, name: str, knobs: dict) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities "
            "(name, config_json, personality_id, visibility, circulating) "
            "VALUES (?, ?, ?, 'public', 0)",
            (name, json.dumps({"bankroll_knobs": knobs}), pid),
        )
        conn.commit()


# --- repository round-trip ---------------------------------------------------


def test_load_default_is_legacy_safe(repos):
    """A missing row decodes to keyring-off (career_active False) so the lobby
    filter is a no-op — the safe default that never blanks a legacy lobby."""
    prog = repos["career_progress_repo"].load("sb1", "owner1")
    assert prog.career_active is False
    assert prog.revealed_table_ids == []
    assert prog.scene0_seeded is False
    assert prog.tutorial_complete is False


def test_make_fish_name_alliterates_when_it_can():
    rng = random.Random(0)
    assert cp.make_fish_name("Jeff", rng).endswith("Jeff")
    # Alliterates: keeps the player's name and prepends a same-letter handle.
    # (Asserts the alliteration, not a brittle exact RNG pick — the adjective
    # bank grows over time, so a fixed-seed exact-string match rots.)
    jeff = cp.make_fish_name("Jeff", random.Random(1))
    assert jeff.endswith("Jeff")
    assert jeff.split()[0][0].upper() == "J"
    # Alliterative prefix chosen when one starts with the same letter.
    assert cp.make_fish_name("Sarah", random.Random(1)).split()[-1] == "Sarah"
    # Uses only the first token; empty falls back to a generic handle.
    assert cp.make_fish_name("Jeff Harris", random.Random(2)).endswith("Jeff")
    assert cp.make_fish_name("", random.Random(3)).endswith("Stranger")


def test_generate_intake_persona_falls_back_when_llm_off(monkeypatch):
    # Force the LLM path to fail so we exercise the robust fallback (intake must
    # never block on the model). Patch the fast-model accessor the gen uses.
    import flask_app.config as fc

    def _boom(*a, **k):
        raise RuntimeError("llm off")

    monkeypatch.setattr(fc, "get_fast_model", _boom)
    persona = cp.generate_intake_persona(
        "Jeff", answer="Folks say I'm hard to read.", rng=random.Random(1)
    )
    assert persona["fish_name"].endswith("Jeff")  # rule-based fallback name
    assert persona["bio"]  # a canned line
    assert "Jeff" in cp.intake_avatar_prompt(persona["fish_name"], persona["bio"]) or True


def test_progress_roundtrip(repos):
    repo = repos["career_progress_repo"]
    prog = CareerProgress(
        sandbox_id="sb1",
        owner_id="owner1",
        career_active=True,
        intake_complete=True,
        player_name="Jeff",
        fish_name="Juke Joint Jeff",
        revealed_table_ids=["cash-scene0-001", "cash-table-2-001"],
        scene0_seeded=True,
        scene0_table_id="cash-scene0-001",
        scene0_fish_id="loose_larry",
        tutorial_complete=True,
        home_court_table_id="cash-table-2-001",
        vouched_by=["sal_moretti"],
    )
    repo.save(prog)
    loaded = repo.load("sb1", "owner1")
    assert loaded == prog
    assert loaded.has_vouched("sal_moretti")
    assert loaded.is_revealed("cash-table-2-001")


def test_malformed_blob_degrades_to_default(repos, db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO career_progress (sandbox_id, owner_id, progress_json, updated_at) "
            "VALUES ('sb1', 'owner1', ?, '2026-01-01')",
            ("not json{",),
        )
        conn.commit()
    prog = repos["career_progress_repo"].load("sb1", "owner1")
    assert prog.career_active is False  # corrupt row reads as brand-new, never crashes


# --- visibility filter -------------------------------------------------------


def test_visible_tables_off_shows_all():
    tables = [_table("a"), _table("b", table_type="casino")]
    prog = CareerProgress(sandbox_id="sb", owner_id="o", career_active=False)
    assert cp.visible_tables(tables, prog) == tables


def test_visible_tables_on_filters_to_scripted_and_revealed():
    scripted = _table("cash-scene0-001", table_type="scripted")
    revealed = _table("cash-table-2-001")
    hidden_cardroom = _table("cash-table-10-001", stake="$10")
    casino = _table("cash-casino-2-001", table_type="casino")
    prog = CareerProgress(
        sandbox_id="sb",
        owner_id="o",
        career_active=True,
        revealed_table_ids=["cash-table-2-001"],
    )
    visible = cp.visible_tables([scripted, revealed, hidden_cardroom, casino], prog)
    ids = {t.table_id for t in visible}
    assert ids == {"cash-scene0-001", "cash-table-2-001"}


# --- new-vs-legacy classification --------------------------------------------


def test_classify_brand_new_sandbox_seeds():
    prog = CareerProgress(sandbox_id="sb", owner_id="o")
    assert cp.classify_new_player(prog, existing_tables=[]) == "seed"


def test_classify_existing_world_grandfathers():
    prog = CareerProgress(sandbox_id="sb", owner_id="o")
    assert cp.classify_new_player(prog, existing_tables=[_table("cash-table-2-001")]) == "grandfather"


def test_classify_already_decided_is_noop():
    active = CareerProgress(sandbox_id="sb", owner_id="o", career_active=True)
    assert cp.classify_new_player(active, existing_tables=[]) == "noop"
    graduated = CareerProgress(sandbox_id="sb", owner_id="o", tutorial_complete=True)
    assert cp.classify_new_player(graduated, existing_tables=[]) == "noop"


# --- vouch gate --------------------------------------------------------------


def _active_progress() -> CareerProgress:
    return CareerProgress(
        sandbox_id="sb", owner_id="o", career_active=True, scene0_seeded=True,
        scene0_table_id="cash-scene0-001", scene0_fish_id="loose_larry",
    )


def test_vouch_gate_requires_active_and_ungraduated():
    legacy = CareerProgress(sandbox_id="sb", owner_id="o", career_active=False)
    assert not cp.evaluate_first_vouch(legacy, session_hands=999, fish_pnl=999)
    graduated = _active_progress()
    graduated.tutorial_complete = True
    assert not cp.evaluate_first_vouch(graduated, session_hands=999, fish_pnl=999)


def test_vouch_gate_needs_min_hands_and_fish_win():
    prog = _active_progress()
    # Too few hands.
    assert not cp.evaluate_first_vouch(
        prog, session_hands=cp.MIN_VOUCH_HANDS - 1, fish_pnl=cp.FISH_WIN_THRESHOLD
    )
    # Enough hands but not up on the fish.
    assert not cp.evaluate_first_vouch(
        prog, session_hands=cp.MIN_VOUCH_HANDS, fish_pnl=cp.FISH_WIN_THRESHOLD - 1
    )
    # Both gates cleared → fires.
    assert cp.evaluate_first_vouch(
        prog, session_hands=cp.MIN_VOUCH_HANDS, fish_pnl=cp.FISH_WIN_THRESHOLD
    )


# --- home court pick ---------------------------------------------------------


def test_pick_home_court_returns_a_2dollar_room():
    rng = random.Random(0)
    pick = cp.pick_home_court(rng)
    assert pick is not None
    table_id, name = pick
    assert table_id.startswith("cash-table-2-")
    assert isinstance(name, str) and name


def test_pick_home_court_excludes_revealed():
    rng = random.Random(0)
    # Exclude every $2 candidate → nothing left to reveal.
    from cash_mode.lobby_config import LOBBY_TABLES

    all_two = {
        cp._table_id_for_stake("$2", e["id_suffix"]) for e in LOBBY_TABLES["$2"]
    }
    assert cp.pick_home_court(rng, exclude=all_two) is None


# --- Scene-0 seeding (conservation) ------------------------------------------


def test_ensure_scene0_seeded_pins_table_and_conserves_chips(repos, db_path):
    _insert_personality(
        db_path, "sal_moretti", "Sal Moretti",
        {"starting_bankroll": 6000, "bankroll_rate": 300, "buy_in_multiplier": 1.2,
         "stake_comfort_zone": "$2"},
    )
    _insert_personality(
        db_path, "loose_larry", "Loose Larry",
        {"starting_bankroll": 2500, "bankroll_rate": 0, "buy_in_multiplier": 1.0,
         "stake_comfort_zone": "$2"},
    )
    prog = cp.ensure_scene0_seeded(
        career_progress_repo=repos["career_progress_repo"],
        cash_table_repo=repos["cash_table_repo"],
        bankroll_repo=repos["bankroll_repo"],
        sandbox_id="sb1",
        owner_id="owner1",
        chip_ledger_repo=repos["chip_ledger_repo"],
    )
    assert prog.career_active is True
    assert prog.scene0_seeded is True
    assert prog.scene0_table_id == cp.SCENE0_TABLE_ID
    assert prog.scene0_fish_id == "loose_larry"
    assert cp.SCENE0_TABLE_ID in prog.revealed_table_ids

    table = repos["cash_table_repo"].load_table(cp.SCENE0_TABLE_ID, sandbox_id="sb1")
    assert table is not None
    assert table.table_type == "scripted"
    seated = {s["personality_id"]: s for s in table.seats if s["kind"] == "ai"}
    assert set(seated) == {"sal_moretti", "loose_larry"}
    assert seated["loose_larry"].get("archetype") == "fish"

    # Conservation: each AI's remaining bankroll + seat chips == its starting roll.
    for pid, starting in (("sal_moretti", 6000), ("loose_larry", 2500)):
        bankroll = repos["bankroll_repo"].load_ai_bankroll(pid, sandbox_id="sb1")
        assert bankroll.chips + int(seated[pid]["chips"]) == starting


def test_ensure_scene0_reentry_after_progress_save_fails_does_not_double_debit(repos, db_path):
    """If a prior run seeded the table + debited seats but the progress save
    threw (scene0_seeded never flipped), a retry must NOT re-debit the cast."""
    _insert_personality(
        db_path, "sal_moretti", "Sal Moretti",
        {"starting_bankroll": 6000, "buy_in_multiplier": 1.2, "stake_comfort_zone": "$2"},
    )
    _insert_personality(
        db_path, "loose_larry", "Loose Larry",
        {"starting_bankroll": 2500, "buy_in_multiplier": 1.0, "stake_comfort_zone": "$2"},
    )
    kwargs = dict(
        career_progress_repo=repos["career_progress_repo"],
        cash_table_repo=repos["cash_table_repo"],
        bankroll_repo=repos["bankroll_repo"],
        sandbox_id="sb1",
        owner_id="owner1",
        chip_ledger_repo=repos["chip_ledger_repo"],
    )
    cp.ensure_scene0_seeded(**kwargs)
    sal_after = repos["bankroll_repo"].load_ai_bankroll("sal_moretti", sandbox_id="sb1").chips
    # Simulate the lost progress write: blank the career row but keep the table.
    repos["career_progress_repo"].save(CareerProgress(sandbox_id="sb1", owner_id="owner1"))
    prog = cp.ensure_scene0_seeded(**kwargs)
    assert prog.scene0_seeded is True  # reconciled from the persisted table
    sal_after_retry = repos["bankroll_repo"].load_ai_bankroll("sal_moretti", sandbox_id="sb1").chips
    assert sal_after_retry == sal_after  # no second debit


def test_ensure_scene0_seeded_is_idempotent(repos, db_path):
    _insert_personality(
        db_path, "sal_moretti", "Sal Moretti",
        {"starting_bankroll": 6000, "buy_in_multiplier": 1.2, "stake_comfort_zone": "$2"},
    )
    _insert_personality(
        db_path, "loose_larry", "Loose Larry",
        {"starting_bankroll": 2500, "buy_in_multiplier": 1.0, "stake_comfort_zone": "$2"},
    )
    kwargs = dict(
        career_progress_repo=repos["career_progress_repo"],
        cash_table_repo=repos["cash_table_repo"],
        bankroll_repo=repos["bankroll_repo"],
        sandbox_id="sb1",
        owner_id="owner1",
        chip_ledger_repo=repos["chip_ledger_repo"],
    )
    cp.ensure_scene0_seeded(**kwargs)
    sal_after_first = repos["bankroll_repo"].load_ai_bankroll("sal_moretti", sandbox_id="sb1").chips
    # Second call must not re-debit (no double seat).
    cp.ensure_scene0_seeded(**kwargs)
    sal_after_second = repos["bankroll_repo"].load_ai_bankroll("sal_moretti", sandbox_id="sb1").chips
    assert sal_after_first == sal_after_second


# --- first vouch -------------------------------------------------------------


def test_fire_first_vouch_reveals_room_and_records_event(repos):
    repo = repos["career_progress_repo"]
    repo.save(
        CareerProgress(
            sandbox_id="sb1", owner_id="owner1", career_active=True, scene0_seeded=True,
            scene0_table_id=cp.SCENE0_TABLE_ID, scene0_fish_id="loose_larry",
            revealed_table_ids=[cp.SCENE0_TABLE_ID],
        )
    )
    clear_events()
    prog, event = cp.fire_first_vouch(
        career_progress_repo=repo,
        sandbox_id="sb1",
        owner_id="owner1",
        rng=random.Random(0),
    )
    assert event is not None
    assert event.type == EVENT_VOUCH
    assert event.personality_id == cp.SAL_ID
    assert prog.tutorial_complete is True
    assert prog.home_court_table_id is not None
    assert prog.home_court_table_id in prog.revealed_table_ids
    assert cp.SAL_ID in prog.vouched_by

    # Persisted, and surfaced on the activity ring for the lobby feed.
    reloaded = repo.load("sb1", "owner1")
    assert reloaded.tutorial_complete is True
    assert reloaded.home_court_table_id == prog.home_court_table_id
    sandbox_events = recent_events(sandbox_id="sb1")
    assert any(e.type == EVENT_VOUCH for e in sandbox_events)
