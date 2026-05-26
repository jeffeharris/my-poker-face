/**
 * SoloTableModal — fires when every opponent has left (or busted out of)
 * a cash table and only the human, still holding chips, remains. The
 * game is paused server-side in HAND_OVER, so this is a hard block over
 * the table with two ways forward:
 *
 *   - Stay & play: seat the named regulars (`rejoin_candidates`) via
 *     /api/cash/reseat and resume. The socket-pushed state update clears
 *     `human_alone`, which unmounts this modal. Hidden when no candidate
 *     is available to name.
 *   - Return to lobby: cash out via /api/cash/leave (settles any sponsor
 *     loan, returns table chips to bankroll), shows the session summary,
 *     then back to /cash.
 *
 * "Review last hand" temporarily hides the modal so the player can study
 * the final board behind it (the table is frozen on the last HAND_OVER),
 * borrowing the idea — not the implementation — from the tournament-end
 * screen's "View Final Hand".
 *
 * Driven entirely by `cashMode.human_alone`; no dedicated socket event.
 * Mirrors BustModal's overlay so it lives inside the table view (not a
 * PageLayout), needing no portal.
 */

import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogOut, Users, Eye, ArrowLeft } from 'lucide-react';
import { logger } from '../../utils/logger';
import type { CashModeInfo } from '../../types/game';
import { reseatTable, leaveTable, type LeaveResponse } from './api';
import { CashOutSummary } from './CashOutSummary';
import './SoloTableModal.css';

interface SoloTableModalProps {
  cashMode: CashModeInfo | null | undefined;
}

export function SoloTableModal({ cashMode }: SoloTableModalProps) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<'stay' | 'leave' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [leaveResult, setLeaveResult] = useState<LeaveResponse | null>(null);

  const candidates = useMemo(() => cashMode?.rejoin_candidates ?? [], [cashMode?.rejoin_candidates]);

  const handleStay = useCallback(async () => {
    if (busy) return;
    setBusy('stay');
    setError(null);
    try {
      await reseatTable(candidates.map((c) => c.personality_id));
      // The server reseats + resumes; the next game-state frame flips
      // `human_alone` false and unmounts this modal. Reset busy anyway so
      // the prompt stays interactive if that frame is slow to arrive.
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Reseat failed:', msg);
      setError(msg);
    } finally {
      setBusy(null);
    }
  }, [busy, candidates]);

  const handleLeave = useCallback(async () => {
    if (busy) return;
    setBusy('leave');
    setError(null);
    try {
      const data = await leaveTable();
      // Without a session_summary (e.g. server lost game_data), skip the
      // summary and go straight to the lobby so the player isn't stranded.
      if (data.session_summary) {
        setLeaveResult(data);
      } else {
        navigate('/cash');
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Leave failed:', msg);
      setError(msg);
      setBusy(null);
    }
  }, [busy, navigate]);

  if (!cashMode?.human_alone) return null;

  // Cash-out succeeded — show the session summary in place of the prompt.
  if (leaveResult?.session_summary) {
    return (
      <CashOutSummary
        summary={leaveResult.session_summary}
        stakeLabel={cashMode.stake_label}
        finalBankroll={leaveResult.bankroll}
        sponsorRepaid={leaveResult.sponsor_repaid}
        onContinue={() => navigate('/cash')}
      />
    );
  }

  // Reviewing the final hand — step out of the way, leave a way back.
  if (reviewing) {
    return (
      <button
        type="button"
        className="solo-modal__review-back"
        onClick={() => setReviewing(false)}
      >
        <ArrowLeft size={16} />
        Back to options
      </button>
    );
  }

  const names = candidates.map((c) => c.name);

  return (
    <div className="solo-modal__overlay">
      <div className="solo-modal__sheet" onClick={(e) => e.stopPropagation()}>
        <div className="solo-modal__header">
          <h3 className="solo-modal__title">Everyone left the table</h3>
          <p className="solo-modal__subtitle">
            {names.length > 0
              ? `You're the last one at ${cashMode.table_name || 'this table'}. Stay and a couple of regulars will pull up a chair, or cash out and head back to the lobby.`
              : `You're the last one at ${cashMode.table_name || 'this table'}, and no one's around to join right now. Cash out and head back to the lobby to find a busier game.`}
          </p>
        </div>

        <div className="solo-modal__body">
          {names.length > 0 && (
            <button
              type="button"
              onClick={handleStay}
              disabled={busy !== null}
              className="solo-modal__primary"
            >
              <Users size={15} />
              {busy === 'stay' ? 'Seating…' : `Stay & play with ${names.join(' & ')}`}
            </button>
          )}

          <button
            type="button"
            onClick={handleLeave}
            disabled={busy !== null}
            className={names.length > 0 ? 'solo-modal__leave' : 'solo-modal__primary'}
          >
            <LogOut size={14} />
            {busy === 'leave' ? 'Leaving…' : 'Return to lobby'}
          </button>

          <button
            type="button"
            onClick={() => setReviewing(true)}
            disabled={busy !== null}
            className="solo-modal__review"
          >
            <Eye size={13} />
            Review last hand
          </button>

          {error && (
            <div className="solo-modal__error" role="alert">
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
