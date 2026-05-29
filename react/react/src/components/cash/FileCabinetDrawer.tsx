/**
 * FileCabinetDrawer — "the file cabinet"
 *
 * Browse everyone you've accumulated scouting on. The collection/retention
 * surface of the dossier meta-game: a row per opponent with their
 * dossier-unlock progress, your lifetime PnL vs them, and a rivalry flag,
 * tappable to pull the full dossier. Sortable (most-played, rivals, biggest
 * winners/losers vs you, recently seen, dossier progress).
 *
 * Centered modal, portal-to-body (mirrors WhereaboutsDrawer / NetWorthDrawer
 * so it clears the fixed PageLayout header). Refetches when `refreshTick`
 * changes to stay in lockstep with the lobby.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, FolderOpen, Flame, Lock, CheckCircle2 } from 'lucide-react';
import { getFileCabinet } from './api';
import type { FileCabinetPerson } from './types';
import { logger } from '../../utils/logger';
import './FileCabinetDrawer.css';

type SortKey = 'most_played' | 'rivals' | 'winners' | 'losers' | 'recent' | 'progress';

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'most_played', label: 'Most played' },
  { key: 'progress', label: 'Dossier progress' },
  { key: 'rivals', label: 'Rivals' },
  { key: 'winners', label: 'You beat' },
  { key: 'losers', label: 'Beat you' },
  { key: 'recent', label: 'Recently seen' },
];

function sortPeople(people: FileCabinetPerson[], key: SortKey): FileCabinetPerson[] {
  const c = [...people];
  switch (key) {
    case 'rivals':
      return c.sort((a, b) => b.heat - a.heat);
    case 'winners':
      return c.sort((a, b) => b.net_pnl - a.net_pnl);
    case 'losers':
      return c.sort((a, b) => a.net_pnl - b.net_pnl);
    case 'recent':
      return c.sort((a, b) => (b.last_seen ?? '').localeCompare(a.last_seen ?? ''));
    case 'progress':
      return c.sort(
        (a, b) => b.reads_unlocked / b.reads_total - a.reads_unlocked / a.reads_total
      );
    case 'most_played':
    default:
      return c.sort((a, b) => b.hands_observed - a.hands_observed);
  }
}

function PnlChip({ value }: { value: number }) {
  if (value === 0) {
    return <span className="filecab-row__pnl filecab-row__pnl--even">even</span>;
  }
  const up = value > 0;
  return (
    <span className={`filecab-row__pnl filecab-row__pnl--${up ? 'pos' : 'neg'}`}>
      {up ? '+' : '−'}${Math.abs(value).toLocaleString()}
    </span>
  );
}

function CabinetRow({
  person,
  onOpen,
}: {
  person: FileCabinetPerson;
  onOpen: (p: FileCabinetPerson) => void;
}) {
  const pct = Math.round((person.reads_unlocked / person.reads_total) * 100);
  const isRival = person.heat >= 0.5;
  return (
    <button type="button" className="filecab-row" onClick={() => onOpen(person)}>
      <div className="filecab-row__head">
        <span className="filecab-row__name">{person.name}</span>
        {isRival && (
          <span className="filecab-row__rival" title="rivalry">
            <Flame size={12} aria-hidden="true" /> rival
          </span>
        )}
        <PnlChip value={person.net_pnl} />
      </div>
      <div className="filecab-row__meta">
        <span className="filecab-row__hands">
          {person.hands_observed.toLocaleString()} hands observed
        </span>
        <span className="filecab-row__dossier">
          {person.fully_unlocked ? (
            <>
              <CheckCircle2 size={12} aria-hidden="true" /> Full dossier
            </>
          ) : person.floor_met ? (
            `Dossier ${person.reads_unlocked}/${person.reads_total}`
          ) : (
            <>
              <Lock size={11} aria-hidden="true" /> Classified
            </>
          )}
        </span>
      </div>
      <div className="filecab-row__bar" aria-hidden="true">
        <span
          className={
            'filecab-row__bar-fill' +
            (person.fully_unlocked ? ' filecab-row__bar-fill--full' : '')
          }
          style={{ width: `${pct}%` }}
        />
      </div>
    </button>
  );
}

interface FileCabinetDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  /** Open a dossier for the tapped opponent. */
  onOpenDossier: (person: FileCabinetPerson) => void;
  /** Bumped by the parent on every lobby refresh so this stays in sync. */
  refreshTick?: number;
}

export function FileCabinetDrawer({
  isOpen,
  onClose,
  onOpenDossier,
  refreshTick,
}: FileCabinetDrawerProps) {
  const [people, setPeople] = useState<FileCabinetPerson[]>([]);
  const [peopleMet, setPeopleMet] = useState(0);
  const [dossiersUnlocked, setDossiersUnlocked] = useState(0);
  const [loading, setLoading] = useState(false);
  const [sort, setSort] = useState<SortKey>('most_played');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getFileCabinet();
      setPeople(data.people);
      setPeopleMet(data.people_met);
      setDossiersUnlocked(data.dossiers_unlocked);
    } catch (e) {
      logger.error('[filecabinet] fetch failed', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) load();
  }, [isOpen, refreshTick, load]);

  const sorted = useMemo(() => sortPeople(people, sort), [people, sort]);

  if (!isOpen) return null;

  return createPortal(
    <div className="filecab-overlay" onClick={onClose}>
      <div
        className="filecab-drawer"
        role="dialog"
        aria-label="File cabinet"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="filecab-drawer__header">
          <div className="filecab-drawer__title">
            <FolderOpen size={16} aria-hidden="true" />
            <span>The File Cabinet</span>
          </div>
          <button
            type="button"
            className="filecab-drawer__close"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </header>

        <div className="filecab-drawer__stats">
          People met: <strong>{peopleMet}</strong> · Dossiers unlocked:{' '}
          <strong>{dossiersUnlocked}</strong>
        </div>

        {people.length > 0 && (
          <div className="filecab-drawer__sorts">
            {SORTS.map((s) => (
              <button
                key={s.key}
                type="button"
                className={
                  'filecab-drawer__sort' +
                  (sort === s.key ? ' filecab-drawer__sort--active' : '')
                }
                onClick={() => setSort(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
        )}

        <div className="filecab-drawer__list">
          {sorted.map((p) => (
            <CabinetRow key={p.personality_id} person={p} onOpen={onOpenDossier} />
          ))}
          {!loading && people.length === 0 && (
            <p className="filecab-drawer__empty">
              No files yet. Play a few hands at the Circuit and your dossiers will
              start filling in.
            </p>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
