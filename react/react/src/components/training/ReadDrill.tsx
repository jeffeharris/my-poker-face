import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SlidersHorizontal, Shuffle } from 'lucide-react';
import { PageLayout, MenuBar } from '../shared';
import { ActionButtons } from '../game/ActionButtons';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { SwipeDeck, type SwipeDeckHandle, type SwipeDir } from './swipe/SwipeDeck';
import { PreflopCardFace, drawNext, pct, type Spot, type Grade } from './preflop/PreflopCard';

// Read drill: "Would a <archetype> open this?" — predict a player type's RFI
// decision (raise/fold), graded against that archetype's own width-tier chart.
// Fully chart-faithful: the archetype chart IS that player's strategy.

const ARCHETYPES: { key: string; label: string }[] = [
  { key: 'nit', label: 'A nit' },
  { key: 'tag', label: 'A TAG' },
  { key: 'lag', label: 'A LAG' },
  { key: 'maniac', label: 'A maniac' },
];
const ARCH_LABEL: Record<string, string> = Object.fromEntries(
  ARCHETYPES.map((a) => [a.key, a.label])
);
// Late positions, where opening ranges diverge most by archetype (early seats are
// tight for everyone, so the read is least interesting there).
const READ_POS = ['CO', 'BTN'];

type Mode = 'mix' | string;

const HOLD_MS: Record<Grade['verdict'], number> = { good: 700, thin: 1050, leak: 1800 };

// "Would they open" is a fold/raise read — drive the game bar (no call, no sizing).
const BIG_BLIND = 100;
const READ_BETTING = {
  playerOptions: ['fold', 'raise'],
  currentPlayerStack: 100 * BIG_BLIND,
  highestBet: BIG_BLIND,
  currentPlayerBet: 0,
  minRaise: BIG_BLIND,
  bigBlind: BIG_BLIND,
  potSize: BIG_BLIND + BIG_BLIND / 2,
};

interface ReadDrillProps {
  onBack: () => void;
}

export function ReadDrill({ onBack }: ReadDrillProps) {
  const [params, setSearchParams] = useSearchParams();
  const paramArch = params.get('archetype') || '';
  const [mode, setMode] = useState<Mode>(ARCH_LABEL[paramArch] ? paramArch : 'mix');
  const [showSettings, setShowSettings] = useState(false);

  const [pool, setPool] = useState<Spot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [grade, setGrade] = useState<Grade | null>(null);
  const [grading, setGrading] = useState(false);
  const [solid, setSolid] = useState(0);
  const [answered, setAnswered] = useState(0);

  const deckRef = useRef<SwipeDeckHandle>(null);

  const pickMode = (m: Mode) => {
    setMode(m);
    setShowSettings(false);
    setSearchParams(m === 'mix' ? {} : { archetype: m }, { replace: true });
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setPool([]);
    setGrade(null);
    setSolid(0);
    setAnswered(0);

    const fetchSpots = async (position: string, archetype: string): Promise<Spot[]> => {
      const resp = await fetch(
        `${config.API_URL}/api/coach/drill?scenario=rfi&position=${position}&archetype=${archetype}`,
        { credentials: 'include' }
      );
      if (!resp.ok) throw new Error(`drill ${resp.status}`);
      const data = await resp.json();
      return (data.spots ?? []) as Spot[];
    };

    try {
      const archetypes = mode === 'mix' ? ARCHETYPES.map((a) => a.key) : [mode];
      const combos = archetypes.flatMap((a) => READ_POS.map((p) => ({ p, a })));
      const results = await Promise.allSettled(combos.map(({ p, a }) => fetchSpots(p, a)));
      const spots = results.flatMap((r) => (r.status === 'fulfilled' ? r.value : []));
      if (!spots.length) throw new Error('no spots');
      setPool(spots);
    } catch (err) {
      logger.error('Failed to load read drill:', err);
      setError('Could not load the drill.');
    } finally {
      setLoading(false);
    }
  }, [mode]);

  useEffect(() => {
    load();
  }, [load]);

  const draw = useCallback((avoid: Spot | null) => drawNext(pool, avoid), [pool]);

  const onSwipe = useCallback(async (spot: Spot, dir: SwipeDir) => {
    const action = dir === 'right' ? 'raise' : 'fold';
    setGrading(true);
    try {
      const resp = await fetch(`${config.API_URL}/api/coach/drill/answer`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario: spot.scenario,
          position: spot.position,
          hand: spot.hand,
          action,
          archetype: spot.archetype,
        }),
      });
      if (!resp.ok) throw new Error(`grade ${resp.status}`);
      const g: Grade = await resp.json();
      setGrade(g);
      setAnswered((n) => n + 1);
      if (g.verdict === 'good') setSolid((n) => n + 1);
    } catch (err) {
      logger.error('Failed to grade answer:', err);
    } finally {
      setGrading(false);
    }
  }, []);

  const interactive = !grade && !grading;

  const next = useCallback(() => {
    setGrade(null);
    deckRef.current?.advance();
  }, []);

  useEffect(() => {
    if (!grade) return;
    const t = setTimeout(next, HOLD_MS[grade.verdict]);
    return () => clearTimeout(t);
  }, [grade, next]);

  const onBarAction = useCallback(
    (action: string) => {
      if (grade || grading) return;
      deckRef.current?.swipe(action === 'fold' ? 'left' : 'right');
    },
    [grade, grading]
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (grade || grading) return;
      if (e.key === 'ArrowRight') deckRef.current?.swipe('right');
      if (e.key === 'ArrowLeft') deckRef.current?.swipe('left');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [grade, grading]);

  const ready = !loading && !error && pool.length > 0;
  const modeLabel = mode === 'mix' ? 'Mixed reads' : `${ARCH_LABEL[mode] ?? mode} only`;

  return (
    <>
      <MenuBar onBack={onBack} title="Read the Player" showUserInfo onMainMenu={onBack} />
      <PageLayout variant="top" glowColor="emerald" maxWidth="md" hasMenuBar>
        <p className="swd-subtitle">Would they open this — or fold?</p>

        <div className="swd-settings">
          <button
            type="button"
            className="swd-settings-toggle"
            onClick={() => setShowSettings((v) => !v)}
            aria-expanded={showSettings}
          >
            {mode === 'mix' ? <Shuffle size={14} /> : <SlidersHorizontal size={14} />}
            {modeLabel}
          </button>
          {showSettings && (
            <div className="swd-pos-chips" role="radiogroup" aria-label="Opponent type">
              {(['mix', ...ARCHETYPES.map((a) => a.key)] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={mode === m}
                  aria-label={m === 'mix' ? 'Mixed' : ARCH_LABEL[m]}
                  className={
                    'swd-pos-chip' +
                    (m === 'mix' ? ' swd-pos-chip--shuffle' : '') +
                    (mode === m ? ' swd-pos-chip--active' : '')
                  }
                  onClick={() => pickMode(m)}
                >
                  {m === 'mix' ? <Shuffle size={15} /> : (ARCH_LABEL[m] ?? m).replace(/^A /, '')}
                </button>
              ))}
            </div>
          )}
        </div>

        {loading && <div className="swd-state">Dealing your spots…</div>}
        {error && (
          <div className="swd-state swd-error">
            <p>{error}</p>
            <button className="swd-next" onClick={load}>
              Try again
            </button>
          </div>
        )}

        {ready && (
          <div className="swd-body">
            <SwipeDeck<Spot>
              ref={deckRef}
              draw={draw}
              renderFace={(spot) => (
                <PreflopCardFace
                  spot={spot}
                  headline={ARCH_LABEL[spot.archetype ?? ''] ?? 'A player'}
                  tag="Folded to them"
                />
              )}
              onSwipe={onSwipe}
              interactive={interactive}
              stamps={{ left: 'FOLDS', right: 'OPENS' }}
            />

            <p className="swd-stats">
              {solid}/{answered} read · swipe or use ← →
            </p>

            <div className="pf-control">
              {grade ? (
                <button
                  type="button"
                  className={`swd-feedback swd-feedback--${grade.verdict}`}
                  onClick={next}
                  aria-label="Continue to next hand"
                >
                  <div className="swd-verdict">
                    {grade.verdict === 'good' && 'Right read.'}
                    {grade.verdict === 'thin' && 'Close — they mix this.'}
                    {grade.verdict === 'leak' && 'Off — they rarely do that.'}
                  </div>
                  <div className="swd-freqs">
                    they open {pct(grade.chart_freq.raise)}% · fold {pct(grade.chart_freq.fold)}%
                  </div>
                </button>
              ) : (
                <ActionButtons {...READ_BETTING} onAction={onBarAction} inline noSizing />
              )}
            </div>
          </div>
        )}
      </PageLayout>
    </>
  );
}
