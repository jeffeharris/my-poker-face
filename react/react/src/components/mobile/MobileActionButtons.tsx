import { memo, useState } from 'react';
import { Check, Crosshair, HandCoins, MessageCircle } from 'lucide-react';
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
  recommendedAction?: string | null;
  raiseToAmount?: number | null;  // Coach-suggested raise amount to pre-fill slider
}

export const MobileActionButtons = memo(function MobileActionButtons({
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
  recommendedAction,
  raiseToAmount,
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
    // Pre-fill with coach's suggested amount if valid, otherwise use minimum
    const suggestedAmount = raiseToAmount &&
      raiseToAmount >= calc.safeMinRaiseTo &&
      raiseToAmount <= calc.safeMaxRaiseTo
        ? raiseToAmount
        : calc.safeMinRaiseTo;
    setRaiseAmount(suggestedAmount);
    setShowRaiseSheet(true);
  };

  const submitRaise = () => {
    // Allow raise if valid OR if it's all-in (even below min)
    const isAllIn = raiseAmount === calc.safeMaxRaiseTo;
    const isValidRaise = calc.isValidRaise(raiseAmount) || isAllIn;

    if (isValidRaise) {
      onAction(isAllIn ? 'all_in' : 'raise', raiseAmount);
      setShowRaiseSheet(false);
    }
  };

  // Get breakdown for display
  const breakdown = calc.getBreakdown(raiseAmount);

  if (showRaiseSheet) {
    return (
      <div className="mobile-raise-sheet" data-testid="raise-sheet">
        <div className="raise-sheet-header">
          <button className="cancel-btn" data-testid="raise-cancel" onClick={() => setShowRaiseSheet(false)}>
            Cancel
          </button>
          <span className="raise-title">
            {playerOptions.includes('raise') ? 'Raise' : 'Bet'}
          </span>
          <button
            className="confirm-btn"
            data-testid="raise-confirm"
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
                data-testid="raise-amount"
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
                // Double the raise portion (amount above the call)
                setRaiseAmount(Math.min(calc.safeMaxRaiseTo, raiseAmount + breakdown.raisePortion));
              }}
              disabled={raiseAmount + breakdown.raisePortion > calc.safeMaxRaiseTo}
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

        {/* Row 1: Fractional pot amounts */}
        <div className="quick-bet-buttons">
          {calc.quickBets.map(({ label, amount, id }) => (
            <button
              key={id}
              className={`quick-bet-btn ${raiseAmount === amount ? 'selected' : ''}`}
              data-testid="quick-bet-btn"
              onClick={() => setRaiseAmount(amount)}
              disabled={amount > calc.safeMaxRaiseTo}
            >
              {label}
              <span className="quick-bet-amount">${amount}</span>
            </button>
          ))}
        </div>

        {/* Row 2: Cover targets + All-In */}
        <div className="quick-bet-buttons">
          {calc.targetBets.map(({ label, amount, id, isCover }) => (
            <button
              key={id}
              className={`quick-bet-btn ${isCover ? 'cover' : ''} ${raiseAmount === amount ? 'selected' : ''}`}
              data-testid="quick-bet-btn"
              onClick={() => setRaiseAmount(amount)}
              disabled={amount > calc.safeMaxRaiseTo}
            >
              {label}
              <span className="quick-bet-amount">{isCover && <Crosshair size={12} style={{verticalAlign: 'middle', display: 'inline'}} />} ${amount}</span>
            </button>
          ))}
        </div>

        <div className="raise-slider-container">
          <input
            type="range"
            className="raise-slider"
            data-testid="raise-slider"
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

        <div className="stack-preview" data-testid="stack-preview">
          Stack after: ${breakdown.stackAfter}
        </div>
      </div>
    );
  }

  return (
    <div className="mobile-action-buttons" data-testid="action-buttons">
      {playerOptions.includes('fold') && (
        <button
          className={`action-btn fold-btn ${recommendedAction === 'fold' ? 'coach-recommended' : ''}`}
          data-testid="action-btn-fold"
          onClick={() => onAction('fold')}
        >
          <span className="action-icon">✕</span>
          <span className="btn-label">Fold</span>
        </button>
      )}

      {playerOptions.includes('check') && (
        <button
          className={`action-btn check-btn ${recommendedAction === 'check' ? 'coach-recommended' : ''}`}
          data-testid="action-btn-check"
          onClick={() => onAction('check')}
        >
          <span className="action-icon"><Check /></span>
          <span className="btn-label">Check</span>
        </button>
      )}

      {playerOptions.includes('call') && (
        <button
          className={`action-btn call-btn ${recommendedAction === 'call' ? 'coach-recommended' : ''}`}
          data-testid="action-btn-call"
          onClick={() => onAction('call')}
        >
          <span className="action-icon">→</span>
          <span className="btn-label">Call ${calc.callAmount}</span>
        </button>
      )}

      {(playerOptions.includes('bet') || playerOptions.includes('raise')) && (
        <button
          className={`action-btn raise-btn ${recommendedAction === 'raise' ? 'coach-recommended' : ''}`}
          data-testid="action-btn-raise"
          onClick={handleRaise}
        >
          <span className="action-icon">↑</span>
          <span className="btn-label">
            {playerOptions.includes('raise') ? 'Raise' : 'Bet'}
          </span>
        </button>
      )}

      {/* When only all_in is available (can't call or raise), show button to open raise interface */}
      {playerOptions.includes('all_in') && !playerOptions.includes('raise') && !playerOptions.includes('bet') && (
        <button
          className="action-btn allin-btn"
          data-testid="action-btn-allin"
          onClick={handleRaise}
        >
          <span className="action-icon"><HandCoins /></span>
          <span className="btn-label">All-In ${calc.safeStack}</span>
        </button>
      )}

      {onQuickChat && (
        <button
          className="action-btn chat-btn"
          data-testid="action-btn-chat"
          onClick={onQuickChat}
        >
          <span className="action-icon"><MessageCircle /></span>
          <span className="btn-label">Chat</span>
        </button>
      )}
    </div>
  );
});
