"""Bet-size classification for postflop strategy decisions.

Maps a faced bet (call_amount + pot context) into one of four
required-equity buckets used as a first-class input to the defense
floor (plan §2), bluff-catch override, station exploitation
rebalance, and bet-size-aware diagnostics. See plan §4.

Thresholds key off **required equity** (the canonical decision
input), not bet/pot ratio directly. The bucket names map cleanly
to the language used in plan §2's floor matrix.

Independent of `value_override._base_call_prob`'s bet/pot bands,
which use different (legacy-tested) boundaries — that matrix
stays put for now; consolidation is future work.
"""

from dataclasses import dataclass
from typing import Optional


# ── Bucket labels ──────────────────────────────────────────────────
BUCKET_SMALL = 'small'       # required_equity ≤ 20%
BUCKET_MEDIUM = 'medium'     # 20% < required_equity ≤ 35%
BUCKET_LARGE = 'large'       # 35% < required_equity ≤ 50%
BUCKET_JAM = 'jam'           # required_equity > 50% OR facing all-in


# ── Required-equity thresholds (per plan §4) ───────────────────────
SMALL_MAX_REQ_EQUITY = 0.20
MEDIUM_MAX_REQ_EQUITY = 0.35
LARGE_MAX_REQ_EQUITY = 0.50


@dataclass(frozen=True)
class BetSizeClassification:
    """Bet-size summary for a single facing-bet decision.

    Fields:
        bucket: 'small' | 'medium' | 'large' | 'jam' — None when
            there's no bet to face (free check / preflop opener).
        required_equity: call_amount / (pot_before_bet + 2*call_amount),
            in [0.0, 0.5). 0.0 when no bet to face.
        bet_size_pot_ratio: call_amount / pot_before_bet — duplicated
            from `DecisionContext.bet_size_pot_ratio` for callers that
            want both inputs in one place. 0.0 when no bet to face.
        facing_all_in: True iff the decision is against an all-in jam.
    """
    bucket: Optional[str]
    required_equity: float
    bet_size_pot_ratio: float
    facing_all_in: bool


def required_equity(call_amount: float, pot_before_bet: float) -> float:
    """Pot-odds-required equity to break even on the call.

    Formula: call_amount / (pot_before_bet + 2 * call_amount).
    The "2 *" reflects the villain's bet already in the pot plus the
    hero's call that completes it.

    Returns 0.0 when there's no bet to face. Asymptotes to 0.5 for
    arbitrarily large bets relative to pot — true 50% required
    equity is mathematically unreachable on a standard postflop
    call structure.
    """
    if call_amount <= 0:
        return 0.0
    final_pot = pot_before_bet + 2.0 * call_amount
    if final_pot <= 0:
        return 0.0
    return float(call_amount) / float(final_pot)


def classify_bet_size_bucket(
    call_amount: float,
    pot_before_bet: float,
    facing_all_in: bool = False,
) -> Optional[str]:
    """Return one of 'small' / 'medium' / 'large' / 'jam', or None
    when there's no bet to face.

    Args:
        call_amount: chips hero must put in to call.
        pot_before_bet: pot size *before* the villain's bet that hero
            is now facing. Matches how `DecisionContext.bet_size_pot_ratio`
            is built in the controller (call_amount / pot_before_bet).
        facing_all_in: True iff the bet is a commitment-class jam.
            Forces the 'jam' bucket regardless of required equity —
            an all-in changes the *kind* of decision (no more streets)
            even when math-wise the price is moderate.

    Returns:
        Bucket label, or None if call_amount <= 0 (no bet to face).
    """
    if facing_all_in:
        return BUCKET_JAM
    if call_amount <= 0:
        return None
    req = required_equity(call_amount, pot_before_bet)
    if req > LARGE_MAX_REQ_EQUITY:
        return BUCKET_JAM
    if req > MEDIUM_MAX_REQ_EQUITY:
        return BUCKET_LARGE
    if req > SMALL_MAX_REQ_EQUITY:
        return BUCKET_MEDIUM
    return BUCKET_SMALL


def classify_bet_size(
    call_amount: float,
    pot_before_bet: float,
    facing_all_in: bool = False,
) -> BetSizeClassification:
    """Build the full BetSizeClassification dataclass for a decision."""
    req = required_equity(call_amount, pot_before_bet)
    if call_amount <= 0 or pot_before_bet <= 0:
        ratio = 0.0
    else:
        ratio = float(call_amount) / float(pot_before_bet)
    return BetSizeClassification(
        bucket=classify_bet_size_bucket(
            call_amount, pot_before_bet, facing_all_in=facing_all_in,
        ),
        required_equity=req,
        bet_size_pot_ratio=ratio,
        facing_all_in=facing_all_in,
    )
