"""Opponent sizing-tell over time — how readable an opponent's bet SIZING is.

Surface B of ``docs/plans/SIZING_COACH_SURFACES.md``. For one opponent, grades
their postflop bet/raise actions by size (big ≥ 0.75 pot vs small) against the
equity they held, into a ``sizing_polarization_score`` = big-bet equity −
small-bet equity (positive ⇒ bets bigger with strength ⇒ face-up ⇒ exploitable).
Splits the history into time-blocks so the dossier can show the tell's
**stability over time** — the "is this read still trustworthy" signal that's the
missing kill-switch for the bot's Phase B sizing-defense
(``poker/strategy/value_override.py:compute_sizing_defense_strategy``).

Why not just read the live opponent model? That score
(``poker/memory/opponent_model.py:sizing_polarization_score``) is a showdown-gated
*lifetime cumulative mean* — it can't show a trend and it flips off slowly when an
opponent adapts.

**Showdown-gated (fairness).** The read is built ONLY from bets in hands that
reached showdown where the bettor was NOT folded — i.e. hands whose cards a human
at the table would actually have seen. Using the analyzer's per-bet ``equity`` for
*mucked* bets (cards never revealed) would hand the player a superhuman read no
opponent could earn, and cheapen the "scout to learn the tell" grind. So the DB
adapters join ``hand_history`` (``showdown = 1``) and exclude any bet by a player
who has a ``fold`` row that hand. The cost is honest sparsity — the same constraint
a real reader lives under (you only learn what got shown down).

Pure core (``compute_opponent_sizing_tell``) takes decision dicts and returns the
tell; ``load_opponent_bet_decisions`` / ``load_owner_bet_decisions`` are the thin
owner-scoped DB adapters. The core is DB-free and unit-testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# A bet ≥ this fraction of the pot is "big". Matches the bot's
# sizing_defense_min_bet_ratio / SIZING_BIG_BET_POT_RATIO so coach and bot agree.
BIG_BET_POT_RATIO = 0.75
# Score ≥ this ⇒ face-up; ≤ −this ⇒ reverse (sizes up with air). Matches the bot's
# sizing_defense_min_polar gate so the dossier and the consumer use one threshold.
FACE_UP_THRESHOLD = 0.15
# Per-bin sample floor before the score means anything (need BOTH bins). Mirrors
# SIZING_MIN_BIN_SAMPLE — the score is big_mean − small_mean, meaningless until
# each side has a sample.
MIN_PER_BIN = 4
# Total bets for a HIGH-confidence read (below ⇒ low / "watching").
CONFIRM_MIN_BETS = 20
# Graded blocks needed before we'll call stability at all.
MIN_GRADED_BLOCKS = 3
# How far the latest graded block must drop below the trailing mean to read as
# "mixing" (they're balancing their sizing / starting to counter-adapt).
MIXING_DELTA = 0.12
# Default number of trend blocks (oldest→newest sparkline points).
DEFAULT_BLOCKS = 6


@dataclass(frozen=True)
class SizingTell:
    """One opponent's size→strength read, with a stability trend."""

    score: float  # big_eq − small_eq (0.0 when ungradeable)
    n_bets: int
    n_big: int
    n_small: int
    big_eq: Optional[float]
    small_eq: Optional[float]
    confidence: str  # 'insufficient' | 'low' | 'high'
    verdict: str  # 'face_up' | 'balanced' | 'reverse' | 'unknown'
    stability: str  # 'stable' | 'mixing' | 'insufficient'
    series: List[Optional[float]]  # per-block score, None where a block lacks a bin
    exploit: Optional[str]


def _grade(decisions: List[dict]) -> Tuple[float, int, int, Optional[float], Optional[float]]:
    """(score, n_big, n_small, big_eq, small_eq) over a set of bet decisions.

    score is big_mean − small_mean, or 0.0 when either bin is empty (the caller
    decides whether that group is gradeable via n_big / n_small).
    """
    big = [d['equity'] for d in decisions if d['bet_fraction'] >= BIG_BET_POT_RATIO]
    small = [d['equity'] for d in decisions if d['bet_fraction'] < BIG_BET_POT_RATIO]
    big_eq = sum(big) / len(big) if big else None
    small_eq = sum(small) / len(small) if small else None
    score = (big_eq - small_eq) if (big_eq is not None and small_eq is not None) else 0.0
    return score, len(big), len(small), big_eq, small_eq


def _classify_stability(series: List[Optional[float]]) -> str:
    """Read the block series: 'mixing' if the latest graded block dropped clearly
    below the trailing mean (they're balancing their sizing), else 'stable'.
    'insufficient' until MIN_GRADED_BLOCKS blocks are gradeable."""
    graded = [s for s in series if s is not None]
    if len(graded) < MIN_GRADED_BLOCKS:
        return 'insufficient'
    latest = graded[-1]
    trailing = sum(graded[:-1]) / len(graded[:-1])
    return 'mixing' if latest <= trailing - MIXING_DELTA else 'stable'


def _exploit_for(verdict: str) -> Optional[str]:
    if verdict == 'face_up':
        return "Fold your marginal hands to their big bets — they size up with strength."
    if verdict == 'reverse':
        return "Don't fold to their big bets — they size up with air (bluffs); call/raise."
    return None


def sizing_label(verdict: str) -> str:
    """Human-facing one-liner for the dossier card (opponent framing)."""
    return {
        'face_up': 'Big bets = strength',
        'reverse': 'Big bets = bluffs',
        'balanced': 'Balanced sizing',
    }.get(verdict, 'Unknown')


def self_label(verdict: str) -> str:
    """Self framing for Surface A (your own sizing readability)."""
    return {
        'face_up': 'Your big bets are face-up',
        'reverse': 'Your big bets skew weak',
        'balanced': 'Your sizing is balanced',
    }.get(verdict, 'Unknown')


def self_advice(verdict: str) -> Optional[str]:
    """What to DO about your own sizing read (Surface A). ``balanced`` = no leak."""
    if verdict == 'face_up':
        return (
            "You almost only bet big with strength — observant opponents fold for free. "
            "Mix some big bluffs in."
        )
    if verdict == 'reverse':
        return (
            "Your big bets skew weak — you may be over-bluffing big. "
            "Tighten your big-bet value."
        )
    return None


def compute_opponent_sizing_tell(
    decisions: List[dict], *, blocks: int = DEFAULT_BLOCKS
) -> SizingTell:
    """Grade one opponent's postflop bets into a size→strength tell + trend.

    ``decisions``: iterable of dicts with ``equity`` (0–1), ``bet_fraction``
    (≥0, the bet over the pot before it), ``created_at``, ``hand_number``. Pure —
    no DB/IO. Returns an ``insufficient`` tell when either size bin is under-
    sampled (honest: the score is big−small, undefined without both).
    """
    valid = [
        d
        for d in decisions
        if d.get('equity') is not None
        and d.get('bet_fraction') is not None
        and 0.0 <= d['equity'] <= 1.0
        and d['bet_fraction'] >= 0
    ]
    score, n_big, n_small, big_eq, small_eq = _grade(valid)
    n = len(valid)

    if n_big < MIN_PER_BIN or n_small < MIN_PER_BIN:
        return SizingTell(
            score=0.0,
            n_bets=n,
            n_big=n_big,
            n_small=n_small,
            big_eq=round(big_eq, 3) if big_eq is not None else None,
            small_eq=round(small_eq, 3) if small_eq is not None else None,
            confidence='insufficient',
            verdict='unknown',
            stability='insufficient',
            series=[],
            exploit=None,
        )

    verdict = (
        'face_up'
        if score >= FACE_UP_THRESHOLD
        else 'reverse'
        if score <= -FACE_UP_THRESHOLD
        else 'balanced'
    )
    confidence = 'high' if n >= CONFIRM_MIN_BETS else 'low'

    # Per-block score (oldest→newest); equal contiguous chunks so each point's
    # sample is comparable. None where a block lacks one of the two bins.
    ordered = sorted(
        valid, key=lambda d: (d.get('created_at') or '', d.get('hand_number') or 0)
    )
    chunks = [ordered[i * n // blocks:(i + 1) * n // blocks] for i in range(blocks)]
    series: List[Optional[float]] = []
    for ch in chunks:
        s, nb, ns, _, _ = _grade(ch)
        series.append(round(s, 3) if (nb and ns) else None)

    return SizingTell(
        score=round(score, 3),
        n_bets=n,
        n_big=n_big,
        n_small=n_small,
        big_eq=round(big_eq, 3),
        small_eq=round(small_eq, 3),
        confidence=confidence,
        verdict=verdict,
        stability=_classify_stability(series),
        series=series,
        exploit=_exploit_for(verdict),
    )


def load_opponent_bet_decisions(db_path: str, owner_id: str, opponent_name: str) -> List[dict]:
    """Load an opponent's postflop bet/raise decisions from the owner's games.

    Owner-scoped (the coach privacy model: you only read opponents you've played).
    SHOWDOWN-GATED for fairness: only bets in hands that reached showdown where the
    bettor wasn't folded (cards a human would have seen) — see module docstring.
    ``bet_fraction`` ≈ ``raise_amount / pot_total`` — a coarse big/small proxy
    (raise-to overstates a re-raise's size, but the big/small bin at 0.75 absorbs
    that). ``equity`` is the analyzer's equity-vs-random at the bet. Read-only.
    """
    import sqlite3

    rows: List[dict] = []
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        # Candidate revealed bets (in showdown hands). The fold-exclusion is a
        # SEPARATE indexed query + a Python anti-join — a correlated NOT EXISTS
        # here ran ~38s on the live DB; this is ~0.05s.
        cur = conn.execute(
            """
            SELECT pda.equity        AS equity,
                   pda.pot_total     AS pot_total,
                   pda.raise_amount  AS raise_amount,
                   pda.created_at    AS created_at,
                   pda.hand_number   AS hand_number,
                   pda.game_id       AS game_id
            FROM player_decision_analysis pda
            JOIN games g ON g.game_id = pda.game_id
            JOIN hand_history hh
              ON hh.game_id = pda.game_id AND hh.hand_number = pda.hand_number
             AND hh.showdown = 1
            WHERE g.owner_id = ?
              AND pda.player_name = ?
              AND pda.phase IN ('FLOP', 'TURN', 'RIVER')
              AND pda.action_taken IN ('bet', 'raise', 'all_in')
              AND pda.equity IS NOT NULL
              AND pda.pot_total > 0
              AND pda.raise_amount > 0
            """,
            (owner_id, opponent_name),
        )
        candidates = cur.fetchall()
        # (game, hand) where this player folded → they weren't revealed there.
        folded = {
            (r['game_id'], r['hand_number'])
            for r in conn.execute(
                """
                SELECT DISTINCT pda.game_id AS game_id, pda.hand_number AS hand_number
                FROM player_decision_analysis pda
                JOIN games g ON g.game_id = pda.game_id
                WHERE g.owner_id = ? AND pda.player_name = ? AND pda.action_taken = 'fold'
                """,
                (owner_id, opponent_name),
            ).fetchall()
        }
        rows = [
            {
                'equity': r['equity'],
                'bet_fraction': r['raise_amount'] / r['pot_total'],
                'created_at': r['created_at'],
                'hand_number': r['hand_number'],
            }
            for r in candidates
            if (r['game_id'], r['hand_number']) not in folded
        ]
        conn.close()
    except Exception as e:  # noqa: BLE001 — best-effort read, never break the page
        logger.warning(
            "load_opponent_bet_decisions failed for %s/%s: %s", owner_id, opponent_name, e
        )
    return rows


def load_owner_bet_decisions(db_path: str, owner_id: str) -> List[dict]:
    """Load the owner's OWN postflop bet/raise decisions — Surface A (self).

    Mirrors ``coach_leaks.load_owner_preflop_decisions``: scope by owner_id and the
    games' index, then keep the human seat (player_name == that game's owner_name)
    in Python (NOT in the WHERE — that makes SQLite nested-loop games × matches).
    SHOWDOWN-GATED like the opponent loader (only revealed bets count). Same
    ``bet_fraction``/``equity`` semantics. Read-only.
    """
    import sqlite3

    rows: List[dict] = []
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        candidates = conn.execute(
            """
            SELECT pda.equity        AS equity,
                   pda.pot_total     AS pot_total,
                   pda.raise_amount  AS raise_amount,
                   pda.created_at    AS created_at,
                   pda.hand_number   AS hand_number,
                   pda.game_id       AS game_id,
                   pda.player_name   AS player_name,
                   g.owner_name      AS owner_name
            FROM player_decision_analysis pda
            JOIN games g ON g.game_id = pda.game_id
            JOIN hand_history hh
              ON hh.game_id = pda.game_id AND hh.hand_number = pda.hand_number
             AND hh.showdown = 1
            WHERE g.owner_id = ?
              AND pda.phase IN ('FLOP', 'TURN', 'RIVER')
              AND pda.action_taken IN ('bet', 'raise', 'all_in')
              AND pda.equity IS NOT NULL
              AND pda.pot_total > 0
              AND pda.raise_amount > 0
            """,
            (owner_id,),
        ).fetchall()
        # (game, hand, player) where a player folded → not revealed there. Keyed
        # by player too since the owner seat is matched in Python below.
        folded = {
            (r['game_id'], r['hand_number'], r['player_name'])
            for r in conn.execute(
                """
                SELECT pda.game_id AS game_id, pda.hand_number AS hand_number,
                       pda.player_name AS player_name
                FROM player_decision_analysis pda
                JOIN games g ON g.game_id = pda.game_id
                WHERE g.owner_id = ? AND pda.action_taken = 'fold'
                """,
                (owner_id,),
            ).fetchall()
        }
        rows = [
            {
                'equity': r['equity'],
                'bet_fraction': r['raise_amount'] / r['pot_total'],
                'created_at': r['created_at'],
                'hand_number': r['hand_number'],
            }
            for r in candidates
            if r['player_name'] == r['owner_name']
            and (r['game_id'], r['hand_number'], r['player_name']) not in folded
        ]
        conn.close()
    except Exception as e:  # noqa: BLE001 — best-effort read, never break the page
        logger.warning("load_owner_bet_decisions failed for %s: %s", owner_id, e)
    return rows
