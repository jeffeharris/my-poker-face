import { useState } from 'react';
import { useFeatureFlags } from '../../debug/FeatureFlags';
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
}

export function ActionButtons({ 
  playerOptions, 
  currentPlayerStack, 
  highestBet, 
  currentPlayerBet,
  minRaise,
  bigBlind,
  potSize,
  onAction 
}: ActionButtonsProps) {
  const [showBetInterface, setShowBetInterface] = useState(false);
  const [betAmount, setBetAmount] = useState(minRaise || 0);
  const [selectedQuickBet, setSelectedQuickBet] = useState<string | null>(null);
  const [recentBets, setRecentBets] = useState<number[]>([]);
  const [showSmartBets, setShowSmartBets] = useState(true);
  const featureFlags = useFeatureFlags();

  // Ensure all values are valid numbers
  // minRaise from backend is the minimum RAISE BY amount (typically big blind)
  const safeMinRaise = Math.max(1, minRaise || bigBlind || 20);
  const safePotSize = Math.max(0, potSize || 0);
  const safeHighestBet = Math.max(0, highestBet || 0);
  const safeCurrentBet = Math.max(0, currentPlayerBet || 0);
  const safeStack = Math.max(0, currentPlayerStack || 0);

  const callAmount = Math.max(0, safeHighestBet - safeCurrentBet);

  // Calculate raise amounts as pot fractions
  // These are the TOTAL amounts to raise TO (displayed to user, converted to "raise BY" when sent)
  const oneThirdPot = Math.max(safeMinRaise, Math.floor(safePotSize / 3));
  const halfPot = Math.max(safeMinRaise, Math.floor(safePotSize / 2));
  const twoThirdsPot = Math.max(safeMinRaise, Math.floor(safePotSize * 0.67));
  const fullPot = Math.max(safeMinRaise, safePotSize);
  const defaultRaise = Math.max(safeMinRaise, bigBlind * 2);

  // Smart bet suggestions based on context
  const getSmartBetSuggestions = () => {
    const suggestions = [];
    
    // Standard continuation bet (60-70% pot)
    const cBetSize = roundToSnap(Math.floor(safePotSize * 0.65));
    if (cBetSize >= safeMinRaise && cBetSize <= safeStack) {
      suggestions.push({ label: 'C-Bet', amount: cBetSize, type: 'strategic' });
    }
    
    // Value bet (30-40% pot for thin value)
    const valueBet = roundToSnap(Math.floor(safePotSize * 0.35));
    if (valueBet >= safeMinRaise && valueBet <= safeStack) {
      suggestions.push({ label: 'Value', amount: valueBet, type: 'value' });
    }
    
    // Overbet (1.2x pot for polarized range)
    const overbet = roundToSnap(Math.floor(safePotSize * 1.2));
    if (overbet <= safeStack && safePotSize > 0) {
      suggestions.push({ label: 'Overbet', amount: overbet, type: 'aggressive' });
    }
    
    // Recent bets (if any) - already snapped when stored
    recentBets.slice(0, 2).forEach((amount, index) => {
      if (amount >= safeMinRaise && amount <= safeStack) {
        suggestions.push({ label: `Recent ${index + 1}`, amount, type: 'history' });
      }
    });
    
    return suggestions.slice(0, 3); // Return top 3 suggestions
  };

  // Calculate snap increment based on big blind
  const getSnapIncrement = () => {
    if (bigBlind <= 2) return 1;      // $1 increments for micro stakes
    if (bigBlind <= 10) return 5;     // $5 increments for small stakes
    if (bigBlind <= 50) return 10;    // $10 increments for mid stakes
    if (bigBlind <= 200) return 25;   // $25 increments for higher stakes
    if (bigBlind <= 1000) return 50;  // $50 increments for high stakes
    return 100;                        // $100 increments for nosebleeds
  };

  const snapIncrement = getSnapIncrement();

  // Round to nearest snap increment
  const roundToSnap = (value: number) => {
    return Math.round(value / snapIncrement) * snapIncrement;
  };

  const handleBetRaise = () => {
    setShowBetInterface(true);
    setBetAmount(roundToSnap(defaultRaise));
  };

  const submitBet = () => {
    if (betAmount >= safeMinRaise && betAmount <= safeStack) {
      // Convert "raise TO" (what user sees) to "raise BY" (what backend expects)
      // Backend's player_raise adds cost_to_call internally, so we send the raise increment
      const raiseByAmount = betAmount - callAmount;
      onAction('raise', raiseByAmount);
      // Track recent bets (keep last 5)
      setRecentBets(prev => [betAmount, ...prev.filter(b => b !== betAmount)].slice(0, 5));
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
    const snappedAmount = buttonId === 'all-in' ? amount : roundToSnap(amount);
    setBetAmount(snappedAmount);
    setSelectedQuickBet(buttonId);
  };

  if (showBetInterface) {
    const smartSuggestions = getSmartBetSuggestions();
    
    return (
      <div className="action-panel betting-interface">
        <div className="bet-header">
          <div className="bet-title">
            {playerOptions.includes('raise') ? 'Raise' : 'Bet'}
          </div>
          <div className="bet-info">
            <span className="info-item">Stack: ${safeStack}</span>
            <span className="info-item">Pot: ${safePotSize}</span>
            {callAmount > 0 && <span className="info-item">To Call: ${callAmount}</span>}
          </div>
        </div>

        {/* Unified Bet Display */}
        <div className="unified-bet-display">
          <div className="bet-preview">
            <span className="bet-label">You'll {playerOptions.includes('raise') ? 'raise to' : 'bet'}:</span>
            <span className="bet-total">${betAmount}</span>
          </div>
          {callAmount > 0 && (
            <div className="bet-breakdown">
              <span className="call-portion">Call ${callAmount}</span>
              <span className="plus">+</span>
              <span className="raise-portion">Raise ${betAmount - callAmount}</span>
            </div>
          )}
          <div className="stack-after">
            Stack after: ${safeStack - betAmount}
          </div>
          <div className="snap-info">
            Increments: ${snapIncrement}
          </div>
        </div>
        
        {/* Smart Bet Suggestions */}
        {featureFlags.smartBetSuggestions && smartSuggestions.length > 0 && (
          <div className="smart-suggestions">
            <div 
              className="suggestions-header"
              onClick={() => setShowSmartBets(!showSmartBets)}
            >
              <span className="header-text">Smart Bets</span>
              <span className="toggle-icon">{showSmartBets ? '−' : '+'}</span>
            </div>
            {showSmartBets && (
              <div className="suggestion-buttons">
                {smartSuggestions.map((suggestion, index) => (
                  <button
                    key={index}
                    className={`suggestion-button ${suggestion.type} ${betAmount === suggestion.amount ? 'selected' : ''}`}
                    onClick={() => selectBetAmount(suggestion.amount, `smart-${index}`)}
                    disabled={suggestion.amount > safeStack}
                  >
                    <span className="suggestion-label">{suggestion.label}</span>
                    <span className="suggestion-amount">${suggestion.amount}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        
        <div className="bet-options">
          {/* Quick bet buttons */}
          <div className="quick-bets">
            <button 
              className={`bet-button ${selectedQuickBet === 'min' && betAmount === safeMinRaise ? 'selected' : ''}`}
              onClick={() => selectBetAmount(safeMinRaise, 'min')}
              disabled={safeMinRaise > safeStack}
            >
              Min<br/>${safeMinRaise}
            </button>
            <button 
              className={`bet-button ${selectedQuickBet === '1/3' && betAmount === oneThirdPot ? 'selected' : ''}`}
              onClick={() => selectBetAmount(oneThirdPot, '1/3')}
              disabled={oneThirdPot > safeStack}
            >
              ⅓ Pot<br/>${oneThirdPot}
            </button>
            <button 
              className={`bet-button ${selectedQuickBet === '1/2' && betAmount === halfPot ? 'selected' : ''}`}
              onClick={() => selectBetAmount(halfPot, '1/2')}
              disabled={halfPot > safeStack}
            >
              ½ Pot<br/>${halfPot}
            </button>
            <button 
              className={`bet-button ${selectedQuickBet === '2/3' && betAmount === twoThirdsPot ? 'selected' : ''}`}
              onClick={() => selectBetAmount(twoThirdsPot, '2/3')}
              disabled={twoThirdsPot > safeStack}
            >
              ⅔ Pot<br/>${twoThirdsPot}
            </button>
            <button 
              className={`bet-button ${selectedQuickBet === 'pot' && betAmount === fullPot ? 'selected' : ''}`}
              onClick={() => selectBetAmount(fullPot, 'pot')}
              disabled={fullPot > safeStack}
            >
              Pot<br/>${fullPot}
            </button>
            <button 
              className={`bet-button all-in ${selectedQuickBet === 'all-in' && betAmount === safeStack ? 'selected' : ''}`}
              onClick={() => selectBetAmount(safeStack, 'all-in')}
            >
              All-In<br/>${safeStack}
            </button>
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
              min={safeMinRaise}
              max={safeStack}
              value={betAmount}
              onChange={(e) => {
                const value = parseInt(e.target.value);
                if (!isNaN(value)) {
                  // Round to snap increment for smooth sliding
                  const snappedValue = roundToSnap(value);
                  
                  // Also check for pot-based snap points
                  const potSnapPoints = [
                    oneThirdPot,
                    halfPot,
                    twoThirdsPot,
                    fullPot
                  ].filter(v => v >= safeMinRaise && v <= safeStack);
                  
                  // If we're very close to a pot-based snap point, use it instead
                  const snapThreshold = snapIncrement * 2;
                  let finalValue = snappedValue;
                  
                  for (const snapPoint of potSnapPoints) {
                    if (Math.abs(value - snapPoint) < snapThreshold) {
                      finalValue = snapPoint;
                      break;
                    }
                  }
                  
                  setBetAmount(finalValue);
                  setSelectedQuickBet(null);
                }
              }}
            />
            <div className="slider-labels">
              <span>${safeMinRaise}</span>
              <span className="pot-marker" style={{ left: '33%' }}>⅓</span>
              <span className="pot-marker" style={{ left: '50%' }}>½</span>
              <span className="pot-marker" style={{ left: '67%' }}>⅔</span>
              <span>${safeStack}</span>
            </div>
          </div>

          {/* Custom amount input */}
          <div className="custom-bet">
            <input
              type="number"
              className="custom-bet-input"
              placeholder={`Enter amount ($${safeMinRaise}-$${safeStack})`}
              value={betAmount}
              onChange={(e) => {
                const val = parseInt(e.target.value);
                if (!isNaN(val)) {
                  // Don't snap while typing, just enforce min/max
                  setBetAmount(Math.min(safeStack, Math.max(safeMinRaise, val)));
                  setSelectedQuickBet(null);
                } else if (e.target.value === '') {
                  setBetAmount(safeMinRaise);
                }
              }}
              onBlur={(e) => {
                // Snap to increment when user finishes typing
                const val = parseInt(e.target.value);
                if (!isNaN(val)) {
                  setBetAmount(Math.min(safeStack, Math.max(safeMinRaise, roundToSnap(val))));
                }
              }}
              onFocus={(e) => e.target.select()}
              min={safeMinRaise}
              max={safeStack}
            />
            <div className="input-shortcuts">
              <button 
                className="shortcut-btn"
                onClick={() => {
                  const doubled = betAmount * 2;
                  setBetAmount(Math.min(safeStack, roundToSnap(doubled)));
                  setSelectedQuickBet(null);
                }}
                disabled={betAmount * 2 > safeStack}
              >
                2x
              </button>
              <button 
                className="shortcut-btn"
                onClick={() => {
                  const halved = betAmount / 2;
                  setBetAmount(Math.max(safeMinRaise, roundToSnap(halved)));
                  setSelectedQuickBet(null);
                }}
              >
                ½x
              </button>
              <button 
                className="shortcut-btn"
                onClick={() => {
                  const increased = betAmount + snapIncrement;
                  setBetAmount(Math.min(safeStack, increased));
                  setSelectedQuickBet(null);
                }}
                disabled={betAmount + snapIncrement > safeStack}
              >
                +${snapIncrement}
              </button>
              <button 
                className="shortcut-btn"
                onClick={() => {
                  const decreased = betAmount - snapIncrement;
                  setBetAmount(Math.max(safeMinRaise, decreased));
                  setSelectedQuickBet(null);
                }}
                disabled={betAmount - snapIncrement < safeMinRaise}
              >
                -${snapIncrement}
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
            disabled={betAmount < safeMinRaise || betAmount > safeStack}
          >
            {playerOptions.includes('raise') ? `Raise $${betAmount}` : `Bet $${betAmount}`}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="action-panel">
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
            Call ${callAmount}
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