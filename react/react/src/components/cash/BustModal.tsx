/**
 * BustModal — fires when the human's stack hits 0 between hands.
 *
 * Two visual branches based on the SocketIO event from the server:
 *   - `cash_rebuy_needed` (bankroll >= min_buy_in, no active loan):
 *     show Rebuy / Top-up-to-max / Leave buttons. Player can stay
 *     at this table with their own bankroll.
 *   - `cash_bust` (bankroll < min_buy_in OR loan still active):
 *     show "Out of chips at this table. Leave to find a sponsor."
 *     Single Leave action navigates to /cash entry, where the
 *     stake picker offers sponsor-required tiers.
 *
 * Reuses the existing two-tap confirm pattern for destructive choices
 * — same UX as the Leave Table button in CashControls.
 */

import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { logger } from '../../utils/logger';
import { rebuy, leaveTable } from './api';
import type { CashBustEvent } from './types';
import './BustModal.css';

interface BustModalProps {
  event: (CashBustEvent & { kind: 'bust' | 'rebuy_needed' }) | null;
  onDismiss: () => void;
}

export function BustModal({ event, onDismiss }: BustModalProps) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmLeave, setConfirmLeave] = useState(false);

  const handleRebuy = useCallback(
    async (amount: number) => {
      if (!event || busy) return;
      setBusy(true);
      setError(null);
      try {
        await rebuy(amount);
        // Server emits a fresh game-state update; the modal goes away
        // when the new event arrives, but dismiss locally for snap.
        onDismiss();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Rebuy failed:', msg);
        setError(msg);
      } finally {
        setBusy(false);
      }
    },
    [event, busy, onDismiss],
  );

  const handleLeave = useCallback(async () => {
    if (busy) return;
    // Two-tap confirm only for the rebuy-available case — when
    // bankroll is 0 there's no "lose something by leaving" risk,
    // so single tap is fine.
    if (event?.kind === 'rebuy_needed' && !confirmLeave) {
      setConfirmLeave(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await leaveTable();
      navigate('/cash');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Leave failed:', msg);
      setError(msg);
      setBusy(false);
    }
  }, [event, busy, confirmLeave, navigate]);

  if (!event) return null;

  const { kind, bankroll, min_buy_in, max_buy_in, stake_label, has_active_loan } = event;
  const canRebuy = kind === 'rebuy_needed' && !has_active_loan;
  const topUpAmount = Math.min(max_buy_in, bankroll);

  return (
    <div className="bust-modal__overlay">
      <div className="bust-modal__sheet" onClick={(e) => e.stopPropagation()}>
        <div className="bust-modal__header">
          <h3 className="bust-modal__title">
            {canRebuy ? "You're out of chips" : 'Out of chips at this table'}
          </h3>
          <p className="bust-modal__subtitle">
            {canRebuy
              ? `Rebuy from your bankroll to keep playing at the ${stake_label} table.`
              : `Bankroll: $${bankroll.toLocaleString()} (need $${min_buy_in.toLocaleString()} to rebuy here). Leave to find a sponsor at the menu.`}
          </p>
        </div>

        <div className="bust-modal__body">
          {canRebuy ? (
            <>
              <button
                type="button"
                onClick={() => handleRebuy(min_buy_in)}
                disabled={busy || bankroll < min_buy_in}
                className="bust-modal__primary"
              >
                {busy
                  ? 'Rebuying…'
                  : `Rebuy $${min_buy_in.toLocaleString()} (min)`}
              </button>
              {topUpAmount > min_buy_in && (
                <button
                  type="button"
                  onClick={() => handleRebuy(topUpAmount)}
                  disabled={busy}
                  className="bust-modal__secondary"
                >
                  Rebuy max $
                  {topUpAmount.toLocaleString()}
                </button>
              )}
              <button
                type="button"
                onClick={handleLeave}
                disabled={busy && confirmLeave}
                className={`bust-modal__leave${confirmLeave ? ' is-confirming' : ''}`}
              >
                <LogOut size={14} />
                {busy && confirmLeave
                  ? 'Leaving…'
                  : confirmLeave
                    ? 'Confirm leave'
                    : 'Leave table'}
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={handleLeave}
              disabled={busy}
              className="bust-modal__primary"
            >
              <LogOut size={14} />
              {busy ? 'Leaving…' : 'Leave to find a sponsor'}
            </button>
          )}

          {has_active_loan && canRebuy === false && (
            <p className="bust-modal__hint">
              Your active sponsor loan must settle when you leave — sponsor
              takes their cut from your remaining chips ($0 at the table),
              then the loan clears.
            </p>
          )}

          {error && <div className="bust-modal__error" role="alert">{error}</div>}
        </div>
      </div>
    </div>
  );
}
