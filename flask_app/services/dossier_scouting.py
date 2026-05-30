"""Dossier scouting gate (Phase 2 — the grind).

Pure, Flask-free helpers that turn "hands observed against an opponent" into
which dossier reads are unlocked, and apply that gate to a dossier response.

The model (see `docs/plans/OPPONENT_DOSSIER_PROGRESSION.md`):

- A dossier's *earnable* reads stay locked until you've observed an opponent
  for a **floor** number of hands; below that the file is "classified".
- Past the floor, individual reads ("bits") unlock one at a time as the
  observed-hand count crosses each bit's threshold — the grind.
- Identity (PROFILE), your own STANDING with them, and your FIELD NOTES are
  never gated — only what you'd genuinely have to *scout* is.

Unlock state is **derived on read** (here), never stored: it's a pure
function of the observed-hand count, so a threshold can cross mid-hand and
the next dossier open reflects it. Thresholds are tuning, not design — edit
`SCOUTING_SCHEDULE` freely.

Item-level granularity matches the hybrid decision (grind drips items; the
informant — Phase 3 — will unlock a whole section at once).
"""

from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional, Tuple

# The floor: earnable reads are fully classified below this many observed
# hands. Equals the lowest threshold in the schedule by construction.
FLOOR_HANDS = 25

class ScoutingTier(NamedTuple):
    """One earnable dossier read and the condition that unlocks it.

    `hands` is the hand-count floor — always required. `sample_fields` +
    `sample_min` add an OPPORTUNITY gate (hybrid AND): the read also needs the
    summed lifetime count across `sample_fields` to reach `sample_min`. An
    empty `sample_fields` ⇒ hand-count only (the Tier-1 reads, where the hand
    count *is* the sample count — every hand is a VPIP/PFR observation).

    Why hybrid: a raw hand-count gate lies for opportunity-rare reads. You can
    play 300 hands against a nit and see them c-bet only a handful of times;
    "folds to c-bet 60%" off 4 samples is noise. Requiring N samples of the
    actual spot makes the unlocked stat statistically real, while the hand
    floor keeps a high-variance opponent from trivializing a deep read after a
    short sit. `sample_noun` is the UI phrase for the opportunity.
    """
    id: str
    label: str
    hands: int
    sample_fields: Tuple[str, ...] = ()
    sample_min: int = 0
    sample_noun: str = ''


# Ordered drip schedule. Gating compares the lifetime counts to each tier's
# condition independently (order is cosmetic). Thresholds are tuning, not
# design — edit freely.
SCOUTING_SCHEDULE: List[ScoutingTier] = [
    ScoutingTier('play_style', 'Play style', 25),
    ScoutingTier('vpip', 'VPIP', 25),
    ScoutingTier('pfr', 'PFR', 40),
    ScoutingTier('aggression_factor', 'Aggression', 60),
    ScoutingTier('behavioral_index', 'Behavioral index', 80),
    ScoutingTier('track_record', 'Track record', 100),
    ScoutingTier('pressure', 'Pressure profile', 100),
    ScoutingTier('memorable', 'Memorable hands', 140),
    ScoutingTier('table_posture', 'Table posture', 180),
    # Tier-2 deep postflop reads (B1) — HYBRID gate: a hand floor AND enough
    # samples of the specific spot, so the unlocked stat isn't noise. The
    # sample fields are the opportunity denominators stored on the lifetime
    # row (schema v125); they're summed when a read spans several action
    # buckets. all_in_freq is hand-only because its denominator IS hands.
    ScoutingTier('fold_to_cbet', 'Fold to c-bet', 180,
                 ('cbet_faced_count',), 20, 'c-bets faced'),
    ScoutingTier('cbet_pct', 'C-bet frequency', 180,
                 ('postflop_seen_as_pfr_count',), 15, 'flops as raiser'),
    ScoutingTier('postflop_aggression', 'Postflop aggression', 200,
                 ('postflop_bet_raise_count', 'postflop_call_count'), 30,
                 'postflop actions'),
    ScoutingTier('all_in_freq', 'All-in frequency', 300),
    ScoutingTier('barrel', 'Barreling', 220,
                 ('barrel_opportunity_count',), 12, 'barrel spots'),
    ScoutingTier('polarization', 'Polarization', 260,
                 ('equity_betting_count', 'equity_raising_count',
                  'equity_calling_count'), 25, 'showdown-equity reads'),
    # B2 "the read" — exploit advice + archetype badge, derived from the
    # tiered-bot exploitation detectors. Hand-only tiers: the per-pattern
    # detectors enforce their own sample minimums, so an unlocked-but-thin
    # opponent simply shows no read rather than a wrong one. Archetype unlocks
    # first ("what they are"), the advice later ("how to beat them").
    ScoutingTier('archetype_badge', 'Archetype', 120),
    ScoutingTier('the_read', 'The read', 200),
    # B3 emotional read + B4 field standing. Hand-only: poise/expressiveness
    # are static personality traits and the tilt gauge self-gates on event
    # count, so a thin file simply shows less rather than something wrong.
    ScoutingTier('field_position', 'Field standing', 90),
    ScoutingTier('temperament', 'Temperament', 100),
]

_LABELS = {tier.id: tier.label for tier in SCOUTING_SCHEDULE}

# Maps each Tier-2 grind item to the `deeper_reads` field(s) it controls.
# A locked item nulls its field(s) so the deep read never reaches the client.
_DEEPER_FIELDS: Dict[str, Tuple[str, ...]] = {
    'fold_to_cbet': ('fold_to_cbet',),
    'cbet_pct': ('cbet_attempt_rate',),
    'postflop_aggression': ('aggression_factor_postflop',),
    'all_in_freq': ('all_in_frequency',),
    'barrel': ('barrel_frequency', 'third_barrel_frequency'),
    'polarization': (
        'equity_when_betting',
        'equity_when_raising',
        'equity_when_calling',
    ),
}

# Every gateable field in the deeper_reads block (used to collapse a fully
# redacted block to None so the client renders nothing).
_ALL_DEEPER_FIELDS: Tuple[str, ...] = tuple(
    f for fields in _DEEPER_FIELDS.values() for f in fields
)

# Informant sections (Phase 3): the chunkier units the informant sells. Each
# bundles one or more grind item-ids; buying a section unlocks all of them at
# once (hybrid: grind drips items, the informant buys a section). `price` is a
# flat per-section chip cost for v1 — a tunable lever (scale by section depth
# or opponent stakes later). The informant also bypasses the grind floor.
INFORMANT_SECTIONS: Dict[str, Dict[str, Any]] = {
    'read': {
        'label': 'Behavioral read',
        'price': 750,
        'items': ['play_style', 'vpip', 'pfr', 'aggression_factor'],
    },
    'behavioral_index': {
        'label': 'Behavioral index',
        'price': 500,
        'items': ['behavioral_index'],
    },
    'track_record': {
        'label': 'Track record',
        'price': 1000,
        'items': ['track_record', 'pressure', 'memorable'],
    },
    'table_posture': {
        'label': 'Table posture',
        'price': 500,
        'items': ['table_posture'],
    },
    'deep_reads': {
        'label': 'Deep postflop read',
        'price': 1500,
        'items': [
            'fold_to_cbet', 'cbet_pct', 'postflop_aggression',
            'all_in_freq', 'barrel', 'polarization',
        ],
    },
    'tactical_read': {
        'label': 'The read',
        'price': 1250,
        'items': ['archetype_badge', 'the_read'],
    },
    'tells': {
        'label': 'Tells & temperament',
        'price': 600,
        'items': ['temperament', 'field_position'],
    },
}


def _purchased_item_ids(purchased_sections) -> set:
    """Flatten purchased section ids into the item-ids they unlock."""
    items: set = set()
    for section_id in purchased_sections or ():
        cfg = INFORMANT_SECTIONS.get(section_id)
        if cfg:
            items.update(cfg['items'])
    return items


def _normalize_counts(observed) -> Dict[str, Any]:
    """Accept either a scalar hand count (legacy callers) or the full lifetime
    counts dict (which carries the opportunity denominators the hybrid gate
    needs). Always returns a dict with at least `hands_observed`."""
    if isinstance(observed, dict):
        return observed
    return {'hands_observed': observed or 0}


def compute_scouting(observed, purchased_sections=None) -> Dict[str, Any]:
    """Derive the unlock state, accounting for informant-purchased sections.

    `observed` is either the lifetime counts dict (preferred — enables the
    Tier-2 opportunity gates) or a bare hand count (legacy; sample-gated tiers
    then read 0 samples and stay locked until the dict is supplied).

    Effective unlock = grind unlocks (hand floor AND, for Tier-2, enough
    samples) ∪ purchased-section items. Returns the descriptor the dossier
    surfaces to the client:
      - `hands_observed`, `floor`, `floor_met`
      - `unlocked`: list of unlocked item_ids
      - `locked`: list of {id, label, unlocks_at[, sample_min, samples_observed,
        sample_noun]} still to earn by grinding
      - `informant_offers`: still-buyable sections {id, label, price}
    """
    counts = _normalize_counts(observed)
    hands = max(0, int(counts.get('hands_observed', 0) or 0))
    bought = _purchased_item_ids(purchased_sections)

    unlocked: List[str] = []
    locked: List[Dict[str, Any]] = []
    for tier in SCOUTING_SCHEDULE:
        hand_ok = hands >= tier.hands
        if tier.sample_fields:
            samples = sum(int(counts.get(f, 0) or 0) for f in tier.sample_fields)
            sample_ok = samples >= tier.sample_min
        else:
            samples = None
            sample_ok = True

        if (hand_ok and sample_ok) or tier.id in bought:
            unlocked.append(tier.id)
        else:
            entry: Dict[str, Any] = {
                'id': tier.id, 'label': tier.label, 'unlocks_at': tier.hands,
            }
            # Surface the sample requirement + progress so the UI can render
            # "Face them c-bet 20 times (12/20)" instead of a bare hand count.
            if tier.sample_fields:
                entry['sample_min'] = tier.sample_min
                entry['samples_observed'] = samples
                entry['sample_noun'] = tier.sample_noun
            locked.append(entry)

    unlocked_set = set(unlocked)
    # A section is still buyable when any of its items remain locked.
    offers = [
        {'id': sid, 'label': cfg['label'], 'price': cfg['price']}
        for sid, cfg in INFORMANT_SECTIONS.items()
        if any(item not in unlocked_set for item in cfg['items'])
    ]

    return {
        'hands_observed': hands,
        'floor': FLOOR_HANDS,
        'floor_met': hands >= FLOOR_HANDS,
        'unlocked': unlocked,
        'locked': locked,
        'informant_offers': offers,
    }


def _strip_observation_field(response: Dict[str, Any], field: str) -> None:
    obs = response.get('observation')
    if isinstance(obs, dict) and obs.get(field) is not None:
        obs[field] = None


def _strip_deeper_field(response: Dict[str, Any], field: str) -> None:
    deeper = response.get('deeper_reads')
    if isinstance(deeper, dict) and deeper.get(field) is not None:
        deeper[field] = None


def _redact_item(response: Dict[str, Any], item_id: str) -> None:
    """Null out the response field(s) a locked item controls."""
    if item_id in _DEEPER_FIELDS:
        for field in _DEEPER_FIELDS[item_id]:
            _strip_deeper_field(response, field)
    elif item_id == 'the_read':
        response['the_read'] = []
    elif item_id == 'archetype_badge':
        response['archetype'] = None
    elif item_id == 'temperament':
        response['temperament'] = None
    elif item_id == 'field_position':
        response['field_position'] = None
    elif item_id in ('play_style', 'vpip', 'pfr', 'aggression_factor'):
        _strip_observation_field(response, item_id)
    elif item_id == 'behavioral_index':
        personality = response.get('personality')
        if isinstance(personality, dict) and isinstance(
            personality.get('anchors'), dict
        ):
            personality['anchors'] = {k: None for k in personality['anchors']}
    elif item_id == 'track_record':
        response['cash_pair_stats'] = None
    elif item_id == 'pressure':
        response['pressure_summary'] = None
    elif item_id == 'memorable':
        response['memorable_hands'] = []
    elif item_id == 'table_posture':
        response['ai_bankroll'] = None
        response['stake_summary'] = {
            'as_borrower': {'carry_count': 0, 'total_carried': 0},
            'as_staker': {'carry_count': 0, 'total_owed_to_them': 0},
        }


def apply_scouting_gate(
    response: Dict[str, Any],
    hands_observed,
    purchased_sections=None,
) -> Dict[str, Any]:
    """Gate a dossier `response` in place and return the scouting descriptor.

    `hands_observed` is either the lifetime counts dict (preferred — drives the
    Tier-2 opportunity gates) or a bare hand count (legacy). Strips the values
    of every still-locked earnable read so locked intel is never sent to the
    client, then returns the descriptor (also attached as
    `response['scouting']`). Informant-purchased sections count as unlocked
    (they bypass the grind floor). When nothing is unlocked, every earnable
    read is redacted. Always-free sections (PROFILE, STANDING, FIELD NOTES,
    emotion) are untouched.
    """
    scouting = compute_scouting(hands_observed, purchased_sections)
    locked_ids = {entry['id'] for entry in scouting['locked']}
    for item_id in locked_ids:
        _redact_item(response, item_id)

    # Collapse a fully-redacted observation block to None so the client
    # renders nothing rather than an empty stat panel — the scouting strip
    # already surfaces the hand count below the floor.
    obs = response.get('observation')
    if isinstance(obs, dict) and all(
        obs.get(f) is None
        for f in ('play_style', 'vpip', 'pfr', 'aggression_factor')
    ):
        response['observation'] = None

    # Same collapse for the deep-reads block: when every gateable field is
    # redacted the client renders nothing rather than an empty panel.
    deeper = response.get('deeper_reads')
    if isinstance(deeper, dict) and all(
        deeper.get(f) is None for f in _ALL_DEEPER_FIELDS
    ):
        response['deeper_reads'] = None

    response['scouting'] = scouting
    return scouting
