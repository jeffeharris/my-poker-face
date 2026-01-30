import { useState } from 'react';
import { Crosshair } from 'lucide-react';
import {
  useBettingCalculations,
  createBettingContext,
  type BettingContext,
} from '../../../hooks/useBettingCalculations';
import './ActionButtons.css';

interface ActionButtonsProps {
  playerOptions: string[];
  currentPlayerStack: number;
  highestBet: number;
  currentPlayerBet: number;
  minRaise: number;
  bigBlind: number;
  potSize: number;
  onAction: (action: string, amount?: number) => void;
  inline?: boolean;  // When true, disables fixed positioning for embedded use
  bettingContext?: BettingContext;  // Optional - use if provided by backend
}

export function ActionButtons({
  playerOptions,
  currentPlayerStack,
  highestBet,
  currentPlayerBet,
  minRaise,
  bigBlind,
  potSize,
  onAction,
  inline = false,
  bettingContext: providedContext,
}: ActionButtonsProps) {
  const [showBetInterface, setShowBetInterface] = useState(false);
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

  // Raise amount state - initialize to default raise
  const [raiseAmount, setRaiseAmount] = useState(calc.getDefaultRaise());

  const handleBetRaise = () => {
    setShowBetInterface(true);
    setRaiseAmount(calc.getDefaultRaise());
  };

  const submitBet = () => {
    // Allow bet if valid OR if it's all-in (even below min)
    const isAllIn = raiseAmount === calc.safeMaxRaiseTo;
    const isValidBet = calc.isValidRaise(raiseAmount) || isAllIn;

    if (isValidBet) {
      onAction(isAllIn ? 'all_in' : 'raise', raiseAmount);
      setShowBetInterface(false);
    }
  };

  const cancelBet = () => {
    setShowBetInterface(false);
  };

  // Update raise amount when quick bet button is clicked
  const selectBetAmount = (amount: number, buttonId: string | null = null) => {
    // Always round to snap increment except for all-in
    const snappedAmount = buttonId === 'all-in' ? amount : calc.roundToSnap(amount);
    setRaiseAmount(snappedAmount);
  };

  // Get breakdown for display
  const breakdown = calc.getBreakdown(raiseAmount);

  if (showBetInterface) {
    return (
      <div className={`action-panel betting-interface ${inline ? 'inline' : ''}`}>
        <div className="bet-header">
          <div className="bet-title">
            {playerOptions.includes('raise') ? 'Raise' : 'Bet'}
          </div>
          <div className="bet-info">
            <span className="info-item">Stack: ${calc.safeStack}</span>
            <span className="info-item">Pot: ${calc.safePotSize}</span>
            {calc.callAmount > 0 && <span className="info-item">To Call: ${calc.callAmount}</span>}
          </div>
        </div>

        {/* Unified Bet Display */}
        <div className="unified-bet-display">
          <div className="bet-preview">
            <span className="bet-label">You'll {playerOptions.includes('raise') ? 'raise to' : 'bet'}:</span>
            <div className="bet-amount-row">
              {isEditingAmount ? (
                <input
                  type="number"
                  className="bet-amount-input"
                  value={raiseAmount}
                  onChange={(e) => {
                    const val = parseInt(e.target.value);
                    if (!isNaN(val) && val > 0) {
                      setRaiseAmount(val);
                    } else if (e.target.value === '') {
                      // Allow clearing - will be fixed on blur
                      setRaiseAmount(calc.safeMinRaiseTo);
                    }
                  }}
                  onBlur={(e) => {
                    const val = parseInt(e.target.value);
                    if (!isNaN(val)) {
                      // No snapping - let user be specific (consistent with mobile)
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
                  min={calc.safeMinRaiseTo}
                  max={calc.safeMaxRaiseTo}
                />
              ) : (
                <span
                  className="bet-total clickable"
                  onClick={() => setIsEditingAmount(true)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setIsEditingAmount(true);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  title="Click to edit"
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
          <div className="bet-breakdown">
            {calc.callAmount > 0 ? (
              <>
                <span className="call-portion">Call ${breakdown.callPortion}</span>
                <span className="plus">+</span>
                <span className="raise-portion">Raise ${breakdown.raisePortion}</span>
              </>
            ) : (
              <span className="total-portion">Adding ${breakdown.totalToAdd} to pot</span>
            )}
          </div>
          <div className="stack-after">
            Stack after: ${breakdown.stackAfter}
          </div>
        </div>

        <div className="bet-options">
          {/* Row 1: Fractional pot amounts */}
          <div className="quick-bets">
            {calc.quickBets.map(({ label, amount, id }) => (
              <button
                key={id}
                className={`bet-button ${raiseAmount === amount ? 'selected' : ''}`}
                onClick={() => selectBetAmount(amount, id)}
                disabled={amount > calc.safeMaxRaiseTo}
              >
                {label}<br/>${amount}
              </button>
            ))}
          </div>

          {/* Row 2: Cover targets + All-In */}
          <div className="quick-bets">
            {calc.targetBets.map(({ label, amount, id, isCover }) => (
              <button
                key={id}
                className={`bet-button ${id === 'all-in' ? 'all-in' : ''} ${isCover ? 'cover' : ''} ${raiseAmount === amount ? 'selected' : ''}`}
                onClick={() => selectBetAmount(amount, id)}
                disabled={amount > calc.safeMaxRaiseTo}
              >
                {label}<br/>{isCover && <Crosshair size={12} style={{verticalAlign: 'middle', display: 'inline'}} />} ${amount}
              </button>
            ))}
          </div>

          {/* Slider */}
          <div className="bet-slider-container">
            <input
              type="range"
              className="bet-slider"
              min={calc.safeMinRaiseTo}
              max={calc.safeMaxRaiseTo}
              value={raiseAmount}
              onChange={(e) => {
                const value = parseInt(e.target.value);
                if (!isNaN(value)) {
                  // Use magnetic snapping (0.5BB increments with pot fraction magnets)
                  const snappedValue = calc.snapWithMagnets(value);
                  setRaiseAmount(snappedValue);
                }
              }}
            />
            <div className="slider-labels">
              <span>${calc.safeMinRaiseTo}</span>
              <span>${calc.safeMaxRaiseTo}</span>
            </div>
          </div>
        </div>

        <div className="bet-actions">
          <button className="action-button cancel" onClick={cancelBet}>
            Cancel
          </button>
          <button
            className="action-button confirm"
            onClick={submitBet}
            disabled={!calc.isValidRaise(raiseAmount) && raiseAmount !== calc.safeMaxRaiseTo}
          >
            {playerOptions.includes('raise') ? `Raise $${raiseAmount}` : `Bet $${raiseAmount}`}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={`action-panel ${inline ? 'inline' : ''}`}>
      <div className="action-buttons">
        {playerOptions.includes('fold') && (
          <button
            className="action-button fold"
            onClick={() => onAction('fold')}
          >
            Fold
          </button>
        )}

        {playerOptions.includes('check') && (
          <button
            className="action-button check"
            onClick={() => onAction('check')}
          >
            Check
          </button>
        )}

        {playerOptions.includes('call') && (
          <button
            className="action-button call"
            onClick={() => onAction('call')}
          >
            Call ${calc.callAmount}
          </button>
        )}

        {playerOptions.includes('bet') && (
          <button
            className="action-button bet"
            onClick={handleBetRaise}
          >
            Bet
          </button>
        )}

        {playerOptions.includes('raise') && (
          <button
            className="action-button raise"
            onClick={handleBetRaise}
          >
            Raise
          </button>
        )}

        {/* When only all_in is available (can't call or raise), show button to open raise interface */}
        {playerOptions.includes('all_in') && !playerOptions.includes('raise') && !playerOptions.includes('bet') && (
          <button
            className="action-button all-in"
            onClick={handleBetRaise}
          >
            All-In ${currentPlayerStack}
          </button>
        )}
      </div>
    </div>
  );
}
