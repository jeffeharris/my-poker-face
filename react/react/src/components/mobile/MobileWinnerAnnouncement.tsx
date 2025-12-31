import { useEffect, useState } from "react";
import { Card } from "../cards";
import "./MobileWinnerAnnouncement.css";

interface PlayerShowdownInfo {
    cards: any[];
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

                        {/* Player Cards - sorted by hand rank (best first) */}
                        {winnerInfo.players_showdown && (
                            <div className="players-hands-section">
                                {Object.entries(winnerInfo.players_showdown)
                                    .sort(([, infoA], [, infoB]) => infoA.hand_rank - infoB.hand_rank)
                                    .map(([playerName, playerInfo]) => {
                                        const isWinner = winnerInfo.winners.includes(playerName);
                                        const hasKickers = playerInfo.kickers && playerInfo.kickers.length > 0;
                                        return (
                                            <div
                                                key={playerName}
                                                className={`player-showdown ${isWinner ? 'winner' : ''}`}
                                            >
                                                <div className="showdown-player-info">
                                                    <div className="showdown-player-name">
                                                        {playerName}
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

                <button className="dismiss-btn" onClick={onComplete}>
                    Continue
                </button>
            </div>
        </div>
    );
}
