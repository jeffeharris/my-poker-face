/**
 * FileCabinetPanel — the case-file roster, as an embeddable panel.
 *
 * The body of the old FileCabinetDrawer (search, sort, manila folders, census)
 * with the overlay/portal stripped so it can live inside the Intel hub as a
 * tab. Self-fetches; tap a folder to pull the dossier.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { getFileCabinet } from './api';
import type { FileCabinetPerson } from './types';
import { logger } from '../../utils/logger';

type SortKey = 'name' | 'most_played' | 'rivals' | 'winners' | 'losers' | 'recent' | 'progress';

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'name', label: 'Name' },
  { key: 'most_played', label: 'Most studied' },
  { key: 'progress', label: 'Declassified' },
  { key: 'rivals', label: 'Rivals' },
  { key: 'winners', label: 'You beat' },
  { key: 'losers', label: 'Beat you' },
  { key: 'recent', label: 'Recent' },
];

/** Natural order for each sort (click again to reverse it). */
function sortPeople(people: FileCabinetPerson[], key: SortKey): FileCabinetPerson[] {
  const c = [...people];
  switch (key) {
    case 'name':
      return c.sort((a, b) => a.name.localeCompare(b.name));
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

/** Stable 4-digit case number from the personality id — flavor only. */
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
  const pnlClass = person.net_pnl === 0 ? 'even' : person.net_pnl > 0 ? 'pos' : 'neg';

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

export function FileCabinetPanel({
  onOpenDossier,
  refreshTick,
}: {
  onOpenDossier: (person: FileCabinetPerson) => void;
  refreshTick?: number;
}) {
  const [people, setPeople] = useState<FileCabinetPerson[]>([]);
  const [peopleMet, setPeopleMet] = useState(0);
  const [dossiersUnlocked, setDossiersUnlocked] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // Sort key + direction kept as ONE piece of state so toggling is a single
  // pure update. (Nesting setReversed() inside a setSort() updater made the
  // toggle a side effect, which StrictMode double-invokes — flipping reversed
  // twice and cancelling it out, so reverse silently did nothing.)
  const [sortState, setSortState] = useState<{ key: SortKey; reversed: boolean }>({
    key: 'most_played',
    reversed: false,
  });
  const { key: sort, reversed } = sortState;
  const [search, setSearch] = useState('');

  // Click a sort to select it; click the active one again to reverse it.
  const chooseSort = useCallback((key: SortKey) => {
    setSortState((cur) =>
      cur.key === key ? { key, reversed: !cur.reversed } : { key, reversed: false }
    );
  }, []);

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
    load();
  }, [refreshTick, load]);

  const sorted = useMemo(() => {
    const q = search.trim().toLowerCase();
    const matched = q ? people.filter((p) => p.name.toLowerCase().includes(q)) : people;
    const ordered = sortPeople(matched, sort);
    return reversed ? ordered.reverse() : ordered;
  }, [people, sort, search, reversed]);

  return (
    <>
      <div className="archive__census">
        <strong>{peopleMet}</strong> subject{peopleMet === 1 ? '' : 's'} on file
        <span className="archive__census-sep" aria-hidden="true">
          ◆
        </span>
        <strong>{dossiersUnlocked}</strong> dossier{dossiersUnlocked === 1 ? '' : 's'} complete
      </div>

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
                onClick={() => chooseSort(s.key)}
                title={sort === s.key ? 'Click to reverse' : undefined}
              >
                {s.label}
                {sort === s.key && (
                  <span className="archive__index-caret" aria-hidden="true">
                    {reversed ? ' ▴' : ' ▾'}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="archive__drawer">
        {sorted.map((p, i) => (
          <FileFolder key={p.personality_id} person={p} index={i} onOpen={onOpenDossier} />
        ))}
        {!loaded && loading && <p className="archive__no-match">Pulling files…</p>}
        {loaded && people.length === 0 && (
          <div className="archive__empty">
            <div className="archive__empty-stamp" aria-hidden="true">
              NO FILES
            </div>
            <p>
              The cabinet is empty. Sit down at the Circuit — every hand you share
              builds a file on the table.
            </p>
          </div>
        )}
        {people.length > 0 && sorted.length === 0 && (
          <p className="archive__no-match">No files matching “{search.trim()}”.</p>
        )}
      </div>
    </>
  );
}
