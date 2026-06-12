/**
 * MobileCashButton — floating bottom-left pill for cash mode.
 *
 * Shows a wallet icon + bankroll amount. Tapping opens
 * MobileCashSheet (full bankroll/stake/top-up controls). Replaces
 * the always-visible top HUD on mobile, which ate screen real
 * estate that's more useful for the table view.
 */

import { Wallet } from 'lucide-react';
import { CountUp } from '../shared/CountUp';
import './MobileCashButton.css';

interface MobileCashButtonProps {
  bankroll: number;
  onClick: () => void;
}

export function MobileCashButton({ bankroll, onClick }: MobileCashButtonProps) {
  return (
    <button
      type="button"
      className="mobile-cash-button"
      onClick={onClick}
      aria-label="Open cash controls"
    >
      <Wallet className="mobile-cash-button__icon" size={16} />
      <span className="mobile-cash-button__amount">
        $<CountUp value={bankroll} useGrouping />
      </span>
    </button>
  );
}
