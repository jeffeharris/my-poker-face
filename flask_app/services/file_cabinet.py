"""File cabinet (dossier Phase 4) — the roster of everyone you've met.

Pure, Flask-free aggregator: builds the browsable index of every opponent the
observer has accumulated scouting on in a sandbox, with the headline stats the
UI sorts by (hands observed, lifetime PnL, heat, dossier-unlock progress) and
the "People met / Dossiers unlocked" header counts.

The roster spine is the lifetime observation store (everyone you've been dealt
in with — the same source the grind gate reads), joined to cash PnL and the
relationship axes. Sorting is left to the client; this returns the full list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from flask_app.services.dossier_scouting import SCOUTING_SCHEDULE, compute_scouting

_READS_TOTAL = len(SCOUTING_SCHEDULE)


def build_file_cabinet(
    *,
    sandbox_id: str,
    observer_id: str,
    game_repo: Any,
    relationship_repo: Any,
    personality_repo: Any,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Assemble the file-cabinet roster for one observer in one sandbox."""
    roster = game_repo.list_observation_lifetime_for_observer(sandbox_id, observer_id)
    if not roster:
        return {'people': [], 'people_met': 0, 'dossiers_unlocked': 0}

    opponent_ids = [r['opponent_id'] for r in roster]

    # PnL (observer POV), relationship axes, names, informant purchases —
    # one query each, joined in memory.
    pnl_by_opp = {
        s.opponent_id: s
        for s in relationship_repo.list_cash_pair_stats_for_observer(
            observer_id, sandbox_id=sandbox_id
        )
    }
    rels = relationship_repo.load_all_relationships(observer_id, now=now)
    names = personality_repo.display_names_by_ids(opponent_ids)
    purchases = game_repo.load_all_informant_unlocks_for_observer(
        sandbox_id, observer_id
    )

    people: List[Dict[str, Any]] = []
    dossiers_unlocked = 0
    for r in roster:
        oid = r['opponent_id']
        scouting = compute_scouting(r['hands_observed'], purchases.get(oid))
        reads_unlocked = len(scouting['unlocked'])
        fully = not scouting['locked']
        if fully:
            dossiers_unlocked += 1

        pnl = pnl_by_opp.get(oid)
        rel = rels.get(oid)
        people.append({
            'personality_id': oid,
            'name': names.get(oid, oid),
            'hands_observed': r['hands_observed'],
            'net_pnl': pnl.cumulative_pnl if pnl else 0,
            'hands_played_cash': pnl.hands_played_cash if pnl else 0,
            'heat': rel.heat if rel else 0.0,
            'respect': rel.respect if rel else 0.5,
            'likability': rel.likability if rel else 0.5,
            'last_seen': (
                rel.last_seen.isoformat() if rel and rel.last_seen else None
            ),
            'reads_unlocked': reads_unlocked,
            'reads_total': _READS_TOTAL,
            'floor_met': scouting['floor_met'],
            'fully_unlocked': fully,
        })

    return {
        'people': people,
        'people_met': len(people),
        'dossiers_unlocked': dossiers_unlocked,
    }
