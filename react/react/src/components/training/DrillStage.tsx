import type { ReactNode } from 'react';
import { PageLayout, MenuBar } from '../shared';
import './DrillStage.css';

// The shared shell every preflop drill renders into, so they all share one UX:
// a non-scrolling stage that fills the viewport under the MenuBar. The head
// (subtitle + settings) pins to the top, the swipe deck flexes in the middle,
// and the action bar pins to the bottom — no page scroll, so the card owns every
// gesture (including the upward "call" swipe). The result wash renders as an
// `overlay` on top of the whole stage.

interface DrillStageProps {
  title: string;
  onBack: () => void;
  subtitle: string;
  /** Settings popover (position / opponent / spot picker) — drill-specific. */
  settings?: ReactNode;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  /** When false (and not loading/error), `empty` renders instead of the deck. */
  ready: boolean;
  /** Shown in place of the deck when there's no spot to drill yet. */
  empty?: ReactNode;
  deck: ReactNode;
  stats: ReactNode;
  /** The action bar that mirrors the swipe. */
  control: ReactNode;
  /** The full-screen result wash, when a hand has been graded. */
  overlay?: ReactNode;
}

export function DrillStage({
  title,
  onBack,
  subtitle,
  settings,
  loading = false,
  error = null,
  onRetry,
  ready,
  empty,
  deck,
  stats,
  control,
  overlay,
}: DrillStageProps) {
  return (
    <>
      <MenuBar onBack={onBack} title={title} showUserInfo onMainMenu={onBack} />
      <PageLayout
        variant="fixed"
        glowColor="emerald"
        maxWidth="md"
        hasMenuBar
        className="drill-page"
      >
        <div className="drill-stage">
          <div className="drill-stage__head">
            <p className="swd-subtitle">{subtitle}</p>
            {settings}
          </div>

          {loading && <div className="swd-state">Dealing your spots…</div>}
          {error && (
            <div className="swd-state swd-error">
              <p>{error}</p>
              {onRetry && (
                <button className="swd-next" onClick={onRetry}>
                  Try again
                </button>
              )}
            </div>
          )}
          {!loading && !error && !ready && empty}

          {ready && (
            <>
              <div className="drill-stage__deck">{deck}</div>
              <div className="drill-stage__foot">
                <p className="swd-stats">{stats}</p>
                <div className="pf-control">{control}</div>
              </div>
            </>
          )}
        </div>
        {overlay}
      </PageLayout>
    </>
  );
}
