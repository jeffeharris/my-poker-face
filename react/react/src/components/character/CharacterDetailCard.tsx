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
 */

import { useEffect, useMemo } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import './CharacterDetailCard.css';

export type RelationshipKind =
  | 'rival'
  | 'friend'
  | 'sponsor'
  | 'neutral'
  | 'admirer'
  | 'antagonist';

export interface CharacterDossierData {
  /** Display name — rendered large in Bodoni Moda. */
  name: string;
  /** Optional alias ("The Caped Crusader"). Italic in Fraunces. */
  nickname?: string;
  /** Avatar URL. Falls back to monogram if missing. */
  avatarUrl?: string;
  /** Current emotion (confident, tilted, focused...). Drives the wax-seal badge. */
  emotion?: string;
  /** Subtitle archetype — "TIGHT-AGGRESSIVE", "MANIAC", etc. */
  playStyle?: string;
  /** Free-form attitude descriptor. */
  attitude?: string;
  /** Free-form confidence descriptor. */
  confidence?: string;
  /** Personality knobs (0–1). Each rendered as a tally strip. */
  traits?: {
    bluffTendency?: number;
    aggression?: number;
    chattiness?: number;
    emojiUsage?: number;
  };
  /** Observed-at-table stats (only shown if handsObserved > 0). */
  observed?: {
    handsObserved?: number;
    vpip?: number;          // 0–1
    pfr?: number;           // 0–1
    aggressionFactor?: number;
  };
  /** Live chip context — shown when present (in-game only). */
  chips?: {
    atTable?: number;
    bankroll?: number;
  };
  /** Sponsor / affiliations (cash mode). */
  affiliation?: {
    sponsor?: string;
    relationship?: RelationshipKind;
    relationshipNote?: string;
  };
  /** A recent quote, last action commentary, or signature line. */
  remark?: string;
  /** Optional file number — auto-derived from name if absent. */
  fileNumber?: string;
}

export interface CharacterDetailCardProps {
  isOpen: boolean;
  onClose: () => void;
  character: CharacterDossierData;
  /**
   * Origin point in viewport coordinates (e.g. the clicked
   * avatar's center). The card unfolds toward the screen center
   * from this point so the open animation feels rooted in the
   * thing you clicked.
   */
  origin?: { x: number; y: number };
}

const RELATIONSHIP_COPY: Record<RelationshipKind, { label: string; tone: string }> = {
  rival:       { label: 'RIVALRY', tone: 'crimson' },
  friend:      { label: 'TRUSTED', tone: 'emerald' },
  sponsor:     { label: 'BACKED BY', tone: 'gold' },
  neutral:     { label: 'NEUTRAL', tone: 'ink' },
  admirer:     { label: 'ADMIRER', tone: 'gold' },
  antagonist:  { label: 'ANTAGONIST', tone: 'crimson' },
};

function deriveFileNumber(name: string): string {
  // Deterministic "looks like a real case file" id from the name.
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const block = String.fromCharCode(65 + (h % 26));
  const digits = String(1000 + (h % 8999)).padStart(4, '0');
  return `${block}-${digits}`;
}

function monogram(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

/** Tally strip: 10 marks, the first `value*10` filled with hand-drawn ticks. */
function TallyStrip({ value, label, readout }: { value: number; label: string; readout?: string }) {
  const filled = Math.max(0, Math.min(10, Math.round(value * 10)));
  return (
    <div className="dossier__tally-row">
      <div className="dossier__tally-label">{label}</div>
      <div className="dossier__tally-strip" aria-hidden="true">
        {Array.from({ length: 10 }).map((_, i) => (
          <motion.span
            key={i}
            className={`dossier__tick${i < filled ? ' is-filled' : ''}`}
            initial={{ scaleY: 0, opacity: 0 }}
            animate={{ scaleY: 1, opacity: 1 }}
            transition={{
              delay: 0.4 + i * 0.03,
              duration: 0.18,
              ease: [0.2, 0.8, 0.2, 1],
            }}
          />
        ))}
      </div>
      <div className="dossier__tally-readout">
        {readout ?? `${Math.round(value * 100)}%`}
      </div>
    </div>
  );
}

function DataRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="dossier__data-row">
      <span className="dossier__data-label">{label}</span>
      <span className="dossier__data-leader" aria-hidden="true" />
      <span className="dossier__data-value">{value}</span>
    </div>
  );
}

function SectionRule({ children }: { children: React.ReactNode }) {
  return (
    <div className="dossier__section-rule">
      <span className="dossier__rule-line" />
      <span className="dossier__rule-label">{children}</span>
      <span className="dossier__rule-line" />
    </div>
  );
}

export function CharacterDetailCard({
  isOpen,
  onClose,
  character,
  origin,
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

  const fileNumber = useMemo(
    () => character.fileNumber ?? deriveFileNumber(character.name),
    [character.fileNumber, character.name],
  );

  // Origin-based transform for the open animation. If no origin
  // given, fall back to dead center (looks like the card just lands).
  const originStyle = useMemo<React.CSSProperties>(() => {
    if (!origin) return {};
    return {
      transformOrigin: `${origin.x}px ${origin.y}px`,
    };
  }, [origin]);

  const hasTraits = !!character.traits && Object.values(character.traits).some(v => v !== undefined);
  const hasObserved = !!character.observed && (character.observed.handsObserved ?? 0) > 0;
  const hasChips = !!character.chips && (
    character.chips.atTable !== undefined || character.chips.bankroll !== undefined
  );
  const hasAffiliation = !!character.affiliation?.sponsor || !!character.affiliation?.relationship;

  const relationship = character.affiliation?.relationship;
  const relMeta = relationship ? RELATIONSHIP_COPY[relationship] : null;

  return (
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
          aria-label={`Dossier for ${character.name}`}
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

            <section className="dossier__subject">
              <div className="dossier__portrait-frame">
                <div className="dossier__portrait">
                  {character.avatarUrl ? (
                    <img
                      src={character.avatarUrl}
                      alt={`${character.name} portrait`}
                      className="dossier__portrait-img"
                      onError={(e) => {
                        // If the image 404s, fall back to monogram by
                        // hiding the img so the underlying initial shows.
                        (e.currentTarget as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  ) : null}
                  <span className="dossier__portrait-monogram" aria-hidden="true">
                    {monogram(character.name)}
                  </span>
                </div>
                {character.emotion && (
                  <div className="dossier__wax-seal" title={`current state: ${character.emotion}`}>
                    <span className="dossier__wax-text">{character.emotion}</span>
                  </div>
                )}
              </div>

              <div className="dossier__subject-text">
                <div className="dossier__eyebrow">SUBJECT</div>
                <h2 className="dossier__name">{character.name}</h2>
                {character.nickname && (
                  <div className="dossier__nickname">
                    <span className="dossier__quote-marks" aria-hidden="true">&ldquo;</span>
                    {character.nickname}
                    <span className="dossier__quote-marks" aria-hidden="true">&rdquo;</span>
                  </div>
                )}
                {character.playStyle && (
                  <div className="dossier__archetype">
                    {character.playStyle}
                  </div>
                )}
              </div>
            </section>

            <SectionRule>PROFILE</SectionRule>
            <section className="dossier__profile">
              {character.attitude && (
                <DataRow label="Attitude" value={character.attitude} />
              )}
              {character.confidence && (
                <DataRow label="Confidence" value={character.confidence} />
              )}
            </section>

            {hasTraits && (
              <>
                <SectionRule>BEHAVIORAL INDEX</SectionRule>
                <section className="dossier__behavior">
                  {character.traits?.bluffTendency !== undefined && (
                    <TallyStrip
                      value={character.traits.bluffTendency}
                      label="Bluff"
                    />
                  )}
                  {character.traits?.aggression !== undefined && (
                    <TallyStrip
                      value={character.traits.aggression}
                      label="Aggression"
                    />
                  )}
                  {character.traits?.chattiness !== undefined && (
                    <TallyStrip
                      value={character.traits.chattiness}
                      label="Chattiness"
                    />
                  )}
                  {character.traits?.emojiUsage !== undefined && (
                    <TallyStrip
                      value={character.traits.emojiUsage}
                      label="Theatrics"
                    />
                  )}
                </section>
              </>
            )}

            {(hasChips || hasObserved) && (
              <>
                <SectionRule>TABLE POSTURE</SectionRule>
                <section className="dossier__posture">
                  {character.chips?.atTable !== undefined && (
                    <DataRow
                      label="Chips at table"
                      value={<span className="dossier__money">${character.chips.atTable.toLocaleString()}</span>}
                    />
                  )}
                  {character.chips?.bankroll !== undefined && (
                    <DataRow
                      label="Bankroll"
                      value={<span className="dossier__money">${character.chips.bankroll.toLocaleString()}</span>}
                    />
                  )}
                  {hasObserved && character.observed?.handsObserved !== undefined && (
                    <DataRow
                      label="Hands observed"
                      value={character.observed.handsObserved.toLocaleString()}
                    />
                  )}
                  {character.observed?.vpip !== undefined && (
                    <DataRow
                      label="VPIP"
                      value={`${Math.round(character.observed.vpip * 100)}%`}
                    />
                  )}
                  {character.observed?.pfr !== undefined && (
                    <DataRow
                      label="PFR"
                      value={`${Math.round(character.observed.pfr * 100)}%`}
                    />
                  )}
                  {character.observed?.aggressionFactor !== undefined && (
                    <DataRow
                      label="Aggression factor"
                      value={character.observed.aggressionFactor.toFixed(1)}
                    />
                  )}
                </section>
              </>
            )}

            {hasAffiliation && (
              <>
                <SectionRule>AFFILIATIONS</SectionRule>
                <section className="dossier__affiliation">
                  {character.affiliation?.sponsor && (
                    <DataRow
                      label="Sponsor"
                      value={character.affiliation.sponsor.toUpperCase()}
                    />
                  )}
                  {relMeta && (
                    <div className="dossier__rel-tag-row">
                      <span className={`dossier__rel-tag dossier__rel-tag--${relMeta.tone}`}>
                        <span className="dossier__rel-tag-pin" aria-hidden="true" />
                        {relMeta.label}
                      </span>
                      {character.affiliation?.relationshipNote && (
                        <span className="dossier__rel-note">
                          — {character.affiliation.relationshipNote}
                        </span>
                      )}
                    </div>
                  )}
                </section>
              </>
            )}

            {character.remark && (
              <>
                <SectionRule>OBSERVED REMARK</SectionRule>
                <blockquote className="dossier__remark">
                  <span className="dossier__remark-flourish" aria-hidden="true">¶</span>
                  <span className="dossier__remark-text">{character.remark}</span>
                  <footer className="dossier__remark-attrib">
                    — table mic, hand №&nbsp;{fileNumber.split('-')[1] ?? '0000'}
                  </footer>
                </blockquote>
              </>
            )}

            <footer className="dossier__footer">
              <span className="dossier__footer-mark" aria-hidden="true">♠</span>
              <span className="dossier__footer-text">
                END OF FILE · DO NOT REMOVE FROM PREMISES
              </span>
              <span className="dossier__footer-mark" aria-hidden="true">♠</span>
            </footer>
          </motion.article>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
