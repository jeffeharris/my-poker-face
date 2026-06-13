import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SlidersHorizontal, Shuffle } from 'lucide-react';
import { ActionButtons } from '../game/ActionButtons';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { SwipeDeck, type SwipeDir } from './swipe/SwipeDeck';
import { PreflopCardFace } from './preflop/PreflopCard';
import { RFI_POS, type Grade, type Spot } from './preflop/preflopUtils';
import { DrillStage } from './DrillStage';
import { DrillResultOverlay } from './DrillResultOverlay';
import { useDrillRunner } from './useDrillRunner';

// Read drill: "Would a <archetype> open this?" — predict a player type's RFI
// decision (raise/fold), graded against that archetype's own width-tier chart.
// Two filter axes (opponent type, seat) each default to a mix. Built on the
// shared drill shell.

const ARCHETYPES: { key: string; label: string }[] = [
  { key: 'nit', label: 'A nit' },
  { key: 'tag', label: 'A TAG' },
  { key: 'lag', label: 'A LAG' },
  { key: 'maniac', label: 'A maniac' },
];
const ARCH_LABEL: Record<string, string> = Object.fromEntries(
  ARCHETYPES.map((a) => [a.key, a.label])
);

type ArchMode = 'mix' | string;
type PosMode = 'mix' | (typeof RFI_POS)[number];

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

// What the read drill calls each verdict (it grades a prediction, not your play).
const READ_HEADING: Record<Grade['verdict'], string> = {
  good: 'Right read.',
  thin: 'Close — they mix this.',
  leak: 'Off — they rarely do that.',
};

const dirToAction = (dir: SwipeDir) => (dir === 'right' ? 'raise' : 'fold');
const actionToDir = (action: string): SwipeDir => (action === 'fold' ? 'left' : 'right');
const answerExtra = (spot: Spot) => ({ archetype: spot.archetype });

interface ReadDrillProps {
  onBack: () => void;
}

export function ReadDrill({ onBack }: ReadDrillProps) {
  const [params, setSearchParams] = useSearchParams();
  const paramArch = params.get('archetype') || '';
  const paramPos = params.get('position') || '';
  const [archMode, setArchMode] = useState<ArchMode>(ARCH_LABEL[paramArch] ? paramArch : 'mix');
  const [posMode, setPosMode] = useState<PosMode>(RFI_POS.includes(paramPos) ? paramPos : 'mix');
  const [showSettings, setShowSettings] = useState(false);

  const [pool, setPool] = useState<Spot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Keep both axes in the URL so a leak nudge can deep-link a specific read.
  const syncParams = (arch: ArchMode, pos: PosMode) => {
    const next: Record<string, string> = {};
    if (arch !== 'mix') next.archetype = arch;
    if (pos !== 'mix') next.position = pos;
    setSearchParams(next, { replace: true });
  };
  const pickArch = (m: ArchMode) => {
    setArchMode(m);
    syncParams(m, posMode);
  };
  const pickPos = (p: PosMode) => {
    setPosMode(p);
    syncParams(archMode, p);
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setPool([]);

    // Position 'mix' fans out server-side (one call per archetype), so the worst
    // case is 4 requests — not 4×5, which bursts the limiter.
    const fetchSpots = async (archetype: string): Promise<Spot[]> => {
      const resp = await fetch(
        `${config.API_URL}/api/coach/drill?scenario=rfi&position=${posMode}&archetype=${archetype}`,
        { credentials: 'include' }
      );
      if (!resp.ok) throw new Error(`drill ${resp.status}`);
      const data = await resp.json();
      return (data.spots ?? []) as Spot[];
    };

    try {
      const archetypes = archMode === 'mix' ? ARCHETYPES.map((a) => a.key) : [archMode];
      const results = await Promise.allSettled(archetypes.map(fetchSpots));
      const spots = results.flatMap((r) => (r.status === 'fulfilled' ? r.value : []));
      if (!spots.length) throw new Error('no spots');
      setPool(spots);
    } catch (err) {
      logger.error('Failed to load read drill:', err);
      setError('Could not load the drill.');
    } finally {
      setLoading(false);
    }
  }, [archMode, posMode]);

  useEffect(() => {
    load();
  }, [load]);

  const runner = useDrillRunner(pool, { dirToAction, actionToDir, answerExtra });
  const {
    grade,
    chosenDir,
    draw,
    deckRef,
    interactive,
    onSwipe,
    next,
    onBarAction,
    solid,
    answered,
  } = runner;

  const ready = !loading && !error && pool.length > 0;
  const archLabel = archMode === 'mix' ? 'Mixed reads' : (ARCH_LABEL[archMode] ?? archMode);
  const posLabel = posMode === 'mix' ? 'all seats' : posMode;
  const allMixed = archMode === 'mix' && posMode === 'mix';

  return (
    <DrillStage
      title="Read the Player"
      onBack={onBack}
      subtitle="Would they open this — or fold?"
      settings={
        <div className="swd-settings">
          <button
            type="button"
            className="swd-settings-toggle"
            onClick={() => setShowSettings((v) => !v)}
            aria-expanded={showSettings}
          >
            {allMixed ? <Shuffle size={14} /> : <SlidersHorizontal size={14} />}
            {archLabel} · {posLabel}
          </button>
          {showSettings && (
            <>
              <div className="swd-filter-group">
                <span className="swd-filter-label">Opponent</span>
                <div className="swd-pos-chips" role="radiogroup" aria-label="Opponent type">
                  {(['mix', ...ARCHETYPES.map((a) => a.key)] as ArchMode[]).map((m) => (
                    <button
                      key={m}
                      type="button"
                      role="radio"
                      aria-checked={archMode === m}
                      aria-label={m === 'mix' ? 'Mixed' : ARCH_LABEL[m]}
                      className={
                        'swd-pos-chip' +
                        (m === 'mix' ? ' swd-pos-chip--shuffle' : '') +
                        (archMode === m ? ' swd-pos-chip--active' : '')
                      }
                      onClick={() => pickArch(m)}
                    >
                      {m === 'mix' ? (
                        <Shuffle size={15} />
                      ) : (
                        (ARCH_LABEL[m] ?? m).replace(/^A /, '')
                      )}
                    </button>
                  ))}
                </div>
              </div>

              <div className="swd-filter-group">
                <span className="swd-filter-label">Position</span>
                <div className="swd-pos-chips" role="radiogroup" aria-label="Position">
                  {(['mix', ...RFI_POS] as PosMode[]).map((m) => (
                    <button
                      key={m}
                      type="button"
                      role="radio"
                      aria-checked={posMode === m}
                      aria-label={m === 'mix' ? 'All seats' : m}
                      className={
                        'swd-pos-chip' +
                        (m === 'mix' ? ' swd-pos-chip--shuffle' : '') +
                        (posMode === m ? ' swd-pos-chip--active' : '')
                      }
                      onClick={() => pickPos(m)}
                    >
                      {m === 'mix' ? <Shuffle size={15} /> : m}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      }
      loading={loading}
      error={error}
      onRetry={load}
      ready={ready}
      deck={
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
      }
      stats={`${solid}/${answered} read · swipe or use ← →`}
      control={<ActionButtons {...READ_BETTING} onAction={onBarAction} inline noSizing />}
      overlay={
        grade && (
          <DrillResultOverlay
            verdict={grade.verdict}
            heading={READ_HEADING[grade.verdict]}
            chosen={chosenDir}
            freqs={[
              { dir: 'left', label: 'Folds', value: grade.chart_freq.fold },
              { dir: 'right', label: 'Opens', value: grade.chart_freq.raise },
            ]}
            onDone={next}
          />
        )
      }
    />
  );
}
