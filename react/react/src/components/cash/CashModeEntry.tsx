/**
 * Cash mode entry screen — pick stake + buy-in, sit at table.
 *
 * v1 minimal flow:
 *   1. Show stake ladder (5 buttons).
 *   2. After stake selected, show a buy-in slider/input bounded by
 *      min_buy_in / max_buy_in for that stake.
 *   3. "Sit at table" submits to /api/cash/start and navigates to
 *      /cash/table on success.
 *
 * The component fetches /api/cash/state on mount: if a session is
 * already active for this user, redirect straight to /cash/table.
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout, PageHeader } from '../shared';
import { startCashSession, getState } from './api';
import { SponsorModal } from './SponsorModal';
import { STAKES, type StakeLabel } from './types';
import './CashMode.css';

const BIG_BLIND_BY_STAKE: Record<StakeLabel, number> = {
  '$2': 2,
  '$10': 10,
  '$50': 50,
  '$200': 200,
  '$1000': 1000,
};

const MIN_BB = 40;
const MAX_BB = 100;

type StakeAvailability = 'affordable' | 'sponsor_eligible' | 'locked';

/**
 * Mirrors `cash_mode.stakes.is_sponsor_eligible` server-side rule:
 *   sponsor-eligible iff bankroll < this tier's min AND
 *                       (this is the lowest tier OR
 *                        bankroll >= previous tier's min).
 * Returning a tri-state lets the picker render three visual states.
 */
function stakeAvailability(stake: StakeLabel, bankroll: number): StakeAvailability {
  const thisMin = BIG_BLIND_BY_STAKE[stake] * MIN_BB;
  if (bankroll >= thisMin) return 'affordable';
  const tierIdx = STAKES.indexOf(stake);
  if (tierIdx === 0) return 'sponsor_eligible';
  const prevMin = BIG_BLIND_BY_STAKE[STAKES[tierIdx - 1]] * MIN_BB;
  return bankroll >= prevMin ? 'sponsor_eligible' : 'locked';
}

export function CashModeEntry() {
  const navigate = useNavigate();
  const [stake, setStake] = useState<StakeLabel | null>(null);
  const [buyIn, setBuyIn] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bankroll, setBankroll] = useState<number | null>(null);
  const [sponsorStake, setSponsorStake] = useState<StakeLabel | null>(null);

  // Check for an existing session on mount; redirect to the existing
  // tournament-style /game/:id page (cash sessions ride the same UI).
  // Always reads bankroll — it's at the top level of /api/cash/state
  // regardless of whether a session is active, so the picker can
  // grey out locked tiers from the start.
  useEffect(() => {
    (async () => {
      try {
        const response = await getState();
        if (response.state?.game_id) {
          navigate(`/game/${response.state.game_id}`, { replace: true });
          return;
        }
        setBankroll(response.bankroll);
      } catch {
        // /api/cash/state errored (auth, network) — fall through to
        // entry UI with bankroll unknown; stakes default to active.
      }
    })();
  }, [navigate]);

  // When stake changes, reset buy-in to min for that stake
  useEffect(() => {
    if (stake) {
      const bb = BIG_BLIND_BY_STAKE[stake];
      setBuyIn(bb * MIN_BB);
    }
  }, [stake]);

  const minBuyIn = stake ? BIG_BLIND_BY_STAKE[stake] * MIN_BB : 0;
  const maxBuyIn = stake ? BIG_BLIND_BY_STAKE[stake] * MAX_BB : 0;
  const canSubmit = stake !== null && buyIn >= minBuyIn && buyIn <= maxBuyIn && !busy;

  const handleSit = async () => {
    if (!canSubmit || !stake) return;
    setBusy(true);
    setError(null);
    try {
      const response = await startCashSession(stake, buyIn);
      // Navigate to the existing /game/:id page. The page connects
      // to the SocketIO room and renders from the cash session's
      // emissions exactly like a tournament game.
      navigate(`/game/${response.game_id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <PageLayout>
      <PageHeader
        title="Cash Game"
        onBack={() => navigate('/menu')}
        subtitle="Pick your stakes, sit at a table, play hands."
      />
      <div className="cash-entry">
        {bankroll !== null && (
          <div className="cash-entry__bankroll">
            <span className="cash-entry__bankroll-label">Your bankroll</span>
            <span className="cash-entry__bankroll-value">
              ${bankroll.toLocaleString()}
            </span>
          </div>
        )}

        <section className="cash-entry__stakes">
          <h2>Choose a stake</h2>
          <div className="cash-entry__stake-grid">
            {STAKES.map((s) => {
              const min = BIG_BLIND_BY_STAKE[s] * MIN_BB;
              // Treat unknown bankroll (still loading) as affordable so the
              // picker remains usable before /api/cash/state resolves.
              const availability =
                bankroll === null
                  ? 'affordable'
                  : stakeAvailability(s, bankroll);
              const locked = availability === 'locked';
              const sponsorOnly = availability === 'sponsor_eligible';
              const handleClick = () => {
                if (locked || busy) return;
                if (sponsorOnly) {
                  setSponsorStake(s);
                  return;
                }
                setStake(s);
              };
              return (
                <button
                  key={s}
                  type="button"
                  onClick={handleClick}
                  className={
                    `cash-entry__stake-button` +
                    (stake === s ? ' is-selected' : '') +
                    (locked ? ' is-disabled' : '') +
                    (sponsorOnly ? ' is-sponsor' : '')
                  }
                  disabled={busy || locked}
                  title={
                    locked
                      ? `Earn $${min.toLocaleString()} bankroll to unlock this tier`
                      : sponsorOnly
                        ? 'Sponsor required — tap to see offers'
                        : undefined
                  }
                >
                  <div className="cash-entry__stake-label">{s} table</div>
                  <div className="cash-entry__stake-meta">
                    BB ${BIG_BLIND_BY_STAKE[s]} · min ${min} · max ${BIG_BLIND_BY_STAKE[s] * MAX_BB}
                  </div>
                  {locked && (
                    <div className="cash-entry__stake-locked">
                      Locked — earn ${min.toLocaleString()}
                    </div>
                  )}
                  {sponsorOnly && (
                    <div className="cash-entry__stake-sponsor">
                      Sponsor required
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </section>

        {stake !== null && (
          <section className="cash-entry__buy-in">
            <h2>Buy in</h2>
            <p className="cash-entry__buy-in-help">
              Choose between ${minBuyIn.toLocaleString()} (min) and ${maxBuyIn.toLocaleString()} (max).
            </p>
            <input
              type="range"
              min={minBuyIn}
              max={maxBuyIn}
              step={BIG_BLIND_BY_STAKE[stake]}
              value={buyIn}
              onChange={(e) => setBuyIn(Number(e.target.value))}
              disabled={busy}
              className="cash-entry__buy-in-slider"
            />
            <div className="cash-entry__buy-in-value">
              ${buyIn.toLocaleString()} (
              {Math.round(buyIn / BIG_BLIND_BY_STAKE[stake])} BB
              )
            </div>
          </section>
        )}

        {error && (
          <div className="cash-entry__error" role="alert">{error}</div>
        )}

        <button
          type="button"
          onClick={handleSit}
          disabled={!canSubmit}
          className="cash-entry__sit-button"
        >
          {busy ? 'Seating…' : 'Sit at table'}
        </button>
      </div>
      <SponsorModal
        isOpen={sponsorStake !== null}
        stakeLabel={sponsorStake}
        onClose={() => setSponsorStake(null)}
      />
    </PageLayout>
  );
}
