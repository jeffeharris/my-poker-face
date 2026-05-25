/**
 * CareerHero — the editorial top section of the cash/career lobby.
 *
 * Flips the old hierarchy: instead of a generic "Pick a Table" title with
 * the bankroll tucked into a small pill below it, the *bankroll is the
 * hero* — a large tabular figure that reads like a scoreboard for the
 * whole mode. Around it:
 *   - an eyebrow label
 *   - a last-session P/L delta chip (emerald up / ruby down)
 *   - a sparkline of the bankroll trajectory
 *   - a net-worth affordance (opens the drawer; carries the pending-
 *     forgiveness badge)
 *
 * Delta + sparkline come from `/api/cash/lobby` (`last_session_delta`,
 * `bankroll_history`) and degrade gracefully: a brand-new player with no
 * finished sessions just sees the eyebrow + figure + net-worth link.
 */

import { Wallet } from 'lucide-react';
import { Sparkline } from './Sparkline';
import './CareerHero.css';

export interface CareerHeroProps {
  bankroll: number;
  /** Net result of the most recent finished session; null = no history. */
  lastSessionDelta?: number | null;
  /** Bankroll trajectory, oldest → newest. <2 points hides the chart. */
  bankrollHistory?: number[];
  pendingForgivenessCount?: number;
  onOpenNetWorth: () => void;
}

function formatChips(n: number): string {
  return `$${Math.abs(n).toLocaleString()}`;
}

export function CareerHero({
  bankroll,
  lastSessionDelta = null,
  bankrollHistory = [],
  pendingForgivenessCount = 0,
  onOpenNetWorth,
}: CareerHeroProps) {
  const tone =
    lastSessionDelta == null || lastSessionDelta === 0
      ? 'flat'
      : lastSessionDelta > 0
        ? 'up'
        : 'down';

  const hasForgiveness = pendingForgivenessCount > 0;

  return (
    <section className="career-hero" aria-label="Your bankroll">
      <div className="career-hero__top">
        <span className="career-hero__eyebrow">Your bankroll</span>
        <button
          type="button"
          className="career-hero__worth"
          onClick={onOpenNetWorth}
          aria-label={
            hasForgiveness
              ? `Open net worth (${pendingForgivenessCount} forgiveness request${pendingForgivenessCount === 1 ? '' : 's'} pending)`
              : 'Open net worth'
          }
          title={
            hasForgiveness
              ? `${pendingForgivenessCount} forgiveness request${pendingForgivenessCount === 1 ? '' : 's'} need${pendingForgivenessCount === 1 ? 's' : ''} your decision`
              : 'View net worth'
          }
        >
          <Wallet size={15} aria-hidden="true" />
          <span className="career-hero__worth-label">Net worth</span>
          <span className="career-hero__worth-caret" aria-hidden="true">
            ›
          </span>
          {hasForgiveness && (
            <span className="career-hero__worth-badge" aria-hidden="true">
              {pendingForgivenessCount > 9 ? '9+' : pendingForgivenessCount}
            </span>
          )}
        </button>
      </div>

      <div className="career-hero__figure">
        <span className="career-hero__amount">
          ${bankroll.toLocaleString()}
        </span>
        {lastSessionDelta != null && (
          <span
            className={`career-hero__delta career-hero__delta--${tone}`}
            title="Result of your last session"
          >
            {tone === 'flat' ? (
              <>Even</>
            ) : (
              <>
                <span className="career-hero__delta-arrow" aria-hidden="true">
                  {tone === 'up' ? '▲' : '▼'}
                </span>
                {tone === 'down' ? '−' : '+'}
                {formatChips(lastSessionDelta)}
              </>
            )}
            <span className="career-hero__delta-tag">last session</span>
          </span>
        )}
      </div>

      {bankrollHistory.length >= 2 && (
        <Sparkline
          className="career-hero__spark"
          values={bankrollHistory}
          tone={tone}
        />
      )}
    </section>
  );
}
