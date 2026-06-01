import { memo, useState, useEffect, useCallback, useMemo } from 'react';
import {
  Trophy,
  HeartCrack,
  PartyPopper,
  Smile,
  Angry,
  Handshake,
  Award,
  ArrowLeft,
  Check,
  type LucideIcon,
} from 'lucide-react';
import { Card } from '../../cards';
import { config } from '../../../config';
import { INTERHAND_TIMING } from '../../../constants/interhandTiming';
import { gameAPI } from '../../../utils/api';
import { logger } from '../../../utils/logger';
import { getOrdinal, type BackendCard } from '../../../types/tournament';
import type { PostRoundTone, PostRoundSuggestion } from '../../../types/chat';
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
  winnings?: { [key: string]: number };
  pot_breakdown?: PotBreakdown[];
  pot_contributions?: { [key: string]: number };
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

interface ToneOption {
  id: PostRoundTone;
  icon: LucideIcon;
  label: string;
}

export interface WinnerAnnouncementProps {
  winnerInfo: WinnerInfo | null;
  commentary?: CommentaryItem[];
  onComplete: () => void;
  /** Live players with current avatar_url/avatar_emotion. Used to render
   *  emotion-aware avatar portraits in the showdown. */
  players?: Player[];
  /** Game ID — required to fetch post-round chat suggestions. Optional;
   *  the tone bar shows fallback suggestions when omitted. */
  gameId?: string | null;
  /** Human player name — determines winner/loser tone set and is sent to
   *  the suggestions API. Optional; tone bar is suppressed when omitted. */
  playerName?: string;
  /** Callback to send a message to the table. Add this to unlock tone-chat
   *  sending. Signature matches `wrappedSendMessage` from PokerTable.
   *  Optional; the component renders the tone bar but clicking a suggestion
   *  is a no-op when this is not wired. */
  onSendMessage?: (
    message: string,
    addressing?: string[],
    tone?: string,
    intensity?: string
  ) => void;
}

// Tone options for winners
const WINNER_TONES: ToneOption[] = [
  { id: 'gloat', icon: PartyPopper, label: 'Gloat' },
  { id: 'humble', icon: Smile, label: 'Humble' },
  { id: 'props', icon: Award, label: 'Props' },
];

// Tone options for losers
const LOSER_TONES: ToneOption[] = [
  { id: 'salty', icon: Angry, label: 'Salty' },
  { id: 'gracious', icon: Handshake, label: 'Gracious' },
  { id: 'props', icon: Award, label: 'Props' },
];

const FALLBACK_SUGGESTIONS: Record<PostRoundTone, PostRoundSuggestion[]> = {
  gloat: [
    { text: 'Too easy.', tone: 'gloat' },
    { text: 'Thanks for the chips!', tone: 'gloat' },
  ],
  humble: [
    { text: 'Got lucky there.', tone: 'humble' },
    { text: 'Good game.', tone: 'humble' },
  ],
  salty: [
    { text: 'Unreal.', tone: 'salty' },
    { text: 'Of course.', tone: 'salty' },
  ],
  gracious: [
    { text: 'Nice hand.', tone: 'gracious' },
    { text: 'Well played.', tone: 'gracious' },
  ],
  props: [
    { text: 'Respect. Well played.', tone: 'props' },
    { text: 'That was a sharp read.', tone: 'props' },
  ],
};

export const WinnerAnnouncement = memo(function WinnerAnnouncement({
  winnerInfo,
  onComplete,
  players,
  gameId,
  playerName,
  onSendMessage,
}: WinnerAnnouncementProps) {
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

  // Post-round chat state
  const showToneBar = !!playerName; // Only show tone bar when we know who's playing
  const [suggestions, setSuggestions] = useState<PostRoundSuggestion[]>([]);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const [messageSent, setMessageSent] = useState(false);
  const [isInteracting, setIsInteracting] = useState(false); // Pauses auto-dismiss

  const playerWon = winnerInfo?.winners.includes(playerName ?? '') ?? false;
  const toneOptions = playerWon ? WINNER_TONES : LOSER_TONES;

  const fetchSuggestions = useCallback(
    async (tone: PostRoundTone) => {
      if (!winnerInfo) return;

      if (!gameId || !playerName) {
        logger.warn('[PostRoundChat] Missing gameId or playerName, using fallback suggestions');
        setSuggestions(FALLBACK_SUGGESTIONS[tone]);
        return;
      }

      setLoadingSuggestions(true);
      try {
        const response = await gameAPI.getPostRoundChatSuggestions(gameId, playerName, tone);
        setSuggestions(response.suggestions || []);
      } catch (error) {
        logger.error('[PostRoundChat] Failed to fetch suggestions:', error);
        setSuggestions(FALLBACK_SUGGESTIONS[tone]);
      } finally {
        setLoadingSuggestions(false);
      }
    },
    [gameId, playerName, winnerInfo]
  );

  const handleToneSelect = useCallback(
    (tone: PostRoundTone) => {
      setIsInteracting(true); // Pause auto-dismiss while interacting
      setSuggestions([]);
      fetchSuggestions(tone);
    },
    [fetchSuggestions]
  );

  const handleSuggestionClick = useCallback(
    (text: string, tone: PostRoundTone) => {
      // Mirror mobile: loser reaction is directed at hand winner, winner broadcast is unaddressed
      const addressing =
        !playerWon && winnerInfo?.winners?.[0] ? [winnerInfo.winners[0]] : undefined;
      onSendMessage?.(text, addressing, tone);
      setMessageSent(true);
      setSuggestions([]);
      setIsInteracting(false); // Resume auto-dismiss possibility
    },
    [playerWon, winnerInfo, onSendMessage]
  );

  const handleBackToTones = useCallback(() => {
    setSuggestions([]);
    // Stay in interacting mode — player may pick another tone
  }, []);

  const handleContinue = useCallback(() => {
    setShow(false);
    setRevealCards(false);
    setTimeout(onComplete, 500);
  }, [onComplete]);

  useEffect(() => {
    if (winnerInfo) {
      setShow(true);
      setSuggestions([]);
      setMessageSent(false);
      setIsInteracting(false);

      if (winnerInfo.showdown && winnerInfo.players_showdown) {
        setTimeout(() => setRevealCards(true), INTERHAND_TIMING.showdownCardRevealMs);
      }

      return;
    }
  }, [winnerInfo]);

  // Separate effect for auto-dismiss: respects isInteracting and final hand
  useEffect(() => {
    // Don't auto-dismiss if interacting OR if it's the final hand
    if (!winnerInfo || isInteracting || winnerInfo.is_final_hand) return;

    const timer = setTimeout(
      () => {
        setShow(false);
        setRevealCards(false);
        setTimeout(onComplete, 500);
      },
      winnerInfo.showdown ? INTERHAND_TIMING.showdownResultMs : INTERHAND_TIMING.foldoutResultMs
    );

    return () => clearTimeout(timer);
  }, [winnerInfo, isInteracting, onComplete]);

  if (!winnerInfo || !show) return null;

  const winnersString =
    winnerInfo.winners.length > 1
      ? winnerInfo.winners.slice(0, -1).join(', ') +
        ' and ' +
        winnerInfo.winners[winnerInfo.winners.length - 1]
      : winnerInfo.winners[0];

  // Calculate winnings from pot_breakdown or legacy winnings field
  let grossWinnings = 0;
  let winnersContributions = 0;
  const perPlayerWinnings: Record<string, number> = {};

  if (winnerInfo.pot_breakdown) {
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

  if (winnerInfo.pot_contributions) {
    for (const winnerName of winnerInfo.winners) {
      winnersContributions += winnerInfo.pot_contributions[winnerName] || 0;
    }
  }

  const netProfit = grossWinnings - winnersContributions;
  const isSplitPot = winnerInfo.winners.length > 1;
  const hasSidePots = winnerInfo.pot_breakdown && winnerInfo.pot_breakdown.length > 1;

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

        {/* Side pots summary — only shown when multiple pots */}
        {hasSidePots && winnerInfo.pot_breakdown && (
          <div className="winner-side-pots">
            {winnerInfo.pot_breakdown.map((pot, index) => (
              <div key={index} className={`winner-side-pot-line pot-rank-${index}`}>
                <span className="winner-side-pot-name">{pot.pot_name}:</span>
                <span className="winner-side-pot-winners">
                  {pot.winners.map((w) => w.name).join(' & ')}
                </span>
                <span className="winner-side-pot-amount">${pot.total_amount}</span>
              </div>
            ))}
          </div>
        )}

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

        {/* Post-round tone chat bar — desktop variant (inline in the card, not a bottom sheet) */}
        {showToneBar && (
          <div className="winner-chat-bar">
            {messageSent ? (
              <div className="winner-chat-sent">
                <Check size={14} /> Sent
              </div>
            ) : loadingSuggestions ? (
              <div className="winner-chat-loading">
                <span className="loading-dots">Thinking</span>
              </div>
            ) : suggestions.length > 0 ? (
              <div className="winner-chat-suggestions">
                <button className="winner-chat-back" onClick={handleBackToTones}>
                  <ArrowLeft size={14} />
                  <span>Change tone</span>
                </button>
                {suggestions.map((suggestion, index) => (
                  <button
                    key={index}
                    className={`winner-chat-suggestion tone-${suggestion.tone}`}
                    onClick={() => handleSuggestionClick(suggestion.text, suggestion.tone)}
                  >
                    {suggestion.text}
                  </button>
                ))}
              </div>
            ) : (
              <div className="winner-chat-tones">
                {toneOptions.map((tone) => (
                  <button
                    key={tone.id}
                    className={`winner-chat-tone tone-${tone.id}`}
                    onClick={() => handleToneSelect(tone.id)}
                  >
                    <tone.icon className="tone-icon" size={16} />
                    <span className="tone-label">{tone.label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Continue button — always present; label changes on final hand */}
        <button className="continue-to-results-btn" onClick={handleContinue}>
          {winnerInfo.is_final_hand ? 'Continue to Results' : 'Continue'}
        </button>
      </div>
    </div>
  );
});
