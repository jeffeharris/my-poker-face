/**
 * NetWorthDrawer — Phase 3 Commit 2 of the backing system handoff.
 *
 * Centered modal accessible from `/cash` via the wallet icon next to
 * the bankroll display. Shows the player's full financial position:
 *   - Bankroll + tier indicator ("$50 stakes — Standard tier")
 *   - Carry cap + remaining headroom
 *   - Payables: each outstanding carry with staker name, amounts, age,
 *     and a "Pay off now" action that debits the bankroll → staker
 *     (greyed out when the bankroll can't cover the carry)
 *   - Receivables column placeholder for Phase 5 (humans-as-stakers);
 *     the structural slot is preserved so the layout doesn't reshuffle
 *     when Phase 5 ships
 *
 * Pattern mirrors `SponsorModal`: overlay click-to-close + centered
 * panel + close button. Polls /api/cash/net-worth on open so the
 * drawer reflects state from concurrent leaves / settlements.
 */

import { useCallback, useEffect, useState } from 'react';
import { X, Wallet } from 'lucide-react';
import { getNetWorth, payOffCarry } from './api';
import type { NetWorthResponse, Payable, TierStatus } from './types';
import { logger } from '../../utils/logger';
import './NetWorthDrawer.css';

interface NetWorthDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  /** Fires after a successful payoff so the parent can re-fetch lobby
   *  state (carry-pin annotations refresh from /api/cash/lobby). */
  onPayoff?: () => void;
}

const TIER_LABELS: Record<TierStatus, string> = {
  premium: 'Premium',
  standard: 'Standard',
  restricted: 'Restricted',
  house_only: 'House only',
};

const TIER_DESCRIPTIONS: Record<TierStatus, string> = {
  premium: 'Full sponsor pool — normal terms.',
  standard: 'Some lenders won\'t back you. Cuts bumped slightly.',
  restricted: 'Only forgiving lenders. Cuts noticeably steeper.',
  house_only: 'No personality offers. House stake only.',
};

function formatAge(createdAt: string | null): string {
  if (!createdAt) return '';
  const created = new Date(createdAt);
  const diffMs = Date.now() - created.getTime();
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (days < 1) {
    const hours = Math.floor(diffMs / (1000 * 60 * 60));
    if (hours < 1) return 'just now';
    return `${hours}h ago`;
  }
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

export function NetWorthDrawer({ isOpen, onClose, onPayoff }: NetWorthDrawerProps) {
  const [data, setData] = useState<NetWorthResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [payoffError, setPayoffError] = useState<string | null>(null);
  const [busyStakeId, setBusyStakeId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await getNetWorth();
      setData(response);
      setLoadError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Failed to load net worth:', msg);
      setLoadError(msg);
    }
  }, []);

  useEffect(() => {
    if (!isOpen) {
      setData(null);
      setLoadError(null);
      setPayoffError(null);
      setBusyStakeId(null);
      return;
    }
    void load();
  }, [isOpen, load]);

  const handlePayoff = useCallback(
    async (stakeId: string) => {
      setPayoffError(null);
      setBusyStakeId(stakeId);
      try {
        await payOffCarry(stakeId);
        await load();
        onPayoff?.();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Payoff failed:', msg);
        setPayoffError(msg);
      } finally {
        setBusyStakeId(null);
      }
    },
    [load, onPayoff],
  );

  if (!isOpen) return null;

  return (
    <div className="net-worth-drawer__overlay" onClick={onClose}>
      <div
        className="net-worth-drawer__sheet"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="net-worth-drawer__header">
          <div className="net-worth-drawer__title-row">
            <h2 className="net-worth-drawer__title">
              <Wallet size={18} aria-hidden="true" />
              <span>Net worth</span>
            </h2>
            <button
              type="button"
              className="net-worth-drawer__close"
              onClick={onClose}
              aria-label="Close net worth"
            >
              <X size={20} />
            </button>
          </div>
          {data && (
            <div className="net-worth-drawer__subtitle">
              {data.tier_stake_label} stakes —{' '}
              <span className={`net-worth-drawer__tier net-worth-drawer__tier--${data.tier_status}`}>
                {TIER_LABELS[data.tier_status]} tier
              </span>
            </div>
          )}
        </div>

        <div className="net-worth-drawer__body">
          {loadError && (
            <div className="net-worth-drawer__error" role="alert">
              {loadError}
            </div>
          )}
          {data === null && !loadError && (
            <div className="net-worth-drawer__loading">Loading…</div>
          )}
          {data !== null && (
            <NetWorthBody
              data={data}
              onPayoff={handlePayoff}
              busyStakeId={busyStakeId}
              payoffError={payoffError}
            />
          )}
        </div>
      </div>
    </div>
  );
}

interface NetWorthBodyProps {
  data: NetWorthResponse;
  onPayoff: (stakeId: string) => void;
  busyStakeId: string | null;
  payoffError: string | null;
}

function NetWorthBody({ data, onPayoff, busyStakeId, payoffError }: NetWorthBodyProps) {
  return (
    <>
      <section className="net-worth-drawer__summary">
        <div className="net-worth-drawer__summary-row">
          <span>Bankroll</span>
          <span className="net-worth-drawer__amount">
            ${data.bankroll.toLocaleString()}
          </span>
        </div>
        <div className="net-worth-drawer__summary-row">
          <span>Net worth</span>
          <span
            className={
              'net-worth-drawer__amount' +
              (data.net_worth < data.bankroll
                ? ' net-worth-drawer__amount--negative'
                : '')
            }
          >
            ${data.net_worth.toLocaleString()}
          </span>
        </div>
        <div className="net-worth-drawer__summary-row net-worth-drawer__summary-row--muted">
          <span>
            Carry headroom
            <span className="net-worth-drawer__hint">
              {' '}
              cap ${data.carry_cap.toLocaleString()}
            </span>
          </span>
          <span>${data.available.toLocaleString()}</span>
        </div>
        <p className="net-worth-drawer__tier-explainer">
          {TIER_DESCRIPTIONS[data.tier_status]}
        </p>
      </section>

      {payoffError && (
        <div className="net-worth-drawer__error" role="alert">
          {payoffError}
        </div>
      )}

      <section className="net-worth-drawer__column">
        <h3 className="net-worth-drawer__column-title">
          Payables
          <span className="net-worth-drawer__column-count">
            {data.payables.length}
          </span>
        </h3>
        {data.payables.length === 0 ? (
          <p className="net-worth-drawer__empty">No outstanding carries.</p>
        ) : (
          <ul className="net-worth-drawer__list">
            {data.payables.map((p) => (
              <PayableRow
                key={p.stake_id}
                payable={p}
                bankroll={data.bankroll}
                busy={busyStakeId === p.stake_id}
                disabled={busyStakeId !== null && busyStakeId !== p.stake_id}
                onPayoff={onPayoff}
              />
            ))}
          </ul>
        )}
      </section>

      <section className="net-worth-drawer__column">
        <h3 className="net-worth-drawer__column-title">
          Receivables
          <span className="net-worth-drawer__column-count">0</span>
        </h3>
        <p className="net-worth-drawer__empty">
          Stake an AI to start earning carries.{' '}
          <span className="net-worth-drawer__hint">(Coming in Phase 5.)</span>
        </p>
      </section>
    </>
  );
}

interface PayableRowProps {
  payable: Payable;
  bankroll: number;
  busy: boolean;
  disabled: boolean;
  onPayoff: (stakeId: string) => void;
}

function PayableRow({ payable, bankroll, busy, disabled, onPayoff }: PayableRowProps) {
  const canAfford = bankroll >= payable.carry_amount;
  const handleClick = useCallback(() => {
    if (!canAfford || busy || disabled) return;
    onPayoff(payable.stake_id);
  }, [canAfford, busy, disabled, onPayoff, payable.stake_id]);
  return (
    <li className="net-worth-drawer__row">
      <div className="net-worth-drawer__row-main">
        <div className="net-worth-drawer__row-name">
          {payable.staker_display_name}
        </div>
        <div className="net-worth-drawer__row-meta">
          <span>${payable.carry_amount.toLocaleString()} owed</span>
          <span className="net-worth-drawer__row-sep">·</span>
          <span className="net-worth-drawer__hint">
            originally ${payable.principal.toLocaleString()} @ {payable.stake_tier}
          </span>
          {payable.created_at && (
            <>
              <span className="net-worth-drawer__row-sep">·</span>
              <span className="net-worth-drawer__hint">
                {formatAge(payable.created_at)}
              </span>
            </>
          )}
        </div>
      </div>
      <button
        type="button"
        className="net-worth-drawer__action"
        onClick={handleClick}
        disabled={!canAfford || busy || disabled}
        title={
          !canAfford
            ? `Need $${payable.carry_amount.toLocaleString()} bankroll`
            : 'Pay off this carry'
        }
      >
        {busy ? 'Paying…' : 'Pay off now'}
      </button>
    </li>
  );
}
