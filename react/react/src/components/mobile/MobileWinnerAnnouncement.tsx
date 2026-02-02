import { memo, useEffect, useState, useCallback } from "react";
import { PartyPopper, Smile, Angry, Handshake, ArrowLeft, Check, type LucideIcon } from "lucide-react";
import { Card } from "../cards";
import { gameAPI } from "../../utils/api";
import { logger } from "../../utils/logger";
import { getOrdinal, type BackendCard } from "../../types/tournament";
import type { PostRoundTone, PostRoundSuggestion } from "../../types/chat";
import "./MobileWinnerAnnouncement.css";

interface PlayerShowdownInfo {
    cards: string[] | BackendCard[];
    hand_name: string;
    hand_rank: number;
    kickers?: string[];
}

interface PotWinner {
    name: string;
    amount: number;
}

interface PotBreakdown {
    pot_name: string;
    total_amount: number;
    winners: PotWinner[];
    hand_name: string;
}

interface WinnerInfo {
    winners: string[];
    pot_breakdown?: PotBreakdown[];
    hand_name?: string;
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
    { id: 'gloat', icon: PartyPopper, label: 'Gloat' },
    { id: 'humble', icon: Smile, label: 'Humble' },
];

// Tone options for losers
const LOSER_TONES: ToneOption[] = [
    { id: 'salty', icon: Angry, label: 'Salty' },
    { id: 'gracious', icon: Handshake, label: 'Gracious' },
];

export const MobileWinnerAnnouncement = memo(function MobileWinnerAnnouncement({
    winnerInfo,
    onComplete,
    gameId,
    playerName,
    onSendMessage,
}: MobileWinnerAnnouncementProps) {
    const [showCards, setShowCards] = useState(false);

    // Post-round chat state
    const [suggestions, setSuggestions] = useState<PostRoundSuggestion[]>([]);
    const [loading, setLoading] = useState(false);
    const [messageSent, setMessageSent] = useState(false);
    const [isInteracting, setIsInteracting] = useState(false); // Pauses auto-dismiss

    // Determine if human player won
    const playerWon = winnerInfo?.winners.includes(playerName) ?? false;
    const toneOptions = playerWon ? WINNER_TONES : LOSER_TONES;

    const fetchSuggestions = useCallback(async (tone: PostRoundTone) => {
        if (!winnerInfo) return;

        // Skip API call if required params are missing
        if (!gameId || !playerName) {
            logger.warn('[PostRoundChat] Missing gameId or playerName, using fallback suggestions');
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
            // Backend now derives all context from RecordedHand - just send playerName and tone
            const response = await gameAPI.getPostRoundChatSuggestions(
                gameId,
                playerName,
                tone
            );
            setSuggestions(response.suggestions || []);
        } catch (error) {
            logger.error('[PostRoundChat] Failed to fetch suggestions:', error);
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
    }, [gameId, playerName, winnerInfo]);

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

    useEffect(() => {
        if (winnerInfo) {
            setShowCards(false);
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

    // Compute per-player total winnings from pot_breakdown
    const playerWinnings: { [name: string]: number } = {};
    if (winnerInfo.pot_breakdown) {
        for (const pot of winnerInfo.pot_breakdown) {
            for (const winner of pot.winners) {
                playerWinnings[winner.name] = (playerWinnings[winner.name] || 0) + winner.amount;
            }
        }
    }

    const totalWinnings = Object.values(playerWinnings).reduce((sum, val) => sum + val, 0);
    const hasSidePots = winnerInfo.pot_breakdown && winnerInfo.pot_breakdown.length > 1;

    return (
        <div className="mobile-winner-overlay" data-testid="winner-overlay">
            <div className="mobile-winner-content">
                {/* Tournament Outcome Banner - only shown on final hand */}
                {winnerInfo.is_final_hand && winnerInfo.tournament_outcome && (
                    <div className={`mobile-tournament-outcome-banner ${winnerInfo.tournament_outcome.human_won ? 'victory' : 'defeat'}`}>
                        {winnerInfo.tournament_outcome.human_won
                            ? 'CHAMPION!'
                            : `Finished ${getOrdinal(winnerInfo.tournament_outcome.human_position)}`}
                    </div>
                )}

                {/* Winner Announcement Header */}
                {winnerInfo.showdown && (
                    <div className="winner-header">
                        <div className="winner-names" data-testid="winner-names">
                            {winnerInfo.winners.length > 1
                                ? `${winnerInfo.winners.join(' & ')} split`
                                : `${winnerInfo.winners[0]} wins`}
                        </div>
                        <div className="winner-amount" data-testid="winner-amount">${totalWinnings}</div>
                        {winnerInfo.hand_name && !hasSidePots && (
                            <div className="winner-hand-name" data-testid="winner-hand-name">{winnerInfo.hand_name}</div>
                        )}
                    </div>
                )}

                {/* Side pots summary - only shown when multiple pots */}
                {hasSidePots && winnerInfo.pot_breakdown && (
                    <div className="side-pots-summary">
                        {winnerInfo.pot_breakdown.map((pot, index) => (
                            <div key={index} className={`side-pot-line pot-rank-${index}`}>
                                <span className="side-pot-name">{pot.pot_name}:</span>
                                <span className="side-pot-winners">
                                    {pot.winners.map(w => w.name).join(' & ')}
                                </span>
                                <span className="side-pot-amount">${pot.total_amount}</span>
                            </div>
                        ))}
                    </div>
                )}

                {winnerInfo.showdown && showCards && (
                    <div className="showdown-section" data-testid="showdown-section">
                        {winnerInfo.community_cards &&
                            winnerInfo.community_cards.length > 0 && (
                                <div className="community-section" data-testid="community-section">
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
                                        const winAmount = playerWinnings[showdownPlayerName];
                                        return (
                                            <div
                                                key={showdownPlayerName}
                                                className={`player-showdown ${isWinner ? 'winner' : ''}`}
                                            data-testid="player-showdown"
                                            >
                                                <div className="showdown-player-info">
                                                    <div className="showdown-player-header">
                                                        <span className="showdown-player-name">
                                                            {showdownPlayerName}
                                                        </span>
                                                        {winAmount > 0 && (
                                                            <span className="showdown-player-winnings">+${winAmount}</span>
                                                        )}
                                                    </div>
                                                    {playerInfo.hand_name && (
                                                        <div className="showdown-hand-name">
                                                            {playerInfo.hand_name}
                                                            {playerInfo.kickers && playerInfo.kickers.length > 0 && (
                                                                <span className="showdown-kickers"> (kicker: {playerInfo.kickers.join(', ')})</span>
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
                                                            size="large"
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
                    <div className="no-showdown-winner" data-testid="no-showdown-winner">
                        <div className="no-showdown-name" data-testid="no-showdown-name">{winnerInfo.winners[0]}</div>
                        <div className="no-showdown-amount" data-testid="no-showdown-amount">Wins ${totalWinnings}</div>
                        <div className="no-showdown-text" data-testid="no-showdown-text">All opponents folded</div>
                    </div>
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
                                    <ArrowLeft size={14} /> Back
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
                                        <tone.icon className="tone-icon" size={18} />
                                        <span className="tone-label">{tone.label}</span>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {messageSent && (
                    <div className="post-round-sent">
                        <Check size={14} /> Sent
                    </div>
                )}

                <button className="dismiss-btn" data-testid="winner-dismiss" onClick={onComplete}>
                    {winnerInfo.is_final_hand ? 'Continue to Results' : 'Continue'}
                </button>
            </div>
        </div>
    );
});
