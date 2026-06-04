"""`named_standings` — the Flask-boundary name resolution for tournament
standings (T3-80 step 1). Verifies that raw field `personality_id`s get a
friendly `name` injected beside them (raw id preserved for keying), the human
seat is left for the frontend's "You" handling, and an unresolved id degrades to
a raw fallback rather than crashing. Pure: a fake repo stands in for the persona
lookup, so no DB/app context is needed."""

from flask_app.services.tournament_naming import named_standings
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.field import Elimination
from tournament.session import TournamentSession


class _FakeRepo:
    """Duck-typed personality repo: only the bulk display-name lookup the
    resolver uses. Returns names only for ids it knows (an omitted id models a
    persona that doesn't resolve)."""

    def __init__(self, mapping):
        self._m = mapping

    def display_names_by_ids(self, ids):
        return {i: self._m[i] for i in ids if i in self._m}


def _session(field_size=4, table_size=4, seed=2):
    cfg = TournamentConfig(
        field_size=field_size, table_size=table_size, starting_stack=10000, seed=seed
    )
    # P01 is the human seat; the rest (P02..) are AI seats. (F1 made the
    # no-human_id default None, so the human is now declared explicitly.)
    return TournamentSession(cfg, ai_resolver=FakeHandResolver(), human_id='P01')


def test_named_standings_injects_resolved_seat_and_leader_names():
    s = _session()
    repo = _FakeRepo({'P02': 'Bob Ross', 'P03': 'Joan of Arc'})  # P04 intentionally absent
    sv = named_standings(s, personality_repo=repo)

    seats = {
        seat['player_id']: seat for t in sv['tables'] for seat in t['seats'] if seat['player_id']
    }
    # AI seat resolves; raw player_id preserved for keying.
    assert seats['P02']['name'] == 'Bob Ross'
    assert seats['P02']['player_id'] == 'P02'
    # Human seat is flagged is_human (frontend renders "You"); its raw id is kept.
    assert seats['P01']['is_human'] is True
    # Unresolved AI id degrades to a raw fallback, never crashes / never blanks.
    assert seats['P04']['name'] == 'P04'

    # Leaders carry the same resolved name beside the raw id.
    leaders = {l['player_id']: l for l in sv['leaders']}
    assert leaders['P03']['name'] == 'Joan of Arc'
    assert all('name' in l for l in sv['leaders'])


def test_named_standings_resolves_winner_and_eliminator():
    s = _session()
    repo = _FakeRepo({'P02': 'Bob Ross', 'P03': 'Joan of Arc', 'P04': 'Cruella de Vil'})

    # P02 knocks P03 out in 4th.
    s.field.eliminations.append(
        Elimination(player_id='P03', finishing_position=4, eliminator='P02', round_index=0)
    )
    s.field.stacks.pop('P03', None)

    sv = named_standings(s, personality_repo=repo)
    ko = sv['recent_eliminations'][0]
    assert ko['player_id'] == 'P03' and ko['name'] == 'Joan of Arc'
    assert ko['eliminator'] == 'P02' and ko['eliminator_name'] == 'Bob Ross'

    # Collapse the field to a single AI survivor → winner_name resolves.
    for pid in ('P01', 'P04'):
        s.field.stacks.pop(pid, None)
    assert s.is_complete() and s.winner() == 'P02'
    sv2 = named_standings(s, personality_repo=repo)
    assert sv2['winner'] == 'P02'
    assert sv2['winner_name'] == 'Bob Ross'
