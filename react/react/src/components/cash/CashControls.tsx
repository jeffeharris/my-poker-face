/**
 * Cash-mode HUD: bankroll display + between-hands top-up button.
 *
 * Renders nothing for non-cash games. Reads cash_mode metadata from
 * the GameState (which the backend includes only for cash games).
 *
 * v1: bankroll display + "Top up to max" button (between hands only).
 * Future: rebuy modal on bust, loan / sponsorship UI.
 */

import { useCallback, useState } from 'react';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import type { CashModeInfo } from '../../types/game';
import './CashControls.css';

interface CashControlsProps {
  cashMode: CashModeInfo;
  playerStack: number;
  handInProgress: boolean;
}

export function CashControls({ cashMode, playerStack, handInProgress }: CashControlsProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Headroom for a top-up: the gap between current stack and the
  // table's max_buy_in. If stack is at or above max, the button is
  // disabled (no legal top-up).
  const headroom = Math.max(0, cashMode.max_buy_in - playerStack);
  const topUpAmount = Math.min(headroom, cashMode.bankroll);
  const canTopUp = !busy && !handInProgress && topUpAmount > 0;

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
            handInProgress
              ? 'Top up only between hands'
              : cashMode.bankroll === 0
                ? 'No bankroll left to top up'
                : `Top up $${topUpAmount.toLocaleString()}`
          }
        >
          {busy ? 'Topping up…' : `Top up +$${topUpAmount.toLocaleString()}`}
        </button>
      )}
      {error && <div className="cash-controls__error">{error}</div>}
    </div>
  );
}
