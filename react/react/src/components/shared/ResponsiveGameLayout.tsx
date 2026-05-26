import { useViewport } from '../../hooks/useViewport';
import { ArrivalWelcome } from '../cash/ArrivalWelcome';
import { PokerTable } from '../game/PokerTable';
import { MobilePokerTable } from '../mobile';

export interface ResponsiveGameLayoutProps {
  gameId?: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
  onBack?: () => void;
  onGameLoadFailed?: () => void;
}

/**
 * Responsive wrapper that renders the appropriate game layout
 * based on viewport size (mobile vs desktop).
 */
export function ResponsiveGameLayout({
  gameId,
  playerName,
  onGameCreated,
  onBack,
  onGameLoadFailed,
}: ResponsiveGameLayoutProps) {
  const { isMobile } = useViewport();

  const table = isMobile ? (
    <MobilePokerTable
      gameId={gameId}
      playerName={playerName}
      onGameCreated={onGameCreated}
      onBack={onBack}
      onGameLoadFailed={onGameLoadFailed}
    />
  ) : (
    <PokerTable
      gameId={gameId}
      playerName={playerName}
      onGameCreated={onGameCreated}
      onBack={onBack}
      onGameLoadFailed={onGameLoadFailed}
    />
  );

  return (
    <>
      {/* Cash-mode "you walked into a room" card on sit-down. Portaled
          overlay; reads the seated table from the store; no-op for
          tournaments. Rendered alongside whichever layout shows. */}
      <ArrivalWelcome />
      {table}
    </>
  );
}
