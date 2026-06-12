import { registerPlugin } from '@capacitor/core';
import { isNativePlatform } from './nativeAuth';
import type { BankrollPoint, ReputationData } from '../components/cash/types';

/**
 * Publishes a small snapshot to the native home-screen widget (net-worth
 * sparkline + renown/regard/status). Native-only and best-effort: on the web,
 * or before the native `WidgetBridge` plugin exists, it's a no-op.
 *
 * The bridge plugin (Swift, app target) writes this JSON to the shared App Group
 * `UserDefaults(suiteName: "group.com.mypokerface.app")` and reloads the widget
 * timelines; the widget extension reads the same suite.
 */

interface WidgetBridgePlugin {
  publish(options: { payload: string }): Promise<void>;
}

const WidgetBridge = registerPlugin<WidgetBridgePlugin>('WidgetBridge');

export interface WidgetSnapshot {
  /** Headline net worth (chips + receivables − payables). */
  netWorth: number;
  /** Net-worth-over-time values for the sparkline (oldest → newest). */
  series: number[];
  /** Fame magnitude, [0, 1]. */
  renown: number;
  /** How the room feels, [-1, 1]. */
  regard: number;
  /** Reputation quadrant — the player's "described status". */
  status: string;
  /** ISO-8601 timestamp of this snapshot. */
  updatedAt: string;
}

export function publishWidgetData(input: {
  bankroll?: number | null;
  history?: BankrollPoint[];
  reputation?: ReputationData | null;
}): void {
  if (!isNativePlatform()) return;

  // BankrollPoint.value is net worth over time, so the freshest point is the
  // current net worth; fall back to the bankroll figure if history is empty.
  const series = (input.history ?? []).map((p) => p.value);
  const netWorth = series.length > 0 ? series[series.length - 1] : (input.bankroll ?? 0);

  const snapshot: WidgetSnapshot = {
    netWorth,
    series,
    renown: input.reputation?.renown ?? 0,
    regard: input.reputation?.regard ?? 0,
    status: input.reputation?.quadrant ?? '',
    updatedAt: new Date().toISOString(),
  };

  void (async () => {
    try {
      await WidgetBridge.publish({ payload: JSON.stringify(snapshot) });
    } catch (e) {
      // Bridge missing (e.g. pre-widget build / web) or App Group write failed —
      // best-effort, the widget just keeps its last snapshot.
      console.warn('[widget] publish failed:', e);
    }
  })();
}
