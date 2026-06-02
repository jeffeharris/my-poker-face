"""Canonical tournament persona-identity resolver (`tournament.identity`).

The single path that turns a tournament field id (`personality_id` slug, a
synthetic `P##` seat, or the human seat) into a display name — the tournament
analogue of cash's `personality_for_seat` → `load_personality_by_id`.
"""

from tournament.identity import humanize_id, resolve_display_name, resolve_display_names


class _RepoNoBulk:
    """A by-id persona lookup WITHOUT the bulk method, so `resolve_display_names`
    must fall back to per-id resolution.

    `raises=True` makes the per-id lookup blow up so we can assert the resolver
    treats display as best-effort and never propagates a repo failure.
    """

    def __init__(self, names: dict, *, raises: bool = False):
        self._names = names
        self._raises = raises

    def load_personality_by_id(self, pid):
        if self._raises:
            raise RuntimeError("DB down")
        name = self._names.get(pid)
        return {'id': pid, 'name': name} if name else None


class _Repo(_RepoNoBulk):
    """`_RepoNoBulk` plus the side-effect-free bulk lookup the batch resolver
    prefers."""

    def display_names_by_ids(self, ids):
        return {pid: self._names[pid] for pid in ids if pid in self._names}


# --- resolve_display_name ----------------------------------------------------


def test_ai_persona_resolves_to_real_name():
    repo = _Repo({'sun_tzu': 'Sun Tzu'})
    assert resolve_display_name('sun_tzu', personality_repo=repo) == 'Sun Tzu'


def test_ai_persona_db_miss_falls_back_to_humanized_slug():
    repo = _Repo({})  # not in the table
    assert resolve_display_name('lady_macbeth', personality_repo=repo) == 'Lady Macbeth'


def test_no_repo_humanizes_the_id():
    assert resolve_display_name('sun_tzu') == 'Sun Tzu'


def test_synthetic_seat_id_is_left_intact():
    # A `/register` synthetic seat has no persona; it should stay legible.
    assert resolve_display_name('P07') == 'P07'
    assert resolve_display_name('P07', personality_repo=_Repo({})) == 'P07'


def test_human_seat_uses_owner_name():
    assert resolve_display_name('human:abc', is_human=True, owner_name='Jeff') == 'Jeff'


def test_human_seat_without_owner_name_humanizes_id():
    # No owner name in scope (some reconcile paths) → never invents "You" here;
    # callers/the frontend handle the "You" rendering for the human's own seat.
    assert resolve_display_name('P01', is_human=True) == 'P01'


def test_repo_failure_is_best_effort():
    repo = _Repo({'sun_tzu': 'Sun Tzu'}, raises=True)
    # The lookup raises, but display must not — and the value can't have come from
    # the repo (it always raises), so it's unambiguously the fallback path.
    assert resolve_display_name('sun_tzu', personality_repo=repo) == 'Sun Tzu'  # humanized
    assert resolve_display_name('sun_tzu', personality_repo=repo, humanize_fallback=False) == 'sun_tzu'


def test_verbatim_fallback_preserves_unresolved_ids():
    # The completion/standings path: an unresolved id must stay EXACT (no
    # `.title()` mangling of a single-table real name, no prettifying a synthetic
    # seat). Only the repo or owner_name may replace it.
    assert resolve_display_name('McQueen', humanize_fallback=False) == 'McQueen'
    assert resolve_display_name('P07', humanize_fallback=False) == 'P07'
    assert resolve_display_name(
        'whatever', is_human=True, owner_name=None, humanize_fallback=False
    ) == 'whatever'


# --- resolve_display_names (batch) -------------------------------------------


def test_batch_resolves_mixed_field_via_bulk():
    repo = _Repo({'sun_tzu': 'Sun Tzu', 'lady_macbeth': 'Lady Macbeth'})
    out = resolve_display_names(
        ['human:abc', 'sun_tzu', 'lady_macbeth', 'P09'],
        human_id='human:abc',
        owner_name='Jeff',
        personality_repo=repo,
    )
    assert out == {
        'human:abc': 'Jeff',
        'sun_tzu': 'Sun Tzu',
        'lady_macbeth': 'Lady Macbeth',
        'P09': 'P09',  # synthetic / unknown → humanized id
    }


def test_batch_falls_back_when_repo_has_no_bulk_method():
    repo = _RepoNoBulk({'sun_tzu': 'Sun Tzu'})
    out = resolve_display_names(['sun_tzu', 'mystery_id'], personality_repo=repo)
    assert out == {'sun_tzu': 'Sun Tzu', 'mystery_id': 'Mystery Id'}


def test_batch_dedupes_and_drops_empty_ids():
    out = resolve_display_names(['sun_tzu', 'sun_tzu', '', None])
    assert out == {'sun_tzu': 'Sun Tzu'}


def test_humanize_id_basic():
    assert humanize_id('sun_tzu') == 'Sun Tzu'
    assert humanize_id('P12') == 'P12'
