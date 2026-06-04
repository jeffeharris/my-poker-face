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
import { createPortal } from 'react-dom';
import { X, Wallet } from 'lucide-react';
import {
  getForgivenessRequests,
  getLedger,
  getNetWorth,
  payOffCarry,
  requestForgiveness,
  stakerForgive,
  type LedgerEntry,
} from './api';
import type {
  ForgivenessRequest,
  NetWorthResponse,
  Payable,
  Receivable,
  StakeHistoryRow,
  TierStatus,
} from './types';
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
  standard: "Some lenders won't back you. Cuts bumped slightly.",
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

/** Per-stake transient outcome notice rendered above the row. */
interface ForgivenessNotice {
  stakeId: string;
  kind: 'granted' | 'refused' | 'rate_limited';
  message: string;
}

export function NetWorthDrawer({ isOpen, onClose, onPayoff }: NetWorthDrawerProps) {
  const [data, setData] = useState<NetWorthResponse | null>(null);
  const [requests, setRequests] = useState<ForgivenessRequest[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [payoffError, setPayoffError] = useState<string | null>(null);
  const [busyStakeId, setBusyStakeId] = useState<string | null>(null);
  const [forgivenessNotice, setForgivenessNotice] = useState<ForgivenessNotice | null>(null);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);

  const load = useCallback(async () => {
    try {
      // Fetch in parallel — the responses are independent. If a secondary
      // endpoint fails, fall back so net worth still renders.
      const [netWorth, reqs, ledgerResp] = await Promise.all([
        getNetWorth(),
        getForgivenessRequests().catch((e) => {
          logger.error(
            'Failed to load forgiveness requests:',
            e instanceof Error ? e.message : String(e)
          );
          return { requests: [] as ForgivenessRequest[] };
        }),
        getLedger().catch((e) => {
          logger.error('Failed to load ledger:', e instanceof Error ? e.message : String(e));
          return { entries: [] as LedgerEntry[], balance: 0 };
        }),
      ]);
      setData(netWorth);
      setRequests(reqs.requests);
      setLedger(ledgerResp.entries);
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
      setRequests([]);
      setLoadError(null);
      setPayoffError(null);
      setBusyStakeId(null);
      setForgivenessNotice(null);
      return;
    }
    void load();
  }, [isOpen, load]);

  const handlePayoff = useCallback(
    async (stakeId: string) => {
      setPayoffError(null);
      setForgivenessNotice(null);
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
    [load, onPayoff]
  );

  const handleForgiveness = useCallback(
    async (payable: Payable) => {
      setPayoffError(null);
      setForgivenessNotice(null);
      setBusyStakeId(payable.stake_id);
      try {
        const result = await requestForgiveness(payable.stake_id);
        if (result.kind === 'rate_limited') {
          const hoursLeft = Math.ceil(result.data.retry_after_seconds / 3600);
          setForgivenessNotice({
            stakeId: payable.stake_id,
            kind: 'rate_limited',
            message: `Already asked recently — try again in about ${hoursLeft}h.`,
          });
          return;
        }
        if (result.data.granted) {
          setForgivenessNotice({
            stakeId: payable.stake_id,
            kind: 'granted',
            message: `${payable.staker_display_name} forgave the carry.`,
          });
          await load();
          onPayoff?.();
        } else {
          setForgivenessNotice({
            stakeId: payable.stake_id,
            kind: 'refused',
            message: `${payable.staker_display_name} refused — build goodwill and try again later.`,
          });
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Forgiveness request failed:', msg);
        setPayoffError(msg);
      } finally {
        setBusyStakeId(null);
      }
    },
    [load, onPayoff]
  );

  /** v110 — player's decision on an AI-initiated forgiveness ask.
   *  Grant clears the carry + warms the AI's view of the player;
   *  refuse keeps the carry and cools the relationship. Either way
   *  the ask is consumed and the badge clears for this stake. */
  const handleStakerForgive = useCallback(
    async (req: ForgivenessRequest, grant: boolean) => {
      setPayoffError(null);
      setForgivenessNotice(null);
      setBusyStakeId(req.stake_id);
      try {
        await stakerForgive(req.stake_id, grant);
        await load();
        // Refreshing /net-worth also bumps the carries list, so let
        // the parent re-fetch lobby state for carry pin annotations.
        onPayoff?.();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Staker-forgive failed:', msg);
        setPayoffError(msg);
      } finally {
        setBusyStakeId(null);
      }
    },
    [load, onPayoff]
  );

  if (!isOpen) return null;

  // Portaled to <body> so the fixed overlay escapes the Lobby's
  // PageLayout stacking context — otherwise the app header (.menu-bar)
  // paints over the centered sheet's close button. See CharacterDetailCard.
  return createPortal(
    <div className="net-worth-drawer__overlay" onClick={onClose}>
      <div className="net-worth-drawer__sheet" onClick={(e) => e.stopPropagation()}>
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
              <span
                className={`net-worth-drawer__tier net-worth-drawer__tier--${data.tier_status}`}
              >
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
          {data === null && !loadError && <div className="net-worth-drawer__loading">Loading…</div>}
          {data !== null && (
            <NetWorthBody
              data={data}
              requests={requests}
              ledger={ledger}
              onPayoff={handlePayoff}
              onForgiveness={handleForgiveness}
              onStakerForgive={handleStakerForgive}
              busyStakeId={busyStakeId}
              payoffError={payoffError}
              forgivenessNotice={forgivenessNotice}
            />
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}

interface NetWorthBodyProps {
  data: NetWorthResponse;
  requests: ForgivenessRequest[];
  ledger: LedgerEntry[];
  onPayoff: (stakeId: string) => void;
  onForgiveness: (payable: Payable) => void;
  onStakerForgive: (req: ForgivenessRequest, grant: boolean) => void;
  busyStakeId: string | null;
  payoffError: string | null;
  forgivenessNotice: ForgivenessNotice | null;
}

function NetWorthBody({
  data,
  requests,
  ledger,
  onPayoff,
  onForgiveness,
  onStakerForgive,
  busyStakeId,
  payoffError,
  forgivenessNotice,
}: NetWorthBodyProps) {
  return (
    <>
      <section className="net-worth-drawer__summary">
        <div className="net-worth-drawer__summary-row">
          <span>Bankroll</span>
          <span className="net-worth-drawer__amount">${data.bankroll.toLocaleString()}</span>
        </div>
        <div className="net-worth-drawer__summary-row">
          <span>Net worth</span>
          <span
            className={
              'net-worth-drawer__amount' +
              (data.net_worth < data.bankroll ? ' net-worth-drawer__amount--negative' : '')
            }
          >
            ${data.net_worth.toLocaleString()}
          </span>
        </div>
        <div className="net-worth-drawer__summary-row net-worth-drawer__summary-row--muted">
          <span>
            Carry headroom
            <span className="net-worth-drawer__hint"> cap ${data.carry_cap.toLocaleString()}</span>
          </span>
          <span>${data.available.toLocaleString()}</span>
        </div>
        <p className="net-worth-drawer__tier-explainer">{TIER_DESCRIPTIONS[data.tier_status]}</p>
      </section>

      {payoffError && (
        <div className="net-worth-drawer__error" role="alert">
          {payoffError}
        </div>
      )}

      {requests.length > 0 && (
        <section className="net-worth-drawer__column net-worth-drawer__column--attention">
          <h3 className="net-worth-drawer__column-title">
            Forgiveness requests
            <span className="net-worth-drawer__column-count net-worth-drawer__column-count--alert">
              {requests.length}
            </span>
          </h3>
          <ul className="net-worth-drawer__list">
            {requests.map((req) => (
              <ForgivenessRequestRow
                key={req.stake_id}
                request={req}
                busy={busyStakeId === req.stake_id}
                disabled={busyStakeId !== null && busyStakeId !== req.stake_id}
                onDecide={onStakerForgive}
              />
            ))}
          </ul>
        </section>
      )}

      <section className="net-worth-drawer__column">
        <h3 className="net-worth-drawer__column-title">
          Payables
          <span className="net-worth-drawer__column-count">{data.payables.length}</span>
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
                onForgiveness={onForgiveness}
                notice={forgivenessNotice?.stakeId === p.stake_id ? forgivenessNotice : null}
              />
            ))}
          </ul>
        )}
      </section>

      <section className="net-worth-drawer__column">
        <h3 className="net-worth-drawer__column-title">
          Receivables
          <span className="net-worth-drawer__column-count">{data.receivables.length}</span>
        </h3>
        {data.receivables.length === 0 ? (
          <p className="net-worth-drawer__empty">
            Stake an AI from the lobby to start earning carries.
          </p>
        ) : (
          <ul className="net-worth-drawer__list">
            {data.receivables.map((r) => (
              <ReceivableRow key={r.stake_id} receivable={r} />
            ))}
          </ul>
        )}
      </section>

      <section className="net-worth-drawer__column">
        <h3 className="net-worth-drawer__column-title">
          History
          <span className="net-worth-drawer__column-count">{data.history.length}</span>
        </h3>
        {data.history.length === 0 ? (
          <p className="net-worth-drawer__empty">
            No closed-out stakes yet — settled and defaulted stakes will appear here.
          </p>
        ) : (
          <ul className="net-worth-drawer__list net-worth-drawer__list--history">
            {data.history.map((h) => (
              <HistoryRow key={h.stake_id} row={h} />
            ))}
          </ul>
        )}
      </section>

      <section className="net-worth-drawer__column">
        <h3 className="net-worth-drawer__column-title">
          Transactions
          <span className="net-worth-drawer__column-count">{ledger.length}</span>
        </h3>
        {ledger.length === 0 ? (
          <p className="net-worth-drawer__empty">
            No chip movements yet — cash game results, tournament prizes, and buy-ins will appear
            here.
          </p>
        ) : (
          <ul className="net-worth-drawer__list net-worth-drawer__list--ledger">
            {ledger.map((e, i) => (
              <LedgerRow key={`${e.created_at}-${i}`} entry={e} />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function LedgerRow({ entry }: { entry: LedgerEntry }) {
  const positive = entry.signed_amount >= 0;
  const date = new Date(entry.created_at);
  const when = Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const label =
    entry.finishing_position && entry.reason === 'tournament_payout'
      ? `${entry.label} (${entry.finishing_position}${ordinalSuffix(entry.finishing_position)})`
      : entry.label;
  return (
    <li className="net-worth-drawer__ledger-row">
      <span className="net-worth-drawer__ledger-label">{label}</span>
      <span className="net-worth-drawer__ledger-date">{when}</span>
      <span
        className={`net-worth-drawer__ledger-amount net-worth-drawer__ledger-amount--${
          positive ? 'in' : 'out'
        }`}
      >
        {positive ? '+' : '−'}${Math.abs(entry.signed_amount).toLocaleString()}
      </span>
      <span className="net-worth-drawer__ledger-balance">
        ${entry.balance_after.toLocaleString()}
      </span>
    </li>
  );
}

function ordinalSuffix(n: number): string {
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return 'th';
  return { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th';
}

interface HistoryRowProps {
  row: StakeHistoryRow;
}

/** Human-readable explanation of how a stake closed out from the
 *  player's POV. Distinguishes four staker outcomes (clean settle,
 *  paid-off-after-bust, forgiven-after-bust, defaulted-after-bust)
 *  and the borrower side's settled vs walked-away cases.
 *
 *  Defaulted: the borrower walked away from the IOU — debt canceled,
 *  reputation hit absorbed. Forgiven: same chip outcome (debt
 *  canceled, no chips moved) but voluntary on the staker's side and
 *  the relationship axes warm instead of fracturing.
 *
 *  When payouts came back >= principal, the stake either settled
 *  cleanly at session end OR went bust-then-paid-off (the route
 *  bumps staker_payout on payoff so both flow into the same line). */
function describeStakeOutcome(args: {
  role: 'staker' | 'borrower';
  defaulted: boolean;
  payout: number;
  principal: number;
}): string {
  const { role, defaulted, payout, principal } = args;
  const unrecovered = Math.max(0, principal - payout);

  if (role === 'staker') {
    if (defaulted) {
      if (payout <= 0) {
        return `Nothing came back at the bust. They defaulted on the $${unrecovered.toLocaleString()} you put up.`;
      }
      return `You got back $${payout.toLocaleString()} at the bust. The remaining $${unrecovered.toLocaleString()} was written off when they defaulted.`;
    }
    // status = 'settled' branch
    if (payout >= principal) {
      // Either clean settle with cut or a carry that was later paid
      // back in full — both end here.
      return `You got back $${payout.toLocaleString()} on your $${principal.toLocaleString()} stake.`;
    }
    // Settled with payout < principal → forgiveness was granted by
    // the staker. They wrote off the carry voluntarily; chips were
    // already lost at the bust, only the IOU was canceled.
    return `You got back $${payout.toLocaleString()} at the bust and forgave the remaining $${unrecovered.toLocaleString()}.`;
  }

  // Borrower side.
  if (defaulted) {
    return `You walked away from the debt — chips were already gone at the bust, but the IOU is now canceled.`;
  }
  if (payout < 0) {
    // The borrower paid the staker back out of bankroll after their
    // own bust — borrower_payout decremented below zero by the
    // payoff route to reflect the realized loss.
    return `You paid back the $${Math.abs(payout).toLocaleString()} carry out of bankroll.`;
  }
  return `You kept $${payout.toLocaleString()} at session end.`;
}

/** One closed-stake row in the history list. Frames from the player's
 *  POV using `role`: as staker, it's "you staked X"; as borrower, "X
 *  staked you". `settled` rows read as clean closures; `defaulted`
 *  rows surface the relationship rupture moment.
 *
 *  When `net_for_player` is known (v106+), shows the per-stake P&L
 *  with a colored amount. Legacy rows pre-v106 settled without
 *  capturing chip flows, so the P&L line is hidden and the player
 *  gets the qualitative outcome only.
 *
 *  Read-only — no actions are possible on closed stakes. */
function HistoryRow({ row }: HistoryRowProps) {
  const defaulted = row.status === 'defaulted';
  // Who walked away from the deal? On defaults, the borrower is the
  // actor; on settles, the framing is neutral ("closed").
  const verb = defaulted
    ? row.role === 'staker'
      ? 'defaulted on you'
      : 'you defaulted on'
    : 'settled with';
  const settleLabel = row.settled_at ? formatAge(row.settled_at) : '';

  const net = row.net_for_player;
  const hasNet = net !== null && net !== undefined;
  const isWin = hasNet && net > 0;
  const isLoss = hasNet && net < 0;
  // Payout the player actually received on this stake — the "how much
  // came back to me" number. Surfaced separately from net so a small
  // win on a big stake still shows the full pay-back amount.
  const payout = row.role === 'staker' ? row.staker_payout : row.borrower_payout;

  return (
    <li
      className={
        'net-worth-drawer__row net-worth-drawer__row--history' +
        (defaulted ? ' net-worth-drawer__row--defaulted' : ' net-worth-drawer__row--settled')
      }
    >
      <div className="net-worth-drawer__row-main">
        <div className="net-worth-drawer__row-name">
          <span>{row.counterparty_display_name}</span>
          <span
            className={
              'net-worth-drawer__status-badge' +
              (defaulted
                ? ' net-worth-drawer__status-badge--defaulted'
                : ' net-worth-drawer__status-badge--settled')
            }
          >
            {defaulted ? 'defaulted' : 'settled'}
          </span>
          {hasNet && (
            <span
              className={
                'net-worth-drawer__history-net' +
                (isWin
                  ? ' net-worth-drawer__history-net--win'
                  : isLoss
                    ? ' net-worth-drawer__history-net--loss'
                    : '')
              }
            >
              {net > 0 ? '+' : net < 0 ? '−' : ''}${Math.abs(net).toLocaleString()}
            </span>
          )}
        </div>
        <div className="net-worth-drawer__row-meta">
          <span>
            {row.role === 'staker' ? 'You staked' : 'They staked you'} for $
            {row.principal.toLocaleString()}
          </span>
          <span className="net-worth-drawer__row-sep">·</span>
          <span className="net-worth-drawer__hint">
            {verb} {settleLabel || 'recently'}
          </span>
        </div>
        {hasNet && payout !== null && payout !== undefined && (
          <div className="net-worth-drawer__row-status-line">
            {describeStakeOutcome({
              role: row.role,
              defaulted,
              payout,
              principal: row.principal,
            })}
          </div>
        )}
        {!hasNet && (
          <div className="net-worth-drawer__row-status-line net-worth-drawer__row-status-line--muted">
            P&L for this stake wasn't captured (settled before history tracking was added).
          </div>
        )}
      </div>
    </li>
  );
}

interface ReceivableRowProps {
  receivable: Receivable;
}

/** Read-only row — chips return automatically when the staked AI's
 *  session settles (or carry rolls forward if they bust). The player
 *  has no direct action to take from here; surfacing the row is the
 *  point.
 *
 *  Two flavors share this row: active stakes (chips in flight on the
 *  AI's seat) and carries (residual debt after a bust). The status
 *  badge + amount framing distinguishes them. */
function ReceivableRow({ receivable }: ReceivableRowProps) {
  const isActive = receivable.status === 'active';
  const cutPct = Math.round(receivable.cut * 100);
  const isMatchShare = receivable.format === 'match_share';

  return (
    <li
      className={
        'net-worth-drawer__row' +
        (isActive ? ' net-worth-drawer__row--active' : ' net-worth-drawer__row--carry')
      }
    >
      <div className="net-worth-drawer__row-main">
        <div className="net-worth-drawer__row-name">
          <span>{receivable.borrower_display_name}</span>
          <span
            className={
              'net-worth-drawer__status-badge' +
              (isActive
                ? ' net-worth-drawer__status-badge--active'
                : ' net-worth-drawer__status-badge--carry')
            }
          >
            {isActive ? 'in play' : 'owed'}
          </span>
        </div>
        <div className="net-worth-drawer__row-meta">
          <span>
            ${receivable.amount.toLocaleString()} {isActive ? 'at the table' : 'owed to you'}
          </span>
          <span className="net-worth-drawer__row-sep">·</span>
          <span className="net-worth-drawer__hint">
            {isMatchShare
              ? `you put up $${receivable.principal.toLocaleString()} + they matched $${receivable.match_amount.toLocaleString()}`
              : `you staked $${receivable.principal.toLocaleString()}`}{' '}
            @ {receivable.stake_tier} · {cutPct}% cut
          </span>
          {receivable.created_at && (
            <>
              <span className="net-worth-drawer__row-sep">·</span>
              <span className="net-worth-drawer__hint">{formatAge(receivable.created_at)}</span>
            </>
          )}
        </div>
        {isActive && (
          <div className="net-worth-drawer__row-status-line">
            Settles when {receivable.borrower_display_name} leaves the table — you recover the
            principal first, then split the upside per your cut.
          </div>
        )}
      </div>
    </li>
  );
}

interface ForgivenessRequestRowProps {
  request: ForgivenessRequest;
  busy: boolean;
  disabled: boolean;
  onDecide: (req: ForgivenessRequest, grant: boolean) => void;
}

/** v110 — one pending forgiveness ask the player needs to decide on.
 *  Surfaces above payables since it's actionable AND time-sensitive.
 *  Grant clears the carry + warms the AI's view of the player; refuse
 *  keeps the carry on the books + cools the relationship. */
function ForgivenessRequestRow({ request, busy, disabled, onDecide }: ForgivenessRequestRowProps) {
  const handleGrant = useCallback(() => {
    if (busy || disabled) return;
    onDecide(request, true);
  }, [busy, disabled, onDecide, request]);
  const handleRefuse = useCallback(() => {
    if (busy || disabled) return;
    onDecide(request, false);
  }, [busy, disabled, onDecide, request]);
  return (
    <li className="net-worth-drawer__row net-worth-drawer__row--request">
      <div className="net-worth-drawer__row-main">
        <div className="net-worth-drawer__row-name">
          <span>{request.borrower_display_name}</span>
          <span className="net-worth-drawer__status-badge net-worth-drawer__status-badge--alert">
            asking
          </span>
        </div>
        <div className="net-worth-drawer__row-meta">
          <span>wants you to forgive ${request.carry_amount.toLocaleString()}</span>
          <span className="net-worth-drawer__row-sep">·</span>
          <span className="net-worth-drawer__hint">{request.stake_tier} carry</span>
          {request.pending_since && (
            <>
              <span className="net-worth-drawer__row-sep">·</span>
              <span className="net-worth-drawer__hint">
                asked {formatAge(request.pending_since)}
              </span>
            </>
          )}
        </div>
        <div className="net-worth-drawer__row-status-line">
          Grant to clear the carry (warmer relationship). Refuse to keep it on the books (cooler
          relationship).
        </div>
      </div>
      <div className="net-worth-drawer__row-actions">
        <button
          type="button"
          className="net-worth-drawer__action net-worth-drawer__action--secondary"
          onClick={handleRefuse}
          disabled={busy || disabled}
          title="Refuse the ask — keep the carry, signal you expect payment."
        >
          {busy ? '…' : 'Refuse'}
        </button>
        <button
          type="button"
          className="net-worth-drawer__action"
          onClick={handleGrant}
          disabled={busy || disabled}
          title="Forgive the carry — write off the debt as goodwill."
        >
          {busy ? '…' : 'Forgive'}
        </button>
      </div>
    </li>
  );
}

interface PayableRowProps {
  payable: Payable;
  bankroll: number;
  busy: boolean;
  disabled: boolean;
  onPayoff: (stakeId: string) => void;
  onForgiveness: (payable: Payable) => void;
  notice: ForgivenessNotice | null;
}

function PayableRow({
  payable,
  bankroll,
  busy,
  disabled,
  onPayoff,
  onForgiveness,
  notice,
}: PayableRowProps) {
  const canAfford = bankroll >= payable.carry_amount;
  const handlePayoff = useCallback(() => {
    if (!canAfford || busy || disabled) return;
    onPayoff(payable.stake_id);
  }, [canAfford, busy, disabled, onPayoff, payable.stake_id]);
  const handleForgiveness = useCallback(() => {
    if (busy || disabled) return;
    onForgiveness(payable);
  }, [busy, disabled, onForgiveness, payable]);
  return (
    <li className="net-worth-drawer__row">
      <div className="net-worth-drawer__row-main">
        <div className="net-worth-drawer__row-name">{payable.staker_display_name}</div>
        <div className="net-worth-drawer__row-meta">
          <span>${payable.carry_amount.toLocaleString()} owed</span>
          <span className="net-worth-drawer__row-sep">·</span>
          <span className="net-worth-drawer__hint">
            originally ${payable.principal.toLocaleString()} @ {payable.stake_tier}
          </span>
          {payable.created_at && (
            <>
              <span className="net-worth-drawer__row-sep">·</span>
              <span className="net-worth-drawer__hint">{formatAge(payable.created_at)}</span>
            </>
          )}
        </div>
        {notice && (
          <div
            className={`net-worth-drawer__notice net-worth-drawer__notice--${notice.kind}`}
            role="status"
          >
            {notice.message}
          </div>
        )}
      </div>
      <div className="net-worth-drawer__row-actions">
        <button
          type="button"
          className="net-worth-drawer__action net-worth-drawer__action--secondary"
          onClick={handleForgiveness}
          disabled={busy || disabled}
          title={`Ask ${payable.staker_display_name} to forgive — depends on goodwill you've built.`}
        >
          {busy ? 'Asking…' : 'Request forgiveness'}
        </button>
        <button
          type="button"
          className="net-worth-drawer__action"
          onClick={handlePayoff}
          disabled={!canAfford || busy || disabled}
          title={
            !canAfford
              ? `Need $${payable.carry_amount.toLocaleString()} bankroll`
              : 'Pay off this carry'
          }
        >
          {busy ? 'Paying…' : 'Pay off now'}
        </button>
      </div>
    </li>
  );
}
