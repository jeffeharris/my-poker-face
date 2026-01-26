import { useState, useEffect } from 'react';
import type { TournamentResult } from '../../../types/tournament';
import { getOrdinal } from '../../../types/tournament';
import { WinnerAnnouncement } from '../WinnerAnnouncement/WinnerAnnouncement';
import { MobileWinnerAnnouncement } from '../../mobile/MobileWinnerAnnouncement';
import { useViewport } from '../../../hooks/useViewport';
import './TournamentComplete.css';

interface TournamentCompleteProps {
  result: (TournamentResult & { human_eliminated?: boolean }) | null;
  onComplete: () => void;
  // For mobile final hand view
  gameId?: string;
  playerName?: string;
  onSendMessage?: (text: string) => void;
}

export function TournamentComplete({ result, onComplete, gameId, playerName, onSendMessage }: TournamentCompleteProps) {
  const [show, setShow] = useState(false);
  const [showFinalHand, setShowFinalHand] = useState(false);
  const { isMobile } = useViewport();

  useEffect(() => {
    if (result) {
      // Small delay before showing for smooth animation
      const showTimer = setTimeout(() => setShow(true), 100);
      return () => clearTimeout(showTimer);
    }
  }, [result]);

  if (!result) return null;

  const humanStanding = result.standings.find(s => s.is_human);
  const isWinner = humanStanding?.finishing_position === 1;
  const humanEliminated = result.human_eliminated && !result.winner;
  const sortedStandings = [...result.standings].sort(
    (a, b) => a.finishing_position - b.finishing_position
  );

  return (
    <div className={`tournament-complete ${show ? 'show' : ''}`}>
      <div className="tournament-overlay" />

      <div className="tournament-content">
        {/* Victory/Defeat Header */}
        <div className="tournament-header">
          <h1 className="tournament-title">
            {isWinner ? 'CHAMPION!' : humanEliminated ? 'Eliminated!' : 'Tournament Complete'}
          </h1>
          {result.winner ? (
            <div className="winner-announcement">
              {result.winner} wins the tournament!
            </div>
          ) : humanEliminated ? (
            <div className="winner-announcement eliminated">
              Better luck next time!
            </div>
          ) : null}
        </div>

        {/* Your Result */}
        {humanStanding && (
          <div className={`your-result ${isWinner ? 'winner' : ''}`}>
            <div className="result-label">Your Finish</div>
            <div className="result-position">{getOrdinal(humanStanding.finishing_position)}</div>
            {!isWinner && humanStanding.eliminated_by && (
              <div className="eliminated-by">
                Eliminated by {humanStanding.eliminated_by}
              </div>
            )}
          </div>
        )}

        {/* Final Standings Table */}
        <div className="standings-section">
          <h3 className="standings-title">Final Standings</h3>
          <div className="standings-table">
            {sortedStandings.map((standing) => (
              <div
                key={standing.player_name}
                className={`standing-row ${standing.is_human ? 'human' : ''} ${standing.finishing_position === 1 ? 'winner' : ''}`}
              >
                <span className="position">{getOrdinal(standing.finishing_position)}</span>
                <span className="name">{standing.player_name}</span>
                <span className="eliminated-info">
                  {standing.eliminated_by ? `by ${standing.eliminated_by}` : 'Winner'}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Stats Summary */}
        <div className="tournament-stats">
          <div className="stat">
            <span className="stat-value">{result.total_hands}</span>
            <span className="stat-label">Hands Played</span>
          </div>
          <div className="stat">
            <span className="stat-value">${result.biggest_pot.toLocaleString()}</span>
            <span className="stat-label">Biggest Pot</span>
          </div>
        </div>

        {/* View Final Hand Button */}
        {result.final_hand_data && (
          <button className="view-final-hand-btn" onClick={() => setShowFinalHand(true)}>
            View Final Hand
          </button>
        )}

        {/* Continue Button */}
        <button className="continue-button" onClick={onComplete}>
          Return to Menu
        </button>
      </div>

      {/* Final Hand Overlay */}
      {showFinalHand && result.final_hand_data && (
        isMobile && gameId && playerName && onSendMessage ? (
          <MobileWinnerAnnouncement
            winnerInfo={{
              ...result.final_hand_data,
              is_final_hand: false  // Allow normal dismiss behavior
            }}
            onComplete={() => setShowFinalHand(false)}
            gameId={gameId}
            playerName={playerName}
            onSendMessage={onSendMessage}
          />
        ) : (
          <WinnerAnnouncement
            winnerInfo={{
              ...result.final_hand_data,
              is_final_hand: false  // Override to enable normal dismiss behavior
            }}
            onComplete={() => setShowFinalHand(false)}
          />
        )
      )}
    </div>
  );
}
