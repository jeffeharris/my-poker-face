import { useEffect, useState } from 'react';
import { TrendingUp } from 'lucide-react';
import { PageLayout, PageHeader, MenuBar, BackButton } from '../shared';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import './PreflopLeaks.css';

interface PositionRow {
  position: string; // early | middle | late | blind
  decisions: number;
  vpip_pct: number;
  reference_vpip_pct: number;
  loose_plays: number;
}
interface ActionFreq {
  fold: number;
  call: number;
  raise: number;
}
// Recent-window rollup attached to each all-time leak (may be absent on
// older responses — code defensively against the trend chip + caption).
interface RecentLeak {
  n: number; // decisions for this spot within the recent window
  gap: number | null; // recent deviation gap, or null if insufficient
  status: 'watching' | 'confirmed' | null;
  trend: 'shrinking' | 'persistent' | 'worsening' | 'cleared' | 'insufficient';
}
interface Leak {
  scenario: string; // rfi | vs_open | vs_3bet
  position: string; // 6-max label (UTG/HJ/CO/BTN/SB/BB)
  hand: string; // canonical, or '' for a position aggregate
  kind: string; // 'limp' | 'too_loose' | 'over_fold' | 'too_passive'
  your_freq: ActionFreq;
  chart_freq: ActionFreq;
  gap: number;
  times_seen: number;
  status: string; // 'confirmed' | 'watching'
  recent?: RecentLeak; // recent-vs-all-time rollup (optional)
}
// Emerging leaks share the all-time leak shape minus the `recent` rollup.
type EmergingLeak = Omit<Leak, 'recent'>;
interface RecentWindow {
  unit: 'hands';
  n: number;
  decisions: number;
}
interface LeaksResponse {
  total_decisions: number;
  enough_data: boolean;
  min_for_signal: number;
  by_position: PositionRow[];
  leaks: Leak[];
  graded: number;
  eligible_groups: number;
  skipped: Record<string, number>;
  emerging?: EmergingLeak[]; // recent-only leaks not in the all-time list
  recent_window?: RecentWindow; // describes the recent window, if computed
}

const TREND_LABEL: Record<RecentLeak['trend'], string> = {
  shrinking: '↓ improving',
  persistent: 'no change',
  worsening: '↑ worse',
  cleared: '✓ cleared recently',
  insufficient: '— recent: too few',
};

const SCENARIO_PHRASE: Record<string, string> = {
  rfi: 'opening from',
  vs_open: 'facing a raise in',
  vs_3bet: 'facing a 3-bet in',
};

interface KindEffect {
  nudges: number;
  followed: number;
  follow_rate: number | null;
}
interface Effectiveness {
  by_kind: Record<string, KindEffect>;
  overall: KindEffect;
}
const KIND_LABEL: Record<string, string> = {
  limp: 'limping',
  too_loose: 'playing too loose',
  over_fold: 'over-folding',
  too_passive: 'playing too passively',
};

const pct = (x: number) => Math.round(x * 100);

// Plain-language description of one chart leak — mirrors the backend's
// _leak_line so the panel and the coach tell the same story.
function leakText(lk: Leak): string {
  const where = `${SCENARIO_PHRASE[lk.scenario] ?? lk.scenario} ${lk.position}`;
  const subject = lk.hand ? `${lk.hand} ${where}` : where;
  let detail: string;
  switch (lk.kind) {
    case 'limp':
      detail = `you open-limp ${pct(lk.your_freq.call)}% of the time — the solver raises or folds here, never limps`;
      break;
    case 'too_loose':
      detail = `you play it ${pct(lk.your_freq.call + lk.your_freq.raise)}%; the solver folds ${pct(lk.chart_freq.fold)}%`;
      break;
    case 'over_fold':
      detail = `you fold ${pct(lk.your_freq.fold)}%; the solver continues ${pct(lk.chart_freq.call + lk.chart_freq.raise)}%`;
      break;
    default: // too_passive
      detail = `you just call; the solver raises ${pct(lk.chart_freq.raise)}% of the time`;
  }
  return `${subject} — ${detail}`;
}

// Small inline chip summarising how the recent window compares to all-time.
// Falls back to the muted "too few" treatment when the rollup is missing.
function TrendChip({ recent }: { recent?: RecentLeak }) {
  const trend = recent?.trend ?? 'insufficient';
  const title =
    recent && recent.n > 0
      ? `recent: ${recent.n} of this spot's hands in the recent window vs all-time`
      : 'recent: too few hands to compare vs all-time';
  return (
    <span className={`pfl-trend pfl-trend--${trend}`} title={title}>
      {TREND_LABEL[trend]}
    </span>
  );
}

const POSITION_LABEL: Record<string, string> = {
  early: 'Early (UTG / MP)',
  middle: 'Middle (HJ)',
  late: 'Late (CO / BTN)',
  blind: 'Blinds (SB / BB)',
};

// Bars auto-scale to the player's own data (with ~15% headroom, rounded to 5)
// so they always fill the track with a bit of room left — a full-100 scale
// leaves real VPIPs (~6-35%) cramped, especially on mobile.
function computeScaleMax(rows: PositionRow[]): number {
  const dataMax = Math.max(1, ...rows.flatMap((r) => [r.vpip_pct, r.reference_vpip_pct]));
  return Math.min(100, Math.max(20, Math.ceil((dataMax * 1.15) / 5) * 5));
}

interface PreflopLeaksProps {
  onBack: () => void;
  onDrill: (scenario: string, position: string) => void;
}

export function PreflopLeaks({ onBack, onDrill }: PreflopLeaksProps) {
  const [data, setData] = useState<LeaksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [effect, setEffect] = useState<Effectiveness | null>(null);

  const askCoach = async () => {
    if (feedbackLoading) return;
    setFeedbackLoading(true);
    try {
      const resp = await fetch(`${config.API_URL}/api/coach/preflop-leaks/feedback`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      const json = await resp.json();
      setFeedback(resp.ok ? json.feedback : (json.error ?? 'The coach is unavailable right now.'));
    } catch (err) {
      logger.error('Failed to get coach feedback:', err);
      setFeedback('The coach is unavailable right now.');
    } finally {
      setFeedbackLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`${config.API_URL}/api/coach/preflop-leaks`, {
          credentials: 'include',
        });
        if (!resp.ok) throw new Error(`Leaks returned ${resp.status}`);
        const json = await resp.json();
        if (!cancelled) setData(json);
      } catch (err) {
        logger.error('Failed to load preflop leaks:', err);
        if (!cancelled) setError('Could not load your preflop review.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // How often you've taken the solver line after a coach nudge (best-effort).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`${config.API_URL}/api/coach/tip-effectiveness`, {
          credentials: 'include',
        });
        if (!resp.ok) return;
        const json = await resp.json();
        if (!cancelled) setEffect(json);
      } catch {
        /* non-fatal — the block just stays hidden */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const scaleMax = data ? computeScaleMax(data.by_position) : 50;

  return (
    <>
      <MenuBar showUserInfo />
      <PageLayout variant="top" glowColor="emerald" maxWidth="md" hasMenuBar>
        <BackButton onClick={onBack} />
        <PageHeader
          title="Your Preflop Game"
          subtitle="What you actually play, from your real hands"
          titleVariant="primary"
        />

        {loading && <div className="pfl-state">Reviewing your hands…</div>}
        {error && <div className="pfl-state pfl-error">{error}</div>}

        {data && !data.enough_data && (
          <div className="pfl-state">
            <TrendingUp size={28} />
            <p>
              Play a bit more and your preflop tendencies will show up here.
              <br />
              <strong>
                {data.total_decisions} / {data.min_for_signal}
              </strong>{' '}
              decisions analyzed so far.
            </p>
          </div>
        )}

        {data && data.enough_data && (
          <div className="pfl-body">
            <p className="pfl-intro">
              Across <strong>{data.total_decisions}</strong> preflop decisions. Your VPIP
              (how often you play a hand) is shown next to a standard opening range for
              orientation — it's context, not a grade (your number includes calls and
              blind defense).
            </p>

            <p className="pfl-scale-note">
              Bars scaled to {scaleMax}% · the line marks a standard opening frequency
            </p>
            <div className="pfl-positions">
              {data.by_position.map((row) => (
                <div key={row.position} className="pfl-pos">
                  <span className="pfl-pos-name">{POSITION_LABEL[row.position] ?? row.position}</span>
                  <span className="pfl-pos-bar-wrap">
                    <span
                      className="pfl-pos-bar"
                      style={{ width: `${Math.min(100, (row.vpip_pct / scaleMax) * 100)}%` }}
                    />
                    <span
                      className="pfl-pos-ref"
                      style={{ left: `${Math.min(100, (row.reference_vpip_pct / scaleMax) * 100)}%` }}
                      title={`standard opens ~${row.reference_vpip_pct}%`}
                    />
                  </span>
                  <span className="pfl-pos-vpip">
                    {row.vpip_pct}%
                    <span className="pfl-pos-ref-label">std ~{row.reference_vpip_pct}%</span>
                  </span>
                  <span className="pfl-pos-n">{row.decisions} hands</span>
                </div>
              ))}
            </div>

            <h3 className="pfl-leaks-head">Where your play diverges from the solver</h3>
            {data.leaks.length === 0 ? (
              <p className="pfl-clean">
                {data.eligible_groups > 0
                  ? "In the spots with enough volume, your play tracks the charts. Keep playing and we'll keep checking."
                  : 'Not enough repeated spots yet to call anything a leak — play more and patterns will surface.'}
              </p>
            ) : (
              <ul className="pfl-leaks">
                {data.leaks.map((lk) => {
                  const confirmed = lk.status === 'confirmed';
                  return (
                    <li
                      key={`${lk.scenario}-${lk.position}-${lk.hand}-${lk.kind}`}
                      className={`pfl-leak${confirmed ? '' : ' pfl-leak--watch'}`}
                    >
                      <span className="pfl-leak-detail">
                        {leakText(lk)} <em>(seen {lk.times_seen}×)</em>
                      </span>
                      <span className={`pfl-leak-badge${confirmed ? '' : ' watch'}`}>
                        {confirmed ? 'leak' : 'watching'}
                      </span>
                      <TrendChip recent={lk.recent} />
                      <button
                        type="button"
                        className="pfl-leak-drill"
                        onClick={() => onDrill(lk.scenario, lk.position)}
                      >
                        Drill
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}

            {data.emerging && data.emerging.length > 0 && (
              <div className="pfl-emerging">
                <h4 className="pfl-emerging-head">Showing up recently</h4>
                <ul className="pfl-leaks">
                  {data.emerging.map((lk) => (
                    <li
                      key={`emerging-${lk.scenario}-${lk.position}-${lk.hand}-${lk.kind}`}
                      className="pfl-leak pfl-leak--emerging"
                    >
                      <span className="pfl-leak-detail">
                        {leakText(lk)} <em>(seen {lk.times_seen}×)</em>
                      </span>
                      <span className="pfl-trend pfl-trend--emerging" title="new in the recent window">
                        ↑ new
                      </span>
                      <button
                        type="button"
                        className="pfl-leak-drill"
                        onClick={() => onDrill(lk.scenario, lk.position)}
                      >
                        Drill
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {data.recent_window && (
              <p className="pfl-note">
                Recent = your last {data.recent_window.n} hands ({data.recent_window.decisions}{' '}
                analyzed).
              </p>
            )}
            <p className="pfl-note pfl-note--tier">
              <strong>watching</strong> = small sample so far (could be variance);{' '}
              <strong>leak</strong> = seen enough to be sure. Keep playing and watch-items
              graduate (or clear).
            </p>
            <p className="pfl-note">
              Graded {data.graded} of your decisions against the same solver charts the bots
              play — measured by how often you take each action, not single hands.
              {data.skipped?.short_multiway
                ? ` ${data.skipped.short_multiway} short-stack multiway spots were skipped (no clean reference there).`
                : ''}{' '}
              This is the GTO baseline; deliberate adjustments against weak players will show
              up here as deviations.
            </p>

            {effect && effect.overall.nudges > 0 && (
              <div className="pfl-effect">
                <div className="pfl-effect-head">Coaching follow-through</div>
                <p className="pfl-effect-lead">
                  In spots the coach flagged, you took the solver line{' '}
                  <strong>
                    {effect.overall.followed}/{effect.overall.nudges}
                  </strong>{' '}
                  ({Math.round((effect.overall.follow_rate ?? 0) * 100)}%).
                </p>
                <ul className="pfl-effect-kinds">
                  {Object.entries(effect.by_kind).map(([kind, e]) => (
                    <li key={kind}>
                      {KIND_LABEL[kind] ?? kind}: {e.followed}/{e.nudges} (
                      {Math.round((e.follow_rate ?? 0) * 100)}%)
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <button
              type="button"
              className="pfl-ask"
              onClick={askCoach}
              disabled={feedbackLoading}
            >
              {feedbackLoading ? 'Coach is reviewing…' : 'Ask the coach about this'}
            </button>
            {feedback && (
              <div className="pfl-feedback">
                <span className="pfl-feedback-label">Coach</span>
                <p>{feedback}</p>
              </div>
            )}
          </div>
        )}
      </PageLayout>
    </>
  );
}
