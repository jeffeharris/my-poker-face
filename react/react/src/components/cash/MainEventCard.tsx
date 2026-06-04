/**
 * MainEventCard — the circuit Main Event invite banner in the cash lobby.
 *
 * Surfaces the one player decision in the tournament circuit: Register (play the
 * Main Event — a real-persona field you sit into) or Decline (it runs
 * autonomously, AI-only). The chairman decides *when* one exists (FLUSH bank +
 * cooldown); this card just renders the open invite and drives accept→sit→play
 * or decline. Lifecycle beats (final table / winner) ride the existing
 * `world_event` socket into the ActivityTicker, not this card.
 *
 * Register flow: `acceptInvite()` (stands the player up from any cash seat) →
 * `sitTournament()` (builds the live table) → `onEnter(game_id)` (Lobby
 * navigates). A buy-in > 0 asks for confirmation first; a 402 surfaces the
 * shortfall inline. See `docs/plans/P3_REMAINING_HANDOFF.md` §P3.8.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Trophy, Users, Coins, Clock } from 'lucide-react';

import {
  acceptInvite,
  declineInvite,
  sitTournament,
  InsufficientFundsError,
  type TournamentInvite,
} from './tournamentApi';
import './MainEventCard.css';

function formatChips(n: number): string {
  return n.toLocaleString('en-US');
}

/** "1st" / "2nd" / "3rd" / "4th"… for a finishing place. */
function ordinalPlace(n: number): string {
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  const suffix = { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th';
  return `${n}${suffix}`;
}

/** Live "starts in Xm Ys" countdown for an invite with an expiry window, or
 *  null when the offer has no auto-expiry (the player decides when present). */
function useCountdown(expiresAt: string | null): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [expiresAt]);
  return useMemo(() => {
    if (!expiresAt) return null;
    const ms = new Date(expiresAt).getTime() - now;
    if (Number.isNaN(ms)) return null;
    if (ms <= 0) return 'starting…';
    const total = Math.floor(ms / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }, [expiresAt, now]);
}

interface MainEventCardProps {
  invite: TournamentInvite;
  /** Navigate into the live tournament table (Lobby owns routing). */
  onEnter: (gameId: string) => void;
  /** The invite was resolved (declined / consumed) — clear + refetch. */
  onResolved: () => void;
  /** Register kicked off — Lobby raises its full-screen "Taking your seat"
   *  overlay so the multi-second accept→sit build reads as in-progress
   *  (and hides the card's mid-flight flip to the Resume bar). */
  onRegisterStart?: () => void;
  /** Register failed before navigation — Lobby drops the overlay. */
  onRegisterError?: () => void;
}

export function MainEventCard({
  invite,
  onEnter,
  onResolved,
  onRegisterStart,
  onRegisterError,
}: MainEventCardProps) {
  const [busy, setBusy] = useState<null | 'register' | 'decline'>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const countdown = useCountdown(invite.expires_at);
  const isFreeroll = invite.buy_in <= 0;

  const doRegister = useCallback(async () => {
    setBusy('register');
    setError(null);
    // Raise the full-screen seat-taking overlay up front: accept+sit builds the
    // persona table (a few seconds) and the lobby poll flips this card to the
    // Resume bar mid-flight — the overlay covers both so it reads as loading.
    onRegisterStart?.();
    try {
      const { tournament_id } = await acceptInvite();
      const { game_id } = await sitTournament(tournament_id);
      onEnter(game_id);
    } catch (e) {
      if (e instanceof InsufficientFundsError) {
        setError(`Buy-in is ${formatChips(e.required)} — you have ${formatChips(e.available)}.`);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
      setBusy(null);
      setConfirming(false);
      onRegisterError?.();
    }
  }, [onEnter, onRegisterStart, onRegisterError]);

  const handleRegister = useCallback(() => {
    if (busy) return;
    // A buy-in asks for an explicit confirm; a freeroll registers straight away.
    if (!isFreeroll && !confirming) {
      setConfirming(true);
      return;
    }
    void doRegister();
  }, [busy, isFreeroll, confirming, doRegister]);

  const handleDecline = useCallback(async () => {
    if (busy) return;
    setBusy('decline');
    setError(null);
    try {
      await declineInvite();
      onResolved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }, [busy, onResolved]);

  return (
    <section className="main-event" aria-label="Main Event invite">
      <div className="main-event__glow" aria-hidden="true" />
      <div className="main-event__body">
        <div className="main-event__heading">
          <Trophy size={20} className="main-event__trophy" aria-hidden="true" />
          <div className="main-event__titles">
            <span className="main-event__kicker">The Circuit</span>
            <h2 className="main-event__title">Main Event</h2>
          </div>
          {countdown && (
            <span className="main-event__countdown" title="Registration window">
              <Clock size={13} aria-hidden="true" />
              {countdown}
            </span>
          )}
        </div>

        <div className="main-event__stats">
          <span className="main-event__stat">
            <Users size={14} aria-hidden="true" />
            {invite.field_size}-player field
          </span>
          <span className="main-event__stat">
            <Coins size={14} aria-hidden="true" />
            {isFreeroll ? 'Freeroll' : `${formatChips(invite.buy_in)} buy-in`}
          </span>
          {invite.prize_pool_estimate ? (
            <span className="main-event__stat">
              <Trophy size={14} aria-hidden="true" />~{formatChips(invite.prize_pool_estimate)}{' '}
              purse
            </span>
          ) : null}
          <span className="main-event__stat">
            {formatChips(invite.starting_stack)} starting stack
          </span>
        </div>

        {invite.payouts && invite.payouts.length > 0 && (
          <div className="main-event__payouts">
            <div className="main-event__payouts-title">Payouts (est.)</div>
            {invite.payouts.map((p) => (
              <div key={p.finishing_position} className="main-event__payout-row">
                <span className="main-event__payout-place">
                  {ordinalPlace(p.finishing_position)}
                </span>
                <span className="main-event__payout-amount">{formatChips(p.amount)}</span>
                {invite.renown_enabled && p.renown ? (
                  <span className="main-event__payout-renown" title="Renown earned">
                    ★{p.renown}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        )}

        {error && (
          <div className="main-event__error" role="alert">
            {error}
          </div>
        )}

        <div className="main-event__actions">
          {confirming && !isFreeroll ? (
            <>
              <button
                type="button"
                className="main-event__btn main-event__btn--primary"
                onClick={handleRegister}
                disabled={busy !== null}
              >
                {busy === 'register'
                  ? 'Registering…'
                  : `Confirm ${formatChips(invite.buy_in)} buy-in`}
              </button>
              <button
                type="button"
                className="main-event__btn main-event__btn--ghost"
                onClick={() => setConfirming(false)}
                disabled={busy !== null}
              >
                Back
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="main-event__btn main-event__btn--primary"
                onClick={handleRegister}
                disabled={busy !== null}
              >
                {busy === 'register' ? 'Registering…' : 'Register'}
              </button>
              <button
                type="button"
                className="main-event__btn main-event__btn--ghost"
                onClick={handleDecline}
                disabled={busy !== null}
              >
                {busy === 'decline' ? 'Declining…' : 'Decline'}
              </button>
            </>
          )}
        </div>
        <p className="main-event__footnote">
          Decline and it runs without you — the field plays on in the background.
        </p>
      </div>
    </section>
  );
}
