/**
 * ReputationPanel — the cash lobby's reputation scoreboard.
 *
 * Sits just below CareerHero: bankroll is the hero, reputation is the
 * second axis bankroll can't measure — *who you are at the table*. Two
 * bars (renown, a one-way fame meter; regard, a centered beloved↔reviled
 * dial) and a quadrant badge that names where you stand.
 *
 * Read-only in v1 — the world doesn't yet respond to these numbers; this is
 * the scoreboard that makes the hero/villain path *visible*. The component
 * renders nothing until the world ticker has captured at least once (the
 * parent guards on `reputation` being non-null).
 *
 * Data comes from `/api/cash/lobby` (`reputation`). See
 * docs/plans/CASH_MODE_PLAYER_PRESTIGE.md.
 */

import { memo } from 'react';
import type { ReputationData, ReputationQuadrant } from './types';
import './ReputationPanel.css';

/** Quadrant → CSS tone modifier (drives the accent colour). */
const QUADRANT_TONE: Record<ReputationQuadrant, string> = {
  'Beloved Legend': 'beloved',
  'Infamous Villain': 'villain',
  'Up-and-comer': 'comer',
  'Disliked Nobody': 'nobody',
};

/** Quadrant → glyph shown in the badge. */
const QUADRANT_ICON: Record<ReputationQuadrant, string> = {
  'Beloved Legend': '★',
  'Infamous Villain': '⚔',
  'Up-and-comer': '↑',
  'Disliked Nobody': '·',
};

/** Quadrant → one-line blurb under the badge. */
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
  const { renown, regard, quadrant, opponent_count } = reputation;
  const tone = QUADRANT_TONE[quadrant] ?? 'comer';

  // renown ∈ [0,1] → a left-anchored fill across the whole track.
  const renownPct = Math.round(Math.max(0, Math.min(1, renown)) * 100);
  // regard ∈ [-1,1] → grows from the centre: right when warm, left when
  // hostile. Half-track each side, so |regard| * 50% of the full width.
  const regardMag = Math.max(0, Math.min(1, Math.abs(regard)));
  const regardHalfPct = regardMag * 50;
  const regardWarm = regard >= 0;
  const regardReadout = `${regardWarm ? '+' : '−'}${Math.round(regardMag * 100)}`;

  return (
    <section className={`rep-panel rep-panel--${tone}`} aria-label="Your reputation">
      <div className="rep-panel__head">
        <span className="rep-panel__eyebrow">Reputation</span>
        <span
          className="rep-panel__badge"
          title={
            opponent_count > 0
              ? `${opponent_count} ${opponent_count === 1 ? 'player has' : 'players have'} an opinion of you`
              : 'The room is still forming an opinion of you'
          }
        >
          <span className="rep-panel__badge-icon" aria-hidden="true">
            {QUADRANT_ICON[quadrant]}
          </span>
          {quadrant}
        </span>
      </div>

      <p className="rep-panel__blurb">{QUADRANT_BLURB[quadrant]}</p>

      <div className="rep-panel__axes">
        {/* Renown — one-way fame meter. */}
        <div className="rep-panel__axis">
          <div className="rep-panel__axis-row">
            <span className="rep-panel__axis-name">Renown</span>
            <span className="rep-panel__axis-value">{renownPct}</span>
          </div>
          <div className="rep-panel__track" aria-hidden="true">
            <div className="rep-panel__fill rep-panel__fill--renown" style={{ width: `${renownPct}%` }} />
          </div>
        </div>

        {/* Regard — centered beloved↔reviled dial. */}
        <div className="rep-panel__axis">
          <div className="rep-panel__axis-row">
            <span className="rep-panel__axis-name">Regard</span>
            <span className={`rep-panel__axis-value rep-panel__axis-value--${regardWarm ? 'warm' : 'hostile'}`}>
              {regardReadout}
            </span>
          </div>
          <div className="rep-panel__track rep-panel__track--centered" aria-hidden="true">
            <span className="rep-panel__center-mark" />
            <div
              className={`rep-panel__fill rep-panel__fill--regard-${regardWarm ? 'warm' : 'hostile'}`}
              style={
                regardWarm
                  ? { left: '50%', width: `${regardHalfPct}%` }
                  : { right: '50%', width: `${regardHalfPct}%` }
              }
            />
          </div>
        </div>
      </div>
    </section>
  );
}

export const ReputationPanel = memo(ReputationPanelInner);
