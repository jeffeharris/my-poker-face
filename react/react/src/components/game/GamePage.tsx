import { useParams, useNavigate } from 'react-router-dom';
import { ResponsiveGameLayout } from '../shared';

interface GamePageProps {
  playerName: string;
}

export function GamePage({ playerName }: GamePageProps) {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();

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
