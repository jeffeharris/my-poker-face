/**
 * Cash-mode HUD: bankroll display + between-hands top-up button +
 * leave-table button. Desktop only — mobile uses MobileCashSheet
 * for the same surface area in a slide-up bottom sheet.
 *
 * Renders nothing for non-cash games. Reads cash_mode metadata
 * from the GameState (which the backend includes only for cash
 * games).
 */

import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import type { CashModeInfo } from '../../types/game';
import { computeLeaveBreakdown } from './loanSettlement';
import { CashOutSummary, type SessionSummary } from './CashOutSummary';
import './CashControls.css';

interface LeaveResponse {
  session_ended: boolean;
  chips_at_table: number;
  had_active_loan: boolean;
  sponsor_repaid: number;
  returned_chips: number;
  bankroll: number;
  session_summary: SessionSummary | null;
}

interface CashControlsProps {
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
    <div className="cash-controls__breakdown">
      <div className="cash-controls__breakdown-row">
        <span>Stack at table</span>
        <span>${b.stack.toLocaleString()}</span>
      </div>
      <div className="cash-controls__breakdown-row is-sponsor">
        <span>
          Loan floor
          <span className="cash-controls__breakdown-detail">
            ${loan.amount.toLocaleString()} × {floorPct}%
          </span>
        </span>
        <span>−${b.floorPayment.toLocaleString()}</span>
      </div>
      {b.sponsorCut > 0 && (
        <div className="cash-controls__breakdown-row is-sponsor">
          <span>
            Sponsor cut
            <span className="cash-controls__breakdown-detail">
              {ratePct}% of ${b.remainder.toLocaleString()}
            </span>
          </span>
          <span>−${b.sponsorCut.toLocaleString()}</span>
        </div>
      )}
      {b.forgiven > 0 && (
        <div className="cash-controls__breakdown-note">
          Floor short by ${b.forgiven.toLocaleString()} — forgiven.
        </div>
      )}
      <div className="cash-controls__breakdown-row is-total is-bankroll">
        <span>To your bankroll</span>
        <span>${b.toBankroll.toLocaleString()}</span>
      </div>
    </div>
  );
}

export function CashControls({
  cashMode,
  playerStack,
  handInProgress,
  playerFolded,
}: CashControlsProps) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [leaveResult, setLeaveResult] = useState<LeaveResponse | null>(null);

  // Headroom for a top-up: the gap between current stack and the
  // table's max_buy_in. If stack is at or above max, the button is
  // disabled (no legal top-up).
  const headroom = Math.max(0, cashMode.max_buy_in - playerStack);
  const topUpAmount = Math.min(headroom, cashMode.bankroll);
  // Mid-hand top-up is allowed once the human has folded — they're
  // out of the betting for this hand, so adding chips to the stack
  // just stages them for the next deal.
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
      // State refresh comes via the SocketIO emit triggered by the route.
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Top up failed:', msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }, [canTopUp, topUpAmount, navigate]);

  const handleLeave = useCallback(async () => {
    // Two-tap confirm: first click flips the button to a red
    // "Confirm leave — return $X to bankroll" state; second click
    // actually leaves. Guards against accidental table abandonment
    // (you lose any heat/respect built with these AI opponents).
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
      // Without a session_summary (e.g. server lost game_data
      // mid-session), skip the modal and go straight to the cash
      // lobby so the user isn't stranded — same destination the bust
      // modal and the summary's Return to Lobby button use.
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

  return (
    <div className="cash-controls glass">
      <div className="cash-controls__row">
        <span className="cash-controls__label">Bankroll</span>
        <span className="cash-controls__value">${cashMode.bankroll.toLocaleString()}</span>
      </div>
      <div className="cash-controls__row">
        <span className="cash-controls__label">Stake</span>
        <span className="cash-controls__value">{cashMode.stake_label}</span>
      </div>
      {headroom > 0 && (
        <button
          type="button"
          onClick={handleTopUp}
          disabled={!canTopUp}
          className="cash-controls__topup"
          title={
            topUpBlocked
              ? 'Top up between hands (or after you fold)'
              : cashMode.bankroll === 0
                ? 'No bankroll left to top up'
                : `Top up $${topUpAmount.toLocaleString()}`
          }
        >
          {busy && !confirmLeave ? 'Topping up…' : `Top up +$${topUpAmount.toLocaleString()}`}
        </button>
      )}
      {confirmLeave && cashMode.active_loan && (
        <LeaveBreakdownPanel stack={playerStack} loan={cashMode.active_loan} />
      )}
      <button
        type="button"
        onClick={handleLeave}
        disabled={busy && confirmLeave}
        className={`cash-controls__leave${confirmLeave ? ' is-confirming' : ''}`}
      >
        <LogOut size={14} />
        {busy && confirmLeave
          ? 'Leaving…'
          : confirmLeave
            ? cashMode.active_loan
              ? `Confirm — $${computeLeaveBreakdown(playerStack, cashMode.active_loan).toBankroll.toLocaleString()} to bankroll`
              : `Confirm — return $${playerStack.toLocaleString()}`
            : 'Leave table'}
      </button>
      {error && <div className="cash-controls__error">{error}</div>}
      {leaveResult && leaveResult.session_summary && (
        <CashOutSummary
          summary={leaveResult.session_summary}
          stakeLabel={cashMode.stake_label}
          finalBankroll={leaveResult.bankroll}
          sponsorRepaid={leaveResult.sponsor_repaid}
          onContinue={() => navigate('/cash')}
        />
      )}
    </div>
  );
}
