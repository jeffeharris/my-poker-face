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
  const [customAmount, setCustomAmount] = useState('');

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

  const handleBetRaise = () => {
    setShowBetInterface(true);
    setBetAmount(defaultRaise);
  };

  const submitBet = () => {
    const amount = customAmount ? parseInt(customAmount) : betAmount;
    if (!isNaN(amount) && amount >= safeMinRaise && amount <= safeStack) {
      onAction('raise', amount);
      setShowBetInterface(false);
      setCustomAmount('');
    }
  };

  const cancelBet = () => {
    setShowBetInterface(false);
    setCustomAmount('');
  };

  if (showBetInterface) {
    return (
      <div className="action-panel betting-interface">
        <div className="bet-header">
          <div className="bet-title">
            {playerOptions.includes('raise') ? 'Raise Amount' : 'Bet Amount'}
          </div>
          <div className="bet-info">
            <span className="info-item">Stack: ${safeStack}</span>
            <span className="info-item">Pot: ${safePotSize}</span>
            <span className="info-item">To Call: ${callAmount}</span>
          </div>
        </div>
        
        <div className="bet-options">
          {/* Quick bet buttons */}
          <div className="quick-bets">
            <button 
              className={`bet-button ${betAmount === defaultRaise ? 'selected' : ''}`}
              onClick={() => { setBetAmount(defaultRaise); setCustomAmount(''); }}
              disabled={defaultRaise > safeStack}
            >
              Min<br/>${defaultRaise}
            </button>
            <button 
              className={`bet-button ${betAmount === halfPot ? 'selected' : ''}`}
              onClick={() => { setBetAmount(halfPot); setCustomAmount(''); }}
              disabled={halfPot > safeStack}
            >
              ½ Pot<br/>${halfPot}
            </button>
            <button 
              className={`bet-button ${betAmount === threeQuarterPot ? 'selected' : ''}`}
              onClick={() => { setBetAmount(threeQuarterPot); setCustomAmount(''); }}
              disabled={threeQuarterPot > safeStack}
            >
              ¾ Pot<br/>${threeQuarterPot}
            </button>
            <button 
              className={`bet-button ${betAmount === fullPot ? 'selected' : ''}`}
              onClick={() => { setBetAmount(fullPot); setCustomAmount(''); }}
              disabled={fullPot > safeStack}
            >
              Pot<br/>${fullPot}
            </button>
            <button 
              className={`bet-button all-in ${betAmount === safeStack ? 'selected' : ''}`}
              onClick={() => { setBetAmount(safeStack); setCustomAmount(''); }}
            >
              All-In<br/>${safeStack}
            </button>
          </div>

          {/* Slider */}
          <div className="bet-slider-container">
            <input
              type="range"
              className="bet-slider"
              min={safeMinRaise}
              max={safeStack}
              value={customAmount ? parseInt(customAmount) || safeMinRaise : betAmount}
              onChange={(e) => {
                const value = parseInt(e.target.value);
                if (!isNaN(value)) {
                  setBetAmount(value);
                  setCustomAmount('');
                }
              }}
            />
            <div className="slider-labels">
              <span>${safeMinRaise}</span>
              <span>${safeStack}</span>
            </div>
          </div>

          {/* Custom amount input */}
          <div className="custom-bet">
            <input
              type="number"
              className="custom-bet-input"
              placeholder={`Custom amount ($${safeMinRaise}-$${safeStack})`}
              value={customAmount}
              onChange={(e) => {
                setCustomAmount(e.target.value);
                if (e.target.value) {
                  const val = parseInt(e.target.value);
                  if (!isNaN(val)) {
                    setBetAmount(Math.min(safeStack, Math.max(safeMinRaise, val)));
                  }
                }
              }}
              min={safeMinRaise}
              max={safeStack}
            />
          </div>

          {/* Current bet display */}
          <div className="current-bet-display">
            <div className="bet-amount-display">
              {playerOptions.includes('raise') ? 'Raise' : 'Bet'}: ${customAmount || betAmount}
            </div>
            <div className="bet-result">
              {callAmount > 0 && (
                <span className="bet-breakdown">
                  (${callAmount} to call + ${(customAmount ? parseInt(customAmount) || 0 : betAmount) - callAmount} raise)
                </span>
              )}
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
            disabled={(customAmount ? parseInt(customAmount) || 0 : betAmount) < safeMinRaise || (customAmount ? parseInt(customAmount) || 0 : betAmount) > safeStack}
          >
            {playerOptions.includes('raise') ? 'Raise' : 'Bet'}
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