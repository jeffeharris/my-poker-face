/**
 * SponsorModal — choose a sponsor offer to fund a buy-in at a stake
 * the player can't self-afford.
 *
 * Opens from `CashModeEntry` when the user taps a "Sponsor required"
 * stake card. Fetches 3 offers (server-sampled) and renders them with
 * plain-English terms.
 *
 * Two-tap confirm pattern: first tap on an offer expands its terms
 * with full math ("$1000 loan → repay $1300 before split, then 40% of
 * what's left to sponsor"); second tap commits the loan + sit-down.
 *
 * Reuses the existing `mobile-cash-sheet` overlay/slide animation for
 * mobile-feel parity with the in-table cash sheet.
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import { getSponsorOffers, sponsorAndSit } from './api';
import type { SponsorOffer, StakeLabel } from './types';
import { logger } from '../../utils/logger';
import './SponsorModal.css';

interface SponsorModalProps {
  isOpen: boolean;
  stakeLabel: StakeLabel | null;
  onClose: () => void;
}

export function SponsorModal({ isOpen, stakeLabel, onClose }: SponsorModalProps) {
  const navigate = useNavigate();
  const [offers, setOffers] = useState<SponsorOffer[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  // Fetch offers when modal opens with a stake.
  useEffect(() => {
    if (!isOpen || !stakeLabel) {
      setOffers(null);
      setLoadError(null);
      setSubmitError(null);
      setConfirmingId(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const response = await getSponsorOffers(stakeLabel);
        if (cancelled) return;
        if (response.eligible) {
          setOffers(response.offers);
        } else {
          setLoadError(
            `Not eligible for sponsorship at ${stakeLabel}. ` +
              `Need at least one tier below's bankroll to qualify.`,
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
      // Two-tap confirm: first tap arms this offer, second commits.
      if (confirmingId !== offer.archetype_id) {
        setConfirmingId(offer.archetype_id);
        return;
      }
      setBusy(true);
      setSubmitError(null);
      try {
        const response = await sponsorAndSit(stakeLabel, offer.archetype_id);
        navigate(`/game/${response.game_id}`);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Sponsor sit failed:', msg);
        setSubmitError(msg);
        setBusy(false);
        setConfirmingId(null);
      }
    },
    [stakeLabel, busy, confirmingId, navigate],
  );

  if (!isOpen) return null;

  return (
    <div className="sponsor-modal__overlay" onClick={onClose}>
      <div
        className="sponsor-modal__sheet"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sponsor-modal__header">
          <div className="sponsor-modal__title-row">
            <h3 className="sponsor-modal__title">
              Sponsor for the {stakeLabel} table
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
            <div className="sponsor-modal__error" role="alert">{loadError}</div>
          )}

          {!offers && !loadError && (
            <div className="sponsor-modal__loading">Finding sponsors…</div>
          )}

          {offers?.map((offer) => {
            const isConfirming = confirmingId === offer.archetype_id;
            const floorAmount = Math.round(offer.amount * offer.floor);
            const ratePct = Math.round(offer.rate * 100);
            return (
              <div
                key={offer.archetype_id}
                className={`sponsor-modal__offer${isConfirming ? ' is-confirming' : ''}`}
              >
                <div className="sponsor-modal__offer-header">
                  <h4 className="sponsor-modal__offer-name">{offer.name}</h4>
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
            <div className="sponsor-modal__error" role="alert">{submitError}</div>
          )}
        </div>
      </div>
    </div>
  );
}
