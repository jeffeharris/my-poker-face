/**
 * CharacterDetailCard — "Dossier 1972"
 *
 * Click a character at the table or in the lobby to pull their
 * dossier. Presented as a noir intelligence file: aged paper,
 * gold-leaf rules, behavioral tally strips, and a wet-ink
 * OBSERVED stamp that slams in on open.
 *
 * Composes any subset of the available data — sections silently
 * drop out if their inputs are missing, so the same component
 * handles "lobby with no live game" and "mid-hand at the table".
 *
 * This file is the thin orchestrator: the portal/overlay chrome, the
 * header, and the composition of the extracted pieces. The heavy lifting
 * lives in `./dossier/`:
 *   • useDossierState — fetch enrichment + note/nickname autosave
 *   • DossierSubject  — portrait, name, nickname editor, renown badge
 *   • ScoutingStrip   — the grind/informant clearance strip
 *   • DossierSections — the stacked, self-gating body sections
 */

import { useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'framer-motion';
import './CharacterDetailCard.css';
import type { CharacterDetailCardProps } from './dossier/types';
import { deriveFileNumber } from './dossier/helpers';
import { DataRow, SectionRule } from './dossier/primitives';
import { ScoutingStrip } from './dossier/ScoutingStrip';
import { DossierSubject } from './dossier/DossierSubject';
import { DossierSections } from './dossier/DossierSections';
import { useDossierState } from './dossier/useDossierState';

// Re-export the public types so the `./index.ts` barrel (and any deep
// importers) keep resolving them from this module path unchanged.
export type {
  RelationshipKind,
  CharacterDossierData,
  CharacterDetailCardProps,
} from './dossier/types';

export function CharacterDetailCard({
  isOpen,
  onClose,
  character,
  origin,
  identifier,
  circuitContext = false,
  onIntelChanged,
}: CharacterDetailCardProps) {
  // ESC to close — felt-tabletop UX expects it.
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  const { fetched, merged, buyingSection, buyError, handleBuyInformant, note, nickname } =
    useDossierState(isOpen, identifier, character, onIntelChanged);

  const fileNumber = useMemo(
    () => character.fileNumber ?? deriveFileNumber(merged.name),
    [character.fileNumber, merged.name]
  );

  // Origin-based transform for the open animation. If no origin
  // given, fall back to dead center (looks like the card just lands).
  const originStyle = useMemo<React.CSSProperties>(() => {
    if (!origin) return {};
    return {
      transformOrigin: `${origin.x}px ${origin.y}px`,
    };
  }, [origin]);

  const reputation = fetched?.reputation ?? null;
  const hasOverride = !!fetched?.personality?.nickname_override;
  const showNotes = !!identifier;

  // Credit-history (bankruptcy record) row. Standalone unlock: revealed
  // free when the viewer has first-hand exposure or was bought from the
  // informant; otherwise a locked "pull credit report" buy. Reuses
  // handleBuyInformant (it refetches the dossier on success).
  const renderCreditHistory = () => {
    const credit = fetched?.credit_history ?? null;
    if (!credit) return null;
    if (credit.revealed) {
      const n = credit.bankruptcy_count;
      return (
        <DataRow
          label="Credit history"
          value={
            <span className="dossier__money">
              {n > 0
                ? `${n} ${n === 1 ? 'bankruptcy' : 'bankruptcies'}`
                : 'No bankruptcies on record'}
              {credit.recently_bankrupt && <span className="dossier__money-note"> · recent</span>}
            </span>
          }
        />
      );
    }
    if (!circuitContext || !identifier) return null;
    const { section_id, price } = credit.unlock;
    const cantAfford = fetched?.player_bankroll != null && fetched.player_bankroll < price;
    return (
      <DataRow
        label="Credit history"
        value={
          <button
            type="button"
            className="dossier__credit-unlock"
            disabled={buyingSection === section_id || cantAfford}
            onClick={() => handleBuyInformant(section_id)}
          >
            {buyingSection === section_id
              ? 'Pulling…'
              : `Pull credit report — $${price.toLocaleString()}`}
          </button>
        }
      />
    );
  };

  // Rendered through a portal to <body> so the fixed-position overlay
  // escapes any ancestor stacking context (e.g. PageLayout's `position:
  // fixed` wrapper). Without this, a higher-z-index app header (.menu-bar,
  // z-index 400) would paint over the dossier — including its close button —
  // because the trapped overlay's z-index only competes inside that ancestor.
  return createPortal(
    <AnimatePresence>
      {isOpen && (
        <motion.div
          className="dossier-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.22, ease: 'easeOut' }}
          onClick={onClose}
          role="dialog"
          aria-modal="true"
          aria-label={`Dossier for ${merged.name}`}
        >
          <div className="dossier-overlay__grain" aria-hidden="true" />
          <div className="dossier-overlay__vignette" aria-hidden="true" />

          <motion.article
            className="dossier"
            style={originStyle}
            initial={{ opacity: 0, scale: 0.86, y: 24, rotate: -2.4 }}
            animate={{ opacity: 1, scale: 1, y: 0, rotate: -0.8 }}
            exit={{ opacity: 0, scale: 0.92, y: 18, rotate: -2 }}
            transition={{
              type: 'spring',
              damping: 22,
              stiffness: 220,
              mass: 0.9,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Deco corner ornaments — pure CSS triangles + diamonds */}
            <span className="dossier__corner dossier__corner--tl" aria-hidden="true" />
            <span className="dossier__corner dossier__corner--tr" aria-hidden="true" />
            <span className="dossier__corner dossier__corner--bl" aria-hidden="true" />
            <span className="dossier__corner dossier__corner--br" aria-hidden="true" />

            {/* Paper texture is applied via CSS pseudo on the card itself */}

            {/* OBSERVED ink stamp — slams in last with a wet-blot keyframe */}
            <motion.div
              className="dossier__stamp"
              initial={{ opacity: 0, scale: 1.5, rotate: -22 }}
              animate={{ opacity: 0.85, scale: 1, rotate: -14 }}
              transition={{ delay: 0.55, duration: 0.32, ease: [0.5, 1.4, 0.4, 1] }}
              aria-hidden="true"
            >
              <span className="dossier__stamp-inner">OBSERVED</span>
              <span className="dossier__stamp-sub">{fileNumber}</span>
            </motion.div>

            <button
              type="button"
              className="dossier__close"
              onClick={onClose}
              aria-label="Close dossier"
            >
              <span aria-hidden="true">×</span>
            </button>

            <header className="dossier__header">
              <div className="dossier__classification">
                <span className="dossier__class-tag">CLASSIFIED</span>
                <span className="dossier__class-dot" aria-hidden="true" />
                <span className="dossier__class-file">FILE №&nbsp;{fileNumber}</span>
              </div>
              <div className="dossier__class-meta">PIT BOSS OBSERVATION · INTERNAL</div>
            </header>

            <DossierSubject
              character={character}
              merged={merged}
              reputation={reputation}
              identifier={identifier}
              hasOverride={hasOverride}
              nickname={nickname}
            />

            <SectionRule>PROFILE</SectionRule>
            <section className="dossier__profile">
              {merged.attitude && <DataRow label="Attitude" value={merged.attitude} />}
              {merged.confidence && <DataRow label="Confidence" value={merged.confidence} />}
            </section>

            {fetched?.scouting && (
              <ScoutingStrip
                scouting={fetched.scouting}
                // Informant purchasing is a Circuit fixture (that's where the
                // bankroll is). Elsewhere the unlock state still shows, but
                // the buy buttons give way to an "unlock in the Circuit" hint.
                onBuy={circuitContext && identifier ? handleBuyInformant : undefined}
                buyingSection={buyingSection}
                buyError={buyError}
                bankroll={fetched.player_bankroll}
              />
            )}

            <DossierSections
              fetched={fetched}
              merged={merged}
              character={character}
              fileNumber={fileNumber}
              creditHistory={renderCreditHistory()}
              note={note}
              showNotes={showNotes}
            />

            <footer className="dossier__footer">
              <span className="dossier__footer-mark" aria-hidden="true">
                ♠
              </span>
              <span className="dossier__footer-text">
                END OF FILE · DO NOT REMOVE FROM PREMISES
              </span>
              <span className="dossier__footer-mark" aria-hidden="true">
                ♠
              </span>
            </footer>
          </motion.article>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body
  );
}
