import { useState } from 'react';
import type { HoldingsRow, SortKey, SortDir } from './types';
import { fmt, signed, compareRows, NET_WORTH_KEYS, STRING_KEYS } from './ledgerUtils';

interface HoldingsTableProps {
  rows: HoldingsRow[] | null;
  // True when a sandbox is selected → show the net-worth columns.
  // False ("All sandboxes") → chips-only, since net worth needs a sandbox.
  scoped: boolean;
  highlightedEntity: string | null;
  onSelectEntity: (entityId: string | null) => void;
}

interface SortableHeaderProps {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  currentDir: SortDir;
  align?: 'left' | 'right';
  onSort: (key: SortKey) => void;
}

function SortableHeader({
  label,
  sortKey,
  currentKey,
  currentDir,
  align,
  onSort,
}: SortableHeaderProps) {
  const isActive = currentKey === sortKey;
  const arrow = isActive ? (currentDir === 'asc' ? '▲' : '▼') : '';
  return (
    <th
      className={`sortable ${align === 'right' ? 'amount-h' : ''} ${isActive ? 'active' : ''}`}
      onClick={() => onSort(sortKey)}
    >
      <span className="label">{label}</span>
      <span className="arrow">{arrow}</span>
    </th>
  );
}

export function HoldingsTable({
  rows,
  scoped,
  highlightedEntity,
  onSelectEntity,
}: HoldingsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('net_worth');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      // Numeric columns default to desc (largest first); strings to asc.
      setSortDir(STRING_KEYS.has(key) ? 'asc' : 'desc');
    }
  };

  if (rows === null) {
    return <p className="chip-ledger-empty">Loading holdings…</p>;
  }
  if (rows.length === 0) {
    return <p className="chip-ledger-empty">No bankroll rows in scope.</p>;
  }

  // In the unscoped view the net-worth columns are absent, so fall back to
  // chips for the active sort if a net-worth column was selected.
  const effectiveSortKey: SortKey = !scoped && NET_WORTH_KEYS.has(sortKey) ? 'chips' : sortKey;
  const sortedRows = [...rows].sort((a, b) => compareRows(a, b, effectiveSortKey, sortDir));

  return (
    <div className="chip-ledger-holdings-table-wrap">
      {!scoped && (
        <p className="chip-ledger-holdings-scope-note">
          Select a sandbox to see net worth, stakes, vice, and side-hustle — stakes are global per
          entity, so they aren't shown cross-sandbox.
        </p>
      )}
      <table>
        <thead>
          <tr>
            <SortableHeader
              label="Player"
              sortKey="name"
              currentKey={sortKey}
              currentDir={sortDir}
              onSort={handleSort}
            />
            <SortableHeader
              label="Kind"
              sortKey="kind"
              currentKey={sortKey}
              currentDir={sortDir}
              onSort={handleSort}
            />
            {scoped && (
              <SortableHeader
                label="Net worth"
                sortKey="net_worth"
                currentKey={sortKey}
                currentDir={sortDir}
                align="right"
                onSort={handleSort}
              />
            )}
            <SortableHeader
              label="Chips"
              sortKey="chips"
              currentKey={sortKey}
              currentDir={sortDir}
              align="right"
              onSort={handleSort}
            />
            {scoped && (
              <>
                <SortableHeader
                  label="Recv"
                  sortKey="receivable"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Owed"
                  sortKey="outstanding"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Staking"
                  sortKey="staking_pnl"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Vice"
                  sortKey="vice_spent"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Side hustle"
                  sortKey="side_hustle_earned"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Rake"
                  sortKey="rake_paid"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
              </>
            )}
            <SortableHeader
              label="Sandbox"
              sortKey="sandbox_id"
              currentKey={sortKey}
              currentDir={sortDir}
              onSort={handleSort}
            />
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row) => {
            const isHighlighted = highlightedEntity === row.entity_id;
            const netWorth = row.net_worth ?? 0;
            return (
              <tr
                key={`${row.entity_id}@${row.sandbox_id ?? ''}`}
                className={isHighlighted ? 'highlighted' : ''}
                onClick={() => onSelectEntity(isHighlighted ? null : row.entity_id)}
              >
                <td>{row.name}</td>
                <td>{row.kind === 'ai' ? 'AI' : 'Human'}</td>
                {scoped && (
                  <td className={`amount ${netWorth > 0 ? 'pos' : netWorth < 0 ? 'neg' : ''}`}>
                    {fmt(netWorth)}
                  </td>
                )}
                <td
                  className="amount"
                  title={
                    row.seat_chips
                      ? `${fmt(row.projected_chips)} bankroll + ${fmt(row.seat_chips)} in play`
                      : undefined
                  }
                >
                  {fmt(row.chips)}
                </td>
                {scoped && (
                  <>
                    <td className="amount pos">{row.receivable ? fmt(row.receivable) : '—'}</td>
                    <td className="amount neg">{row.outstanding ? fmt(row.outstanding) : '—'}</td>
                    <td
                      className={`amount ${(row.staking_pnl ?? 0) > 0 ? 'pos' : (row.staking_pnl ?? 0) < 0 ? 'neg' : ''}`}
                    >
                      {row.staking_pnl ? signed(row.staking_pnl) : '—'}
                    </td>
                    <td className="amount neg">{row.vice_spent ? fmt(row.vice_spent) : '—'}</td>
                    <td className="amount pos">
                      {row.side_hustle_earned ? fmt(row.side_hustle_earned) : '—'}
                    </td>
                    <td className="amount neg">{row.rake_paid ? fmt(row.rake_paid) : '—'}</td>
                  </>
                )}
                <td className="sandbox-cell">
                  {row.sandbox_id ? row.sandbox_id.slice(0, 8) : '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
