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
import { useNavigate } from 'react-router-dom';
import { X, LogOut } from 'lucide-react';
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
  playerFolded: boolean;
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

  // Reset error + confirmation state when sheet opens (a stale
  // message or partial-confirm from a previous open shouldn't
  // carry over).
  useEffect(() => {
    if (isOpen) {
      setError(null);
      setClosing(false);
      setConfirmLeave(false);
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
      navigate('/menu');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Leave failed:', msg);
      setError(msg);
      setBusy(false);
    }
  }, [confirmLeave, navigate]);

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
                ? `Confirm leave — return $${playerStack.toLocaleString()} to bankroll`
                : 'Leave table'}
          </button>

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
