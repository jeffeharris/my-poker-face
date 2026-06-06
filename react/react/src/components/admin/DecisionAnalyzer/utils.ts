import type { PromptCapture, CaptureFilters } from './types';

// Normalize a card string to the canonical engine format ("Ts", "Qh", "5d",
// "Ac"). Captures stored prior to the unified format use mixed conventions —
// LLM-bot captures persist e.g. "10♠" (decimal-T + Unicode suit), while the
// strategy pipeline persists "Ts" (single-char rank + letter suit). This
// helper makes the list/detail views render both the same way without
// mutating stored data.
const SUIT_UNICODE_TO_LETTER: Record<string, string> = {
  '♠': 's',
  '♥': 'h',
  '♦': 'd',
  '♣': 'c',
};

export function formatCardCanonical(card: string): string {
  if (!card) return card;
  let s = card;
  // "10" → "T" (only at the start, so "10s" → "Ts" but "Q10" — not a card — is left alone)
  if (s.startsWith('10')) s = 'T' + s.slice(2);
  for (const [u, letter] of Object.entries(SUIT_UNICODE_TO_LETTER)) {
    s = s.replace(u, letter);
  }
  return s;
}

export function formatCardsCanonical(cards: string[] | null | undefined): string {
  if (!cards || cards.length === 0) return '';
  return cards.map(formatCardCanonical).join(' ');
}

// Safe JSON parser for stored fields rendered inside JSX. Bare JSON.parse()
// in a render path crashes the entire panel to a white screen on malformed
// or legacy data — this returns a fallback instead.
export function safeJsonParse<T>(s: string | null | undefined, fallback: T): T {
  if (!s) return fallback;
  try {
    return JSON.parse(s) as T;
  } catch {
    return fallback;
  }
}

// Map the engine's full position name to a poker-table abbreviation. Falls
// back to the raw string for unknown values so we don't silently hide data.
const POSITION_ABBREVIATIONS: Record<string, string> = {
  small_blind_player: 'SB',
  big_blind_player: 'BB',
  button: 'BTN',
  under_the_gun: 'UTG',
  cutoff: 'CO',
  middle: 'MP',
  early: 'EP',
  late: 'LP',
};

export function formatPosition(pos: string | null | undefined): string {
  if (!pos) return '';
  return POSITION_ABBREVIATIONS[pos] ?? pos;
}

// Pair opponent positions with their names. Both lists are written together
// by the analyzer (same `opponents_in_hand` iteration), so positions[i]
// belongs to the i-th key in the ranges dict — relying on JS Map / dict
// insertion order. If ranges is missing or shorter, we fall back to
// position-only entries so the panel still renders something useful.
export function pairOpponentSeats(
  positionsJson: string | null | undefined,
  rangesJson: string | null | undefined
): Array<{ position: string; name: string | null }> {
  const positions = safeJsonParse<string[]>(positionsJson, []);
  const ranges = safeJsonParse<Record<string, unknown>>(rangesJson, {});
  const names = Object.keys(ranges);
  return positions.map((position, i) => ({
    position,
    name: i < names.length ? names[i] : null,
  }));
}

export function formatPotOdds(potOdds: number | null): string {
  if (potOdds === null) return '-';
  return `${potOdds.toFixed(1)}:1`;
}

export function getActionColor(action: string | null): string {
  switch (action) {
    case 'fold':
      return 'action-fold';
    case 'check':
      return 'action-check';
    case 'call':
      return 'action-call';
    case 'raise':
      return 'action-raise';
    case 'all_in':
      return 'action-allin';
    default:
      return '';
  }
}

export function isSuspiciousFold(capture: PromptCapture): boolean {
  return capture.action_taken === 'fold' && capture.pot_odds !== null && capture.pot_odds >= 5;
}

// Format label name for display
export function formatLabelName(label: string): string {
  return label.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// Get severity class for label
export function getLabelSeverity(label: string): 'high' | 'medium' | 'low' {
  const highSeverity = ['fold_mistake', 'high_ev_loss', 'short_stack_fold', 'pot_committed_fold'];
  const mediumSeverity = ['bad_all_in', 'suspicious_fold'];
  if (highSeverity.includes(label)) return 'high';
  if (mediumSeverity.includes(label)) return 'medium';
  return 'low';
}

// Format emotion name for display
export function formatEmotionName(emotion: string): string {
  return emotion.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// Get tilt bar color class
export function getTiltBarClass(tiltLevel: number): string {
  if (tiltLevel < 0.3) return 'tilt-bar-fill--low';
  if (tiltLevel < 0.6) return 'tilt-bar-fill--medium';
  return 'tilt-bar-fill--high';
}

// Default filter state
export const DEFAULT_FILTERS: CaptureFilters = {
  limit: 50,
  offset: 0,
  labels: undefined,
  error_type: undefined,
  has_error: undefined,
  is_correction: undefined,
  display_emotion: undefined,
  min_tilt_level: undefined,
  max_tilt_level: undefined,
};

// Build the raw request messages array (for the "Download Request" button)
export function buildRawRequest(capture: PromptCapture) {
  const messages: Array<{ role: string; content: string }> = [];

  // System prompt
  if (capture.system_prompt) {
    messages.push({ role: 'system', content: capture.system_prompt });
  }

  // Conversation history (prior turns only - current turn is stored separately)
  if (capture.conversation_history) {
    for (const msg of capture.conversation_history) {
      messages.push({ role: msg.role, content: msg.content });
    }
  }

  // Current user message
  if (capture.user_message) {
    messages.push({ role: 'user', content: capture.user_message });
  }

  return {
    model: capture.model,
    messages,
    // Include other request params if available
    ...(capture.reasoning_effort && { reasoning_effort: capture.reasoning_effort }),
  };
}

// Download JSON file helper
export function downloadJson(data: unknown, filename: string): void {
  const json = JSON.stringify(data, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Game context resolved from the selected capture + its linked analysis.
// Computed by the orchestrator (capture-first → analysis-second fallback)
// and passed to the detail panel.
export interface CaptureContext {
  phase: string | null;
  pot_total: number | null;
  cost_to_call: number | null;
  pot_odds: number | null;
  player_stack: number | null;
  player_hand: string[] | null;
  community_cards: string[] | null;
  action_taken: string | null;
  raise_amount: number | null;
}
