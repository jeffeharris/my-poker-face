/**
 * Client-side preview of the leave-time loan settlement.
 *
 * Mirrors `cash_mode/loan_settlement.py::settle_loan_on_leave` so the
 * confirm-leave UI can show the player exactly where their stack will go
 * before they tap through. The server remains the source of truth; this is
 * a preview only — the actual settlement runs server-side on `/api/cash/leave`.
 *
 * Uses Math.floor (not Math.trunc) — matches Python's int() for non-negative
 * values, which is the only case we hit here (chips/floors/rates are all >= 0).
 */

import type { CashActiveLoan } from '../../types/game';

export interface LeaveBreakdown {
  stack: number;
  /** Chips returned to the player's bankroll. */
  toBankroll: number;
  /** Total going to the sponsor (floor payment + post-floor cut). */
  toSponsor: number;
  /** Required repayment before any split (amount * floor multiplier). */
  floor: number;
  /** Of the stack, what's paid toward the floor (capped at stack). */
  floorPayment: number;
  /** Post-floor remainder available for the sponsor split. */
  remainder: number;
  /** Sponsor's cut of the remainder. */
  sponsorCut: number;
  /** Floor shortfall forgiven (v1 rule) when stack < floor. */
  forgiven: number;
}

export function computeLeaveBreakdown(
  stack: number,
  loan: CashActiveLoan | null | undefined
): LeaveBreakdown {
  if (!loan || loan.amount <= 0) {
    return {
      stack,
      toBankroll: stack,
      toSponsor: 0,
      floor: 0,
      floorPayment: 0,
      remainder: 0,
      sponsorCut: 0,
      forgiven: 0,
    };
  }
  const floor = Math.floor(loan.amount * loan.floor);
  const floorPayment = Math.min(floor, stack);
  const remainder = stack - floorPayment;
  const sponsorCut = Math.floor(remainder * loan.rate);
  const toSponsor = floorPayment + sponsorCut;
  const toBankroll = remainder - sponsorCut;
  const forgiven = Math.max(0, floor - stack);
  return {
    stack,
    toBankroll,
    toSponsor,
    floor,
    floorPayment,
    remainder,
    sponsorCut,
    forgiven,
  };
}
