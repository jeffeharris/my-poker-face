import { Users } from 'lucide-react';
import type { Player } from '../../types';
import { config } from '../../config';
import { useDisplayNickname } from '../../stores/nicknameOverridesStore';

interface ChatTargetSelectorProps {
  /** AI players to render as targets. Callers should pre-filter (e.g. drop humans). */
  aiPlayers: Player[];
  /** Selected target: `null` = nothing picked, `'table'` = broadcast, or a player name. */
  selectedTarget: string | null;
  onTargetSelect: (target: string) => void;
  label?: string;
}

/**
 * "Who?" target picker shared between Quick Chat (tone-based) and the
 * free-text tab. Styled by the existing `.target-selector` / `.target-btn`
 * rules in `QuickChatSuggestions.css` so it inherits the mobile-tuned
 * overrides scoped to `.mcs-quick-chat-wrapper` automatically.
 */
export function ChatTargetSelector({
  aiPlayers,
  selectedTarget,
  onTargetSelect,
  label = 'Who?',
}: ChatTargetSelectorProps) {
  const displayNickname = useDisplayNickname();

  return (
    <div className="target-selector">
      <div className="selector-label">{label}</div>
      <div className="target-options">
        <button
          className={`target-btn target-btn-table ${selectedTarget === 'table' ? 'selected' : ''}`}
          onClick={() => onTargetSelect('table')}
          title="Talk to the table"
        >
          <Users size={22} style={{ opacity: 0.85 }} />
          <span className="target-name">Table</span>
        </button>
        {aiPlayers.map((player) => {
          // Backend already URL-encodes the personality segment of
          // `avatar_url`; only the fallback (built from raw `player.name`)
          // needs encoding here.
          const path =
            typeof player.avatar_url === 'string' && player.avatar_url.length > 0
              ? player.avatar_url
              : `/api/avatar/${encodeURIComponent(player.name)}/confident/full`;
          const avatarUrl = `${config.API_URL}${path}`;
          const isFolded = !!player.is_folded;
          const nickname = displayNickname(player);
          return (
            <button
              key={player.name}
              className={`target-btn target-btn-player ${selectedTarget === player.name ? 'selected' : ''} ${isFolded ? 'folded' : ''} has-bg-image`}
              onClick={() => onTargetSelect(player.name)}
              title={isFolded ? `Talk to ${nickname} (folded)` : `Talk to ${nickname}`}
              style={{ backgroundImage: `url(${avatarUrl})` }}
            >
              <span className="target-name">{nickname}</span>
              {isFolded && (
                <span className="target-folded-badge" aria-hidden="true">
                  folded
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
