/**
 * MobileCashSheet — bottom-sheet for cash-mode controls on mobile.
 *
 * Opens from MobileCashButton. Renders bankroll + table info +
 * top-up button. Mirrors MobileChatSheet's overlay + slide-up
 * animation pattern.
 *
 * Future: rebuy button on bust, loan / sponsor offer UI.
 */

import { useCallback, useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { X, LogOut } from 'lucide-react';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import type { CashModeInfo } from '../../types/game';
import { computeLeaveBreakdown } from './loanSettlement';
import { CashOutSummary, type SessionSummary } from './CashOutSummary';
import { getNetWorth } from './api';
import type { NetWorthResponse, TierStatus } from './types';
import './MobileCashSheet.css';

const TIER_LABELS: Record<TierStatus, string> = {
  premium: 'Premium',
  standard: 'Standard',
  restricted: 'Restricted',
  house_only: 'House only',
};

interface LeaveResponse {
  session_ended: boolean;
  chips_at_table: number;
  had_active_loan: boolean;
  sponsor_repaid: number;
  returned_chips: number;
  bankroll: number;
  session_summary: SessionSummary | null;
}

interface MobileCashSheetProps {
  isOpen: boolean;
  onClose: () => void;
  cashMode: CashModeInfo;
  playerStack: number;
  handInProgress: boolean;
  playerFolded: boolean;
}

interface LeaveBreakdownPanelProps {
  stack: number;
  loan: NonNullable<CashModeInfo['active_loan']>;
}

function LeaveBreakdownPanel({ stack, loan }: LeaveBreakdownPanelProps) {
  const b = computeLeaveBreakdown(stack, loan);
  const floorPct = Math.round(loan.floor * 100);
  const ratePct = Math.round(loan.rate * 100);
  return (
    <div className="mobile-cash-sheet__breakdown">
      <div className="mobile-cash-sheet__breakdown-row">
        <span>Stack at table</span>
        <span>${b.stack.toLocaleString()}</span>
      </div>
      <div className="mobile-cash-sheet__breakdown-row is-sponsor">
        <span>
          Loan floor
          <span className="mobile-cash-sheet__breakdown-detail">
            ${loan.amount.toLocaleString()} × {floorPct}%
          </span>
        </span>
        <span>−${b.floorPayment.toLocaleString()}</span>
      </div>
      {b.sponsorCut > 0 && (
        <div className="mobile-cash-sheet__breakdown-row is-sponsor">
          <span>
            Sponsor cut
            <span className="mobile-cash-sheet__breakdown-detail">
              {ratePct}% of ${b.remainder.toLocaleString()}
            </span>
          </span>
          <span>−${b.sponsorCut.toLocaleString()}</span>
        </div>
      )}
      {b.forgiven > 0 && (
        <div className="mobile-cash-sheet__breakdown-note">
          Floor short by ${b.forgiven.toLocaleString()} — forgiven.
        </div>
      )}
      <div className="mobile-cash-sheet__breakdown-row is-total is-bankroll">
        <span>To your bankroll</span>
        <span>${b.toBankroll.toLocaleString()}</span>
      </div>
    </div>
  );
}

export function MobileCashSheet({
  isOpen,
  onClose,
  cashMode,
  playerStack,
  handInProgress,
  playerFolded,
}: MobileCashSheetProps) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [leaveResult, setLeaveResult] = useState<LeaveResponse | null>(null);
  const [netWorth, setNetWorth] = useState<NetWorthResponse | null>(null);

  // Reset error + confirmation state when sheet opens (a stale
  // message or partial-confirm from a previous open shouldn't
  // carry over). Fetch net-worth on open so the in-game sheet
  // surfaces carry visibility — manage actions still live on /cash.
  useEffect(() => {
    if (!isOpen) {
      setNetWorth(null);
      return;
    }
    setError(null);
    setClosing(false);
    setConfirmLeave(false);
    let cancelled = false;
    void (async () => {
      try {
        const data = await getNetWorth();
        if (cancelled) return;
        setNetWorth(data);
      } catch (e) {
        // Net worth is auxiliary; failure shouldn't break the sheet.
        logger.warn('Net worth fetch failed:', e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  const handleClose = useCallback(() => {
    setClosing(true);
    // Match the animation duration in MobileCashSheet.css
    setTimeout(() => {
      onClose();
    }, 250);
  }, [onClose]);

  const headroom = Math.max(0, cashMode.max_buy_in - playerStack);
  const topUpAmount = Math.min(headroom, cashMode.bankroll);
  // Mid-hand top-up is allowed once the human has folded — they're
  // out of the betting for this hand, so adding chips can't shift
  // the call/raise math in front of the AIs.
  const topUpBlocked = handInProgress && !playerFolded;
  const canTopUp = !busy && !topUpBlocked && topUpAmount > 0;

  const handleTopUp = useCallback(async () => {
    if (!canTopUp) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${config.API_URL}/api/cash/topup`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: topUpAmount }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        if (res.status === 404 && data.error === 'No active cash session') {
          navigate('/cash');
          return;
        }
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      handleClose();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Top up failed:', msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }, [canTopUp, topUpAmount, handleClose, navigate]);

  const handleLeave = useCallback(async () => {
    // First click: show the confirmation. Second click: actually leave.
    // Two-tap confirm avoids "I tapped the wrong button" regret since
    // leaving the table forfeits any heat/respect we've built with
    // the AIs at this table.
    if (!confirmLeave) {
      setConfirmLeave(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${config.API_URL}/api/cash/leave`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      const data: LeaveResponse = await res.json();
      if (data.session_summary) {
        setLeaveResult(data);
      } else {
        navigate('/cash');
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Leave failed:', msg);
      setError(msg);
      setBusy(false);
    }
  }, [confirmLeave, navigate]);

  if (!isOpen) return null;

  // Portaled to <body> so the fixed overlay escapes any ancestor
  // stacking context (e.g. PageLayout). See CharacterDetailCard.
  return createPortal(
    <div
      className={`mobile-cash-sheet__overlay${closing ? ' is-closing' : ''}`}
      onClick={handleClose}
    >
      <div
        className={`mobile-cash-sheet__sheet${closing ? ' is-closing' : ''}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mobile-cash-sheet__header">
          <span className="mobile-cash-sheet__drag-handle" />
          <div className="mobile-cash-sheet__title-row">
            <h3 className="mobile-cash-sheet__title">Cash table</h3>
            <button
              type="button"
              className="mobile-cash-sheet__close"
              onClick={handleClose}
              aria-label="Close"
            >
              <X size={20} />
            </button>
          </div>
        </div>

        <div className="mobile-cash-sheet__body">
          <div className="mobile-cash-sheet__row">
            <span className="mobile-cash-sheet__label">Bankroll</span>
            <span className="mobile-cash-sheet__value">${cashMode.bankroll.toLocaleString()}</span>
          </div>
          <div className="mobile-cash-sheet__row">
            <span className="mobile-cash-sheet__label">At table</span>
            <span className="mobile-cash-sheet__value">${playerStack.toLocaleString()}</span>
          </div>
          <div className="mobile-cash-sheet__row">
            <span className="mobile-cash-sheet__label">Stake</span>
            <span className="mobile-cash-sheet__value">{cashMode.stake_label}</span>
          </div>
          <div className="mobile-cash-sheet__row">
            <span className="mobile-cash-sheet__label">Buy-in</span>
            <span className="mobile-cash-sheet__value mobile-cash-sheet__value--secondary">
              ${cashMode.min_buy_in.toLocaleString()} – ${cashMode.max_buy_in.toLocaleString()}
            </span>
          </div>

          {netWorth && (netWorth.payables.length > 0 || netWorth.tier_status !== 'premium') && (
            <div className="mobile-cash-sheet__net-worth">
              <div className="mobile-cash-sheet__net-worth-title">Net worth</div>
              <div className="mobile-cash-sheet__row">
                <span className="mobile-cash-sheet__label">Tier</span>
                <span className="mobile-cash-sheet__value">
                  {TIER_LABELS[netWorth.tier_status]}
                </span>
              </div>
              {netWorth.payables.length > 0 && (
                <div className="mobile-cash-sheet__row">
                  <span className="mobile-cash-sheet__label">
                    {netWorth.payables.length === 1 ? 'Carry' : 'Carries'}
                  </span>
                  <span className="mobile-cash-sheet__value">
                    ${netWorth.payables.reduce((s, p) => s + p.carry_amount, 0).toLocaleString()}{' '}
                    owed
                  </span>
                </div>
              )}
              <div className="mobile-cash-sheet__net-worth-hint">Manage from the cash lobby.</div>
            </div>
          )}

          {headroom > 0 ? (
            <button
              type="button"
              onClick={handleTopUp}
              disabled={!canTopUp}
              className="mobile-cash-sheet__topup"
            >
              {busy && !confirmLeave
                ? 'Topping up…'
                : topUpBlocked
                  ? 'Top up between hands (or after you fold)'
                  : topUpAmount === 0
                    ? 'No bankroll to top up'
                    : `Top up +$${topUpAmount.toLocaleString()}`}
            </button>
          ) : (
            <div className="mobile-cash-sheet__note">
              Stack at max buy-in — no headroom to top up.
            </div>
          )}

          {confirmLeave && cashMode.active_loan && (
            <LeaveBreakdownPanel stack={playerStack} loan={cashMode.active_loan} />
          )}
          <button
            type="button"
            onClick={handleLeave}
            disabled={busy && confirmLeave}
            className={`mobile-cash-sheet__leave${confirmLeave ? ' is-confirming' : ''}`}
          >
            <LogOut size={16} />
            {busy && confirmLeave
              ? 'Leaving…'
              : confirmLeave
                ? cashMode.active_loan
                  ? `Confirm — $${computeLeaveBreakdown(playerStack, cashMode.active_loan).toBankroll.toLocaleString()} to bankroll`
                  : `Confirm leave — return $${playerStack.toLocaleString()} to bankroll`
                : 'Leave table'}
          </button>

          {error && (
            <div className="mobile-cash-sheet__error" role="alert">
              {error}
            </div>
          )}
        </div>
      </div>
      {leaveResult && leaveResult.session_summary && (
        <CashOutSummary
          summary={leaveResult.session_summary}
          stakeLabel={cashMode.stake_label}
          finalBankroll={leaveResult.bankroll}
          sponsorRepaid={leaveResult.sponsor_repaid}
          onContinue={() => navigate('/cash')}
        />
      )}
    </div>,
    document.body
  );
}
