/**
 * FileCabinetDrawer — "The Archive"
 *
 * The browsable index of every opponent you've scouted, dressed as a physical
 * case-file drawer in the same noir universe as CharacterDetailCard (aged
 * paper, gold-leaf rules, JetBrains Mono classification type, a wet-ink
 * CONFIDENTIAL stamp). Each opponent is a manila folder — colored tab (gold =
 * intel in progress, emerald = full dossier, crimson = rival), a stamped file
 * number, pages-declassified progress, and your lifetime take. Tap a folder to
 * pull the full dossier. Folders stagger in on open.
 *
 * Centered modal, portal-to-body (clears the fixed PageLayout header).
 * Refetches when `refreshTick` changes to stay in lockstep with the lobby.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Search } from 'lucide-react';
import { getFileCabinet } from './api';
import type { FileCabinetPerson } from './types';
import { logger } from '../../utils/logger';
import './FileCabinetDrawer.css';

type SortKey = 'most_played' | 'rivals' | 'winners' | 'losers' | 'recent' | 'progress';

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'most_played', label: 'Most studied' },
  { key: 'progress', label: 'Declassified' },
  { key: 'rivals', label: 'Rivals' },
  { key: 'winners', label: 'You beat' },
  { key: 'losers', label: 'Beat you' },
  { key: 'recent', label: 'Recent' },
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

/** Stable 4-digit case number from the personality id — flavor only, mirrors
 *  the dossier card's deriveFileNumber so the same subject reads consistently. */
function caseNumber(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return String(1000 + (h % 9000));
}

function FileFolder({
  person,
  index,
  onOpen,
}: {
  person: FileCabinetPerson;
  index: number;
  onOpen: (p: FileCabinetPerson) => void;
}) {
  const pct = Math.round((person.reads_unlocked / person.reads_total) * 100);
  const isRival = person.heat >= 0.5;
  const status = person.fully_unlocked
    ? 'complete'
    : person.floor_met
      ? 'partial'
      : 'classified';

  const statusLabel = person.fully_unlocked
    ? 'FILE COMPLETE'
    : person.floor_met
      ? `${person.reads_unlocked}/${person.reads_total} PAGES`
      : 'CLASSIFIED';

  const pnlText =
    person.net_pnl === 0
      ? 'EVEN'
      : `${person.net_pnl > 0 ? '+' : '−'}$${Math.abs(person.net_pnl).toLocaleString()}`;
  const pnlClass =
    person.net_pnl === 0 ? 'even' : person.net_pnl > 0 ? 'pos' : 'neg';

  return (
    <button
      type="button"
      className={`folder folder--${status}`}
      style={{ animationDelay: `${Math.min(index, 12) * 34}ms` }}
      onClick={() => onOpen(person)}
    >
      <span className="folder__tab" aria-hidden="true" />
      <div className="folder__body">
        <div className="folder__head">
          <span className="folder__no">№ {caseNumber(person.personality_id)}</span>
          {isRival && <span className="folder__rival">RIVAL</span>}
          <span className={`folder__pnl folder__pnl--${pnlClass}`}>{pnlText}</span>
        </div>
        <div className="folder__name">{person.name}</div>
        <div className="folder__meta">
          <span className="folder__hands">
            {person.hands_observed.toLocaleString()} hands studied
          </span>
          <span className={`folder__status folder__status--${status}`}>{statusLabel}</span>
        </div>
        <div className="folder__progress" aria-hidden="true">
          <span className="folder__progress-fill" style={{ width: `${pct}%` }} />
        </div>
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
  const [loaded, setLoaded] = useState(false);
  const [sort, setSort] = useState<SortKey>('most_played');
  const [search, setSearch] = useState('');

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
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (isOpen) load();
  }, [isOpen, refreshTick, load]);

  const sorted = useMemo(() => {
    const q = search.trim().toLowerCase();
    const matched = q
      ? people.filter((p) => p.name.toLowerCase().includes(q))
      : people;
    return sortPeople(matched, sort);
  }, [people, sort, search]);

  if (!isOpen) return null;

  return createPortal(
    <div className="archive-overlay" onClick={onClose}>
      <div className="archive-overlay__grain" aria-hidden="true" />
      <div
        className="archive"
        role="dialog"
        aria-label="Case file archive"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="archive__close"
          onClick={onClose}
          aria-label="Close"
        >
          ×
        </button>

        <div className="archive__stamp" aria-hidden="true">
          <span className="archive__stamp-inner">CONFIDENTIAL</span>
          <span className="archive__stamp-sub">EYES ONLY</span>
        </div>

        <header className="archive__header">
          <div className="archive__classification">
            <span className="archive__class-tag">ARCHIVE</span>
            <span className="archive__class-dot" aria-hidden="true" />
            <span>CASE FILES</span>
          </div>
          <h2 className="archive__title">The File Cabinet</h2>
          <div className="archive__census">
            <strong>{peopleMet}</strong> subject{peopleMet === 1 ? '' : 's'} on file
            <span className="archive__census-sep" aria-hidden="true">
              ◆
            </span>
            <strong>{dossiersUnlocked}</strong> dossier
            {dossiersUnlocked === 1 ? '' : 's'} complete
          </div>
        </header>

        {people.length > 0 && (
          <div className="archive__search">
            <Search size={13} aria-hidden="true" className="archive__search-icon" />
            <input
              type="text"
              className="archive__search-input"
              placeholder="Search the files by name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search && (
              <button
                type="button"
                className="archive__search-clear"
                onClick={() => setSearch('')}
                aria-label="Clear search"
              >
                ×
              </button>
            )}
          </div>
        )}

        {people.length > 0 && (
          <div className="archive__index">
            <span className="archive__index-label">FILED BY</span>
            <div className="archive__index-tabs">
              {SORTS.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  className={
                    'archive__index-tab' +
                    (sort === s.key ? ' archive__index-tab--active' : '')
                  }
                  onClick={() => setSort(s.key)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="archive__drawer">
          {sorted.map((p, i) => (
            <FileFolder
              key={p.personality_id}
              person={p}
              index={i}
              onOpen={onOpenDossier}
            />
          ))}
          {!loaded && loading && (
            <p className="archive__no-match">Pulling files…</p>
          )}
          {loaded && people.length === 0 && (
            <div className="archive__empty">
              <div className="archive__empty-stamp" aria-hidden="true">
                NO FILES
              </div>
              <p>
                The cabinet is empty. Sit down at the Circuit — every hand you
                share builds a file on the table.
              </p>
            </div>
          )}
          {people.length > 0 && sorted.length === 0 && (
            <p className="archive__no-match">
              No files matching “{search.trim()}”.
            </p>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
