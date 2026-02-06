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
  runItOut: boolean | undefined;

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
  runItOut: undefined as boolean | undefined,
};

/** Compare two Player objects field-by-field, including nested objects. */
function arePlayersEqual(a: Player, b: Player): boolean {
  if (a === b) return true;

  // Primitive fields
  if (
    a.name !== b.name ||
    a.nickname !== b.nickname ||
    a.stack !== b.stack ||
    a.bet !== b.bet ||
    a.is_folded !== b.is_folded ||
    a.is_all_in !== b.is_all_in ||
    a.is_human !== b.is_human ||
    a.avatar_url !== b.avatar_url ||
    a.avatar_emotion !== b.avatar_emotion ||
    a.last_action !== b.last_action
  ) {
    return false;
  }

  // Hand array
  if (a.hand !== b.hand) {
    if (!a.hand || !b.hand || a.hand.length !== b.hand.length) return false;
    for (let i = 0; i < a.hand.length; i++) {
      if (a.hand[i].rank !== b.hand[i].rank || a.hand[i].suit !== b.hand[i].suit) return false;
    }
  }

  // Psychology
  if (a.psychology !== b.psychology) {
    if (!a.psychology || !b.psychology) return false;
    if (
      a.psychology.narrative !== b.psychology.narrative ||
      a.psychology.inner_voice !== b.psychology.inner_voice ||
      a.psychology.tilt_level !== b.psychology.tilt_level ||
      a.psychology.tilt_category !== b.psychology.tilt_category ||
      a.psychology.tilt_source !== b.psychology.tilt_source ||
      a.psychology.losing_streak !== b.psychology.losing_streak
    ) {
      return false;
    }
  }

  // LLM debug
  if (a.llm_debug !== b.llm_debug) {
    if (!a.llm_debug || !b.llm_debug) return false;
    if (
      a.llm_debug.provider !== b.llm_debug.provider ||
      a.llm_debug.model !== b.llm_debug.model ||
      a.llm_debug.reasoning_effort !== b.llm_debug.reasoning_effort ||
      a.llm_debug.total_calls !== b.llm_debug.total_calls ||
      a.llm_debug.avg_latency_ms !== b.llm_debug.avg_latency_ms ||
      a.llm_debug.avg_cost_per_call !== b.llm_debug.avg_cost_per_call
    ) {
      return false;
    }
  }

  return true;
}

export const useGameStore = create<GameStore>((set) => ({
  ...initialState,

  applyGameState: (state: GameState) => {
    set((prev) => {
      // Structural sharing: reuse Player references when data hasn't changed
      let players = state.players;
      if (prev.players && state.players) {
        players = state.players.map(incoming => {
          const existing = prev.players!.find(p => p.name === incoming.name);
          return existing && arePlayersEqual(existing, incoming) ? existing : incoming;
        });
      }

      return {
        players,
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
        runItOut: state.run_it_out,
      };
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
    run_it_out: state.runItOut,
  };
}
