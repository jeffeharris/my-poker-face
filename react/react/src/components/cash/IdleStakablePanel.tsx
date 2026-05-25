/**
 * IdleStakablePanel — Phase 5 refinement (2026-05-21).
 *
 * Sits below the lobby's table grid. Shows the curated per-tier list
 * of AIs the player can offer a stake to right now (returned by
 * `/api/cash/stakable-ai`). Per the locked design rules:
 *
 *   - Only AIs *not currently in a session* surface here. Seated
 *     AIs are off-limits (the route would refuse the offer anyway).
 *   - Capped at ~3 candidates per tier so the menu stays focused.
 *   - Each candidate's `target_stake_label` is the only tier the
 *     player can stake them at (comfort_zone + 1, the
 *     "help-them-work-up-the-ranks" rule). The modal then locks the
 *     tier picker to that one option.
 *
 * Empty state: a friendly "no one's ready right now" message when
 * no AI clears every gate (cooldowns, met-before, relationship floor,
 * etc.). Not an error condition — it's expected during early play.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Coins, RefreshCw } from 'lucide-react';
import { getStakableAi } from './api';
import type { StakableAiCandidate, StakableAiResponse } from './types';
import { absolutizeAvatarUrl } from './avatarUrl';
// type-only import keeps the Lobby ↔ IdleStakablePanel cycle erased at runtime
import type { AiSeatClick } from './Lobby';
import { logger } from '../../utils/logger';
import './IdleStakablePanel.css';

interface IdleStakablePanelProps {
  /** Polled by parent (Lobby) — when this changes we re-fetch so the
   *  panel stays in sync with table state. Bumping a tick counter
   *  from the lobby's poll loop is the simplest signal. */
  refreshKey: number;
  /** Fires when the player taps "Stake" on a candidate. Lobby opens
   *  the StakeOfferModal pre-targeted to this AI. */
  onStake: (candidate: StakableAiCandidate, targetStakeLabel: string) => void;
  /** Fires when the player taps a candidate's portrait — Lobby opens
   *  the dossier (same handler as the table-card seat portraits). */
  onOpenDossier: (click: AiSeatClick) => void;
}

/** Read the desperation signal into a soft cue. Avoid showing the
 *  raw number — it's a backstage signal, not a player-facing stat. */
function desperationLabel(desperation: number): string {
  if (desperation >= 0.65) return 'rough patch';
  if (desperation >= 0.35) return 'between sessions';
  return '';
}

export function IdleStakablePanel({ refreshKey, onStake, onOpenDossier }: IdleStakablePanelProps) {
  const [data, setData] = useState<StakableAiResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Mirrors whether we've ever loaded data, readable from the dep-free
  // `load` callback without forcing it (and the poll effect) to re-run.
  const hasDataRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const response = await getStakableAi();
      hasDataRef.current = true;
      setData(response);
      setLoadError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Failed to load stakable AI:', msg);
      // This panel refetches on every lobby poll tick (~8s). A
      // transient failure on a background refresh — e.g. a one-off
      // 429 — shouldn't wipe out the candidates we already have.
      // Only surface a hard error on the *initial* load, when there's
      // nothing else to show. Subsequent polls quietly recover.
      if (!hasDataRef.current) setLoadError(msg);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  if (loadError) {
    return (
      <section className="idle-stakable-panel">
        <h2 className="idle-stakable-panel__heading">Players willing to be staked</h2>
        <div className="idle-stakable-panel__error" role="alert">
          {loadError}
        </div>
      </section>
    );
  }

  if (!data) {
    return (
      <section className="idle-stakable-panel">
        <h2 className="idle-stakable-panel__heading">Players willing to be staked</h2>
        <div className="idle-stakable-panel__loading">Loading…</div>
      </section>
    );
  }

  const totalCandidates = data.by_tier.reduce((sum, tier) => sum + tier.candidates.length, 0);

  return (
    <section className="idle-stakable-panel">
      <div className="idle-stakable-panel__header">
        <h2 className="idle-stakable-panel__heading">
          <Coins size={16} aria-hidden="true" />
          <span>Players willing to be staked</span>
        </h2>
        <button
          type="button"
          className="idle-stakable-panel__refresh"
          onClick={() => void load()}
          title="Refresh available list"
          aria-label="Refresh stakable AI list"
        >
          <RefreshCw size={14} aria-hidden="true" />
        </button>
      </div>

      {totalCandidates === 0 ? (
        <p className="idle-stakable-panel__empty">
          No one's ready for a stake right now. Play more hands, build relationships, then check
          back — staking unlocks once you've shared a table with someone.
        </p>
      ) : (
        <div className="idle-stakable-panel__tiers">
          {data.by_tier.map((tier) => (
            <div key={tier.stake_label} className="idle-stakable-panel__tier">
              <div className="idle-stakable-panel__tier-header">
                <span className="idle-stakable-panel__tier-label">
                  Stake into {tier.stake_label}
                </span>
                <span className="idle-stakable-panel__tier-meta">
                  min ${tier.min_buy_in.toLocaleString()}
                </span>
              </div>
              <ul className="idle-stakable-panel__list">
                {tier.candidates.map((c) => (
                  <CandidateCard
                    key={c.personality_id}
                    candidate={c}
                    targetStakeLabel={tier.stake_label}
                    onStake={onStake}
                    onOpenDossier={onOpenDossier}
                  />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

interface CandidateCardProps {
  candidate: StakableAiCandidate;
  targetStakeLabel: string;
  onStake: (candidate: StakableAiCandidate, targetStakeLabel: string) => void;
  onOpenDossier: (click: AiSeatClick) => void;
}

function CandidateCard({
  candidate,
  targetStakeLabel,
  onStake,
  onOpenDossier,
}: CandidateCardProps) {
  const handleClick = useCallback(() => {
    onStake(candidate, targetStakeLabel);
  }, [candidate, targetStakeLabel, onStake]);

  // Tapping the portrait/name opens the dossier — mirrors the table-card
  // seat behavior so the gesture is consistent across the lobby. Origin
  // is the tapped element's center so the card can animate out from it.
  const handleOpenDossier = useCallback(
    (e: React.MouseEvent<HTMLButtonElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      onOpenDossier({
        dossier: {
          name: candidate.name,
          avatarUrl: absolutizeAvatarUrl(candidate.avatar_url) ?? undefined,
          emotion: candidate.emotion,
          affiliation: candidate.relationship_hint
            ? {
                relationship: 'neutral',
                relationshipNote: candidate.relationship_hint,
              }
            : undefined,
        },
        origin: { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 },
        identifier: candidate.personality_id,
      });
    },
    [candidate, onOpenDossier]
  );

  const desperation = desperationLabel(candidate.desperation);
  const avatarSrc = absolutizeAvatarUrl(candidate.avatar_url);

  return (
    <li className="idle-stakable-panel__card">
      <button
        type="button"
        className="idle-stakable-panel__identity"
        onClick={handleOpenDossier}
        title={`View ${candidate.name}'s dossier`}
        aria-label={`Open dossier for ${candidate.name}`}
      >
        <span className="idle-stakable-panel__avatar">
          {avatarSrc ? (
            <img src={avatarSrc} alt="" loading="lazy" />
          ) : (
            <span aria-hidden="true">{candidate.name.charAt(0).toUpperCase()}</span>
          )}
        </span>
        <span className="idle-stakable-panel__body">
          <span className="idle-stakable-panel__name">{candidate.name}</span>
          <span className="idle-stakable-panel__meta">
            <span className="idle-stakable-panel__comfort">plays {candidate.comfort_zone}</span>
            {candidate.relationship_hint && (
              <>
                <span className="idle-stakable-panel__sep">·</span>
                <span className="idle-stakable-panel__hint">{candidate.relationship_hint}</span>
              </>
            )}
            {desperation && (
              <>
                <span className="idle-stakable-panel__sep">·</span>
                <span className="idle-stakable-panel__desperation">{desperation}</span>
              </>
            )}
          </span>
        </span>
      </button>
      <button type="button" className="idle-stakable-panel__cta" onClick={handleClick}>
        Stake
      </button>
    </li>
  );
}
