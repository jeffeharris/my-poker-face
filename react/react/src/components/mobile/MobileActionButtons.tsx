import { useState } from 'react';
import { Check, MessageCircle } from 'lucide-react';
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
  onQuickChat
}: MobileActionButtonsProps) {
  const [showRaiseSheet, setShowRaiseSheet] = useState(false);
  const [raiseAmount, setRaiseAmount] = useState(minRaise || bigBlind * 2);

  const safeMinRaise = Math.max(1, minRaise || bigBlind || 20);
  const safePotSize = Math.max(0, potSize || 0);
  const safeHighestBet = Math.max(0, highestBet || 0);
  const safeCurrentBet = Math.max(0, currentPlayerBet || 0);
  const safeStack = Math.max(0, currentPlayerStack || 0);
  const callAmount = Math.max(0, safeHighestBet - safeCurrentBet);

  const oneQuarterPot = Math.max(safeMinRaise, Math.floor(safePotSize / 4));
  const halfPot = Math.max(safeMinRaise, Math.floor(safePotSize / 2));
  const threeQuarterPot = Math.max(safeMinRaise, Math.floor(safePotSize * 0.75));
  const fullPot = Math.max(safeMinRaise, safePotSize);

  const handleRaise = () => {
    setRaiseAmount(safeMinRaise);
    setShowRaiseSheet(true);
  };

  const submitRaise = () => {
    // Allow raise if it meets min raise OR if it's an all-in (even below min)
    const isValidRaise = (raiseAmount >= safeMinRaise || raiseAmount === safeStack) && raiseAmount <= safeStack;
    if (isValidRaise) {
      onAction('raise', raiseAmount);
      setShowRaiseSheet(false);
    }
  };

  const quickBets = [
    { label: 'Min', amount: safeMinRaise, alwaysShow: true },
    { label: '¼ Pot', amount: oneQuarterPot, alwaysShow: false },
    { label: '½ Pot', amount: halfPot, alwaysShow: false },
    { label: '¾ Pot', amount: threeQuarterPot, alwaysShow: false },
    { label: 'Pot', amount: fullPot, alwaysShow: false },
    { label: 'All-In', amount: safeStack, alwaysShow: true },
  ].filter(b => b.amount <= safeStack && (b.alwaysShow || b.amount > safeMinRaise));

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
            disabled={(raiseAmount < safeMinRaise && raiseAmount !== safeStack) || raiseAmount > safeStack}
          >
            Confirm
          </button>
        </div>

        <div className="raise-amount-display">
          <span className="amount-label">Amount</span>
          <div className="amount-with-2x">
            <span className="amount-value">${raiseAmount}</span>
            <button
              className="double-btn"
              onClick={() => setRaiseAmount(Math.min(safeStack, raiseAmount * 2))}
              disabled={raiseAmount * 2 > safeStack}
            >
              2x
            </button>
          </div>
        </div>

        <div className="quick-bet-buttons">
          {quickBets.map(({ label, amount }) => (
            <button
              key={label}
              className={`quick-bet-btn ${raiseAmount === amount ? 'selected' : ''}`}
              onClick={() => setRaiseAmount(amount)}
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
            min={safeMinRaise}
            max={safeStack}
            value={raiseAmount}
            onChange={(e) => setRaiseAmount(parseInt(e.target.value))}
          />
          <div className="slider-labels">
            <span>${safeMinRaise}</span>
            <span>${safeStack}</span>
          </div>
        </div>

        <div className="stack-preview">
          Stack after: ${safeStack - raiseAmount}
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
          <span className="btn-label">Call ${callAmount}</span>
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
          <span className="btn-label">All-In ${safeStack}</span>
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
