"""Round-trip serialization for tournament persistence (Persistence layer A).

These cover the pure `to_dict`/`from_dict` on the engine types — no DB, no
Flask. The repository + cold-load wiring is tested separately. The invariant
that matters: a serialized-then-restored session is indistinguishable from the
original (same standings, same chip conservation) and can keep playing.
"""

import json

import pytest

from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.field import TournamentField
from tournament.seating import Seating
from tournament.session import TournamentSession


def _session(seed=1, field_size=12, table_size=6, human_id=None):
    cfg = TournamentConfig(field_size=field_size, table_size=table_size, seed=seed)
    return TournamentSession(cfg, ai_resolver=FakeHandResolver(), human_id=human_id)


def _fake_human_hand(seat_order, stacks, level, button, seed):
    return FakeHandResolver().resolve(seat_order, stacks, level, button, seed)


def _roundtrip(session) -> TournamentSession:
    """Serialize through JSON (the real storage path) and back."""
    blob = json.dumps(session.to_dict())
    return TournamentSession.from_dict(json.loads(blob), FakeHandResolver())


# ── component round-trips ────────────────────────────────────────────────────


def test_config_roundtrip_preserves_all_fields():
    cfg = TournamentConfig(
        field_size=24,
        table_size=8,
        starting_stack=15_000,
        seed=7,
        starting_big_blind=200,
        blind_growth=2.0,
        rounds_per_level=3,
        field_archetypes=('TAG', 'LAG', 'Rock'),
        max_rounds=500,
    )
    restored = TournamentConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
    assert restored == cfg  # frozen dataclass equality covers every field


def test_field_roundtrip_preserves_stacks_and_eliminations():
    s = _session()
    for _ in range(6):
        if s.is_complete() or s.human_out:
            break
        s.play_round(_fake_human_hand)
    field = s.field
    restored = TournamentField.from_dict(json.loads(json.dumps(field.to_dict())))
    assert restored.stacks == field.stacks
    assert restored.entries == field.entries
    assert [e.to_dict() for e in restored.eliminations] == [
        e.to_dict() for e in field.eliminations
    ]
    assert restored.chip_sum() == field.chip_sum()


def test_seating_roundtrip_preserves_tables_and_buttons():
    s = _session()
    for _ in range(4):
        if s.is_complete() or s.human_out:
            break
        s.play_round(_fake_human_hand)
    seating = s.seating
    restored = Seating.from_dict(json.loads(json.dumps(seating.to_dict())))
    assert restored.table_size == seating.table_size
    assert [(t.table_id, t.seats, t.button) for t in restored.tables] == [
        (t.table_id, t.seats, t.button) for t in seating.tables
    ]


# ── session round-trips ──────────────────────────────────────────────────────


def test_fresh_session_roundtrip_matches_standings():
    s = _session(seed=3)
    restored = _roundtrip(s)
    assert restored.standings_view() == s.standings_view()
    assert restored.human_id == s.human_id
    assert restored.rounds == s.rounds
    assert restored._hand_counter == s._hand_counter
    restored.field.assert_conservation()


def test_real_persona_field_with_human_seat_roundtrips():
    """Regression: a real-persona field whose human seat is `human:<owner>` (every
    P3 invite tournament — and a persona-id human for autonomous ones) must survive
    a `from_dict` cold load. The bug regenerated a synthetic `P##` field from
    config and couldn't find the saved human seat → "human_id ... is not in the
    field", crashing cold-load /sit and the autonomous ticker advance after any
    restart."""
    entries = {
        'human:owner-x': 'You', 'einstein': 'Einstein', 'napoleon': 'Napoleon',
        'batman': 'Batman', 'ada': 'Ada', 'sun_tzu': 'Sun Tzu',
    }
    cfg = TournamentConfig(field_size=len(entries), table_size=3, seed=2)
    s = TournamentSession(cfg, ai_resolver=FakeHandResolver(),
                          human_id='human:owner-x', entries=entries)
    restored = _roundtrip(s)  # would raise ValueError before the fix
    assert restored.human_id == 'human:owner-x'
    assert 'human:owner-x' in restored.entries
    assert restored.standings_view() == s.standings_view()
    restored.field.assert_conservation()


def test_midgame_session_roundtrip_matches_standings():
    s = _session(seed=5)
    for _ in range(10):
        if s.is_complete() or s.human_out:
            break
        s.play_round(_fake_human_hand)
    restored = _roundtrip(s)
    assert restored.standings_view() == s.standings_view()
    assert restored.rounds == s.rounds
    assert restored._hand_counter == s._hand_counter
    restored.field.assert_conservation()


def test_restored_session_keeps_playing_deterministically():
    """A restored session must advance identically to one that never left
    memory — same seed, same counters in, same world out."""
    a = _session(seed=9)
    for _ in range(5):
        if a.is_complete() or a.human_out:
            break
        a.play_round(_fake_human_hand)

    b = _roundtrip(a)

    for _ in range(5):
        if a.is_complete() or a.human_out:
            break
        a.play_round(_fake_human_hand)
        b.play_round(_fake_human_hand)

    assert b.standings_view() == a.standings_view()
    b.field.assert_conservation()


def test_roundtrip_after_human_out_then_play_out():
    """Persistence has to survive the human busting and the field finishing."""
    s = _session(seed=2, field_size=6, table_size=3)

    def _human_loses(seat_order, stacks, level, button, seed):
        out = dict(stacks)
        human = s.human_id
        if human in out and len(seat_order) >= 2:
            victim_chips = out[human]
            out[human] = 0
            other = next(p for p in seat_order if p != human)
            out[other] += victim_chips
        return out

    while not s.is_complete() and not s.human_out:
        s.play_round(_human_loses)

    restored = _roundtrip(s)
    assert restored.human_out == s.human_out
    assert restored.standings_view() == s.standings_view()
    if not restored.is_complete():
        restored.play_out()
        restored.field.assert_conservation()
        assert restored.is_complete()


def test_corrupt_restore_fails_conservation_loudly():
    """A tampered blob (dropped chips) must raise on rehydrate, not silently
    restore a broken world."""
    s = _session(seed=4)
    d = s.to_dict()
    victim = next(iter(d['field']['stacks']))
    d['field']['stacks'][victim] -= 1
    with pytest.raises(AssertionError):
        TournamentSession.from_dict(d, FakeHandResolver())
