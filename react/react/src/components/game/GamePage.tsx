import { useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { ResponsiveGameLayout } from '../shared';
import { isCashGameId, isTrainingGameId } from '../../utils/gameId';

interface GamePageProps {
  playerName: string;
}

export function GamePage({ playerName }: GamePageProps) {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();

  // Back button = pause, routed to the menu the game was launched from:
  // career games return to the cash lobby, tournaments to the tournament
  // menu. The cash session stays alive in game_state_service for its TTL
  // window — the lobby is a browsable hub that shows the player's seated
  // table (a "you're here" pin + a Resume bar), so backing out no longer
  // needs a one-shot flag to avoid bouncing straight back in. To actually
  // cash out — return chips to bankroll, free the table to sit at a
  // different stake — use the "Leave table" button in the cash HUD/sheet,
  // which hits /api/cash/leave.
  const handleBack = () => {
    if (isTrainingGameId(gameId)) {
      navigate('/menu/training');
      return;
    }
    const isCashGame = isCashGameId(gameId);
    navigate(isCashGame ? '/cash' : '/menu/tournament');
  };

  const handleGameCreated = (newGameId: string) => {
    navigate(`/game/${newGameId}`, { replace: true });
  };

  // The backend has no record of this game (HTTP 404). Cash sessions
  // are in-memory-only, so they vanish on backend restart — kick the
  // player back to the cash menu where they can start fresh.
  // Tournament games can also 404 if the in-memory entry was evicted
  // and persistence couldn't rehydrate.
  const handleGameLoadFailed = useCallback(() => {
    if (isTrainingGameId(gameId)) {
      toast.error('Your training session ended — back to practice setup.');
      navigate('/menu/training', { replace: true });
      return;
    }
    const isCashGame = isCashGameId(gameId);
    if (isCashGame) {
      toast.error('Your cash session ended — back to the cash menu.');
      navigate('/cash', { replace: true });
    } else {
      toast.error('Game not found.');
      navigate('/menu', { replace: true });
    }
  }, [gameId, navigate]);

  return (
    <ResponsiveGameLayout
      gameId={gameId || null}
      playerName={playerName}
      onGameCreated={handleGameCreated}
      onBack={handleBack}
      onGameLoadFailed={handleGameLoadFailed}
    />
  );
}
