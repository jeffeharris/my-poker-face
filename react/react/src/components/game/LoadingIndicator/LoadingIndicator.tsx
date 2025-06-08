import { useState, useEffect } from 'react';
import './LoadingIndicator.css';

interface LoadingIndicatorProps {
  currentPlayerName: string;
  playerIndex: number;
  totalPlayers: number;
}

export function LoadingIndicator({ currentPlayerName, playerIndex, totalPlayers }: LoadingIndicatorProps) {
  const [dots, setDots] = useState(1);
  const [showThinkingBubble, setShowThinkingBubble] = useState(false);

  // Animate dots
  useEffect(() => {
    const interval = setInterval(() => {
      setDots(prev => (prev % 3) + 1);
    }, 500);
    return () => clearInterval(interval);
  }, []);

  // Show thinking bubble after a delay
  useEffect(() => {
    const timer = setTimeout(() => {
      setShowThinkingBubble(true);
    }, 3000);
    return () => clearTimeout(timer);
  }, [currentPlayerName]);

  const thinkingPhrases = [
    "Calculating odds...",
    "Analyzing opponents...",
    "Considering the pot...",
    "Reading the table...",
    "Planning next move...",
    "Evaluating hand strength..."
  ];

  const randomPhrase = thinkingPhrases[Math.floor(Math.random() * thinkingPhrases.length)];

  return (
    <div className="loading-overlay">
      <div className="loading-content">
        <div className="ai-avatar">
          <div className="avatar-circle">
            <span className="avatar-initial">{currentPlayerName.charAt(0)}</span>
          </div>
          <div className="thinking-indicator">
            <div className="thinking-dot dot-1"></div>
            <div className="thinking-dot dot-2"></div>
            <div className="thinking-dot dot-3"></div>
          </div>
        </div>
        
        <h3 className="player-thinking-name">{currentPlayerName}</h3>
        <p className="thinking-status">
          Making a decision{'.'.repeat(dots)}
        </p>
        
        {showThinkingBubble && (
          <div className="thinking-bubble">
            <p>{randomPhrase}</p>
          </div>
        )}
        
        <div className="progress-bar">
          <div className="progress-fill"></div>
        </div>
        
        <div className="other-players">
          <p>Next up: {totalPlayers - 1} other players</p>
        </div>
      </div>
    </div>
  );
}