/**
 * ScoutingStrip — the grind's progress, framed as the case file's clearance
 * level. Below the floor the file reads CLASSIFIED with a hands-to-go count;
 * past it, a progress bar toward the next unlock plus a list of reads still
 * to earn. Hidden entirely when the dossier is ungated (no scouting block).
 *
 * Extracted from CharacterDetailCard.tsx.
 */

import type { DossierScouting } from '../api';

export function ScoutingStrip({
  scouting,
  onBuy,
  buyingSection,
  buyError,
  bankroll,
}: {
  scouting: DossierScouting;
  onBuy?: (sectionId: string) => void;
  buyingSection?: string | null;
  buyError?: string | null;
  bankroll?: number | null;
}) {
  const { hands_observed, floor, floor_met, locked } = scouting;
  const offers = scouting.informant_offers ?? [];
  // Next HAND threshold to cross (floor when below it, else the nearest
  // locked item's hand floor still ahead of us). Drives the progress bar.
  // Sample-gated tiers whose hand floor is already met are excluded — their
  // remaining requirement is opportunity count, shown per-row below, not on
  // this hand bar — so once every hand floor is cleared the bar retires.
  const nextAt = !floor_met
    ? floor
    : locked.reduce<number | null>(
        (min, l) =>
          l.unlocks_at > hands_observed && (min == null || l.unlocks_at < min) ? l.unlocks_at : min,
        null
      );
  const pct = nextAt ? Math.min(100, Math.round((hands_observed / nextAt) * 100)) : 100;

  return (
    <section className={'dossier__scouting' + (floor_met ? '' : ' dossier__scouting--classified')}>
      <div className="dossier__scouting-head">
        <span className="dossier__scouting-stamp">{floor_met ? 'CLEARANCE' : 'CLASSIFIED'}</span>
        <span className="dossier__scouting-count">
          {hands_observed.toLocaleString()} {hands_observed === 1 ? 'hand' : 'hands'} observed
        </span>
      </div>

      {!floor_met ? (
        <p className="dossier__scouting-note">
          Insufficient observation. Play <strong>{Math.max(0, floor - hands_observed)}</strong> more{' '}
          {floor - hands_observed === 1 ? 'hand' : 'hands'} to open this file.
        </p>
      ) : locked.length > 0 ? (
        <>
          <p className="dossier__scouting-note">
            Still to scout — keep playing them to declassify:
          </p>
          <ul className="dossier__scouting-locked">
            {locked.map((l) => (
              <li key={l.id} className="dossier__scouting-lock">
                <span className="dossier__scouting-lock-icon" aria-hidden="true">
                  🔒
                </span>
                <span className="dossier__scouting-lock-label">{l.label}</span>
                <span className="dossier__scouting-lock-at">
                  {/* Sample-gated reads (Tier-2) show progress toward the
                      opportunity requirement — the binding, honest gate —
                      rather than just a hand count. */}
                  {l.sample_min
                    ? `${l.samples_observed ?? 0}/${l.sample_min} ${l.sample_noun ?? 'seen'}`
                    : `${l.unlocks_at} hands`}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="dossier__scouting-note dossier__scouting-note--complete">
          Full dossier declassified ✓
        </p>
      )}

      {nextAt != null && (
        <div className="dossier__scouting-bar" aria-hidden="true">
          <span className="dossier__scouting-bar-fill" style={{ width: `${pct}%` }} />
        </div>
      )}

      {offers.length > 0 &&
        (onBuy ? (
          <div className="dossier__informant">
            <div className="dossier__informant-head">
              <p className="dossier__informant-pitch">
                Or pay an informant to declassify a section:
              </p>
              {bankroll != null && (
                <span className="dossier__informant-stack">
                  Your stack {bankroll.toLocaleString()}
                </span>
              )}
            </div>
            <div className="dossier__informant-offers">
              {offers.map((o) => {
                const busy = buyingSection === o.id;
                // Unknown bankroll → allow (the server still guards with 402).
                const canAfford = bankroll == null || bankroll >= o.price;
                const short = bankroll != null ? o.price - bankroll : 0;
                return (
                  <button
                    key={o.id}
                    type="button"
                    className={
                      'dossier__informant-buy' + (canAfford ? '' : ' dossier__informant-buy--cant')
                    }
                    disabled={busy || !!buyingSection || !canAfford}
                    onClick={() => onBuy(o.id)}
                    title={
                      canAfford
                        ? `Reveal ${o.label} for ${o.price.toLocaleString()} chips`
                        : `Need ${short.toLocaleString()} more chips`
                    }
                  >
                    <span className="dossier__informant-buy-label">{o.label}</span>
                    <span className="dossier__informant-buy-price">
                      {busy
                        ? 'Paying…'
                        : canAfford
                          ? o.price.toLocaleString()
                          : `−${short.toLocaleString()}`}
                    </span>
                  </button>
                );
              })}
            </div>
            {buyError && <p className="dossier__informant-error">{buyError}</p>}
          </div>
        ) : (
          // Off in a tournament etc. — the informant only works the Circuit.
          <p className="dossier__informant-elsewhere">
            Visit the Circuit to pay an informant for the rest.
          </p>
        ))}
    </section>
  );
}
