import { create } from 'zustand';
import type { Player } from '../types/player';
import type { GameState, BettingContext, CashModeInfo } from '../types/game';
import type { LobbyEvent } from '../components/cash/types';
import type { RunoutSchedule } from '../types/runout';

// Stable references to avoid creating new objects on every selectGameState call
const EMPTY_MESSAGES: never[] = [];
const ZERO_POT = { total: 0 };

/** A world-ticker event tagged with the hand it arrived during, so the
 *  interhand screen can scope to "what happened in the world since this
 *  hand started" (events whose `hand` matches the hand that just ended). */
export interface BufferedWorldEvent {
  event: LobbyEvent;
  hand: number;
}

// Only the current (and immediately prior) hand's events are ever read, so
// the buffer stays tiny; this cap is a hard backstop against unbounded growth.
const WORLD_EVENT_BUFFER_CAP = 60;

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
  cashMode: CashModeInfo | null;
  fastForward: boolean;
  // Cash/career mode: realtime world-ticker events buffered while at the
  // table, each tagged with the hand it arrived during. Feeds the interhand
  // "meanwhile, elsewhere" ticker. Empty in tournament mode.
  worldEvents: BufferedWorldEvent[];
  /** Every AI seat resolves with zero LLM calls → nothing to fast-forward. */
  aiInstant: boolean;
  /** Owner's game speed is 'always' (fast-forward every turn) → FF button hidden. */
  alwaysFastForward: boolean;
  // Run-out reveal director (mobile, all-in run-outs). The backend emits the
  // per-card reaction schedule once at reveal; `useRunoutDirector` walks it to
  // play per-card avatar reactions on a client-owned beat. `runoutDirectorActive`
  // marks that the director owns reactions right now — the socket layer drops
  // the backend's street-level `is_reaction` avatar updates while it's true so
  // they don't clobber the finer per-card faces (desktop, which has no director,
  // leaves it false and keeps the backend reactions).
  runoutSchedule: RunoutSchedule | null;
  runoutDirectorActive: boolean;
  // Optimistic-action rollback snapshot. When the human commits a chip action
  // we move chips to the pot immediately (before the server confirms) for
  // responsiveness; this holds the pre-action slices so we can revert if the
  // action is rejected. Cleared as soon as authoritative state arrives.
  optimisticSnapshot: OptimisticSnapshot | null;

  // Actions
  applyGameState: (state: GameState) => void;
  updatePlayers: (updater: (prev: Player[] | null) => Player[] | null) => void;
  updatePlayerOptions: (options: string[]) => void;
  pushWorldEvent: (event: LobbyEvent) => void;
  setRunoutSchedule: (schedule: RunoutSchedule | null) => void;
  setRunoutDirectorActive: (active: boolean) => void;
  /** Optimistically move chips to the pot for the acting player's commit. */
  applyOptimisticAction: (action: string, amount: number | undefined) => void;
  /** Revert the last optimistic action (used when the server rejects it). */
  rollbackOptimisticAction: () => void;
  reset: () => void;
}

/** Pre-action snapshot of the chip-bearing slices, for optimistic rollback. */
interface OptimisticSnapshot {
  players: Player[] | null;
  pot: { total: number } | null;
  highestBet: number;
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
  cashMode: null as CashModeInfo | null,
  fastForward: false,
  worldEvents: [] as BufferedWorldEvent[],
  aiInstant: false,
  alwaysFastForward: false,
  runoutSchedule: null as RunoutSchedule | null,
  runoutDirectorActive: false,
  optimisticSnapshot: null as OptimisticSnapshot | null,
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
        const directing = prev.runoutDirectorActive;
        players = state.players.map((incoming) => {
          const existing = prev.players!.find((p) => p.name === incoming.name);
          // While the run-out director owns faces, keep the director-set
          // emotion/avatar even as fresh game state arrives. A full state push
          // carries the backend's display emotion (a street-level override, or
          // the baseline once overrides clear at hand end) — applying it would
          // clobber the per-card reaction, making the face flicker back a beat
          // after it changed. Suppressing the avatar_update socket channel isn't
          // enough; this is the full-push seam (RUNOUT_REVEAL_DIRECTOR.md §C.1).
          const candidate =
            directing && existing
              ? {
                  ...incoming,
                  avatar_emotion: existing.avatar_emotion,
                  avatar_url: existing.avatar_url,
                }
              : incoming;
          return existing && arePlayersEqual(existing, candidate) ? existing : candidate;
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
        cashMode: state.cash_mode ?? null,
        fastForward: state.fast_forward ?? false,
        aiInstant: state.ai_instant ?? false,
        alwaysFastForward: state.always_fast_forward ?? false,
        // Authoritative state supersedes any optimistic guess — drop the
        // rollback snapshot so a later, unrelated action can't revert to it.
        optimisticSnapshot: null,
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

  setRunoutSchedule: (schedule) => {
    set({ runoutSchedule: schedule });
  },

  setRunoutDirectorActive: (active) => {
    set({ runoutDirectorActive: active });
  },

  applyOptimisticAction: (action, amount) => {
    set((prev) => {
      if (!prev.players) return {};
      const idx = prev.currentPlayerIdx;
      const player = prev.players[idx];
      if (!player) return {};

      // Chips this commit moves to the pot. Mirrors the backend's place_bet:
      // stack↓, bet↑ and pot.total↑ all move by the same delta (pot.total
      // already includes current-street bets). check/fold move nothing.
      let delta = 0;
      if (action === 'call') {
        delta = prev.highestBet - player.bet;
      } else if (action === 'raise' || action === 'bet' || action === 'all_in') {
        // amount is a "raise TO" total bet; the delta is the top-up from the
        // player's current bet. all_in floors to the whole stack via the clamp.
        delta = (amount ?? 0) - player.bet;
      }
      delta = Math.min(Math.max(0, delta), player.stack);
      if (delta <= 0) return {}; // nothing to move — no visual change, no snapshot

      // Snapshot once per pending action so a rollback restores the true
      // pre-action state even if applyOptimisticAction were called twice.
      const snapshot: OptimisticSnapshot = prev.optimisticSnapshot ?? {
        players: prev.players,
        pot: prev.pot,
        highestBet: prev.highestBet,
      };

      const newBet = player.bet + delta;
      const newStack = player.stack - delta;
      const players = prev.players.map((p, i) =>
        i === idx
          ? {
              ...p,
              stack: newStack,
              bet: newBet,
              is_all_in: p.is_all_in || newStack === 0,
              last_action: action,
            }
          : p
      );

      return {
        players,
        pot: { ...(prev.pot ?? { total: 0 }), total: (prev.pot?.total ?? 0) + delta },
        highestBet: Math.max(prev.highestBet, newBet),
        optimisticSnapshot: snapshot,
      };
    });
  },

  rollbackOptimisticAction: () => {
    set((prev) => {
      if (!prev.optimisticSnapshot) return {};
      return {
        players: prev.optimisticSnapshot.players,
        pot: prev.optimisticSnapshot.pot,
        highestBet: prev.optimisticSnapshot.highestBet,
        optimisticSnapshot: null,
      };
    });
  },

  pushWorldEvent: (event: LobbyEvent) => {
    set((state) => {
      // Tag with the hand in progress so the interhand digest can scope to
      // "since this hand started". Drop anything older than the prior hand
      // and cap length so the buffer can never grow without bound.
      const minHand = state.handNumber - 1;
      const next = [...state.worldEvents, { event, hand: state.handNumber }]
        .filter((w) => w.hand >= minHand)
        .slice(-WORLD_EVENT_BUFFER_CAP);
      return { worldEvents: next };
    });
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
    cash_mode: state.cashMode ?? undefined,
    fast_forward: state.fastForward,
  };
}
