/**
 * Post-session cash-out summary modal.
 *
 * Rendered after `/api/cash/leave` returns, before navigating back to
 * the menu. Mirrors the visual structure of `TournamentComplete` so
 * tournament and cash-mode endings feel like the same product surface.
 */

import { useEffect, useState } from 'react';
import './CashOutSummary.css';

export interface SessionSummary {
  buy_in: number;
  cash_out: number;
  net_pnl: number;
  // Staked-session fields. `is_staked=true` flips the headline P&L to
  // mean "what you take home" (instead of "table profit"), since the
  // chips on the table aren't fully yours when a sponsor funded the seat.
  is_staked?: boolean;
  sponsor_principal?: number;
  sponsor_repaid?: number;
  player_take_home?: number | null;
  hands_played: number;
  hands_won: number;
  biggest_pot_won: number;
  vpip_pct: number;
  pfr_pct: number;
  aggression_pct: number;
  play_style: string;
  duration_seconds: number;
}

interface CashOutSummaryProps {
  summary: SessionSummary;
  stakeLabel: string | null;
  finalBankroll: number;
  sponsorRepaid: number;
  onContinue: () => void;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remainder}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function formatChips(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? '-' : '';
  return `${sign}$${abs.toLocaleString()}`;
}

export function CashOutSummary({
  summary,
  stakeLabel,
  finalBankroll,
  sponsorRepaid,
  onContinue,
}: CashOutSummaryProps) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setShow(true), 100);
    return () => clearTimeout(t);
  }, []);

  const isWin = summary.net_pnl > 0;
  const isLoss = summary.net_pnl < 0;
  const headline = isWin ? 'You Cashed Out Ahead' : isLoss ? 'Session Ended' : 'Broke Even';
  const outcomeClass = isWin ? 'is-win' : isLoss ? 'is-loss' : 'is-flat';
  // For staked sessions, prefer fields from the durable summary over
  // the legacy `sponsorRepaid` prop (which the live route still sends).
  // The summary's view of the world has the sponsor split correct on
  // every leave path — including the memory-miss / cold-load ones the
  // top-level prop doesn't see.
  const effectiveSponsorRepaid = summary.sponsor_repaid ?? sponsorRepaid;
  const isStaked = summary.is_staked === true;

  return (
    <div className={`cashout-summary ${show ? 'show' : ''}`}>
      <div className="cashout-overlay" />

      <div className="cashout-content">
        <div className="cashout-header">
          <h1 className="cashout-title">{headline}</h1>
          {stakeLabel && <div className="cashout-subtitle">{stakeLabel} cash game</div>}
        </div>

        <div className={`cashout-pnl ${outcomeClass}`}>
          <div className="cashout-pnl__label">{isStaked ? 'Your Take-Home' : 'Net P&L'}</div>
          <div className="cashout-pnl__value">
            {summary.net_pnl >= 0 ? '+' : ''}
            {formatChips(summary.net_pnl)}
          </div>
          <div className="cashout-pnl__breakdown">
            {isStaked ? (
              <>
                <span>Sponsor put up {formatChips(summary.sponsor_principal ?? 0)}</span>
                <span aria-hidden="true">→</span>
                <span>Table ended at {formatChips(summary.cash_out)}</span>
              </>
            ) : (
              <>
                <span>Buy-in {formatChips(summary.buy_in)}</span>
                <span aria-hidden="true">→</span>
                <span>Cashed out {formatChips(summary.cash_out)}</span>
              </>
            )}
          </div>
          {effectiveSponsorRepaid > 0 && (
            <div className="cashout-pnl__note">
              Sponsor took {formatChips(effectiveSponsorRepaid)} off the top.
            </div>
          )}
        </div>

        <div className="cashout-stats-grid">
          <div className="cashout-stat">
            <span className="cashout-stat__value">{summary.hands_played}</span>
            <span className="cashout-stat__label">Hands Played</span>
          </div>
          <div className="cashout-stat">
            <span className="cashout-stat__value">{summary.hands_won}</span>
            <span className="cashout-stat__label">Hands Won</span>
          </div>
          <div className="cashout-stat">
            <span className="cashout-stat__value">{formatChips(summary.biggest_pot_won)}</span>
            <span className="cashout-stat__label">Biggest Pot Won</span>
          </div>
          <div className="cashout-stat">
            <span className="cashout-stat__value">{formatDuration(summary.duration_seconds)}</span>
            <span className="cashout-stat__label">Time at Table</span>
          </div>
        </div>

        <div className="cashout-style">
          <div className="cashout-style__label">Your Play Style</div>
          <div className="cashout-style__value">{summary.play_style}</div>
          <div className="cashout-style__metrics">
            <span>VPIP {(summary.vpip_pct ?? 0).toFixed(1)}%</span>
            <span>PFR {(summary.pfr_pct ?? 0).toFixed(1)}%</span>
            <span>Aggression {(summary.aggression_pct ?? 0).toFixed(1)}%</span>
          </div>
        </div>

        <div className="cashout-bankroll">
          <span className="cashout-bankroll__label">Bankroll now</span>
          <span className="cashout-bankroll__value">{formatChips(finalBankroll)}</span>
        </div>

        <button type="button" className="cashout-continue" onClick={onContinue}>
          Return to Lobby
        </button>
      </div>
    </div>
  );
}
