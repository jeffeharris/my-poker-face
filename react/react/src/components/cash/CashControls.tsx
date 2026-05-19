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
import './CashControls.css';

interface CashControlsProps {
  cashMode: CashModeInfo;
  playerStack: number;
  handInProgress: boolean;
  playerFolded: boolean;
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
  }, [canTopUp, topUpAmount]);

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
      navigate('/menu');
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
        <span className="cash-controls__value">
          ${cashMode.bankroll.toLocaleString()}
        </span>
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
          {busy && !confirmLeave
            ? 'Topping up…'
            : `Top up +$${topUpAmount.toLocaleString()}`}
        </button>
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
            ? `Confirm — return $${playerStack.toLocaleString()}`
            : 'Leave table'}
      </button>
      {error && <div className="cash-controls__error">{error}</div>}
    </div>
  );
}
