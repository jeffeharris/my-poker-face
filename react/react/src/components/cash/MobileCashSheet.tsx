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
import { X } from 'lucide-react';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import type { CashModeInfo } from '../../types/game';
import './MobileCashSheet.css';

interface MobileCashSheetProps {
  isOpen: boolean;
  onClose: () => void;
  cashMode: CashModeInfo;
  playerStack: number;
  handInProgress: boolean;
}

export function MobileCashSheet({
  isOpen,
  onClose,
  cashMode,
  playerStack,
  handInProgress,
}: MobileCashSheetProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  // Reset error when sheet opens (a stale message from a previous
  // attempt shouldn't carry over).
  useEffect(() => {
    if (isOpen) {
      setError(null);
      setClosing(false);
    }
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
      handleClose();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Top up failed:', msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }, [canTopUp, topUpAmount, handleClose]);

  if (!isOpen) return null;

  return (
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
            <span className="mobile-cash-sheet__value">
              ${cashMode.bankroll.toLocaleString()}
            </span>
          </div>
          <div className="mobile-cash-sheet__row">
            <span className="mobile-cash-sheet__label">At table</span>
            <span className="mobile-cash-sheet__value">
              ${playerStack.toLocaleString()}
            </span>
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

          {headroom > 0 ? (
            <button
              type="button"
              onClick={handleTopUp}
              disabled={!canTopUp}
              className="mobile-cash-sheet__topup"
            >
              {busy
                ? 'Topping up…'
                : handInProgress
                  ? 'Top up between hands only'
                  : topUpAmount === 0
                    ? 'No bankroll to top up'
                    : `Top up +$${topUpAmount.toLocaleString()}`}
            </button>
          ) : (
            <div className="mobile-cash-sheet__note">
              Stack at max buy-in — no headroom to top up.
            </div>
          )}

          {error && (
            <div className="mobile-cash-sheet__error" role="alert">
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
