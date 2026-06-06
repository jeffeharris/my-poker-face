/**
 * useInterhandMessaging — everything that feeds the between-hands ShuffleLoading
 * screen on mobile:
 *   • the fold-out "walk" result line (winner name + WON/SPLIT $X), since walks
 *     skip the winner overlay and show their result in the shuffle screen instead
 *   • a flavor quote, re-picked each hand
 *   • the cash-mode "meanwhile, elsewhere" world ticker
 *
 * The walk-result effect also hands the beat straight to the interhand director
 * (beginShuffle) and clears the winner so the showdown overlay never mounts for a
 * walk — so beginShuffle/clearWinnerInfo are injected by the caller.
 *
 * Lifted out of MobilePokerTable to keep the container lean.
 */

import { useEffect, useMemo, useState } from 'react';
import type { WinnerInfo, CashModeInfo } from '../types/game';
import type { BufferedWorldEvent } from '../stores/gameStore';
import type { ShuffleQuote, TickerLine } from '../components/shared/ShuffleLoading';
import { pickQuote } from '../components/game/WinnerAnnouncement/quote-flavor';
import { selectInterhandTicker } from '../components/cash/interhandTicker';
import { feedEventKey, renderEventIcon } from '../components/cash/tickerEvents';

// How many world-ticker beats the interhand "meanwhile, elsewhere" strip shows at
// once. A few of the biggest/rarest — not a full feed.
const MAX_INTERHAND_TICKER = 3;

export interface InterhandMessaging {
  interhandMessage: string | null;
  interhandSubmessage: string | undefined;
  interhandQuote: ShuffleQuote | undefined;
  interhandTicker: TickerLine[] | undefined;
}

export function useInterhandMessaging({
  winnerInfo,
  handNumber,
  cashMode,
  worldEvents,
  beginShuffle,
  clearWinnerInfo,
}: {
  winnerInfo: WinnerInfo | null;
  handNumber: number;
  cashMode: CashModeInfo | null;
  worldEvents: BufferedWorldEvent[];
  beginShuffle: () => void;
  clearWinnerInfo: () => void;
}): InterhandMessaging {
  // Fold-out (walk) wins are intentionally uneventful: no winner overlay, just
  // the shuffle screen with the winner line in place of "Shuffling". Capture
  // that line, hand straight off to the director's shuffle beat (whose minimum
  // floor keeps it from flashing), and clear winnerInfo so the showdown overlay
  // never mounts for a walk.
  const [interhandMessage, setInterhandMessage] = useState<string | null>(null);
  const [interhandSubmessage, setInterhandSubmessage] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!winnerInfo || winnerInfo.showdown) return;
    // Compute net profit (gross winnings minus what the winner put in)
    let netProfit: number | null = null;
    if (winnerInfo.pot_breakdown) {
      const gross = winnerInfo.pot_breakdown.reduce(
        (sum, pot) => sum + pot.winners.reduce((s, w) => s + w.amount, 0),
        0
      );
      const contributions = winnerInfo.pot_contributions ?? {};
      const winnerContrib = winnerInfo.winners.reduce(
        (sum, name) => sum + (contributions[name] ?? 0),
        0
      );
      netProfit = gross - winnerContrib;
    }
    const names =
      winnerInfo.winners.length > 1 ? winnerInfo.winners.join(' & ') : winnerInfo.winners[0];
    const verb = winnerInfo.winners.length > 1 ? 'SPLIT' : 'WON';
    // Name on its own line (the hero); the amount drops to the line below as
    // "WON $X" — no animated dots, since the hand is finished, not loading.
    setInterhandMessage(names);
    setInterhandSubmessage(
      netProfit != null && netProfit > 0 ? `${verb} $${netProfit.toLocaleString()}` : verb
    );

    if (!winnerInfo.is_final_hand) {
      beginShuffle();
    }
    clearWinnerInfo();
  }, [winnerInfo, clearWinnerInfo, beginShuffle]);

  // Clear the walk message once the next hand starts.
  useEffect(() => {
    setInterhandMessage(null);
    setInterhandSubmessage(undefined);
  }, [handNumber]);

  // Pick a flavor quote for the interhand shuffle. Memoized by handNumber so
  // it stays stable across re-renders during a single shuffle and changes
  // each hand.
  const interhandQuote = useMemo(() => {
    const q = pickQuote('between_hands');
    return q ? { text: q.text, attribution: q.attribution } : undefined;
    // handNumber is an intentional recompute key (not read inside): it re-picks
    // the random quote each new hand while staying stable on re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handNumber]);

  // Cash/career mode: turn the interhand pause into a "meanwhile, elsewhere"
  // world ticker — the bigger, rarer beats from around the room since this
  // hand started (events tagged with the hand that just ended), minus routine
  // sit-downs/leaves. `undefined` in tournament mode, where the world isn't
  // simulated and the hand-number badge stays.
  const interhandTicker = useMemo<TickerLine[] | undefined>(() => {
    if (!cashMode) return undefined;
    const thisHand = worldEvents.filter((w) => w.hand === handNumber).map((w) => w.event);
    return selectInterhandTicker(thisHand, MAX_INTERHAND_TICKER).map((e) => ({
      key: feedEventKey(e),
      icon: renderEventIcon(e.type),
      message: e.message,
    }));
  }, [cashMode, worldEvents, handNumber]);

  return { interhandMessage, interhandSubmessage, interhandQuote, interhandTicker };
}
