"""Dossier "the history" (parked item, 2026-05-30) — the rivalry read.

Relationship events between the human and an opponent are persisted in
`memorable_hands.memory_type` (the `RelationshipEvent.value` taxonomy). The
dossier already lists individual memorable hands; this turns the *aggregate*
into a rivalry read: a one-line headline, the single defining clash hand, and
the clash/banter event tallies.

Pure presentation over `GameRepository.load_relationship_history` output. No
poker logic, no new storage.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Hand-vs-hand events (RelationshipEvent hand-outcome `.value`s) — the rivalry
# moments. The chat-event family ("chat_*") is banter, bucketed separately.
CLASH_EVENTS = (
    'cooler',
    'bad_beat',
    'hero_call',
    'dominated_showdown',
    'bluffed_off',
    'strong_fold_shown',
    'big_win',
    'big_loss',
)

_EVENT_LABELS = {
    'cooler': 'cooler',
    'bad_beat': 'bad beat',
    'hero_call': 'hero call',
    'dominated_showdown': 'domination',
    'bluffed_off': 'bluff',
    'strong_fold_shown': 'big laydown',
    'big_win': 'big win',
    'big_loss': 'big loss',
    'chat_trash_talk': 'trash talk',
    'chat_compliment': 'compliment',
    'chat_taunt_post_win': 'taunt',
    'chat_friendly_banter': 'banter',
    'chat_props': 'props',
    'chat_flattery_landed': 'flattery',
    'chat_flattery_backfired': 'flattery (caught)',
}


def _label(event: str) -> str:
    return _EVENT_LABELS.get(event, event.replace('_', ' '))


def _synthesize_line(counts: Dict[str, int]) -> str:
    """Punchy rivalry headline from the clash tallies.

    Directionally safe: `cooler`/`bad_beat` are unambiguously suffered by the
    human-observer; `hero_call` is the human's moment. The other clash events
    show as chips but don't drive a directional claim.
    """
    against = counts.get('cooler', 0) + counts.get('bad_beat', 0)
    your_moments = counts.get('hero_call', 0)
    if against >= 2:
        return "Bad blood — they've put more than one brutal hand on you."
    if against == 1:
        return "There's a scar here — one you won't forget."
    if your_moments >= 1:
        return "You've had their number in the big moments."
    return "Some history between you, nothing decisive yet."


def build_relationship_history(
    history: Optional[dict],
) -> Optional[dict]:
    """Shape the repo's relationship-history aggregate into the dossier block.

    Returns `{line, defining, clash, banter}` or None when there's no logged
    history. `clash`/`banter` are `[{event, label, count}]` ordered most-frequent
    first; `defining` is the top clash hand with a pretty label, or None.
    """
    counts = (history or {}).get('counts') or {}
    if not counts:
        return None

    clash: List[Dict[str, Any]] = [
        {'event': ev, 'label': _label(ev), 'count': n}
        for ev, n in counts.items()
        if ev in CLASH_EVENTS and n
    ]
    banter: List[Dict[str, Any]] = [
        {'event': ev, 'label': _label(ev), 'count': n}
        for ev, n in counts.items()
        if ev.startswith('chat_') and n
    ]
    if not clash and not banter:
        return None

    clash.sort(key=lambda c: (-c['count'], c['label']))
    banter.sort(key=lambda c: (-c['count'], c['label']))

    defining = (history or {}).get('defining')
    if defining:
        defining = {
            'event': defining['event'],
            'label': _label(defining['event']),
            'impact_score': defining.get('impact_score', 0.0),
            'narrative': defining.get('narrative', ''),
        }

    return {
        'line': _synthesize_line(counts),
        'defining': defining,
        'clash': clash,
        'banter': banter,
    }
