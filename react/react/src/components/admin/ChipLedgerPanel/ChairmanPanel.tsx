import { useEffect, useRef, useState } from 'react';
import type { ChairmanResponse, ChairmanBand, ChairmanLevers } from './types';
import { fmt } from './ledgerUtils';

// The economy-chairman (Director thermostat) status card. Read-only projection of
// `core.economy.economy_signal`: which stage the bank is in, the full ladder of
// stages with their policies, and how long the current policy is locked in.

const REGIME_LABEL: Record<string, string> = {
  flush: 'Flush',
  neutral: 'Neutral',
  empty: 'Empty',
};

function pct(n: number): string {
  return `${(n * 100).toFixed(n < 0.1 ? 1 : 0)}%`;
}

function rakeSummary(l: ChairmanLevers): string {
  return `$${l.rake.tiers.join(' / $')} @ ${(l.rake.rate * 100).toFixed(0)}%`;
}

function mmss(total: number): string {
  if (total <= 0) return '0:00';
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

// The held rake schedule isn't on a wall clock — it's recomputed opportunistically
// on the next lobby refresh AFTER its window lapses (cash_mode/lobby.py), not at a
// fixed tick. So rather than a countdown that dies at 0:00 (which reads as broken on
// an idle table), describe the three real states: counting down inside the window,
// past the window and due, or never cached yet (idle sandbox).
function rakeHoldLabel(
  hold: { hold_enabled: boolean; window_seconds: number },
  remaining: number | null
): string {
  if (!hold.hold_enabled) {
    return `off — recomputed live every hand (${hold.window_seconds}s window when on)`;
  }
  if (remaining === null) {
    return `idle — recomputes when the lobby next ticks (${hold.window_seconds}s window)`;
  }
  if (remaining > 0) {
    return `held · unlocks in ${mmss(remaining)}`;
  }
  return 'due — refreshes on the next lobby activity';
}

// Live countdown that ticks down each second from the last-fetched value, so the
// "policy locked for…" readout moves between the 60s panel polls instead of
// jumping. Re-seeds whenever the backend value changes.
function useCountdown(seconds: number | null): number | null {
  const [remaining, setRemaining] = useState(seconds);
  const baseRef = useRef<{ at: number; value: number } | null>(null);

  useEffect(() => {
    if (seconds === null) {
      baseRef.current = null;
      setRemaining(null);
      return;
    }
    baseRef.current = { at: Date.now(), value: seconds };
    setRemaining(seconds);
    const id = setInterval(() => {
      const base = baseRef.current;
      if (!base) return;
      const elapsed = Math.floor((Date.now() - base.at) / 1000);
      setRemaining(Math.max(0, base.value - elapsed));
    }, 1000);
    return () => clearInterval(id);
  }, [seconds]);

  return remaining;
}

function ViceState({ multiplier }: { multiplier: number }) {
  if (multiplier >= 0.999) return <>Full refill ({pct(multiplier)})</>;
  if (multiplier <= 0.001) return <>Off — braking</>;
  return <>Tapering — {pct(multiplier)}</>;
}

function LeverList({ levers }: { levers: ChairmanLevers }) {
  return (
    <ul className="chairman-levers">
      <li>
        <span className="chairman-lever__name">Rake</span>
        <span className="chairman-lever__val">{rakeSummary(levers)}</span>
      </li>
      <li>
        <span className="chairman-lever__name">Vice refill</span>
        <span className="chairman-lever__val">
          <ViceState multiplier={levers.vice_multiplier} />
        </span>
      </li>
      <li>
        <span className="chairman-lever__name">Main Event</span>
        <span className="chairman-lever__val">
          {levers.tournament_armed ? 'Armed — fires on cooldown' : 'Held'}
        </span>
      </li>
    </ul>
  );
}

// A horizontal reserve-ratio gauge with the three band edges (+ the vice ceiling)
// marked, and a "you are here" needle at the live ratio.
function RatioGauge({ chairman }: { chairman: ChairmanResponse }) {
  const { signal, thresholds } = chairman;
  // Domain runs a little past the vice ceiling so the trigger band has room and a
  // hot bank's needle stays on-scale.
  const domainMax = Math.max(thresholds.vice_ceiling * 1.15, signal.ratio * 1.1, 0.01);
  const at = (r: number) => `${Math.min(100, (r / domainMax) * 100)}%`;

  const marks: { r: number; label: string; cls: string }[] = [
    { r: thresholds.critical, label: 'crit', cls: 'critical' },
    { r: thresholds.healthy, label: 'healthy', cls: 'low' },
    { r: thresholds.trigger, label: 'trigger', cls: 'climbing' },
    { r: thresholds.vice_ceiling, label: 'vice off', cls: 'trigger' },
  ];

  return (
    <div className="chairman-gauge">
      <div className="chairman-gauge__track">
        {/* Coloured band segments */}
        <span className="chairman-gauge__seg critical" style={{ width: at(thresholds.critical) }} />
        <span
          className="chairman-gauge__seg low"
          style={{
            left: at(thresholds.critical),
            width: `${((thresholds.healthy - thresholds.critical) / domainMax) * 100}%`,
          }}
        />
        <span
          className="chairman-gauge__seg climbing"
          style={{
            left: at(thresholds.healthy),
            width: `${((thresholds.trigger - thresholds.healthy) / domainMax) * 100}%`,
          }}
        />
        <span
          className="chairman-gauge__seg trigger"
          style={{ left: at(thresholds.trigger), right: 0 }}
        />
        {/* Threshold ticks */}
        {marks.map((m) => (
          <span key={m.label} className="chairman-gauge__mark" style={{ left: at(m.r) }}>
            <span className="chairman-gauge__mark-label">{m.label}</span>
          </span>
        ))}
        {/* Live needle */}
        <span className="chairman-gauge__needle" style={{ left: at(signal.ratio) }} />
      </div>
      <div className="chairman-gauge__scale">
        <span>0%</span>
        <span>{pct(domainMax)}</span>
      </div>
    </div>
  );
}

export function ChairmanPanel({ chairman }: { chairman: ChairmanResponse | null }) {
  const remaining = useCountdown(chairman?.policy_lock.seconds_remaining ?? null);

  if (!chairman) return null;
  const { signal, current_band, bands, whale, policy_lock } = chairman;
  const cold = current_band === null;

  return (
    <section className="chip-ledger-card chairman-panel">
      <div className="chairman-header">
        <h3>Economy chairman</h3>
        {cold ? (
          <span className="chairman-stage chairman-stage--cold">Cold — no chips in play</span>
        ) : (
          <span className={`chairman-stage chairman-stage--${current_band}`}>
            {bands.find((b) => b.key === current_band)?.label}
          </span>
        )}
        <span className="chairman-regime" title="The FLUSH/NEUTRAL/EMPTY overlay regime (EXP_006).">
          regime: {REGIME_LABEL[signal.regime] ?? signal.regime}
        </span>
      </div>

      <p className="chairman-caveat">
        The Director reads one signal — <code>reserves / holdings</code> — and steers four levers
        off it (rake, vice refill, side hustle, the Main Event). The stage below is where that ratio
        sits on the reserve ladder; each stage dictates a different policy.
      </p>

      <div className="chairman-signal">
        <div>
          <span className="chairman-signal__num">{fmt(signal.reserves)}</span>
          <span className="chairman-signal__label">Reserves</span>
        </div>
        <div className="chairman-signal__op">÷</div>
        <div>
          <span className="chairman-signal__num">{fmt(signal.holdings)}</span>
          <span className="chairman-signal__label">Holdings</span>
        </div>
        <div className="chairman-signal__op">=</div>
        <div>
          <span className="chairman-signal__num chairman-signal__ratio">{pct(signal.ratio)}</span>
          <span className="chairman-signal__label">Reserve ratio</span>
        </div>
      </div>

      {!cold && <RatioGauge chairman={chairman} />}

      <h4 className="chairman-ladder__title">Stages</h4>
      <div className="chairman-ladder">
        {bands.map((b: ChairmanBand) => {
          const lo = pct(b.ratio_min);
          const hi = b.ratio_max === null ? '∞' : pct(b.ratio_max);
          const active = b.key === current_band;
          return (
            <div
              key={b.key}
              className={`chairman-band chairman-band--${b.key}${active ? ' is-active' : ''}`}
            >
              <div className="chairman-band__head">
                <span className="chairman-band__label">{b.label}</span>
                <span className="chairman-band__range">
                  {lo} – {hi}
                </span>
                {active && <span className="chairman-band__here">◀ now</span>}
              </div>
              <p className="chairman-band__blurb">{b.blurb}</p>
              <LeverList levers={b.levers} />
            </div>
          );
        })}
      </div>

      <h4 className="chairman-ladder__title">Whale</h4>
      <div className="chairman-whale">
        <div className="chairman-whale__head">
          <span className="chairman-whale__lead">
            One pool-funded "rich fish" at a time — a reserves → field distribution.
          </span>
          <span
            className={'chairman-whale__gate' + (whale.gated ? ' is-on' : '')}
            title={
              whale.gated
                ? 'Whale spawn/recall is wired to the chairman (WHALE_RESERVE_GATED on).'
                : 'Advisory: the live system still uses the legacy absolute pool watermarks. This shows what the chairman would decide if WHALE_RESERVE_GATED were on.'
            }
          >
            {whale.gated ? 'chairman-gated' : 'advisory (legacy watermarks live)'}
          </span>
        </div>
        <ul className="chairman-whale__stakes">
          {whale.stakes.map((s) => (
            <li key={s.stake} className={s.can_fund ? 'can-fund' : 'cant-fund'}>
              <span className="chairman-whale__stake">{s.stake} whale</span>
              <span className="chairman-whale__verdict">
                {cold
                  ? '—'
                  : s.can_fund
                    ? `fundable (${fmt(s.prefund_cost)} draw)`
                    : `can't fund (needs ${fmt(s.prefund_cost)} + healthy floor)`}
              </span>
            </li>
          ))}
        </ul>
        {whale.recall_now && !cold && (
          <p className="chairman-whale__recall">
            ⚠ Reserves in the critical band — a live whale would be recalled (residual returned to
            the pool).
          </p>
        )}
      </div>

      <div className="chairman-lock">
        <h4>Policy lock</h4>
        <div className="chairman-lock__grid">
          <div className="chairman-lock__item">
            <span className="chairman-lock__label">Rake schedule hold</span>
            <span className="chairman-lock__val">{rakeHoldLabel(policy_lock, remaining)}</span>
          </div>
          <div className="chairman-lock__item">
            <span className="chairman-lock__label">Main Event cooldown</span>
            <span className="chairman-lock__val">
              {mmss(policy_lock.tournament_cooldown_seconds)} between offers
            </span>
          </div>
          <div className="chairman-lock__item">
            <span className="chairman-lock__label">Registration window</span>
            <span className="chairman-lock__val">
              {mmss(policy_lock.registration_window_seconds)} before an offer auto-expires
            </span>
          </div>
        </div>
        <p className="chairman-caveat">
          The Director steers slowly: the rake schedule is held for a window rather than re-derived
          every hand, and a Main Event can't re-fire until its cooldown elapses (a successful event
          also drains reserves below the trigger, so it won't re-arm until vice refills the pool).
        </p>
      </div>
    </section>
  );
}
