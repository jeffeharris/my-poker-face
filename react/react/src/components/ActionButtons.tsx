import { useState } from 'react';
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

  // Ensure all values are valid numbers
  const safeMinRaise = Math.max(1, minRaise || bigBlind || 20);
  const safePotSize = Math.max(0, potSize || 0);
  const safeHighestBet = Math.max(0, highestBet || 0);
  const safeCurrentBet = Math.max(0, currentPlayerBet || 0);
  const safeStack = Math.max(0, currentPlayerStack || 0);
  
  const callAmount = Math.max(0, safeHighestBet - safeCurrentBet);
  
  // Calculate default bet amounts
  const halfPot = Math.max(safeMinRaise, Math.floor(safePotSize / 2));
  const fullPot = Math.max(safeMinRaise, safePotSize);
  const threeQuarterPot = Math.max(safeMinRaise, Math.floor(safePotSize * 0.75));
  const defaultRaise = Math.max(safeMinRaise, bigBlind * 2);
  const oneThirdPot = Math.max(safeMinRaise, Math.floor(safePotSize / 3));
  const twoThirdsPot = Math.max(safeMinRaise, Math.floor(safePotSize * 0.67));

  // Smart bet suggestions based on context
  const getSmartBetSuggestions = () => {
    const suggestions = [];
    
    // Standard continuation bet (60-70% pot)
    const cBetSize = Math.floor(safePotSize * 0.65);
    if (cBetSize >= safeMinRaise && cBetSize <= safeStack) {
      suggestions.push({ label: 'C-Bet', amount: cBetSize, type: 'strategic' });
    }
    
    // Value bet (30-40% pot for thin value)
    const valueBet = Math.floor(safePotSize * 0.35);
    if (valueBet >= safeMinRaise && valueBet <= safeStack) {
      suggestions.push({ label: 'Value', amount: valueBet, type: 'value' });
    }
    
    // Overbet (1.2x pot for polarized range)
    const overbet = Math.floor(safePotSize * 1.2);
    if (overbet <= safeStack && safePotSize > 0) {
      suggestions.push({ label: 'Overbet', amount: overbet, type: 'aggressive' });
    }
    
    // Recent bets (if any)
    recentBets.slice(0, 2).forEach((amount, index) => {
      if (amount >= safeMinRaise && amount <= safeStack) {
        suggestions.push({ label: `Recent ${index + 1}`, amount, type: 'history' });
      }
    });
    
    return suggestions.slice(0, 3); // Return top 3 suggestions
  };

  const handleBetRaise = () => {
    setShowBetInterface(true);
    setBetAmount(defaultRaise);
  };

  const submitBet = () => {
    if (betAmount >= safeMinRaise && betAmount <= safeStack) {
      onAction('raise', betAmount);
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
    setBetAmount(amount);
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
        </div>
        
        {/* Smart Bet Suggestions */}
        {smartSuggestions.length > 0 && (
          <div className="smart-suggestions">
            <div className="suggestions-header">Smart Bets</div>
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
                  // Find nearest snap point
                  const snapPoints = [
                    safeMinRaise,
                    oneThirdPot,
                    halfPot,
                    twoThirdsPot,
                    fullPot,
                    safeStack
                  ].filter(v => v >= safeMinRaise && v <= safeStack);
                  
                  // Snap to nearest point if within 5% of range
                  const range = safeStack - safeMinRaise;
                  const snapThreshold = range * 0.05;
                  
                  let snappedValue = value;
                  for (const snapPoint of snapPoints) {
                    if (Math.abs(value - snapPoint) < snapThreshold) {
                      snappedValue = snapPoint;
                      break;
                    }
                  }
                  
                  setBetAmount(snappedValue);
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
                  setBetAmount(Math.min(safeStack, Math.max(safeMinRaise, val)));
                  setSelectedQuickBet(null);
                } else if (e.target.value === '') {
                  setBetAmount(safeMinRaise);
                }
              }}
              onFocus={(e) => e.target.select()}
              min={safeMinRaise}
              max={safeStack}
            />
            <div className="input-shortcuts">
              <button 
                className="shortcut-btn"
                onClick={() => setBetAmount(Math.min(safeStack, betAmount * 2))}
                disabled={betAmount * 2 > safeStack}
              >
                2x
              </button>
              <button 
                className="shortcut-btn"
                onClick={() => setBetAmount(Math.max(safeMinRaise, Math.floor(betAmount / 2)))}
              >
                ½x
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