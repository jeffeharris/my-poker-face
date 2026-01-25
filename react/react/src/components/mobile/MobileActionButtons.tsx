import { useState } from 'react';
import { Check, MessageCircle } from 'lucide-react';
import {
  useBettingCalculations,
  createBettingContext,
  type BettingContext,
} from '../../hooks/useBettingCalculations';
import './MobileActionButtons.css';

interface MobileActionButtonsProps {
  playerOptions: string[];
  currentPlayerStack: number;
  highestBet: number;
  currentPlayerBet: number;
  minRaise: number;
  bigBlind: number;
  potSize: number;
  onAction: (action: string, amount?: number) => void;
  onQuickChat?: () => void;
  bettingContext?: BettingContext;  // Optional - use if provided by backend
}

export function MobileActionButtons({
  playerOptions,
  currentPlayerStack,
  highestBet,
  currentPlayerBet,
  minRaise,
  bigBlind,
  potSize,
  onAction,
  onQuickChat,
  bettingContext: providedContext,
}: MobileActionButtonsProps) {
  const [showRaiseSheet, setShowRaiseSheet] = useState(false);
  const [isEditingAmount, setIsEditingAmount] = useState(false);

  // Create betting context from props if not provided
  const bettingContext = providedContext ?? createBettingContext({
    playerStack: currentPlayerStack,
    playerCurrentBet: currentPlayerBet,
    highestBet,
    potSize,
    minRaise,
    playerOptions,
  });

  // Use the shared hook for all calculations
  const calc = useBettingCalculations(bettingContext, bigBlind);

  // Raise amount state - initialize to min raise TO
  const [raiseAmount, setRaiseAmount] = useState(calc.safeMinRaiseTo);

  const handleRaise = () => {
    setRaiseAmount(calc.safeMinRaiseTo);
    setShowRaiseSheet(true);
  };

  const submitRaise = () => {
    // Allow raise if valid OR if it's all-in (even below min)
    const isAllIn = raiseAmount === calc.safeMaxRaiseTo;
    const isValidRaise = calc.isValidRaise(raiseAmount) || isAllIn;

    if (isValidRaise) {
      // Send the "raise TO" amount directly - backend now expects this
      onAction('raise', raiseAmount);
      setShowRaiseSheet(false);
    }
  };

  // Get breakdown for display
  const breakdown = calc.getBreakdown(raiseAmount);

  if (showRaiseSheet) {
    return (
      <div className="mobile-raise-sheet">
        <div className="raise-sheet-header">
          <button className="cancel-btn" onClick={() => setShowRaiseSheet(false)}>
            Cancel
          </button>
          <span className="raise-title">
            {playerOptions.includes('raise') ? 'Raise' : 'Bet'}
          </span>
          <button
            className="confirm-btn"
            onClick={submitRaise}
            disabled={!calc.isValidRaise(raiseAmount) && raiseAmount !== calc.safeMaxRaiseTo}
          >
            Confirm
          </button>
        </div>

        <div className="raise-amount-display">
          <span className="amount-label">
            {playerOptions.includes('raise') ? 'Raise to' : 'Bet'}
          </span>
          <div className="amount-with-2x">
            {isEditingAmount ? (
              <input
                type="number"
                className="amount-input"
                value={raiseAmount}
                onChange={(e) => {
                  // Allow free typing - no clamping during input
                  const val = parseInt(e.target.value);
                  if (!isNaN(val) && val > 0) {
                    setRaiseAmount(val);
                  } else if (e.target.value === '') {
                    // Allow clearing - will be fixed on blur
                    setRaiseAmount(calc.safeMinRaiseTo);
                  }
                }}
                onBlur={(e) => {
                  // Enforce limits when done typing
                  const val = parseInt(e.target.value);
                  if (!isNaN(val)) {
                    setRaiseAmount(Math.min(calc.safeMaxRaiseTo, Math.max(calc.safeMinRaiseTo, val)));
                  } else {
                    setRaiseAmount(calc.safeMinRaiseTo);
                  }
                  setIsEditingAmount(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    (e.target as HTMLInputElement).blur();
                  }
                }}
                autoFocus
                onFocus={(e) => e.target.select()}
                inputMode="numeric"
              />
            ) : (
              <span
                className="amount-value"
                onClick={() => setIsEditingAmount(true)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setIsEditingAmount(true);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                ${raiseAmount}
              </span>
            )}
            <button
              className="double-btn"
              onClick={() => {
                // Double the "adding to pot" amount (totalToAdd)
                // newRaise = currentBet + (totalToAdd * 2) = raiseAmount + totalToAdd
                setRaiseAmount(Math.min(calc.safeMaxRaiseTo, raiseAmount + breakdown.totalToAdd));
              }}
              disabled={raiseAmount + breakdown.totalToAdd > calc.safeMaxRaiseTo}
            >
              2x
            </button>
          </div>
        </div>

        {/* Breakdown display */}
        <div className="raise-breakdown">
          {calc.callAmount > 0 ? (
            <>
              <span className="breakdown-call">Call ${breakdown.callPortion}</span>
              <span className="breakdown-plus">+</span>
              <span className="breakdown-raise">Raise ${breakdown.raisePortion}</span>
            </>
          ) : (
            <span className="breakdown-total">Adding ${breakdown.totalToAdd} to pot</span>
          )}
        </div>

        <div className="quick-bet-buttons">
          {calc.quickBets.map(({ label, amount, id }) => (
            <button
              key={id}
              className={`quick-bet-btn ${raiseAmount === amount ? 'selected' : ''}`}
              onClick={() => setRaiseAmount(amount)}
              disabled={amount > calc.safeMaxRaiseTo}
            >
              {label}
              <span className="quick-bet-amount">${amount}</span>
            </button>
          ))}
        </div>

        <div className="raise-slider-container">
          <input
            type="range"
            className="raise-slider"
            min={calc.safeMinRaiseTo}
            max={calc.safeMaxRaiseTo}
            value={raiseAmount}
            onChange={(e) => {
              const value = parseInt(e.target.value);
              if (!isNaN(value)) {
                // Use magnetic snapping (0.5BB increments with pot fraction magnets)
                setRaiseAmount(calc.snapWithMagnets(value));
              }
            }}
          />
          <div className="slider-labels">
            <span>${calc.safeMinRaiseTo}</span>
            <span>${calc.safeMaxRaiseTo}</span>
          </div>
        </div>

        <div className="stack-preview">
          Stack after: ${breakdown.stackAfter}
        </div>
      </div>
    );
  }

  return (
    <div className="mobile-action-buttons">
      {playerOptions.includes('fold') && (
        <button
          className="action-btn fold-btn"
          onClick={() => onAction('fold')}
        >
          <span className="btn-icon">✕</span>
          <span className="btn-label">Fold</span>
        </button>
      )}

      {playerOptions.includes('check') && (
        <button
          className="action-btn check-btn"
          onClick={() => onAction('check')}
        >
          <Check className="btn-icon" size={18} />
          <span className="btn-label">Check</span>
        </button>
      )}

      {playerOptions.includes('call') && (
        <button
          className="action-btn call-btn"
          onClick={() => onAction('call')}
        >
          <span className="btn-icon">→</span>
          <span className="btn-label">Call ${calc.callAmount}</span>
        </button>
      )}

      {(playerOptions.includes('bet') || playerOptions.includes('raise')) && (
        <button
          className="action-btn raise-btn"
          onClick={handleRaise}
        >
          <span className="btn-icon">↑</span>
          <span className="btn-label">
            {playerOptions.includes('raise') ? 'Raise' : 'Bet'}
          </span>
        </button>
      )}

      {playerOptions.includes('all_in') && (
        <button
          className="action-btn allin-btn"
          onClick={() => onAction('all_in')}
        >
          <span className="btn-icon">★</span>
          <span className="btn-label">All-In ${calc.safeStack}</span>
        </button>
      )}

      {onQuickChat && (
        <button
          className="action-btn chat-btn"
          onClick={onQuickChat}
        >
          <MessageCircle className="btn-icon" size={18} />
          <span className="btn-label">Chat</span>
        </button>
      )}
    </div>
  );
}
