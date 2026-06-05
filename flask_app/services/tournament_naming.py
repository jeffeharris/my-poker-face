"""Resolve a tournament's raw field ids to display names in the Flask layer —
exactly as the live table (`tournament_game_builder`) and the completion screen
(`tournament_completion`) already do.

`TournamentSession.standings_view()` is the game engine's pure read, so it ships
raw `personality_id` slugs (and the human seat id). This is the ONE place the
standings surfaces turn those into friendly names before they reach the client:
it resolves the field ids via the canonical `tournament.identity` resolver (one
bulk persona query) and writes a `name`/`*_name` beside every raw `player_id` in
the payload. Every route/handler that returns standings goes through
`named_standings`, so internal names never leak again.
"""

from __future__ import annotations

from tournament.identity import resolve_display_names


def named_standings(session, *, recent: int = 8, personality_repo=None, owner_name=None) -> dict:
    """`session.standings_view()` with friendly `name` fields injected.

    The raw `player_id`s stay (the frontend keys on them); `name` is additive, so
    an unresolved id is no worse than before. `personality_repo` defaults to the
    app's repo from `extensions` so call sites are just `named_standings(session)`
    and can't forget to resolve.
    """
    if personality_repo is None:
        from flask_app import extensions

        personality_repo = getattr(extensions, 'personality_repo', None)

    standings = session.standings_view(recent=recent)
    human_id = session.human_id

    # Collect every field id the view already carries, resolve once.
    winner = standings.get('winner')
    pids: set[str] = set()
    if winner:
        pids.add(winner)
    for leader in standings.get('leaders', []):
        if leader.get('player_id'):
            pids.add(leader['player_id'])
    for table in standings.get('tables', []):
        for seat in table.get('seats', []):
            if seat.get('player_id'):
                pids.add(seat['player_id'])
    for elim in standings.get('recent_eliminations', []):
        if elim.get('player_id'):
            pids.add(elim['player_id'])
        if elim.get('eliminator'):
            pids.add(elim['eliminator'])
    if human_id:
        pids.add(human_id)

    names = resolve_display_names(
        pids,
        human_id=human_id,
        owner_name=owner_name,
        personality_repo=personality_repo,
    )

    def name_of(pid):
        return names.get(pid) if pid else None

    # Write the resolved names back onto the payload (raw ids untouched).
    standings['winner_name'] = name_of(winner)
    for leader in standings.get('leaders', []):
        leader['name'] = name_of(leader.get('player_id'))
    for table in standings.get('tables', []):
        for seat in table.get('seats', []):
            seat['name'] = name_of(seat.get('player_id'))
    for elim in standings.get('recent_eliminations', []):
        elim['name'] = name_of(elim.get('player_id'))
        elim['eliminator_name'] = name_of(elim.get('eliminator'))
    human = standings.get('human')
    if human and human.get('player_id'):
        human['name'] = name_of(human['player_id'])

    return standings
