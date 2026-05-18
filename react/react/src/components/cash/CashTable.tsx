/**
 * Cash table page — bankroll display, seat layout, action buttons.
 *
 * v1 minimal: text-based seat list + action buttons. Reuses the
 * existing poker table view would require deeper integration with
 * the Zustand game store (which is wired to SocketIO tournament
 * games). Cash mode uses REST polling instead, so this view is
 * standalone for now. v2 can fold cash into the unified game view.
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout, PageHeader } from '../shared';
import {
  getState,
  leaveTable,
  submitAction,
  topUp,
} from './api';
import type {
  CashAction,
  CashSessionState,
  HandResult,
} from './types';
import './CashMode.css';

const ACTIONS: CashAction[] = ['fold', 'check', 'call', 'raise', 'all_in'];
const POLL_INTERVAL_MS = 2_000;

export function CashTablePage() {
  const navigate = useNavigate();
  const [state, setState] = useState<CashSessionState | null>(null);
  const [lastResult, setLastResult] = useState<HandResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [raiseAmount, setRaiseAmount] = useState<number>(0);

  // Bootstrap: fetch state on mount. If no session, redirect to entry.
  useEffect(() => {
    (async () => {
      try {
        const data = await getState();
        setState(data.state);
      } catch {
        navigate('/cash', { replace: true });
      }
    })();
  }, [navigate]);

  // Poll state when waiting (AI turn, between hands, etc.).
  // Stops polling when player is the one awaiting input.
  useEffect(() => {
    if (!state) return;
    if (lastResult?.status === 'awaiting_human') return;
    if (state.player_pending_quit) return;

    const tick = async () => {
      try {
        const data = await getState();
        setState(data.state);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [state, lastResult]);

  const refresh = useCallback(async () => {
    try {
      const data = await getState();
      setState(data.state);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const handleAction = useCallback(async (action: CashAction) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const data = await submitAction(action, action === 'raise' ? raiseAmount : 0);
      setState(data.state);
      setLastResult(data.result ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [busy, raiseAmount]);

  const handleTopUp = useCallback(async (amount: number) => {
    if (busy || amount <= 0) return;
    setBusy(true);
    setError(null);
    try {
      const data = await topUp(amount);
      setState(data.state);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [busy]);

  const handleLeave = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await leaveTable();
      navigate('/cash');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }, [busy, navigate]);

  if (!state) {
    return (
      <PageLayout>
        <PageHeader title="Cash Table" onBack={() => navigate('/menu')} />
        <div className="cash-table__loading">Loading…</div>
      </PageLayout>
    );
  }

  const awaitingHuman = lastResult?.status === 'awaiting_human';
  const playerStack = state.table.stacks['player'] ?? 0;
  const remainingTopUpRoom = Math.max(0, state.table.max_buy_in - playerStack);

  return (
    <PageLayout>
      <PageHeader
        title={`Cash Table — ${state.table.stake_label}`}
        onBack={() => navigate('/menu')}
      />
      <div className="cash-table">
        <header className="cash-table__header">
          <div className="cash-table__stat">
            <span className="cash-table__stat-label">Bankroll</span>
            <span className="cash-table__stat-value">
              ${state.player_bankroll.chips.toLocaleString()}
            </span>
          </div>
          <div className="cash-table__stat">
            <span className="cash-table__stat-label">At table</span>
            <span className="cash-table__stat-value">${playerStack.toLocaleString()}</span>
          </div>
          <div className="cash-table__stat">
            <span className="cash-table__stat-label">Hand</span>
            <span className="cash-table__stat-value">#{state.hand_number}</span>
          </div>
        </header>

        <section className="cash-table__seats">
          <h3>Seats</h3>
          <ol className="cash-table__seat-list">
            {state.table.seats.map((seatId, idx) => (
              <li key={idx} className="cash-table__seat">
                <span className="cash-table__seat-index">{idx + 1}</span>
                <span className="cash-table__seat-name">
                  {seatId === 'player'
                    ? 'You'
                    : seatId ?? <em>empty</em>}
                </span>
                <span className="cash-table__seat-stack">
                  ${(seatId ? state.table.stacks[seatId] ?? 0 : 0).toLocaleString()}
                </span>
              </li>
            ))}
          </ol>
        </section>

        {error && (
          <div className="cash-table__error" role="alert">{error}</div>
        )}

        {awaitingHuman && (
          <section className="cash-table__actions">
            <h3>Your turn</h3>
            <div className="cash-table__action-buttons">
              {ACTIONS.map((action) => (
                <button
                  key={action}
                  type="button"
                  disabled={busy}
                  onClick={() => handleAction(action)}
                  className={`cash-table__action cash-table__action--${action}`}
                >
                  {action}
                </button>
              ))}
            </div>
            <div className="cash-table__raise-input">
              <label htmlFor="raise-to">Raise amount (chips):</label>
              <input
                id="raise-to"
                type="number"
                min={0}
                value={raiseAmount}
                onChange={(e) => setRaiseAmount(Number(e.target.value))}
                disabled={busy}
              />
            </div>
          </section>
        )}

        {!state.table.hand_in_progress && (
          <section className="cash-table__between-hands">
            <h3>Between hands</h3>
            <div className="cash-table__between-actions">
              <button
                type="button"
                disabled={busy || remainingTopUpRoom <= 0}
                onClick={() => handleTopUp(remainingTopUpRoom)}
                className="cash-table__top-up"
              >
                Top up to max (${(playerStack + remainingTopUpRoom).toLocaleString()})
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={handleLeave}
                className="cash-table__leave"
              >
                Leave table
              </button>
            </div>
          </section>
        )}

        {!awaitingHuman && state.table.hand_in_progress && (
          <div className="cash-table__waiting">
            <span className="cash-table__waiting-spinner" />
            Waiting for AI…
            <button
              type="button"
              className="cash-table__refresh"
              onClick={refresh}
              disabled={busy}
            >
              Refresh
            </button>
          </div>
        )}
      </div>
    </PageLayout>
  );
}
