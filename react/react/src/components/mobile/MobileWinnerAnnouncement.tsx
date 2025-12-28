import { useEffect, useState } from 'react';
import { Card } from '../cards';
import './MobileWinnerAnnouncement.css';

interface WinnerInfo {
  winners: string[];
  winnings: { [key: string]: number };
  hand_name?: string;
  winning_hand?: string[];
  showdown: boolean;
  players_cards?: { [key: string]: string[] };
  community_cards?: string[];
}

interface MobileWinnerAnnouncementProps {
  winnerInfo: WinnerInfo | null;
  onComplete: () => void;
}

export function MobileWinnerAnnouncement({ winnerInfo, onComplete }: MobileWinnerAnnouncementProps) {
  const [showCards, setShowCards] = useState(false);

  useEffect(() => {
    if (winnerInfo) {
      // Show cards after a short delay for dramatic effect
      const cardTimer = setTimeout(() => {
        setShowCards(true);
      }, 800);

      // Auto-dismiss after showing
      const dismissTimer = setTimeout(() => {
        setShowCards(false);
        onComplete();
      }, winnerInfo.showdown ? 6000 : 3000);

      return () => {
        clearTimeout(cardTimer);
        clearTimeout(dismissTimer);
      };
    }
  }, [winnerInfo, onComplete]);

  if (!winnerInfo) return null;

  const winner = winnerInfo.winners[0];
  const winAmount = winnerInfo.winnings[winner] || 0;

  return (
    <div className="mobile-winner-overlay">
      <div className="mobile-winner-content">
        <div className="winner-trophy">🏆</div>
        <div className="winner-name">{winner}</div>
        <div className="winner-amount">Wins ${winAmount}</div>

        {winnerInfo.hand_name && (
          <div className="winner-hand-name">{winnerInfo.hand_name}</div>
        )}

        {showCards && winnerInfo.showdown && winnerInfo.players_cards && (
          <div className="showdown-cards">
            {Object.entries(winnerInfo.players_cards).map(([playerName, cards]) => (
              <div key={playerName} className="player-showdown">
                <div className="showdown-player-name">{playerName}</div>
                <div className="showdown-cards-row">
                  {cards.map((card, i) => (
                    <Card key={i} card={card} faceDown={false} size="medium" />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        <button className="dismiss-btn" onClick={onComplete}>
          Continue
        </button>
      </div>
    </div>
  );
}
