import { useParams, useNavigate } from 'react-router-dom';
import { useEffect } from 'react';
import { ResponsiveGameLayout } from '../shared';

interface GamePageProps {
  playerName: string;
}

export function GamePage({ playerName }: GamePageProps) {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();

  // Save the active game ID to localStorage for session restoration
  useEffect(() => {
    if (gameId) {
      localStorage.setItem('activePokerGameId', gameId);
    }
    return () => {
      // Clear when leaving the game page
      localStorage.removeItem('activePokerGameId');
    };
  }, [gameId]);

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
