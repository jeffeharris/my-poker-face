/**
 * SponsorModal — choose a sponsor offer to fund a buy-in at a stake
 * the player can't self-afford.
 *
 * Opens from `CashModeEntry` when the user taps a "Sponsor required"
 * stake card. Fetches 3 offers (server-sampled) and renders them with
 * plain-English terms.
 *
 * Path B: offers come from two pools — named AI personalities (with
 * avatar + name + relationship hint) and anonymous house archetypes
 * (the v1 fallback). The mixed-pool shape is set server-side; the
 * modal renders the right card for each `kind`.
 *
 * Two-tap confirm pattern: first tap on an offer expands its terms
 * with full math ("$1000 loan → repay $1300 before split, then 40% of
 * what's left to sponsor"); second tap commits the loan + sit-down.
 *
 * Reuses the existing `mobile-cash-sheet` overlay/slide animation for
 * mobile-feel parity with the in-table cash sheet.
 */

import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import { getSponsorOffers, sponsorAndSit } from './api';
import type { SponsorOffer, StakeLabel } from './types';
import { logger } from '../../utils/logger';
import './SponsorModal.css';

interface SponsorModalProps {
  isOpen: boolean;
  stakeLabel: StakeLabel | null;
  // When the sponsor flow originated from a specific seat tap on a
  // lobby table card, pass the origin so the resulting game is built
  // against that table's AI roster instead of a fresh sample. Omit
  // (or pass null) when opened from a stake-level card with no seat
  // context — the backend then runs the legacy path.
  origin?: { tableId: string; seatIndex: number } | null;
  // v111: user-facing name of the specific table the sponsor is
  // targeting ("The Lodge"). Surfaced in the modal title so the
  // player sees which table they're sponsoring at when multiple
  // tables share a stake tier. NULL → fall back to the stake label.
  tableName?: string | null;
  onClose: () => void;
}

/** Stable identity key for an offer — `kind` distinguishes house vs
 *  personality so a Lender named "Friendly Boost" (archetype id of
 *  the same name) can't collide with a personality id that maps to
 *  the same string. */
function offerKey(offer: SponsorOffer): string {
  if (offer.kind === 'personality') return `personality:${offer.lender_id}`;
  return `house:${offer.archetype_id}`;
}

export function SponsorModal({
  isOpen,
  stakeLabel,
  origin,
  tableName,
  onClose,
}: SponsorModalProps) {
  const navigate = useNavigate();
  const [offers, setOffers] = useState<SponsorOffer[] | null>(null);
  // Player-prestige hook 2: when true, the player is too reviled for named-AI
  // backing — only house offers show, and we explain why.
  const [backingRestricted, setBackingRestricted] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingKey, setConfirmingKey] = useState<string | null>(null);

  // Fetch offers when modal opens with a stake.
  useEffect(() => {
    if (!isOpen || !stakeLabel) {
      setOffers(null);
      setBackingRestricted(false);
      setLoadError(null);
      setSubmitError(null);
      setConfirmingKey(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const response = await getSponsorOffers(stakeLabel);
        if (cancelled) return;
        if (response.eligible) {
          setOffers(response.offers);
          setBackingRestricted(response.backing_restricted ?? false);
        } else {
          setLoadError(
            `Not eligible for sponsorship at ${stakeLabel}. ` +
              `Need at least one tier below's bankroll to qualify.`
          );
        }
      } catch (e) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Failed to load sponsor offers:', msg);
        setLoadError(msg);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpen, stakeLabel]);

  const handleAccept = useCallback(
    async (offer: SponsorOffer) => {
      if (!stakeLabel || busy) return;
      const key = offerKey(offer);
      // Two-tap confirm: first tap arms this offer, second commits.
      if (confirmingKey !== key) {
        setConfirmingKey(key);
        return;
      }
      setBusy(true);
      setSubmitError(null);
      try {
        const acceptor =
          offer.kind === 'personality'
            ? { lender_id: offer.lender_id }
            : { archetype_id: offer.archetype_id };
        const originPayload = origin
          ? { table_id: origin.tableId, seat_index: origin.seatIndex }
          : undefined;
        const response = await sponsorAndSit(stakeLabel, acceptor, originPayload);
        navigate(`/game/${response.game_id}`);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Sponsor sit failed:', msg);
        setSubmitError(msg);
        setBusy(false);
        setConfirmingKey(null);
      }
    },
    [stakeLabel, busy, confirmingKey, navigate, origin]
  );

  if (!isOpen) return null;

  // Portaled to <body> so the fixed overlay escapes the Lobby's
  // PageLayout stacking context — otherwise the app header (.menu-bar)
  // paints over the centered sheet's close button. See CharacterDetailCard.
  return createPortal(
    <div className="sponsor-modal__overlay" onClick={onClose}>
      <div className="sponsor-modal__sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sponsor-modal__header">
          <div className="sponsor-modal__title-row">
            <h3 className="sponsor-modal__title">
              {tableName
                ? `Sponsor for ${tableName} (${stakeLabel})`
                : `Sponsor for the ${stakeLabel} table`}
            </h3>
            <button
              type="button"
              className="sponsor-modal__close"
              onClick={onClose}
              aria-label="Close"
            >
              <X size={20} />
            </button>
          </div>
          <p className="sponsor-modal__subtitle">
            You can't afford this table on your own. Pick a backer.
          </p>
        </div>

        <div className="sponsor-modal__body">
          {loadError && (
            <div className="sponsor-modal__error" role="alert">
              {loadError}
            </div>
          )}

          {!offers && !loadError && <div className="sponsor-modal__loading">Finding sponsors…</div>}

          {backingRestricted && offers && (
            <div className="sponsor-modal__restricted" role="note">
              Your reputation precedes you. No one here will personally back a
              player like you right now — only the house will deal. Win back the
              room's regard and the named backers return.
            </div>
          )}

          {offers?.map((offer) => {
            const key = offerKey(offer);
            const isConfirming = confirmingKey === key;
            const floorAmount = Math.round(offer.amount * offer.floor);
            const ratePct = Math.round(offer.rate * 100);
            const isPersonality = offer.kind === 'personality';
            return (
              <div
                key={key}
                className={
                  'sponsor-modal__offer' +
                  (isConfirming ? ' is-confirming' : '') +
                  (isPersonality
                    ? ' sponsor-modal__offer--personality'
                    : ' sponsor-modal__offer--house')
                }
              >
                <div className="sponsor-modal__offer-header">
                  <div className="sponsor-modal__offer-identity">
                    {isPersonality && (
                      <span className="sponsor-modal__offer-avatar" aria-hidden="true">
                        {offer.name.charAt(0).toUpperCase()}
                      </span>
                    )}
                    <div className="sponsor-modal__offer-name-block">
                      <h4 className="sponsor-modal__offer-name">{offer.name}</h4>
                      {isPersonality && offer.relationship_hint && (
                        <span className="sponsor-modal__offer-hint">{offer.relationship_hint}</span>
                      )}
                    </div>
                  </div>
                  <span className="sponsor-modal__offer-amount">
                    ${offer.amount.toLocaleString()}
                  </span>
                </div>
                <p className="sponsor-modal__offer-flavor">{offer.flavor}</p>
                <div className="sponsor-modal__offer-terms">
                  <div className="sponsor-modal__offer-term">
                    <span className="sponsor-modal__offer-term-label">Loan</span>
                    <span>${offer.amount.toLocaleString()}</span>
                  </div>
                  <div className="sponsor-modal__offer-term">
                    <span className="sponsor-modal__offer-term-label">Repay before split</span>
                    <span>${floorAmount.toLocaleString()}</span>
                  </div>
                  <div className="sponsor-modal__offer-term">
                    <span className="sponsor-modal__offer-term-label">Then sponsor's cut</span>
                    <span>{ratePct}% of remaining</span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleAccept(offer)}
                  disabled={busy}
                  className={`sponsor-modal__accept${isConfirming ? ' is-confirming' : ''}`}
                >
                  {busy && isConfirming
                    ? 'Sitting down…'
                    : isConfirming
                      ? `Confirm — take $${offer.amount.toLocaleString()} loan`
                      : 'Take this offer'}
                </button>
              </div>
            );
          })}

          {submitError && (
            <div className="sponsor-modal__error" role="alert">
              {submitError}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
