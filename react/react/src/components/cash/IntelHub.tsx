/**
 * IntelHub — "The Field Office"
 *
 * One home for everything you'd want to know about the room and its players,
 * folded into a single case-file surface (the dossier/archive aesthetic):
 *
 *   • Dispatches  — the live wire of what's happening now (the activity feed)
 *   • Whereabouts — where the players you've met are right now
 *   • Case Files  — your dossiers on everyone you've scouted
 *
 * Replaces the scattered lobby intel surfaces (inline ticker dropdown +
 * separate whereabouts / cabinet drawers) with one tabbed war-room panel.
 * Portal-to-body, archive paper aesthetic shared with CharacterDetailCard.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Radio, MapPin, FolderOpen } from 'lucide-react';
import { getWhereabouts } from './api';
import { dedupeFeed, feedEventKey, renderEventIcon } from './tickerEvents';
import { FileCabinetPanel } from './FileCabinetPanel';
import type { FileCabinetPerson, LobbyEvent, WhereaboutsPerson } from './types';
import { logger } from '../../utils/logger';
import './FileCabinetDrawer.css';

type IntelTab = 'dispatches' | 'whereabouts' | 'files';

const TABS: { key: IntelTab; label: string; Icon: typeof Radio }[] = [
  { key: 'dispatches', label: 'The Wire', Icon: Radio },
  { key: 'whereabouts', label: 'Whereabouts', Icon: MapPin },
  { key: 'files', label: 'Case Files', Icon: FolderOpen },
];

/* ── Dispatches: the live wire, as a typed log ────────────────────── */
function DispatchesPanel({ events }: { events: LobbyEvent[] }) {
  const visible = useMemo(() => dedupeFeed(events), [events]);
  return (
    <div className="intel-wire">
      {visible.length === 0 && (
        <p className="archive__no-match">The wire is quiet. Waiting for the next move…</p>
      )}
      {visible.map((e) => (
        <div key={feedEventKey(e)} className={`intel-wire__line intel-wire__line--${e.type}`}>
          <span className="intel-wire__glyph">{renderEventIcon(e.type)}</span>
          <span className="intel-wire__msg">{e.message}</span>
        </div>
      ))}
    </div>
  );
}

/* ── Whereabouts: where the met players are right now ─────────────── */
const STATUS_TAG: Record<string, string> = {
  seated: 'AT A TABLE',
  idle: 'ON A BREAK',
  side_hustle: 'ON A HUSTLE',
  vice: 'INDULGING',
  unknown: 'OFF GRID',
};

function whereaboutsDetail(p: WhereaboutsPerson): string {
  switch (p.status) {
    case 'seated':
      return p.table_name
        ? `${p.table_name}${p.stake_label ? ` · ${p.stake_label}` : ''}`
        : 'Seated at another table';
    case 'side_hustle':
      return p.narration || 'Off earning a side hustle';
    case 'vice':
      return p.narration || 'Out indulging a vice';
    case 'idle':
      return p.narration || 'Resting in the idle pool';
    default:
      return 'Whereabouts unknown';
  }
}

function WhereaboutsPanel() {
  const [people, setPeople] = useState<WhereaboutsPerson[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getWhereabouts();
        if (!cancelled) setPeople(data.people);
      } catch (e) {
        logger.error('[intel] whereabouts fetch failed', e);
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!loaded) return <p className="archive__no-match">Locating the field…</p>;
  if (people.length === 0) {
    return (
      <p className="archive__no-match">
        No one you've met is off-table right now. Check back after a few hands.
      </p>
    );
  }

  return (
    <div className="intel-field">
      {people.map((p) => {
        const pnlClass = p.net_pnl === 0 ? 'even' : p.net_pnl > 0 ? 'pos' : 'neg';
        const pnlText =
          p.net_pnl === 0
            ? ''
            : `${p.net_pnl > 0 ? '+' : '−'}$${Math.abs(p.net_pnl).toLocaleString()}`;
        return (
          <div key={p.personality_id} className={`intel-field__row intel-field__row--${p.status}`}>
            <span className="intel-field__dot" aria-hidden="true" />
            <div className="intel-field__body">
              <div className="intel-field__head">
                <span className="intel-field__name">{p.name}</span>
                <span className="intel-field__tag">{STATUS_TAG[p.status] ?? 'OFF GRID'}</span>
                {pnlText && (
                  <span className={`intel-field__pnl intel-field__pnl--${pnlClass}`}>
                    {pnlText}
                  </span>
                )}
              </div>
              <div className="intel-field__detail">{whereaboutsDetail(p)}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

interface IntelHubProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenDossier: (person: FileCabinetPerson) => void;
  events: LobbyEvent[];
  refreshTick?: number;
  /** Which tab to open on. Defaults to Dispatches. */
  initialTab?: IntelTab;
}

export function IntelHub({
  isOpen,
  onClose,
  onOpenDossier,
  events,
  refreshTick,
  initialTab = 'dispatches',
}: IntelHubProps) {
  const [tab, setTab] = useState<IntelTab>(initialTab);

  // Re-arm the requested tab each time the hub is opened.
  useEffect(() => {
    if (isOpen) setTab(initialTab);
  }, [isOpen, initialTab]);

  const onOpenDossierAndClose = useCallback(
    (p: FileCabinetPerson) => {
      onOpenDossier(p);
    },
    [onOpenDossier]
  );

  if (!isOpen) return null;

  return createPortal(
    <div className="archive-overlay" onClick={onClose}>
      <div className="archive-overlay__grain" aria-hidden="true" />
      <div
        className="archive intel-hub"
        role="dialog"
        aria-label="Intel — the field office"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="archive__close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <div className="archive__stamp" aria-hidden="true">
          <span className="archive__stamp-inner">CONFIDENTIAL</span>
          <span className="archive__stamp-sub">EYES ONLY</span>
        </div>

        <header className="archive__header">
          <div className="archive__classification">
            <span className="archive__class-tag">INTEL</span>
            <span className="archive__class-dot" aria-hidden="true" />
            <span>FIELD OFFICE</span>
          </div>
          <h2 className="archive__title">Intelligence</h2>
        </header>

        <nav className="intel-hub__tabs" role="tablist">
          {TABS.map(({ key, label, Icon }) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={'intel-hub__tab' + (tab === key ? ' intel-hub__tab--active' : '')}
              onClick={() => setTab(key)}
            >
              <Icon size={13} aria-hidden="true" />
              {label}
            </button>
          ))}
        </nav>

        <div className="intel-hub__panel">
          {tab === 'dispatches' && <DispatchesPanel events={events} />}
          {tab === 'whereabouts' && <WhereaboutsPanel />}
          {tab === 'files' && (
            <FileCabinetPanel onOpenDossier={onOpenDossierAndClose} refreshTick={refreshTick} />
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
