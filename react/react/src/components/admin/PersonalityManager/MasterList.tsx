import { useMemo } from 'react';
import { SearchIcon, CheckIcon, PlusIcon } from './icons';

interface MasterListProps {
  characters: string[];
  groups: { label: string; names: string[] }[];
  selected: string | null;
  onSelect: (name: string) => void;
  onCreate: () => void;
  search: string;
  onSearchChange: (search: string) => void;
  personalityMeta?: Record<string, { visibility?: string; owner_id?: string }>;
}

export function MasterList({
  characters,
  groups,
  selected,
  onSelect,
  onCreate,
  search,
  onSearchChange,
  personalityMeta,
}: MasterListProps) {
  const searchLower = search.toLowerCase();

  const filteredGroups = useMemo(
    () =>
      groups
        .map((g) => ({
          ...g,
          names: g.names.filter((name) => name.toLowerCase().includes(searchLower)),
        }))
        .filter((g) => g.names.length > 0),
    [groups, searchLower]
  );

  const totalFiltered = filteredGroups.reduce((sum, g) => sum + g.names.length, 0);

  return (
    <>
      <div className="admin-master__header">
        <h3 className="admin-master__title">Characters</h3>
        <span className="admin-master__count">{characters.length}</span>
      </div>
      <div className="admin-master__search">
        <div className="admin-master__search-wrap">
          <span className="admin-master__search-icon">
            <SearchIcon />
          </span>
          <input
            type="text"
            className="admin-master__search-input"
            placeholder="Search..."
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
          />
        </div>
      </div>
      <div className="admin-master__list">
        {filteredGroups.map((group) => (
          <div key={group.label}>
            {groups.length > 1 && <div className="admin-master__section-header">{group.label}</div>}
            {group.names.map((name) => {
              const vis = personalityMeta?.[name]?.visibility;
              return (
                <button
                  key={name}
                  type="button"
                  className={`admin-master__item ${selected === name ? 'admin-master__item--selected' : ''}`}
                  onClick={() => onSelect(name)}
                >
                  <span className="admin-master__item-avatar">{name.charAt(0)}</span>
                  <span className="admin-master__item-name">{name}</span>
                  {vis && vis !== 'public' && (
                    <span className={`pm-visibility-badge pm-visibility-badge--${vis}`} title={vis}>
                      {vis === 'private' ? '🔒' : '⊘'}
                    </span>
                  )}
                  {selected === name && (
                    <span className="admin-master__item-check">
                      <CheckIcon />
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
        {totalFiltered === 0 && (
          <div className="admin-master__empty">
            No characters found{search ? ` matching "${search}"` : ''}
          </div>
        )}
      </div>
      <div className="admin-master__footer">
        <button type="button" className="admin-master__create" onClick={onCreate}>
          <PlusIcon />
          New Character
        </button>
      </div>
    </>
  );
}
