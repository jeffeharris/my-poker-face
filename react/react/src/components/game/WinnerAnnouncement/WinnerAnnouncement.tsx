import { useState, useEffect } from 'react';
import { Card } from '../../cards';
import './WinnerAnnouncement.css';

interface PlayerShowdownInfo {
  cards: any[];
  hand_name: string;
  hand_rank: number;
  kickers?: string[];
}

interface WinnerInfo {
  winners: string[];
  winnings: { [key: string]: number };
  hand_name: string;
  winning_hand?: string[];
  showdown: boolean;
  players_showdown?: { [key: string]: PlayerShowdownInfo };
  community_cards?: any[];
}

interface CommentaryItem {
  player_name: string;
  comment: string;
  ttl: number;
  id: string;
  timestamp: number;
}

interface WinnerAnnouncementProps {
  winnerInfo: WinnerInfo | null;
  commentary?: CommentaryItem[];
  onComplete: () => void;
}

export function WinnerAnnouncement({ winnerInfo, onComplete }: WinnerAnnouncementProps) {
  const [show, setShow] = useState(false);
  const [revealCards, setRevealCards] = useState(false);

  useEffect(() => {
    if (winnerInfo) {
      setShow(true);
      
      // If it's a showdown, reveal cards after a delay
      if (winnerInfo.showdown && winnerInfo.players_cards) {
        setTimeout(() => setRevealCards(true), 1000);
      }
      
      // Auto-hide after animation
      const timer = setTimeout(() => {
        setShow(false);
        setRevealCards(false);
        setTimeout(onComplete, 500); // Wait for fade out
      }, winnerInfo.showdown ? 6000 : 3000); // Longer for showdowns

      return () => clearTimeout(timer);
    }
  }, [winnerInfo, onComplete]);

  if (!winnerInfo || !show) return null;

  const winnersString = winnerInfo.winners.length > 1
    ? winnerInfo.winners.slice(0, -1).join(', ') + ' and ' + winnerInfo.winners[winnerInfo.winners.length - 1]
    : winnerInfo.winners[0];

  const totalWinnings = Object.values(winnerInfo.winnings).reduce((sum, val) => sum + val, 0);
  const isSplitPot = winnerInfo.winners.length > 1;

  return (
    <div className={`winner-announcement ${show ? 'show' : ''}`}>
      <div className="winner-overlay" />

      <div className="winner-content">
        <div className="winner-header">
          <h1 className="winner-title">{isSplitPot ? '🏆 Split Pot! 🏆' : '🏆 Winner! 🏆'}</h1>
          <div className="winner-name">{winnersString}</div>
        </div>

        <div className="winner-details">
          <div className="pot-won">{isSplitPot ? `Split $${totalWinnings}` : `Won $${totalWinnings}`}</div>
          {winnerInfo.showdown && winnerInfo.hand_name && (
            <div className="hand-name">with {winnerInfo.hand_name}</div>
          )}
        </div>

        {winnerInfo.showdown && (
          <div className={`showdown-cards ${revealCards ? 'reveal' : ''}`}>
            {/* Community Cards */}
            {winnerInfo.community_cards && winnerInfo.community_cards.length > 0 && (
              <div className="community-cards-section">
                <div className="section-label">Community Cards</div>
                <div className="community-cards-display">
                  {winnerInfo.community_cards.map((card, i) => (
                    <Card key={i} card={card} size="medium" faceDown={false} />
                  ))}
                </div>
              </div>
            )}
            
            {/* Player Cards - sorted by hand rank (best first) */}
            {winnerInfo.players_showdown && (
              <div className="players-section">
                {Object.entries(winnerInfo.players_showdown)
                  .sort(([, infoA], [, infoB]) => infoA.hand_rank - infoB.hand_rank)
                  .map(([player, playerInfo]) => {
                    const isWinner = winnerInfo.winners.includes(player);
                    const hasKickers = playerInfo.kickers && playerInfo.kickers.length > 0;
                    return (
                      <div key={player} className={`player-showdown ${isWinner ? 'winner' : ''}`}>
                        <div className="player-info">
                          <div className="player-name">{player}</div>
                          {playerInfo.hand_name && (
                            <div className="player-hand-name">
                              {playerInfo.hand_name}
                              {hasKickers && (
                                <span className="player-kickers"> (kicker: {playerInfo.kickers!.join(', ')})</span>
                              )}
                            </div>
                          )}
                        </div>
                        <div className="player-cards">
                          {playerInfo.cards.map((card, i) => (
                            <Card key={i} card={card} size="large" faceDown={false} />
                          ))}
                        </div>
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        )}

        {!winnerInfo.showdown && (
          <div className="no-showdown">
            <p>All opponents folded</p>
          </div>
        )}
      </div>
    </div>
  );
}