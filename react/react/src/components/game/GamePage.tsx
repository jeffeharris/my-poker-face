import { useParams, useNavigate } from 'react-router-dom';
import { ResponsiveGameLayout } from '../shared';

interface GamePageProps {
  playerName: string;
}

export function GamePage({ playerName }: GamePageProps) {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();

  // Back button = pause. The cash session stays alive in
  // game_state_service for its TTL window; the player can return by
  // visiting /cash (which auto-redirects if an active session
  // exists). To actually cash out — return chips to bankroll, free
  // the table to sit at a different stake — use the "Leave table"
  // button in the cash HUD/sheet, which hits /api/cash/leave.
  const handleBack = () => {
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
