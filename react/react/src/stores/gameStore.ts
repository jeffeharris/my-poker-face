import { create } from 'zustand';
import type { Player } from '../types/player';
import type { GameState, BettingContext } from '../types/game';

// Stable references to avoid creating new objects on every selectGameState call
const EMPTY_MESSAGES: never[] = [];
const ZERO_POT = { total: 0 };

interface GameStore {
  // Game state slices
  players: Player[] | null;
  phase: string;
  pot: { total: number } | null;
  communityCards: string[];
  currentPlayerIdx: number;
  dealerIdx: number;
  smallBlindIdx: number;
  bigBlindIdx: number;
  highestBet: number;
  playerOptions: string[];
  minRaise: number;
  bigBlind: number;
  smallBlind: number;
  handNumber: number;
  bettingContext: BettingContext | null;
  newlyDealtCount: number | undefined;
  awaitingAction: boolean | undefined;

  // Actions
  applyGameState: (state: GameState) => void;
  updatePlayers: (updater: (prev: Player[] | null) => Player[] | null) => void;
  updatePlayerOptions: (options: string[]) => void;
  reset: () => void;
}

const initialState = {
  players: null as Player[] | null,
  phase: '',
  pot: null as { total: number } | null,
  communityCards: [] as string[],
  currentPlayerIdx: 0,
  dealerIdx: 0,
  smallBlindIdx: 0,
  bigBlindIdx: 0,
  highestBet: 0,
  playerOptions: [] as string[],
  minRaise: 0,
  bigBlind: 0,
  smallBlind: 0,
  handNumber: 0,
  bettingContext: null as BettingContext | null,
  newlyDealtCount: undefined as number | undefined,
  awaitingAction: undefined as boolean | undefined,
};

export const useGameStore = create<GameStore>((set) => ({
  ...initialState,

  applyGameState: (state: GameState) => {
    set({
      players: state.players,
      phase: state.phase,
      pot: state.pot,
      communityCards: state.community_cards,
      currentPlayerIdx: state.current_player_idx,
      dealerIdx: state.current_dealer_idx,
      smallBlindIdx: state.small_blind_idx,
      bigBlindIdx: state.big_blind_idx,
      highestBet: state.highest_bet,
      playerOptions: state.player_options,
      minRaise: state.min_raise,
      bigBlind: state.big_blind,
      smallBlind: state.small_blind,
      handNumber: state.hand_number,
      bettingContext: state.betting_context ?? null,
      newlyDealtCount: state.newly_dealt_count,
      awaitingAction: state.awaiting_action,
    });
  },

  updatePlayers: (updater) => {
    set((state) => ({
      players: updater(state.players),
    }));
  },

  updatePlayerOptions: (options) => {
    set({ playerOptions: options });
  },

  reset: () => {
    set(initialState);
  },
}));

/**
 * Reconstruct a GameState object from the store for backward compatibility.
 * Used by desktop PokerTable and other consumers that expect the full object.
 */
export function selectGameState(state: GameStore): GameState | null {
  if (!state.players) return null;
  return {
    players: state.players,
    phase: state.phase,
    pot: state.pot ?? ZERO_POT,
    community_cards: state.communityCards,
    current_player_idx: state.currentPlayerIdx,
    current_dealer_idx: state.dealerIdx,
    small_blind_idx: state.smallBlindIdx,
    big_blind_idx: state.bigBlindIdx,
    highest_bet: state.highestBet,
    player_options: state.playerOptions,
    min_raise: state.minRaise,
    big_blind: state.bigBlind,
    small_blind: state.smallBlind,
    hand_number: state.handNumber,
    messages: EMPTY_MESSAGES,
    betting_context: state.bettingContext ?? undefined,
    newly_dealt_count: state.newlyDealtCount,
    awaiting_action: state.awaitingAction,
  };
}
