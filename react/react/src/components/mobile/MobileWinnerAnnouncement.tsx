import { useEffect, useState, useCallback } from "react";
import { Card } from "../cards";
import { gameAPI } from "../../utils/api";
import { getOrdinal } from "../../types/tournament";
import type { PostRoundTone, PostRoundSuggestion } from "../../types/chat";
import "./MobileWinnerAnnouncement.css";

interface PlayerShowdownInfo {
    cards: string[];
    hand_name: string;
    hand_rank: number;
    kickers?: string[];
}

interface WinnerInfo {
    winners: string[];
    winnings: { [key: string]: number };
    hand_name?: string;
    winning_hand?: string[];
    showdown: boolean;
    players_showdown?: { [key: string]: PlayerShowdownInfo };
    community_cards?: string[];
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
    emoji: string;
    label: string;
}

interface MobileWinnerAnnouncementProps {
    winnerInfo: WinnerInfo | null;
    commentary?: CommentaryItem[];
    onComplete: () => void;
    gameId: string;
    playerName: string;
    onSendMessage: (text: string) => void;
}

// Tone options for winners
const WINNER_TONES: ToneOption[] = [
    { id: 'gloat', emoji: '🎉', label: 'Gloat' },
    { id: 'humble', emoji: '😇', label: 'Humble' },
];

// Tone options for losers
const LOSER_TONES: ToneOption[] = [
    { id: 'salty', emoji: '😤', label: 'Salty' },
    { id: 'gracious', emoji: '🤝', label: 'Gracious' },
];

export function MobileWinnerAnnouncement({
    winnerInfo,
    commentary = [],
    onComplete,
    gameId,
    playerName,
    onSendMessage,
}: MobileWinnerAnnouncementProps) {
    const [showCards, setShowCards] = useState(false);
    const [, setVisibleComments] = useState<CommentaryItem[]>([]);

    // Post-round chat state
    const [suggestions, setSuggestions] = useState<PostRoundSuggestion[]>([]);
    const [loading, setLoading] = useState(false);
    const [messageSent, setMessageSent] = useState(false);
    const [isInteracting, setIsInteracting] = useState(false); // Pauses auto-dismiss

    // Determine if human player won
    const playerWon = winnerInfo?.winners.includes(playerName) ?? false;
    const toneOptions = playerWon ? WINNER_TONES : LOSER_TONES;

    // Get opponent info for context
    const getOpponent = useCallback(() => {
        if (!winnerInfo) return undefined;
        if (playerWon) {
            // If we won, opponent is whoever lost (first non-winner in showdown)
            if (winnerInfo.players_showdown) {
                const losers = Object.keys(winnerInfo.players_showdown).filter(
                    name => !winnerInfo.winners.includes(name)
                );
                return losers[0];
            }
            return undefined;
        } else {
            // If we lost, opponent is the winner
            return winnerInfo.winners[0];
        }
    }, [winnerInfo, playerWon]);

    const fetchSuggestions = useCallback(async (tone: PostRoundTone) => {
        if (!winnerInfo) return;

        // Skip API call if required params are missing
        if (!gameId || !playerName) {
            console.warn('[PostRoundChat] Missing gameId or playerName, using fallback suggestions');
            const fallbacks: Record<PostRoundTone, PostRoundSuggestion[]> = {
                gloat: [{ text: 'Too easy.', tone: 'gloat' }, { text: 'Thanks for the chips!', tone: 'gloat' }],
                humble: [{ text: 'Got lucky there.', tone: 'humble' }, { text: 'Good game.', tone: 'humble' }],
                salty: [{ text: 'Unreal.', tone: 'salty' }, { text: 'Of course.', tone: 'salty' }],
                gracious: [{ text: 'Nice hand.', tone: 'gracious' }, { text: 'Well played.', tone: 'gracious' }],
            };
            setSuggestions(fallbacks[tone]);
            return;
        }

        setLoading(true);
        try {
            const opponent = getOpponent();

            // Build showdown context for accurate commentary
            let showdownContext: {
                communityCards?: string[];
                winnerHand?: { cards: string[]; handName: string };
                loserHand?: { cards: string[]; handName: string };
            } | undefined;

            if (winnerInfo.showdown && winnerInfo.players_showdown) {
                const winnerName = winnerInfo.winners[0];
                const winnerShowdown = winnerInfo.players_showdown[winnerName];
                const loserShowdown = opponent ? winnerInfo.players_showdown[opponent] : undefined;

                showdownContext = {
                    communityCards: winnerInfo.community_cards,
                    winnerHand: winnerShowdown ? {
                        cards: winnerShowdown.cards,
                        handName: winnerShowdown.hand_name,
                    } : undefined,
                    loserHand: loserShowdown ? {
                        cards: loserShowdown.cards,
                        handName: loserShowdown.hand_name,
                    } : undefined,
                };
            }

            const response = await gameAPI.getPostRoundChatSuggestions(
                gameId,
                playerName,
                tone,
                playerWon,
                winnerInfo.hand_name,
                opponent,
                showdownContext
            );
            setSuggestions(response.suggestions || []);
        } catch (error) {
            console.error('[PostRoundChat] Failed to fetch suggestions:', error);
            // Set fallback suggestions
            const fallbacks: Record<PostRoundTone, PostRoundSuggestion[]> = {
                gloat: [{ text: 'Too easy.', tone: 'gloat' }, { text: 'Thanks for the chips!', tone: 'gloat' }],
                humble: [{ text: 'Got lucky there.', tone: 'humble' }, { text: 'Good game.', tone: 'humble' }],
                salty: [{ text: 'Unreal.', tone: 'salty' }, { text: 'Of course.', tone: 'salty' }],
                gracious: [{ text: 'Nice hand.', tone: 'gracious' }, { text: 'Well played.', tone: 'gracious' }],
            };
            setSuggestions(fallbacks[tone]);
        } finally {
            setLoading(false);
        }
    }, [gameId, playerName, playerWon, winnerInfo, getOpponent]);

    const handleToneSelect = (tone: PostRoundTone) => {
        setIsInteracting(true); // Pause auto-dismiss
        setSuggestions([]);
        fetchSuggestions(tone);
    };

    const handleSuggestionClick = (text: string) => {
        onSendMessage(text);
        setMessageSent(true);
        setSuggestions([]);
        setIsInteracting(false); // Resume auto-dismiss possibility
    };

    const handleBackToTones = () => {
        setSuggestions([]);
        // Stay in interacting mode - they might pick another tone
    };

    // Handle comment TTL expiration
    useEffect(() => {
        if (commentary.length === 0) return;

        setVisibleComments(prev => {
            const existingIds = new Set(prev.map(c => c.id));
            const newComments = commentary.filter(c => !existingIds.has(c.id));
            return [...prev, ...newComments];
        });

        const timers = commentary.map(comment => {
            const elapsed = Date.now() - comment.timestamp;
            const remaining = Math.max(0, comment.ttl - elapsed);

            return setTimeout(() => {
                setVisibleComments(prev => prev.filter(c => c.id !== comment.id));
            }, remaining);
        });

        return () => timers.forEach(t => clearTimeout(t));
    }, [commentary]);

    useEffect(() => {
        if (winnerInfo) {
            setShowCards(false);
            setVisibleComments([]);
            setSuggestions([]);
            setMessageSent(false);
            setIsInteracting(false);

            const cardTimer = setTimeout(() => {
                setShowCards(true);
            }, 800);

            return () => {
                clearTimeout(cardTimer);
            };
        }
    }, [winnerInfo]);

    // Separate effect for auto-dismiss that respects isInteracting and final hand
    useEffect(() => {
        // Don't auto-dismiss if interacting OR if it's the final hand
        if (!winnerInfo || isInteracting || winnerInfo.is_final_hand) return;

        const dismissTimer = setTimeout(
            () => {
                setShowCards(false);
                onComplete();
            },
            winnerInfo.showdown ? 12000 : 8000,
        );

        return () => {
            clearTimeout(dismissTimer);
        };
    }, [winnerInfo, isInteracting, onComplete]);

    if (!winnerInfo) return null;

    const winnersString = winnerInfo.winners.length > 1
        ? winnerInfo.winners.slice(0, -1).join(', ') + ' & ' + winnerInfo.winners[winnerInfo.winners.length - 1]
        : winnerInfo.winners[0];

    const totalWinnings = Object.values(winnerInfo.winnings).reduce((sum, val) => sum + val, 0);
    const isSplitPot = winnerInfo.winners.length > 1;

    return (
        <div className="mobile-winner-overlay">
            <div className="mobile-winner-content">
                <div className="winner-trophy">🏆</div>
                <div className="winner-name">{winnersString}</div>
                <div className="winner-amount">
                    {isSplitPot ? `Split Pot - $${totalWinnings}` : `Wins $${totalWinnings}`}
                </div>

                {winnerInfo.hand_name && (
                    <div className="winner-hand-name">
                        with {winnerInfo.hand_name}
                    </div>
                )}

                {/* Tournament Outcome Banner - only shown on final hand */}
                {winnerInfo.is_final_hand && winnerInfo.tournament_outcome && (
                    <div className={`mobile-tournament-outcome-banner ${winnerInfo.tournament_outcome.human_won ? 'victory' : 'defeat'}`}>
                        {winnerInfo.tournament_outcome.human_won
                            ? 'CHAMPION!'
                            : `Finished ${getOrdinal(winnerInfo.tournament_outcome.human_position)}`}
                    </div>
                )}

                {winnerInfo.showdown && showCards && (
                    <div className="showdown-section">
                        {winnerInfo.community_cards &&
                            winnerInfo.community_cards.length > 0 && (
                                <div className="community-section">
                                    <div className="section-label">Board</div>
                                    <div className="community-cards-row">
                                        {winnerInfo.community_cards.map(
                                            (card, i) => (
                                                <Card
                                                    key={i}
                                                    card={card}
                                                    faceDown={false}
                                                    size="small"
                                                />
                                            ),
                                        )}
                                    </div>
                                </div>
                            )}

                        {winnerInfo.players_showdown && (
                            <div className="players-hands-section">
                                {Object.entries(winnerInfo.players_showdown)
                                    .sort(([, infoA], [, infoB]) => infoA.hand_rank - infoB.hand_rank)
                                    .map(([showdownPlayerName, playerInfo]) => {
                                        const isWinner = winnerInfo.winners.includes(showdownPlayerName);
                                        const hasKickers = playerInfo.kickers && playerInfo.kickers.length > 0;
                                        return (
                                            <div
                                                key={showdownPlayerName}
                                                className={`player-showdown ${isWinner ? 'winner' : ''}`}
                                            >
                                                <div className="showdown-player-info">
                                                    <div className="showdown-player-name">
                                                        {showdownPlayerName}
                                                    </div>
                                                    {playerInfo.hand_name && (
                                                        <div className="showdown-hand-name">
                                                            {playerInfo.hand_name}
                                                            {hasKickers && (
                                                                <span className="showdown-kickers"> ({playerInfo.kickers!.join(', ')})</span>
                                                            )}
                                                        </div>
                                                    )}
                                                </div>
                                                <div className="showdown-cards-row">
                                                    {playerInfo.cards.map((card, i) => (
                                                        <Card
                                                            key={i}
                                                            card={card}
                                                            faceDown={false}
                                                            size="small"
                                                        />
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
                    <div className="no-showdown-text">All opponents folded</div>
                )}

                {/* Post-round quick chat section */}
                {!messageSent && (
                    <div className="post-round-chat">
                        {loading ? (
                            <div className="post-round-loading">
                                <span className="loading-dots">Thinking</span>
                            </div>
                        ) : suggestions.length > 0 ? (
                            <div className="post-round-suggestions">
                                {suggestions.map((suggestion, index) => (
                                    <button
                                        key={index}
                                        className={`post-round-suggestion tone-${suggestion.tone}`}
                                        onClick={() => handleSuggestionClick(suggestion.text)}
                                    >
                                        {suggestion.text}
                                    </button>
                                ))}
                                <button
                                    className="post-round-back"
                                    onClick={handleBackToTones}
                                >
                                    ← Back
                                </button>
                            </div>
                        ) : (
                            <div className="post-round-tones">
                                {toneOptions.map((tone) => (
                                    <button
                                        key={tone.id}
                                        className={`post-round-tone tone-${tone.id}`}
                                        onClick={() => handleToneSelect(tone.id)}
                                    >
                                        <span className="tone-emoji">{tone.emoji}</span>
                                        <span className="tone-label">{tone.label}</span>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {messageSent && (
                    <div className="post-round-sent">
                        ✓ Sent
                    </div>
                )}

                <button className="dismiss-btn" onClick={onComplete}>
                    {winnerInfo.is_final_hand ? 'Continue to Results' : 'Continue'}
                </button>
            </div>
        </div>
    );
}
