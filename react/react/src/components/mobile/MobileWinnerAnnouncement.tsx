import { useEffect, useState } from "react";
import { Card } from "../cards";
import "./MobileWinnerAnnouncement.css";

interface WinnerInfo {
    winners: string[];
    winnings: { [key: string]: number };
    hand_name?: string;
    winning_hand?: string[];
    showdown: boolean;
    players_cards?: { [key: string]: string[] };
    community_cards?: string[];
}

interface CommentaryItem {
    player_name: string;
    comment: string;
    ttl: number;
    id: string;
    timestamp: number;
}

interface MobileWinnerAnnouncementProps {
    winnerInfo: WinnerInfo | null;
    commentary?: CommentaryItem[];
    onComplete: () => void;
}

export function MobileWinnerAnnouncement({
    winnerInfo,
    commentary = [],
    onComplete,
}: MobileWinnerAnnouncementProps) {
    const [showCards, setShowCards] = useState(false);
    const [visibleComments, setVisibleComments] = useState<CommentaryItem[]>([]);

    // Handle comment TTL expiration
    useEffect(() => {
        if (commentary.length === 0) return;

        // Add new comments to visible list
        setVisibleComments(prev => {
            const existingIds = new Set(prev.map(c => c.id));
            const newComments = commentary.filter(c => !existingIds.has(c.id));
            return [...prev, ...newComments];
        });

        // Set up timers to remove expired comments
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
            setVisibleComments([]); // Clear comments for new announcement

            // Show cards after a short delay for dramatic effect
            const cardTimer = setTimeout(() => {
                setShowCards(true);
            }, 800);

            // Auto-dismiss after showing (longer to allow commentary)
            const dismissTimer = setTimeout(
                () => {
                    setShowCards(false);
                    onComplete();
                },
                winnerInfo.showdown ? 10000 : 6000,
            );

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
                    <div className="winner-hand-name">
                        with {winnerInfo.hand_name}
                    </div>
                )}

                {winnerInfo.showdown && showCards && (
                    <div className="showdown-section">
                        {/* Community Cards */}
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

                        {/* Player Cards */}
                        {winnerInfo.players_cards && (
                            <div className="players-hands-section">
                                {Object.entries(winnerInfo.players_cards).map(
                                    ([playerName, cards]) => (
                                        <div
                                            key={playerName}
                                            className="player-showdown"
                                        >
                                            <div className="showdown-player-name">
                                                {playerName}
                                            </div>
                                            <div className="showdown-cards-row">
                                                {cards.map((card, i) => (
                                                    <Card
                                                        key={i}
                                                        card={card}
                                                        faceDown={false}
                                                        size="small"
                                                    />
                                                ))}
                                            </div>
                                        </div>
                                    ),
                                )}
                            </div>
                        )}
                    </div>
                )}

                {!winnerInfo.showdown && (
                    <div className="no-showdown-text">All opponents folded</div>
                )}

                <button className="dismiss-btn" onClick={onComplete}>
                    Continue
                </button>
            </div>
        </div>
    );
}
