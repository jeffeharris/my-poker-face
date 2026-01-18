import { useViewport } from '../../hooks/useViewport';
import { PokerTable } from '../game/PokerTable';
import { MobilePokerTable } from '../mobile';

export interface ResponsiveGameLayoutProps {
  gameId?: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
  onBack?: () => void;
}

/**
 * Responsive wrapper that renders the appropriate game layout
 * based on viewport size (mobile vs desktop).
 */
export function ResponsiveGameLayout({
  gameId,
  playerName,
  onGameCreated,
  onBack
}: ResponsiveGameLayoutProps) {
  const { isMobile } = useViewport();

  if (isMobile) {
    return (
      <MobilePokerTable
        gameId={gameId}
        playerName={playerName}
        onGameCreated={onGameCreated}
        onBack={onBack}
      />
    );
  }

  return (
    <PokerTable
      gameId={gameId}
      playerName={playerName}
      onGameCreated={onGameCreated}
    />
  );
}
