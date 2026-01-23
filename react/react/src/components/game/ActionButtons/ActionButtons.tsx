import { useState } from 'react';
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
  const [selectedQuickBet, setSelectedQuickBet] = useState<string | null>(null);

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

  // Bet amount state - initialize to default raise
  const [betAmount, setBetAmount] = useState(calc.getDefaultRaise());

  const handleBetRaise = () => {
    setShowBetInterface(true);
    setBetAmount(calc.getDefaultRaise());
  };

  const submitBet = () => {
    // Allow bet if valid OR if it's all-in (even below min)
    const isAllIn = betAmount === calc.safeMaxRaiseTo;
    const isValidBet = calc.isValidRaise(betAmount) || isAllIn;

    if (isValidBet) {
      // Send the "raise TO" amount directly - backend now expects this
      onAction('raise', betAmount);
      setShowBetInterface(false);
      setSelectedQuickBet(null);
    }
  };

  const cancelBet = () => {
    setShowBetInterface(false);
    setSelectedQuickBet(null);
  };

  // Update bet amount and track which button was selected
  const selectBetAmount = (amount: number, buttonId: string | null = null) => {
    // Always round to snap increment except for all-in
    const snappedAmount = buttonId === 'all-in' ? amount : calc.roundToSnap(amount);
    setBetAmount(snappedAmount);
    setSelectedQuickBet(buttonId);
  };

  // Get breakdown for display
  const breakdown = calc.getBreakdown(betAmount);

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
            <span className="bet-total">${betAmount}</span>
          </div>
          {calc.callAmount > 0 && (
            <div className="bet-breakdown">
              <span className="call-portion">Call ${breakdown.callPortion}</span>
              <span className="plus">+</span>
              <span className="raise-portion">Raise ${breakdown.raisePortion}</span>
            </div>
          )}
          <div className="stack-after">
            Stack after: ${breakdown.stackAfter}
          </div>
          <div className="snap-info">
            Increments: ${calc.snapIncrement} (½ BB)
          </div>
        </div>

        <div className="bet-options">
          {/* Quick bet buttons */}
          <div className="quick-bets">
            {calc.quickBets.map(({ label, amount, id }) => (
              <button
                key={id}
                className={`bet-button ${id === 'all-in' ? 'all-in' : ''} ${selectedQuickBet === id && betAmount === amount ? 'selected' : ''}`}
                onClick={() => selectBetAmount(amount, id)}
                disabled={amount > calc.safeMaxRaiseTo}
              >
                {label}<br/>${amount}
              </button>
            ))}
          </div>

          {/* Enhanced Slider with Snap Points */}
          <div className="bet-slider-container">
            <div className="slider-snap-points">
              <div className="snap-point" style={{ left: '0%' }} />
              <div className="snap-point" style={{ left: '33%' }} />
              <div className="snap-point" style={{ left: '50%' }} />
              <div className="snap-point" style={{ left: '67%' }} />
              <div className="snap-point" style={{ left: '100%' }} />
            </div>
            <input
              type="range"
              className="bet-slider"
              min={calc.safeMinRaiseTo}
              max={calc.safeMaxRaiseTo}
              value={betAmount}
              onChange={(e) => {
                const value = parseInt(e.target.value);
                if (!isNaN(value)) {
                  // Use magnetic snapping (0.5BB increments with pot fraction magnets)
                  const snappedValue = calc.snapWithMagnets(value);
                  setBetAmount(snappedValue);
                  setSelectedQuickBet(null);
                }
              }}
            />
            <div className="slider-labels">
              <span>${calc.safeMinRaiseTo}</span>
              <span className="pot-marker" style={{ left: '33%' }}>⅓</span>
              <span className="pot-marker" style={{ left: '50%' }}>½</span>
              <span className="pot-marker" style={{ left: '67%' }}>⅔</span>
              <span>${calc.safeMaxRaiseTo}</span>
            </div>
          </div>

          {/* Custom amount input */}
          <div className="custom-bet">
            <input
              type="number"
              className="custom-bet-input"
              placeholder={`Enter amount ($${calc.safeMinRaiseTo}-$${calc.safeMaxRaiseTo})`}
              value={betAmount}
              onChange={(e) => {
                const val = parseInt(e.target.value);
                if (!isNaN(val)) {
                  // Don't snap while typing, just enforce min/max
                  setBetAmount(Math.min(calc.safeMaxRaiseTo, Math.max(calc.safeMinRaiseTo, val)));
                  setSelectedQuickBet(null);
                } else if (e.target.value === '') {
                  setBetAmount(calc.safeMinRaiseTo);
                }
              }}
              onBlur={(e) => {
                // Snap to increment when user finishes typing
                const val = parseInt(e.target.value);
                if (!isNaN(val)) {
                  setBetAmount(Math.min(calc.safeMaxRaiseTo, Math.max(calc.safeMinRaiseTo, calc.roundToSnap(val))));
                }
              }}
              onFocus={(e) => e.target.select()}
              min={calc.safeMinRaiseTo}
              max={calc.safeMaxRaiseTo}
            />
            <div className="input-shortcuts">
              <button
                className="shortcut-btn"
                onClick={() => {
                  const doubled = betAmount * 2;
                  setBetAmount(Math.min(calc.safeMaxRaiseTo, calc.roundToSnap(doubled)));
                  setSelectedQuickBet(null);
                }}
                disabled={betAmount * 2 > calc.safeMaxRaiseTo}
              >
                2x
              </button>
              <button
                className="shortcut-btn"
                onClick={() => {
                  const halved = betAmount / 2;
                  setBetAmount(Math.max(calc.safeMinRaiseTo, calc.roundToSnap(halved)));
                  setSelectedQuickBet(null);
                }}
              >
                ½x
              </button>
              <button
                className="shortcut-btn"
                onClick={() => {
                  const increased = betAmount + calc.snapIncrement;
                  setBetAmount(Math.min(calc.safeMaxRaiseTo, increased));
                  setSelectedQuickBet(null);
                }}
                disabled={betAmount + calc.snapIncrement > calc.safeMaxRaiseTo}
              >
                +${calc.snapIncrement}
              </button>
              <button
                className="shortcut-btn"
                onClick={() => {
                  const decreased = betAmount - calc.snapIncrement;
                  setBetAmount(Math.max(calc.safeMinRaiseTo, decreased));
                  setSelectedQuickBet(null);
                }}
                disabled={betAmount - calc.snapIncrement < calc.safeMinRaiseTo}
              >
                -${calc.snapIncrement}
              </button>
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
            disabled={!calc.isValidRaise(betAmount) && betAmount !== calc.safeMaxRaiseTo}
          >
            {playerOptions.includes('raise') ? `Raise $${betAmount}` : `Bet $${betAmount}`}
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

        {playerOptions.includes('all_in') && (
          <button
            className="action-button all-in"
            onClick={() => onAction('all_in')}
          >
            All-In ${currentPlayerStack}
          </button>
        )}
      </div>
    </div>
  );
}
