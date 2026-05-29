/**
 * ReputationPanel — the cash lobby's reputation "standing" crest.
 *
 * Sits just below CareerHero: bankroll is the hero, reputation is the second
 * axis bankroll can't measure — *who you are at the table*. Rendered as a
 * heraldic plaque: the quadrant is a TITLE stamped like a wax seal; RENOWN is
 * an engraved gold gauge (fame magnitude); REGARD is a bipolar needle dial
 * (reviled ↔ beloved). An expandable ledger explains the standing.
 *
 * Read-only in v1 — the scoreboard that makes the hero/villain path *visible*.
 * Renders nothing until the world ticker has captured once (parent guards on
 * `reputation` being non-null). Data: `/api/cash/lobby` (`reputation`). See
 * docs/plans/CASH_MODE_PLAYER_PRESTIGE.md.
 */

import { memo, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ChevronDown, Crown, Ghost, Swords, TrendingUp } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReputationComponents, ReputationData, ReputationQuadrant } from './types';
import './ReputationPanel.css';

/** Renown drivers, each with the weight cap it saturates at (must match the
 *  W_* constants in cash_mode/prestige.py). The bar fills to value/max; the
 *  number shown is the contribution in points (value × 100). */
const RENOWN_DRIVERS: { key: keyof ReputationComponents; label: string; max: number }[] = [
  { key: 'breadth', label: 'Breadth — who knows you', max: 0.25 },
  { key: 'stake_tier', label: 'Stakes reached', max: 0.25 },
  { key: 'beat_respected', label: 'Beating respected players', max: 0.2 },
  { key: 'tenure', label: 'Time at the tables', max: 0.2 },
  { key: 'high_stakes', label: 'High-stakes wins', max: 0.1 },
];

/** Regard drivers — signed contributions (heat only ever subtracts). */
const REGARD_DRIVERS: { key: keyof ReputationComponents; label: string }[] = [
  { key: 'likability', label: 'Likability' },
  { key: 'respect', label: 'Respect' },
  { key: 'heat', label: 'Heat — notoriety' },
];

const pts = (v: number) => Math.round(v * 100);
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Quadrant → CSS tone modifier (drives the --rep-accent colour). */
const QUADRANT_TONE: Record<ReputationQuadrant, string> = {
  'Beloved Legend': 'beloved',
  'Infamous Villain': 'villain',
  'Up-and-comer': 'comer',
  'Disliked Nobody': 'nobody',
};

/** Quadrant → the sigil stamped into the seal. */
const QUADRANT_ICON: Record<ReputationQuadrant, LucideIcon> = {
  'Beloved Legend': Crown,
  'Infamous Villain': Swords,
  'Up-and-comer': TrendingUp,
  'Disliked Nobody': Ghost,
};

/** Quadrant → one-line standing blurb. */
const QUADRANT_BLURB: Record<ReputationQuadrant, string> = {
  'Beloved Legend': 'The room respects you — and the welcome is warm.',
  'Infamous Villain': 'Feared and reviled, and impossible to ignore.',
  'Up-and-comer': 'Warmly regarded — still earning your name.',
  'Disliked Nobody': 'Little renown yet, and the room runs cold.',
};

export interface ReputationPanelProps {
  reputation: ReputationData;
}

function ReputationPanelInner({ reputation }: ReputationPanelProps) {
  const { renown, regard, quadrant, opponent_count, components } = reputation;
  const tone = QUADRANT_TONE[quadrant] ?? 'comer';
  const Sigil = QUADRANT_ICON[quadrant] ?? TrendingUp;
  const [open, setOpen] = useState(false);
  const reduce = useReducedMotion();

  // Renown drivers, biggest contribution first; drop zero-contribution rows.
  const renownRows = RENOWN_DRIVERS.map((d) => ({ ...d, value: components?.[d.key] ?? 0 }))
    .filter((r) => r.value > 0.0005)
    .sort((a, b) => b.value - a.value);
  // Regard drivers, biggest magnitude first; drop ~zero rows.
  const regardRows = REGARD_DRIVERS.map((d) => ({ ...d, value: components?.[d.key] ?? 0 }))
    .filter((r) => Math.abs(r.value) > 0.0005)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const hasLedger = !!components && (renownRows.length > 0 || regardRows.length > 0);

  const renownPct = Math.round(clamp(renown, 0, 1) * 100);
  const regardWarm = regard >= 0;
  const regardPts = Math.round(Math.abs(regard) * 100);
  // Needle position across the full bipolar track: −1 → 0%, 0 → 50%, +1 → 100%.
  const regardPos = ((clamp(regard, -1, 1) + 1) / 2) * 100;

  // The seal "stamps" down on mount — the signature high-impact moment.
  const stamp = reduce
    ? {}
    : {
        initial: { scale: 1.55, opacity: 0, rotate: -12 },
        animate: { scale: 1, opacity: 1, rotate: 0 },
        transition: { type: 'spring' as const, stiffness: 320, damping: 19, delay: 0.06 },
      };

  return (
    <section className={`rep-panel rep-panel--${tone}`} aria-label={`Your reputation: ${quadrant}`}>
      <span className="rep-panel__bloom" aria-hidden="true" />
      <span className="rep-panel__spine" aria-hidden="true" />

      <header className="rep-panel__head">
        <span className="rep-panel__eyebrow">Standing</span>
        <span
          className="rep-panel__known"
          title={
            opponent_count > 0
              ? `${opponent_count} ${opponent_count === 1 ? 'player has' : 'players have'} an opinion of you`
              : 'The room is still forming an opinion of you'
          }
        >
          known by {opponent_count}
        </span>
      </header>

      <div className="rep-panel__crest">
        <motion.span className="rep-panel__seal" aria-hidden="true" {...stamp}>
          <span className="rep-panel__seal-ring" />
          <Sigil size={22} strokeWidth={1.75} />
        </motion.span>
        <span className="rep-panel__title-block">
          <span className="rep-panel__title">{quadrant}</span>
          <span className="rep-panel__blurb">{QUADRANT_BLURB[quadrant]}</span>
        </span>
      </div>

      <div className="rep-panel__meters">
        {/* Renown — engraved one-way fame gauge. */}
        <div className="rep-panel__meter">
          <div className="rep-panel__meter-head">
            <span className="rep-panel__meter-name">Renown</span>
            <span className="rep-panel__meter-figure">{renownPct}</span>
          </div>
          <div
            className="rep-panel__rail"
            role="meter"
            aria-valuenow={renownPct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Renown"
          >
            <motion.span
              className="rep-panel__rail-fill"
              initial={reduce ? false : { width: 0 }}
              animate={{ width: `${renownPct}%` }}
              transition={reduce ? undefined : { duration: 0.85, ease: [0.16, 1, 0.3, 1], delay: 0.18 }}
            />
            <span className="rep-panel__rail-ticks" aria-hidden="true" />
          </div>
        </div>

        {/* Regard — bipolar reviled↔beloved needle dial. */}
        <div className="rep-panel__meter">
          <div className="rep-panel__meter-head">
            <span className="rep-panel__meter-name">Regard</span>
            <span
              className={`rep-panel__meter-figure rep-panel__meter-figure--${regardWarm ? 'warm' : 'hostile'}`}
            >
              {regardWarm ? '+' : '−'}
              {regardPts}
            </span>
          </div>
          <div
            className="rep-panel__dial"
            role="meter"
            aria-valuenow={Math.round(clamp(regard, -1, 1) * 100)}
            aria-valuemin={-100}
            aria-valuemax={100}
            aria-label="Regard, from reviled to beloved"
          >
            <span className="rep-panel__dial-track" aria-hidden="true" />
            <span className="rep-panel__dial-origin" aria-hidden="true" />
            <motion.span
              className={`rep-panel__needle rep-panel__needle--${regardWarm ? 'warm' : 'hostile'}`}
              aria-hidden="true"
              initial={reduce ? false : { left: '50%' }}
              animate={{ left: `${regardPos}%` }}
              transition={reduce ? undefined : { type: 'spring', stiffness: 120, damping: 15, delay: 0.32 }}
            />
          </div>
          <div className="rep-panel__poles" aria-hidden="true">
            <span>Reviled</span>
            <span>Beloved</span>
          </div>
        </div>
      </div>

      {hasLedger && (
        <>
          <button
            type="button"
            className="rep-panel__why"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <span>{open ? 'Hide the ledger' : 'Why?'}</span>
            <ChevronDown
              className={`rep-panel__why-caret${open ? ' is-open' : ''}`}
              size={14}
              strokeWidth={2.25}
              aria-hidden="true"
            />
          </button>

          <AnimatePresence initial={false}>
            {open && (
              <motion.div
                className="rep-panel__ledger"
                initial={reduce ? undefined : { height: 0, opacity: 0 }}
                animate={reduce ? undefined : { height: 'auto', opacity: 1 }}
                exit={reduce ? undefined : { height: 0, opacity: 0 }}
                transition={{ duration: 0.34, ease: [0.16, 1, 0.3, 1] }}
              >
                <div className="rep-panel__ledger-inner">
                  {renownRows.length > 0 && (
                    <div className="rep-panel__group">
                      <span className="rep-panel__group-title">What makes you a figure</span>
                      {renownRows.map((r) => (
                        <div className="rep-panel__driver" key={r.key}>
                          <span className="rep-panel__driver-label">{r.label}</span>
                          <span className="rep-panel__driver-track" aria-hidden="true">
                            <span
                              className="rep-panel__driver-fill"
                              style={{ width: `${Math.min(100, (r.value / r.max) * 100)}%` }}
                            />
                          </span>
                          <span className="rep-panel__driver-value">{pts(r.value)}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {regardRows.length > 0 && (
                    <div className="rep-panel__group rep-panel__group--regard">
                      <span className="rep-panel__group-title">How the room feels</span>
                      {regardRows.map((r) => {
                        const warm = r.value >= 0;
                        return (
                          <div className="rep-panel__driver" key={r.key}>
                            <span className="rep-panel__driver-label">{r.label}</span>
                            <span
                              className={`rep-panel__driver-value rep-panel__driver-value--${warm ? 'warm' : 'hostile'}`}
                            >
                              {warm ? '+' : '−'}
                              {Math.abs(pts(r.value))}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </>
      )}
    </section>
  );
}

export const ReputationPanel = memo(ReputationPanelInner);
