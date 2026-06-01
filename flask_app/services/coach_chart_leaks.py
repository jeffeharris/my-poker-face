"""Chart-graded preflop leaks — your real frequencies vs the solver chart.

The honest upgrade over the opening-only VPIP finder (``coach_leaks``): grade a
human's preflop decisions against the *same* depth-aware solver charts the
TieredBot plays from (see ``poker.strategy.preflop_reference``), by
**frequency deviation over a sample** rather than a binary in/out-of-range call.

A single off-chart action is never a leak — the charts are mixed strategies
(fold 75% / call 10% / 3-bet 15%). A *repeated* frequency gap is. That matches
the leak-loop framing: catch the things you habitually get wrong, not variance.

Three plain-language leak kinds:
  - ``too_loose``   — you play a hand the chart folds.
  - ``over_fold``   — you fold a hand the chart plays.
  - ``too_passive`` — you flat where the chart raises (the faced-raise / 3-bet
                      signal the VPIP finder can't see).

Pure core: the reference resolver is injected (like ``coach_leaks.reference``),
so this is fully unit-testable with no chart load and no DB.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .coach_leaks import CONFIRM_MIN_SEEN, DEFAULT_MIN_SAMPLE

# A frequency gap (0-1) on the dominant action must exceed this to count as a
# leak. Honest with the heuristic: we only claim the obvious stuff.
#  - exact-hand grouping is sparse and noisy → demand a big gap (~a third).
#  - position aggregates are stable (normalized to the hands you held) → a
#    smaller, still-gross gap is trustworthy.
HAND_DEVIATION_MIN = 0.35
POSITION_DEVIATION_MIN = 0.20

# A hand the chart folds at least this often is "trash here" — playing it is a
# too_loose leak, not a limp leak (the primary error is playing it at all).
TRASH_FOLD_FLOOR = 0.60

# Below this effective stack, multiway preflop is push/fold-ish and the deep
# charts mis-calibrate; the bot routes HU to a push/fold chart and multiway to
# a heuristic. We grade neither here — disclosed, not silently mis-graded.
PUSH_FOLD_FLOOR_BB = 15.0

# A reference resolver: (hand, position, scenario, opener, eff_bb, num_players)
# -> {fold, call, raise} freqs, or None when the chart has no entry.
ReferenceResolver = Callable[
    [str, str, str, Optional[str], float, int], Optional[Dict[str, float]]
]


@dataclass
class ChartLeak:
    """One (scenario, position, hand) where your frequencies diverge."""

    scenario: str  # 'rfi' | 'vs_open' | 'vs_3bet'
    position: str  # 6-max label
    hand: str  # canonical e.g. 'KJs', or '' for a position aggregate
    kind: str  # 'limp' | 'too_loose' | 'over_fold' | 'too_passive'
    n: int  # decisions observed for this node-class
    your_freq: Dict[str, float]  # {fold, call, raise}
    chart_freq: Dict[str, float]  # {fold, call, raise}
    gap: float  # 0-1, deviation on the dominant action
    severity: float  # gap * n — habitual-bleed proxy, ranking key
    status: str = 'watching'  # 'watching' (small sample) | 'confirmed'


@dataclass
class ChartLeakReport:
    leaks: List[ChartLeak]  # ranked worst-first
    total_decisions: int
    graded: int  # decisions actually compared to a chart
    eligible_groups: int = 0  # node-classes that met the sample gate
    skipped: Dict[str, int] = field(default_factory=dict)  # reason -> count


# ── Action bucketing ────────────────────────────────────────────────────

def bucket_action(action_taken: Optional[str]) -> Optional[str]:
    """Human ``action_taken`` → ``fold`` | ``call`` | ``raise`` (or None).

    Preflop ``check`` means the SB completed / limped (a voluntary continue) —
    grade it as ``call``. BB checking its free option is excluded upstream (no
    decision). Anything aggressive (raise/bet/jam/all-in) → ``raise``.
    """
    if not action_taken:
        return None
    a = action_taken.strip().lower()
    if a == 'fold':
        return 'fold'
    if a in ('call', 'check', 'limp', 'complete'):
        return 'call'
    if a in ('raise', 'bet', 'jam', 'all_in', 'all-in', 'allin', 'shove', 'reraise'):
        return 'raise'
    return None


def _vpip(freq: Dict[str, float]) -> float:
    return freq.get('call', 0.0) + freq.get('raise', 0.0)


def _classify(
    scenario: str, your: Dict[str, float], chart: Dict[str, float], deviation_min: float
) -> Optional[Tuple[str, float]]:
    """Pick the dominant leak kind + its frequency gap, or None if clean.

    Evaluates candidate gaps and returns the largest that clears
    ``deviation_min``:
      - too_loose:   chart mostly folds, you play it       (your_vpip - chart_vpip)
      - limp:        opening spot, you call where the chart raises-or-folds a
                     playable hand                          (your_call - chart_call)
      - over_fold:   chart mostly plays, you fold it        (your_fold - chart_fold)
      - too_passive: chart strongly raises, you flat instead (chart_raise - your_raise)
    """
    candidates: List[Tuple[str, float]] = []

    # too_loose: chart wants to fold this hand here, you don't.
    if chart['fold'] >= TRASH_FOLD_FLOOR:
        candidates.append(('too_loose', _vpip(your) - _vpip(chart)))

    # limp: an open spot where the chart raises-or-folds (call ≈ 0) a PLAYABLE
    # hand, but you flat-call. Gated to non-trash (else it's too_loose) — the
    # error is forfeiting the raise, not playing the hand.
    elif scenario == 'rfi':
        candidates.append(('limp', your['call'] - chart['call']))

    # over_fold: chart continues with this hand, you over-fold it.
    if chart['fold'] <= 0.40:
        candidates.append(('over_fold', your['fold'] - chart['fold']))

    # too_passive: chart strongly raises, you call instead of raising.
    if chart['raise'] >= 0.55 and your['call'] > your['raise']:
        candidates.append(('too_passive', chart['raise'] - your['raise']))

    best = max(candidates, key=lambda c: c[1], default=None)
    if best is None or best[1] < deviation_min:
        return None
    return best


# ── Grading ─────────────────────────────────────────────────────────────

def compute_chart_leaks(
    decisions: List[dict],
    resolve_ref: ReferenceResolver,
    *,
    group_by: str = 'hand',
    min_sample: Optional[int] = None,
    deviation_min: Optional[float] = None,
) -> ChartLeakReport:
    """Grade decisions against the injected chart reference.

    Each decision dict needs: ``hand``, ``position`` (6-max label),
    ``scenario`` ('rfi'|'vs_open'|'vs_3bet'), ``opener`` (str|None),
    ``effective_stack_bb`` (float), ``num_players`` (int), ``action`` (raw).

    ``group_by``:
      - ``'position'`` (the headline): aggregate every hand at a
        (scenario, position) into one read, with the chart expectation
        normalized to the hands you actually held. Volume-efficient — gives a
        signal from a few dozen hands.
      - ``'hand'``: per (scenario, position, hand). Concrete but sparse — needs
        a hand to repeat. A finer detail tier for high volume.

    Defaults pick a sample gate and deviation threshold appropriate to the
    granularity (aggregates are stable, so a smaller gap is trustworthy).
    """
    by_hand = group_by == 'hand'
    if deviation_min is None:
        deviation_min = HAND_DEVIATION_MIN if by_hand else POSITION_DEVIATION_MIN

    metrics, graded, eligible, skipped = _grade_groups(
        decisions, resolve_ref, group_by=group_by, min_sample=min_sample
    )
    leaks = [
        ChartLeak(
            scenario=m['scenario'],
            position=m['position'],
            hand=m['hand'],
            kind=m['kind'],
            n=m['n'],
            your_freq=m['your_freq'],
            chart_freq=m['chart_freq'],
            gap=m['gap'],
            severity=round(m['gap'] * m['n'], 3),
            status=m['status'],
        )
        for m in metrics.values()
        if m['kind'] is not None and m['gap'] >= deviation_min
    ]
    leaks.sort(key=lambda lk: lk.severity, reverse=True)
    return ChartLeakReport(
        leaks=leaks,
        total_decisions=len(decisions),
        graded=graded,
        eligible_groups=eligible,
        skipped=skipped,
    )


def _grade_groups(
    decisions: List[dict],
    resolve_ref: ReferenceResolver,
    *,
    group_by: str = 'position',
    min_sample: Optional[int] = None,
) -> tuple:
    """Grade decisions into per-group metrics — the shared core under
    ``compute_chart_leaks`` and ``compute_slice_diff``.

    Returns ``(metrics, graded, eligible, skipped)`` where ``metrics`` maps a
    group key to ``{scenario, position, hand, kind, gap, n, status, your_freq,
    chart_freq}`` for EVERY eligible group — leak or not (so slices can be
    diffed on the dominant gap, not just thresholded leaks). ``kind`` is None
    when no leak shape applies; ``gap`` is the dominant deviation regardless of
    threshold.
    """
    by_hand = group_by == 'hand'
    if min_sample is None:
        min_sample = DEFAULT_MIN_SAMPLE if by_hand else 5

    skipped: Dict[str, int] = defaultdict(int)
    groups: Dict[tuple, dict] = defaultdict(lambda: {'actions': [], 'refs': []})

    for d in decisions:
        bucket = bucket_action(d.get('action'))
        if bucket is None:
            skipped['unparsed'] += 1
            continue
        pos, scen, hand = d.get('position'), d.get('scenario'), d.get('hand')
        if not (pos and scen and hand):
            skipped['unparsed'] += 1
            continue
        eff_bb = d.get('effective_stack_bb') or 0.0
        nplayers = d.get('num_players') or 0
        # Short multiway has no clean chart reference (push/fold is HU-only).
        if eff_bb and eff_bb < PUSH_FOLD_FLOOR_BB and nplayers > 2:
            skipped['short_multiway'] += 1
            continue
        opener = d.get('opener')
        ref = resolve_ref(hand, pos, scen, opener, eff_bb, nplayers)
        if ref is None:
            skipped['no_reference'] += 1
            continue
        key = (scen, pos, opener, hand) if by_hand else (scen, pos)
        g = groups[key]
        g['actions'].append(bucket)
        g['refs'].append(ref)

    graded = sum(len(g['actions']) for g in groups.values())
    eligible = sum(1 for g in groups.values() if len(g['actions']) >= min_sample)

    metrics: Dict[tuple, dict] = {}
    for key, g in groups.items():
        n = len(g['actions'])
        if n < min_sample:
            continue
        scen, pos = key[0], key[1]
        hand = key[3] if by_hand else ''
        your = {
            k: sum(1 for a in g['actions'] if a == k) / n for k in ('fold', 'call', 'raise')
        }
        chart = {k: sum(r[k] for r in g['refs']) / n for k in ('fold', 'call', 'raise')}
        # deviation_min=-inf → always return the dominant (kind, gap), unthresholded.
        verdict = _classify(scen, your, chart, float('-inf'))
        kind, gap = verdict if verdict is not None else (None, 0.0)
        metrics[key] = {
            'scenario': scen,
            'position': pos,
            'hand': hand,
            'kind': kind,
            'gap': round(gap, 3),
            'n': n,
            'status': 'confirmed' if n >= CONFIRM_MIN_SEEN else 'watching',
            'your_freq': {k: round(v, 3) for k, v in your.items()},
            'chart_freq': {k: round(v, 3) for k, v in chart.items()},
        }
    return metrics, graded, eligible, dict(skipped)


# ── Recency slicing + diff ───────────────────────────────────────────────

# A leak's gap must move by at least this (≈10 points) to count as shrinking or
# worsening rather than holding steady.
TREND_DELTA = 0.10


def recent_slice(decisions: List[dict], n_hands: int = 500) -> List[dict]:
    """The player's most recent ``n_hands`` decisions, by created_at then
    hand_number. Decisions missing a timestamp sort oldest (so they fall out of
    the recent window first). Returns all of them when fewer than the window."""
    ordered = sorted(
        decisions, key=lambda d: (d.get('created_at') or '', d.get('hand_number') or 0)
    )
    if n_hands and len(ordered) > n_hands:
        return ordered[-n_hands:]
    return ordered


def compute_slice_diff(
    all_decisions: List[dict],
    recent_decisions: List[dict],
    resolve_ref: ReferenceResolver,
    *,
    group_by: str = 'position',
    delta: float = TREND_DELTA,
) -> tuple:
    """Diff a recent slice against all-time. Returns ``(trends, emerging)``.

    ``trends`` maps each ALL-TIME leak's key → ``{n, gap, status, trend}`` where
    trend ∈ shrinking / persistent / worsening / cleared / insufficient.
    ``emerging`` is the list of metric dicts that are leaks in the recent slice
    but NOT all-time (newly showing up).
    """
    dev = HAND_DEVIATION_MIN if group_by == 'hand' else POSITION_DEVIATION_MIN
    all_m, *_ = _grade_groups(all_decisions, resolve_ref, group_by=group_by)
    rec_m, *_ = _grade_groups(recent_decisions, resolve_ref, group_by=group_by)

    def is_leak(m: dict) -> bool:
        return m['kind'] is not None and m['gap'] >= dev

    all_leak_keys = {k for k, m in all_m.items() if is_leak(m)}
    trends: Dict[tuple, dict] = {}
    for k in all_leak_keys:
        rm = rec_m.get(k)
        if rm is None:  # not enough recent hands in this spot to judge
            trends[k] = {'n': 0, 'gap': None, 'status': None, 'trend': 'insufficient'}
            continue
        ga, gr = all_m[k]['gap'], rm['gap']
        if gr < dev:
            trend = 'cleared'
        elif gr <= ga - delta:
            trend = 'shrinking'
        elif gr >= ga + delta:
            trend = 'worsening'
        else:
            trend = 'persistent'
        trends[k] = {'n': rm['n'], 'gap': rm['gap'], 'status': rm['status'], 'trend': trend}

    emerging = [rec_m[k] for k in rec_m if is_leak(rec_m[k]) and k not in all_leak_keys]
    return trends, emerging


# ── Prompt text ─────────────────────────────────────────────────────────

_SCENARIO_PHRASE = {
    'rfi': 'opening from {pos}',
    'vs_open': 'facing a raise in {pos}',
    'vs_3bet': 'facing a 3-bet in {pos}',
}


def _pct(x: float) -> int:
    return round(x * 100)


def _leak_line(lk: ChartLeak) -> str:
    spot = _SCENARIO_PHRASE.get(lk.scenario, '{pos}').format(pos=lk.position)
    subject = f"{lk.hand} {spot}" if lk.hand else spot
    if lk.kind == 'limp':
        detail = (
            f"you open-limp (call) {_pct(lk.your_freq['call'])}% — the solver "
            "raises or folds here, never limps"
        )
    elif lk.kind == 'too_loose':
        detail = (
            f"you play it {_pct(_vpip(lk.your_freq))}% of the time; the solver "
            f"folds {_pct(lk.chart_freq['fold'])}%"
        )
    elif lk.kind == 'over_fold':
        detail = (
            f"you fold {_pct(lk.your_freq['fold'])}%; the solver continues "
            f"{_pct(_vpip(lk.chart_freq))}%"
        )
    else:  # too_passive
        detail = (
            f"you just call; the solver raises {_pct(lk.chart_freq['raise'])}% "
            "of the time"
        )
    return f"- {subject}: {detail} (seen {lk.n}×)"


def format_chart_leaks_for_prompt(report: ChartLeakReport) -> str:
    """Plain-text profile for the coach prompt — confirmed vs watching."""
    if report.graded == 0:
        return "No chart-gradeable preflop history yet for this player."

    confirmed = [lk for lk in report.leaks if lk.status == 'confirmed']
    watching = [lk for lk in report.leaks if lk.status == 'watching']

    lines = [
        f"PREFLOP CHART PROFILE — graded {report.graded} decisions against the "
        "solver charts (the standard the bots play)."
    ]
    if confirmed:
        lines.append("\nCONFIRMED LEAKS (seen enough to be sure):")
        lines += [_leak_line(lk) for lk in confirmed]
    if watching:
        lines.append("\nWATCHING (small sample so far — could be variance):")
        lines += [_leak_line(lk) for lk in watching]
    if not confirmed and not watching:
        # Distinguish "graded enough and clean" from "not enough repeated spots
        # to judge" — never claim discipline we haven't actually measured.
        if report.eligible_groups > 0:
            lines.append(
                "\nNo clear frequency leaks in the spots with enough volume — "
                "your preflop play tracks the charts there."
            )
        else:
            lines.append(
                "\nNot enough repeated spots yet to judge — keep playing and "
                "patterns will surface."
            )
    return "\n".join(lines)
