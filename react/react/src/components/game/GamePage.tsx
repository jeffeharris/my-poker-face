import { useParams, useNavigate } from 'react-router-dom';
import { ResponsiveGameLayout } from '../shared';
import { config } from '../../config';
import { logger } from '../../utils/logger';

interface GamePageProps {
  playerName: string;
}

export function GamePage({ playerName }: GamePageProps) {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();

  // Cash sessions: leave the table (return chips to bankroll, tear
  // down the session) before navigating. Tournament games leave the
  // game running for Continue Games — same as before. Handled at
  // this level so both mobile and desktop pick it up via the shared
  // ResponsiveGameLayout onBack prop.
  const handleBack = async () => {
    if (gameId?.startsWith('cash-')) {
      try {
        await fetch(`${config.API_URL}/api/cash/leave`, {
          method: 'POST',
          credentials: 'include',
        });
      } catch (e) {
        logger.error('Cash leave failed:', e);
      }
    }
    navigate('/menu');
  };

  const handleGameCreated = (newGameId: string) => {
    navigate(`/game/${newGameId}`, { replace: true });
  };

  return (
    <ResponsiveGameLayout
      gameId={gameId || null}
      playerName={playerName}
      onGameCreated={handleGameCreated}
      onBack={handleBack}
    />
  );
}
