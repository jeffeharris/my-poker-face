import { useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { ResponsiveGameLayout } from '../shared';
import { isCashGameId, isTournamentGameId, isTrainingGameId } from '../../utils/gameId';

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
    if (isTournamentGameId(gameId)) {
      navigate('/tournament'); // multi-table event → standings hub
      return;
    }
    navigate(isCashGameId(gameId) ? '/cash' : '/menu/tournament');
  };

  // Memoized so the identity is stable across re-renders — an unstable
  // onGameCreated used to re-run usePokerGame's socket-init effect and leak a
  // second socket for the same game_id (the "two hands flickering" bug).
  const handleGameCreated = useCallback(
    (newGameId: string) => {
      navigate(`/game/${newGameId}`, { replace: true });
    },
    [navigate]
  );

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
    if (isTournamentGameId(gameId)) {
      toast.error('Your tournament table ended — back to standings.');
      navigate('/tournament', { replace: true });
      return;
    }
    if (isCashGameId(gameId)) {
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
