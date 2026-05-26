import { useState, useEffect, useMemo } from 'react';
import { Trophy, HeartCrack } from 'lucide-react';
import { Card } from '../../cards';
import { config } from '../../../config';
import { getOrdinal, type BackendCard } from '../../../types/tournament';
import type { Player } from '../../../types/player';
import './WinnerAnnouncement.css';

interface PlayerShowdownInfo {
  cards: string[] | BackendCard[];
  hand_name: string;
  hand_rank: number;
  hand_score?: number;
  kickers?: string[];
}

interface PotBreakdown {
  pot_name: string;
  total_amount: number;
  winners: { name: string; amount: number }[];
  hand_name?: string;
}

interface WinnerInfo {
  winners: string[];
  winnings?: { [key: string]: number }; // Optional - may use pot_breakdown instead
  pot_breakdown?: PotBreakdown[]; // New format from backend
  pot_contributions?: { [key: string]: number }; // Player name -> amount contributed to pot
  hand_name: string;
  winning_hand?: string[];
  showdown: boolean;
  players_showdown?: { [key: string]: PlayerShowdownInfo };
  community_cards?: string[] | BackendCard[];
  // Tournament final hand context
  is_final_hand?: boolean;
  tournament_outcome?: {
    human_won: boolean;
    human_position: number;
  };
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
  /** Live players with current avatar_url/avatar_emotion. Used to render
   *  emotion-aware avatar portraits in the showdown. */
  players?: Player[];
}

export function WinnerAnnouncement({ winnerInfo, onComplete, players }: WinnerAnnouncementProps) {
  // Force the `/full` suffix so the winner card shows the full uncropped
  // square portrait, not the circle-cropped headshot the table uses.
  const avatarByName = useMemo(() => {
    const map = new Map<string, { url?: string; emotion?: string }>();
    for (const p of players ?? []) {
      const url =
        p.avatar_url && !p.avatar_url.endsWith('/full') ? `${p.avatar_url}/full` : p.avatar_url;
      map.set(p.name, { url, emotion: p.avatar_emotion });
    }
    return map;
  }, [players]);
  const [show, setShow] = useState(false);
  const [revealCards, setRevealCards] = useState(false);

  useEffect(() => {
    if (winnerInfo) {
      setShow(true);

      // If it's a showdown, reveal cards after a delay
      if (winnerInfo.showdown && winnerInfo.players_showdown) {
        setTimeout(() => setRevealCards(true), 1000);
      }

      // For final hand, don't auto-dismiss - require user to click continue
      if (winnerInfo.is_final_hand) {
        return;
      }

      // Auto-hide after animation (for non-final hands)
      const timer = setTimeout(
        () => {
          setShow(false);
          setRevealCards(false);
          setTimeout(onComplete, 500); // Wait for fade out
        },
        winnerInfo.showdown ? 6000 : 3000
      ); // Longer for showdowns

      return () => clearTimeout(timer);
    }
  }, [winnerInfo, onComplete]);

  if (!winnerInfo || !show) return null;

  const winnersString =
    winnerInfo.winners.length > 1
      ? winnerInfo.winners.slice(0, -1).join(', ') +
        ' and ' +
        winnerInfo.winners[winnerInfo.winners.length - 1]
      : winnerInfo.winners[0];

  // Calculate net profit (winnings minus contributions) - standard poker display
  // Gross winnings = what winners receive from pots
  // Net profit = gross winnings - what winners contributed to the pot
  let grossWinnings = 0;
  let winnersContributions = 0;
  const perPlayerWinnings: Record<string, number> = {};

  if (winnerInfo.pot_breakdown) {
    // Sum each winner's share from all pots
    for (const pot of winnerInfo.pot_breakdown) {
      for (const winner of pot.winners) {
        grossWinnings += winner.amount;
        perPlayerWinnings[winner.name] = (perPlayerWinnings[winner.name] || 0) + winner.amount;
      }
    }
  } else if (winnerInfo.winnings) {
    grossWinnings = Object.values(winnerInfo.winnings).reduce((sum, val) => sum + val, 0);
    Object.assign(perPlayerWinnings, winnerInfo.winnings);
  }

  // Subtract winners' contributions for net profit
  if (winnerInfo.pot_contributions) {
    for (const winnerName of winnerInfo.winners) {
      winnersContributions += winnerInfo.pot_contributions[winnerName] || 0;
    }
  }

  const netProfit = grossWinnings - winnersContributions;
  const isSplitPot = winnerInfo.winners.length > 1;

  return (
    <div className={`winner-announcement ${show ? 'show' : ''}`}>
      <div className="winner-overlay" />

      <div className="winner-content">
        <div className="winner-header">
          <h1 className="winner-title">
            <Trophy size={28} /> {isSplitPot ? 'Split Pot!' : 'Winner!'} <Trophy size={28} />
          </h1>
          {winnerInfo.winners.length > 0 && (
            <div className="winner-avatars-row" data-testid="winner-avatars-row">
              {winnerInfo.winners.map((name) => {
                const avatar = avatarByName.get(name);
                return (
                  <div
                    key={name}
                    className="winner-avatar-badge"
                    data-emotion={avatar?.emotion || 'neutral'}
                    aria-label={`${name} — ${avatar?.emotion || 'neutral'}`}
                  >
                    {avatar?.url ? (
                      <img
                        src={`${config.API_URL}${avatar.url}`}
                        alt={`${name} - ${avatar.emotion || 'neutral'}`}
                        className="winner-avatar-image"
                        onError={(e) => {
                          e.currentTarget.style.display = 'none';
                        }}
                      />
                    ) : (
                      <span className="winner-avatar-initial">{name.charAt(0).toUpperCase()}</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          <div className="winner-name">{winnersString}</div>
        </div>

        {/* Tournament Outcome Banner - only shown on final hand */}
        {winnerInfo.is_final_hand && winnerInfo.tournament_outcome && (
          <div
            className={`tournament-outcome-banner ${winnerInfo.tournament_outcome.human_won ? 'victory' : 'defeat'}`}
          >
            {winnerInfo.tournament_outcome.human_won ? (
              <>
                <Trophy size={20} /> YOU WON THE TOURNAMENT! <Trophy size={20} />
              </>
            ) : (
              <>
                <HeartCrack size={20} /> YOU'RE OUT! Finished{' '}
                {getOrdinal(winnerInfo.tournament_outcome.human_position)}
              </>
            )}
          </div>
        )}

        <div className="winner-details">
          <div className="pot-won">
            {isSplitPot ? `Split Pot +$${netProfit}` : `+$${netProfit}`}
          </div>
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
                  .sort(([, a], [, b]) => (b.hand_score ?? 0) - (a.hand_score ?? 0))
                  .map(([player, playerInfo]) => {
                    const isWinner = winnerInfo.winners.includes(player);
                    const hasKickers = playerInfo.kickers && playerInfo.kickers.length > 0;
                    const avatar = avatarByName.get(player);
                    return (
                      <div key={player} className={`player-showdown ${isWinner ? 'winner' : ''}`}>
                        <div className="player-showdown-header">
                          <span className="player-name">{player}</span>
                        </div>
                        <div className="player-showdown-main">
                          <div
                            className="player-showdown-avatar"
                            data-emotion={avatar?.emotion || 'neutral'}
                            aria-label={`${player} — ${avatar?.emotion || 'neutral'}`}
                          >
                            {avatar?.url ? (
                              <img
                                src={`${config.API_URL}${avatar.url}`}
                                alt={`${player} - ${avatar.emotion || 'neutral'}`}
                                className="player-showdown-avatar-image"
                                onError={(e) => {
                                  e.currentTarget.style.display = 'none';
                                }}
                              />
                            ) : (
                              <span className="player-showdown-avatar-initial">
                                {player.charAt(0).toUpperCase()}
                              </span>
                            )}
                          </div>
                          <div className="player-showdown-middle">
                            {playerInfo.hand_name && (
                              <div className="player-hand-name">
                                {playerInfo.hand_name}
                                {hasKickers && (
                                  <span className="player-kickers">
                                    {' '}
                                    (kicker: {playerInfo.kickers!.join(', ')})
                                  </span>
                                )}
                              </div>
                            )}
                            {perPlayerWinnings[player] > 0 && (
                              <div className="player-winnings">+${perPlayerWinnings[player]}</div>
                            )}
                          </div>
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

        {/* Continue button for final hand - requires manual dismiss */}
        {winnerInfo.is_final_hand && (
          <button
            className="continue-to-results-btn"
            onClick={() => {
              setShow(false);
              setRevealCards(false);
              setTimeout(onComplete, 500);
            }}
          >
            Continue to Results
          </button>
        )}
      </div>
    </div>
  );
}
