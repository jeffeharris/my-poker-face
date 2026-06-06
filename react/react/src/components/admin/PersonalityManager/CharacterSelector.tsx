import { useState } from 'react';
import { BottomSheet } from '../../shared';
import { SearchIcon, CheckIcon, PlusIcon } from './icons';

interface CharacterSelectorProps {
  characters: string[];
  groups: { label: string; names: string[] }[];
  selected: string | null;
  onSelect: (name: string) => void;
  onCreate: () => void;
  isOpen: boolean;
  onClose: () => void;
  personalityMeta?: Record<string, { visibility?: string; owner_id?: string }>;
}

/**
 * Mobile character picker. Rebuilt on the shared <BottomSheet> primitive
 * (drag handle, header, backdrop, body-scroll, portal-to-body) — only the
 * inner search/list/create surface is bespoke.
 */
export function CharacterSelector({
  characters,
  groups,
  selected,
  onSelect,
  onCreate,
  isOpen,
  onClose,
  personalityMeta,
}: CharacterSelectorProps) {
  const [search, setSearch] = useState('');
  const searchLower = search.toLowerCase();

  const filteredGroups = groups
    .map((g) => ({
      ...g,
      names: g.names.filter((name) => name.toLowerCase().includes(searchLower)),
    }))
    .filter((g) => g.names.length > 0);

  const totalFiltered = filteredGroups.reduce((sum, g) => sum + g.names.length, 0);
  let itemIndex = 0;

  const handleSelect = (name: string) => {
    onSelect(name);
    onClose();
  };

  return (
    <BottomSheet
      isOpen={isOpen}
      onClose={onClose}
      title={
        <>
          Select Character
          <span className="pm-sheet__count">{characters.length} personalities</span>
        </>
      }
    >
      <div className="pm-sheet__search">
        <span className="pm-sheet__search-icon">
          <SearchIcon />
        </span>
        <input
          type="text"
          className="pm-sheet__search-input"
          placeholder="Search characters..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="pm-sheet__list">
        {filteredGroups.map((group) => (
          <div key={group.label}>
            {groups.length > 1 && <div className="admin-master__section-header">{group.label}</div>}
            {group.names.map((name) => {
              const vis = personalityMeta?.[name]?.visibility;
              const idx = itemIndex++;
              return (
                <button
                  key={name}
                  type="button"
                  className={`pm-sheet__item ${selected === name ? 'pm-sheet__item--active' : ''}`}
                  onClick={() => handleSelect(name)}
                  style={{ animationDelay: `${idx * 20}ms` }}
                >
                  <span className="pm-sheet__item-avatar">{name.charAt(0)}</span>
                  <span className="pm-sheet__item-name">{name}</span>
                  {vis && vis !== 'public' && (
                    <span className={`pm-visibility-badge pm-visibility-badge--${vis}`}>
                      {vis === 'private' ? '🔒' : '⊘'}
                    </span>
                  )}
                  {selected === name && (
                    <span className="pm-sheet__item-check">
                      <CheckIcon />
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
        {totalFiltered === 0 && (
          <div className="pm-sheet__empty">No characters found matching &quot;{search}&quot;</div>
        )}
      </div>
      <button type="button" className="pm-sheet__create" onClick={onCreate}>
        <PlusIcon />
        Create New Character
      </button>
    </BottomSheet>
  );
}
