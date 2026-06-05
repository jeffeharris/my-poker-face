/**
 * MobileActionArea — the always-visible bottom action strip. When it's the
 * human's turn it renders the full MobileActionButtons; otherwise it shows the
 * waiting state with the optional preemptive check/fold, fast-forward, and chat
 * affordances. Extracted verbatim from MobilePokerTable.
 */

import { Check, X, MessageCircle, FastForward } from 'lucide-react';
import type { Player } from '../../types/player';
import type { BettingContext } from '../../types/game';
import type { QueuedAction } from '../../hooks/usePokerGame';
import { MobileActionButtons } from './MobileActionButtons';
import { gameAPI } from '../../utils/api';
import { logger } from '../../utils/logger';

export function MobileActionArea({
  showActionButtons,
  currentPlayer,
  hasWinner,
  isShuffling,
  playerOptions,
  highestBet,
  minRaise,
  bigBlind,
  potTotal,
  onAction,
  onQuickChat,
  bettingContext,
  recommendedAction,
  raiseToAmount,
  humanPlayer,
  playerName,
  aiThinking,
  queuedAction,
  setQueuedAction,
  gameId,
  aiInstant,
  alwaysFastForward,
  fastForward,
}: {
  showActionButtons: boolean;
  currentPlayer: Player | undefined;
  hasWinner: boolean;
  isShuffling: boolean;
  playerOptions: string[];
  highestBet: number;
  minRaise: number;
  bigBlind: number;
  potTotal: number;
  onAction: (action: string, amount?: number) => void;
  onQuickChat: () => void;
  bettingContext: BettingContext | null;
  recommendedAction: string | null;
  raiseToAmount: number | null;
  humanPlayer: Player | undefined;
  playerName?: string;
  aiThinking: boolean;
  queuedAction: QueuedAction;
  setQueuedAction: (action: QueuedAction) => void;
  gameId: string | null;
  aiInstant: boolean;
  alwaysFastForward: boolean;
  fastForward: boolean;
}) {
  return (
    <div className="mobile-action-area">
      {showActionButtons && currentPlayer && !hasWinner && !isShuffling ? (
        <MobileActionButtons
          playerOptions={playerOptions}
          currentPlayerStack={currentPlayer.stack}
          highestBet={highestBet}
          currentPlayerBet={currentPlayer.bet}
          minRaise={minRaise}
          bigBlind={bigBlind}
          potSize={potTotal}
          onAction={onAction}
          onQuickChat={onQuickChat}
          bettingContext={bettingContext ?? undefined}
          recommendedAction={recommendedAction}
          raiseToAmount={raiseToAmount}
        />
      ) : (
        <div className="mobile-action-buttons">
          {/* Preemptive Check/Fold - shows when AI is thinking and it's this player's view */}
          {humanPlayer &&
            humanPlayer.name === playerName &&
            !humanPlayer.is_folded &&
            aiThinking &&
            currentPlayer &&
            !currentPlayer.is_human && (
              <button
                className={`action-btn preemptive-btn ${queuedAction === 'check_fold' ? 'queued' : ''}`}
                data-testid="action-btn-preemptive"
                onClick={() => setQueuedAction(queuedAction === 'check_fold' ? null : 'check_fold')}
              >
                <span className="action-icon">
                  {queuedAction === 'check_fold' ? (
                    <Check />
                  ) : (
                    <>
                      <Check />
                      <X />
                    </>
                  )}
                </span>
                <span className="btn-label">
                  {queuedAction === 'check_fold' ? 'Queued' : 'Chk/Fold'}
                </span>
              </button>
            )}
          <span className="waiting-text" data-testid="waiting-text">
            {aiThinking && currentPlayer && !currentPlayer.is_human
              ? `${currentPlayer.name} is thinking...`
              : aiThinking
                ? 'Submitting...'
                : 'Waiting...'}
          </span>
          {/* Fast-forward: any time someone else is acting — including
          while the human is folded (waiting out the hand is exactly
          when FF matters). The auto-reset fires when action returns
          to the human on the next hand's preflop. */}
          {gameId &&
            humanPlayer &&
            currentPlayer &&
            !currentPlayer.is_human &&
            !aiInstant &&
            !alwaysFastForward && (
              <button
                className={`action-btn ff-btn ${fastForward ? 'queued' : ''}`}
                data-testid="action-btn-ff"
                onClick={() => {
                  gameAPI.fastForward(gameId, !fastForward).catch((e) => {
                    logger.warn('[FF] toggle failed', e);
                  });
                }}
                title={
                  fastForward
                    ? 'Tap to return to normal speed'
                    : 'Skip AI deliberation — resolve to your next turn'
                }
              >
                <span className="action-icon">
                  <FastForward />
                </span>
                <span className="btn-label">{fastForward ? 'Stop' : 'FF'}</span>
              </button>
            )}
          <button
            className="action-btn chat-btn"
            data-testid="action-btn-chat"
            onClick={onQuickChat}
          >
            <span className="action-icon">
              <MessageCircle />
            </span>
            <span className="btn-label">Chat</span>
          </button>
        </div>
      )}
    </div>
  );
}
