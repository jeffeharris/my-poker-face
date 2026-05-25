/**
 * StakeOfferModal — Phase 5 + 2026-05-21 refinement.
 *
 * The reverse of `SponsorModal`: instead of picking from offers the AI
 * extends, the player proposes terms to a specific AI personality and
 * the AI evaluates accept-or-refuse.
 *
 * Per the refined design:
 *   - The target AI is pre-selected from the IdleStakablePanel.
 *   - The tier is LOCKED to the candidate's `target_stake_label`
 *     (comfort_zone + 1, "help-them-work-up-the-ranks" rule).
 *   - Player picks principal (within the target table's [min, max]
 *     window), cut, and optionally toggles match-share.
 *   - On match-share: AI contributes an equal match from their
 *     bankroll. UI defaults match_amount = principal (50/50 split per
 *     spec) and bumps the suggested cut to 40%.
 *
 * On refusal, the modal shows the AI's evaluation breakdown (score,
 * cut penalty, desperation relief) so the player learns to read the
 * formula rather than guess. On accept, success notice + bankroll
 * update — the AI will appear at the target table next refresh.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, HandCoins } from 'lucide-react';
import { offerStake } from './api';
import type {
  StakableAiCandidate,
  StakeFormat,
  StakeLabel,
  StakeOfferAccepted,
  StakeOfferRefused,
} from './types';
import { logger } from '../../utils/logger';
import './StakeOfferModal.css';

interface StakeOfferModalProps {
  /** When non-null, the modal is open for this AI at this tier.
   *  The tier comes from the candidate's `target_stake_label`
   *  (only valid +1 tier from their comfort zone). */
  target: {
    candidate: StakableAiCandidate;
    stakeLabel: StakeLabel;
    minBuyIn: number;
    maxBuyIn: number;
  } | null;
  bankroll: number;
  onClose: () => void;
  /** Fires after a successful accept so the lobby can re-fetch state
   *  (the AI now appears at the target table; the stakable-AI list
   *  loses them). */
  onAccepted?: (response: StakeOfferAccepted) => void;
}

const PURE_CUT_DEFAULT = 0.30;
const MATCH_SHARE_CUT_DEFAULT = 0.40;
const MIN_CUT = 0.10;
const MAX_CUT = 0.55;

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

export function StakeOfferModal({
  target,
  bankroll,
  onClose,
  onAccepted,
}: StakeOfferModalProps) {
  const [format, setFormat] = useState<StakeFormat>('pure');
  const [principal, setPrincipal] = useState<number>(0);
  const [cut, setCut] = useState<number>(PURE_CUT_DEFAULT);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [refusal, setRefusal] = useState<StakeOfferRefused | null>(null);
  const [accepted, setAccepted] = useState<StakeOfferAccepted | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Reset state when the modal opens for a new candidate.
  useEffect(() => {
    if (!target) return;
    setFormat('pure');
    setPrincipal(target.minBuyIn);
    setCut(PURE_CUT_DEFAULT);
    setConfirming(false);
    setRefusal(null);
    setAccepted(null);
    setSubmitError(null);
  }, [target?.candidate.personality_id, target?.stakeLabel, target?.minBuyIn]);

  // Match-share clamps differently — principal + match must fit
  // [min_buy_in, max_buy_in]. Use the 50/50 simplification: match
  // always equals principal, so principal range is [min/2, max/2].
  // Cap principal at half the player's bankroll too — they have to
  // fund only their half but the AI's match has to fit on the seat,
  // so the effective limit is the table's max.
  const matchAmount = format === 'match_share' ? principal : 0;
  const principalMin = target
    ? Math.max(
        1,
        format === 'match_share' ? Math.ceil(target.minBuyIn / 2) : target.minBuyIn,
      )
    : 0;
  const principalMax = target
    ? Math.min(
        bankroll,
        format === 'match_share'
          ? Math.floor(target.maxBuyIn / 2)
          : target.maxBuyIn,
      )
    : 0;

  // When format toggles, re-seed principal to the new min and cut to
  // the new default. Keeps the slider valid without per-toggle math
  // in the handler.
  const handleFormatChange = useCallback(
    (next: StakeFormat) => {
      if (next === format) return;
      setFormat(next);
      setConfirming(false);
      setRefusal(null);
      if (!target) return;
      const newMin =
        next === 'match_share'
          ? Math.ceil(target.minBuyIn / 2)
          : target.minBuyIn;
      setPrincipal(Math.min(newMin, bankroll));
      setCut(
        next === 'match_share' ? MATCH_SHARE_CUT_DEFAULT : PURE_CUT_DEFAULT,
      );
    },
    [format, target, bankroll],
  );

  const handleSubmit = useCallback(async () => {
    if (!target) return;
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setBusy(true);
    setSubmitError(null);
    setRefusal(null);
    try {
      const response = await offerStake({
        targetPid: target.candidate.personality_id,
        stakeLabel: target.stakeLabel,
        principal,
        cut,
        format,
        matchAmount: format === 'match_share' ? matchAmount : undefined,
      });
      if (response.accepted) {
        setAccepted(response);
        onAccepted?.(response);
      } else {
        setRefusal(response);
        setConfirming(false);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Stake offer failed:', msg);
      setSubmitError(msg);
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }, [
    target,
    confirming,
    principal,
    cut,
    format,
    matchAmount,
    onAccepted,
  ]);

  if (!target) return null;

  const totalSeat = principal + matchAmount;
  const principalValid =
    principalMax >= principalMin &&
    principal >= principalMin &&
    principal <= principalMax;
  const submitDisabled =
    busy || !principalValid || accepted !== null;

  const candidate = target.candidate;
  const desperationHint =
    candidate.desperation >= 0.65
      ? 'running well below their starting bankroll'
      : candidate.desperation >= 0.35
        ? 'down on their starting bankroll'
        : 'comfortable financially';

  // Portaled to <body> so the fixed overlay escapes the Lobby's
  // PageLayout stacking context — otherwise the app header (.menu-bar)
  // paints over the centered sheet's close button. See CharacterDetailCard.
  return createPortal(
    <div className="stake-offer-modal__overlay" onClick={onClose}>
      <div
        className="stake-offer-modal__sheet"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="stake-offer-modal__header">
          <div className="stake-offer-modal__title-row">
            <h3 className="stake-offer-modal__title">
              <HandCoins size={18} aria-hidden="true" />
              <span>Stake {candidate.name}</span>
            </h3>
            <button
              type="button"
              className="stake-offer-modal__close"
              onClick={onClose}
              aria-label="Close stake offer"
            >
              <X size={20} />
            </button>
          </div>
          <p className="stake-offer-modal__subtitle">
            {candidate.name} plays {candidate.comfort_zone} — fund them
            into {target.stakeLabel} and split the upside.
            {candidate.relationship_hint && (
              <>
                {' '}
                <span className="stake-offer-modal__hint-chip">
                  {candidate.relationship_hint}
                </span>
              </>
            )}
          </p>
          <p className="stake-offer-modal__subtitle stake-offer-modal__subtitle--muted">
            {desperationHint}.
          </p>
        </div>

        <div className="stake-offer-modal__body">
          {/* Tier — locked to the candidate's target_stake_label. */}
          <section className="stake-offer-modal__field">
            <label className="stake-offer-modal__label">Stake tier</label>
            <div className="stake-offer-modal__locked-tier">
              <span className="stake-offer-modal__locked-tier-label">
                {target.stakeLabel}
              </span>
              <span className="stake-offer-modal__locked-tier-meta">
                ${target.minBuyIn.toLocaleString()}–${target.maxBuyIn.toLocaleString()} buy-in
              </span>
            </div>
            <p className="stake-offer-modal__field-help">
              You can only stake them one tier up from where they
              normally play. Help-them-grow rule.
            </p>
          </section>

          {/* Format toggle: pure vs match-share. */}
          <section className="stake-offer-modal__field">
            <label className="stake-offer-modal__label">Stake type</label>
            <div className="stake-offer-modal__format-toggle" role="group">
              <button
                type="button"
                className={
                  'stake-offer-modal__format' +
                  (format === 'pure' ? ' is-selected' : '')
                }
                onClick={() => handleFormatChange('pure')}
                disabled={busy || accepted !== null}
              >
                <span className="stake-offer-modal__format-label">Pure</span>
                <span className="stake-offer-modal__format-desc">
                  You fund everything; lower cut
                </span>
              </button>
              <button
                type="button"
                className={
                  'stake-offer-modal__format' +
                  (format === 'match_share' ? ' is-selected' : '')
                }
                onClick={() => handleFormatChange('match_share')}
                disabled={busy || accepted !== null}
              >
                <span className="stake-offer-modal__format-label">Match-share</span>
                <span className="stake-offer-modal__format-desc">
                  They match your stake; higher cut
                </span>
              </button>
            </div>
          </section>

          {/* Principal slider — range depends on format. */}
          <section className="stake-offer-modal__field">
            <div className="stake-offer-modal__label-row">
              <label className="stake-offer-modal__label">
                {format === 'match_share' ? 'Your half' : 'Principal'}
              </label>
              <span className="stake-offer-modal__value">
                ${principal.toLocaleString()}
              </span>
            </div>
            {principalMax >= principalMin ? (
              <input
                type="range"
                min={principalMin}
                max={principalMax}
                step={Math.max(
                  1,
                  Math.floor((principalMax - principalMin) / 40),
                )}
                value={principal}
                onChange={(e) => {
                  setPrincipal(
                    clamp(
                      Number(e.target.value),
                      principalMin,
                      principalMax,
                    ),
                  );
                  setConfirming(false);
                  setRefusal(null);
                }}
                disabled={busy || accepted !== null}
                className="stake-offer-modal__slider"
              />
            ) : (
              <div className="stake-offer-modal__notice stake-offer-modal__notice--info">
                Bankroll too low for this format — try the other type
                or build up more chips.
              </div>
            )}
            <div className="stake-offer-modal__range-meta">
              <span>min ${principalMin.toLocaleString()}</span>
              <span>max ${principalMax.toLocaleString()}</span>
            </div>
            {format === 'match_share' && (
              <p className="stake-offer-modal__field-help">
                {candidate.name} matches your half. Seat total:{' '}
                ${totalSeat.toLocaleString()}.
              </p>
            )}
          </section>

          {/* Cut slider. */}
          <section className="stake-offer-modal__field">
            <div className="stake-offer-modal__label-row">
              <label className="stake-offer-modal__label">Your cut</label>
              <span className="stake-offer-modal__value">
                {Math.round(cut * 100)}%
              </span>
            </div>
            <input
              type="range"
              min={Math.round(MIN_CUT * 100)}
              max={Math.round(MAX_CUT * 100)}
              step={5}
              value={Math.round(cut * 100)}
              onChange={(e) => {
                setCut(Number(e.target.value) / 100);
                setConfirming(false);
                setRefusal(null);
              }}
              disabled={busy || accepted !== null}
              className="stake-offer-modal__slider"
            />
            <div className="stake-offer-modal__range-meta">
              <span>10%</span>
              <span>55%</span>
            </div>
            <p className="stake-offer-modal__field-help">
              {cut > 0.35
                ? 'Steep cuts make AIs less likely to accept — they need real goodwill or desperation to take this.'
                : 'Fair cut — comfortable AIs will entertain this.'}
            </p>
          </section>

          {/* Outcomes preview. */}
          <section className="stake-offer-modal__terms">
            <div className="stake-offer-modal__term">
              <span>Your bankroll after</span>
              <span>${(bankroll - principal).toLocaleString()}</span>
            </div>
            <div className="stake-offer-modal__term">
              <span>If they 2× the seat</span>
              <span>
                +${Math.round(totalSeat * cut).toLocaleString()} to you
              </span>
            </div>
            <div className="stake-offer-modal__term">
              <span>If they bust completely</span>
              <span className="stake-offer-modal__term--loss">
                -${principal.toLocaleString()} carried
              </span>
            </div>
          </section>

          {refusal && (
            <div className="stake-offer-modal__notice stake-offer-modal__notice--refused">
              {refusal.detail}
              {refusal.evaluation && (
                <div className="stake-offer-modal__score-grid">
                  <div>Score</div>
                  <div>{refusal.evaluation.score.toFixed(2)}</div>
                  <div>Base threshold</div>
                  <div>{refusal.evaluation.base_threshold.toFixed(2)}</div>
                  {refusal.evaluation.cut_penalty > 0 && (
                    <>
                      <div>Cut penalty</div>
                      <div>+{refusal.evaluation.cut_penalty.toFixed(2)}</div>
                    </>
                  )}
                  {refusal.evaluation.desperation_relief > 0 && (
                    <>
                      <div>Desperation relief</div>
                      <div>−{refusal.evaluation.desperation_relief.toFixed(2)}</div>
                    </>
                  )}
                  <div>Needed</div>
                  <div>{refusal.evaluation.effective_threshold.toFixed(2)}</div>
                </div>
              )}
            </div>
          )}

          {accepted && (
            <div className="stake-offer-modal__notice stake-offer-modal__notice--accepted">
              {accepted.target_display_name} accepted — they're sitting
              at the {accepted.stake_label} table.
              Watch the lobby for their session outcome.
            </div>
          )}

          {submitError && (
            <div className="stake-offer-modal__notice stake-offer-modal__notice--error">
              {submitError}
            </div>
          )}

          <button
            type="button"
            className={
              'stake-offer-modal__submit' +
              (confirming ? ' is-confirming' : '') +
              (accepted ? ' is-done' : '')
            }
            onClick={accepted ? onClose : handleSubmit}
            disabled={submitDisabled && !accepted}
          >
            {accepted
              ? 'Close'
              : busy
                ? 'Sending offer…'
                : confirming
                  ? `Confirm — stake $${principal.toLocaleString()} at ${Math.round(cut * 100)}%`
                  : 'Review offer'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
