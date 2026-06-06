/**
 * DossierSections — the stacked, self-gating body of the dossier card:
 * everything below the PROFILE block (BEHAVIORAL INDEX → OBSERVED REMARK).
 *
 * Each section silently drops out when its inputs are missing, so the same
 * component renders "lobby with no live game" and "mid-hand at the table".
 * The presence flags that used to live in CharacterDetailCard's body are
 * computed here, co-located with the markup they gate.
 *
 * Two slots are rendered by the parent and passed in as nodes:
 *   • `creditHistory` — the TABLE POSTURE credit row (needs the buy callback)
 *   • the FIELD NOTES textarea is driven by the `note` controller / `showNotes`
 */

import { OpponentSizingTell } from '../OpponentSizingTell';
import type { DossierResponse } from '../api';
import { DataRow, SectionRule, TallyStrip } from './primitives';
import type { DossierMergedView, DossierNoteController } from './useDossierState';
import { RELATIONSHIP_COPY, type CharacterDossierData } from './types';

export function DossierSections({
  fetched,
  merged,
  character,
  fileNumber,
  creditHistory,
  note,
  showNotes,
}: {
  fetched: DossierResponse | null;
  merged: DossierMergedView;
  character: CharacterDossierData;
  fileNumber: string;
  creditHistory: React.ReactNode;
  note: DossierNoteController;
  showNotes: boolean;
}) {
  // ─── Section-presence flags ─────────────────────────────────
  // BEHAVIORAL INDEX reads the curated anchor subset from the server fetch.
  // Static-prop fallback is intentionally absent — anchors live on the
  // personality config, which only the dossier endpoint resolves.
  const anchors = fetched?.personality?.anchors ?? null;
  const hasAnchors = !!anchors && Object.values(anchors).some((v) => v != null);
  const hasObserved = !!merged.observed && (merged.observed.handsObserved ?? 0) > 0;
  // Tier-2 deep postflop reads. The server nulls each field when its grind
  // tier is still locked (or there's no data yet); we render only the rows
  // that survived, and the whole section only when at least one did.
  const deeperReads = fetched?.deeper_reads ?? null;
  const hasDeeperReads =
    !!deeperReads &&
    (Object.keys(deeperReads) as (keyof typeof deeperReads)[]).some(
      (k) => k !== 'lifetime' && deeperReads[k] != null
    );
  // B2 "the read": exploit advice + archetype badge.
  const theRead = fetched?.the_read ?? [];
  const archetype = fetched?.archetype ?? null;
  const hasRead = theRead.length > 0 || !!archetype;
  // B3 emotional read + B4 field standing.
  const temperament = fetched?.temperament ?? null;
  const hasTemperament =
    !!temperament &&
    (temperament.lines.length > 0 ||
      temperament.tilt_label != null ||
      temperament.poise != null ||
      temperament.expressiveness != null);
  const fieldPos = fetched?.field_position ?? null;
  const hasFieldPos = !!fieldPos && (!!fieldPos.vpip_label || !!fieldPos.af_label);
  // "The history" — rivalry read.
  const history = fetched?.relationship_history ?? null;
  const hasHistory =
    !!history && (history.clash.length > 0 || history.banter.length > 0 || !!history.defining);
  const hasChips =
    !!character.chips &&
    (character.chips.atTable !== undefined || character.chips.bankroll !== undefined);
  const hasAffiliation = !!character.affiliation?.sponsor || !!character.affiliation?.relationship;
  const hasStanding = !!fetched?.relationship;
  // Pressure-summary surfaces only the highlights with non-zero values;
  // omitting them entirely keeps the card from showing rows of zeros
  // for opponents the human hasn't tangled with yet.
  const ps = fetched?.pressure_summary ?? null;
  const pressureRows: Array<[string, string]> = ps
    ? [
        ps.signature_move ? ['Signature move', ps.signature_move!] : null,
        (ps.biggest_pot_won ?? 0) > 0
          ? ['Biggest pot won', `$${ps.biggest_pot_won!.toLocaleString()}`]
          : null,
        (ps.biggest_pot_lost ?? 0) > 0
          ? ['Biggest pot lost', `$${ps.biggest_pot_lost!.toLocaleString()}`]
          : null,
        (ps.successful_bluffs ?? 0) > 0 ? ['Bluffs landed', `${ps.successful_bluffs}`] : null,
        (ps.bluffs_caught ?? 0) > 0 ? ['Bluffs caught', `${ps.bluffs_caught}`] : null,
        (ps.bad_beats ?? 0) > 0 ? ['Bad beats', `${ps.bad_beats}`] : null,
        (ps.headsup_wins ?? 0) + (ps.headsup_losses ?? 0) > 0
          ? ['Heads-up record', `${ps.headsup_wins ?? 0}–${ps.headsup_losses ?? 0}`]
          : null,
      ].filter((r): r is [string, string] => r !== null)
    : [];
  const hasPressureRows = pressureRows.length > 0;
  const memorable = fetched?.memorable_hands ?? [];
  const hasMemorable = memorable.length > 0;
  const hasTrackRecord = !!fetched?.cash_pair_stats || hasMemorable || hasPressureRows;

  return (
    <>
      {hasAnchors && anchors && (
        <>
          <SectionRule>BEHAVIORAL INDEX</SectionRule>
          <section className="dossier__behavior">
            {anchors.aggression != null && (
              <TallyStrip value={anchors.aggression} label="Aggression" />
            )}
            {anchors.looseness != null && (
              <TallyStrip value={anchors.looseness} label="Looseness" />
            )}
            {anchors.poise != null && <TallyStrip value={anchors.poise} label="Poise" />}
            {anchors.expressiveness != null && (
              <TallyStrip value={anchors.expressiveness} label="Expressiveness" />
            )}
            {anchors.risk != null && <TallyStrip value={anchors.risk} label="Risk" />}
          </section>
        </>
      )}

      {/* Surface B (SIZING_COACH_SURFACES.md): how readable this opponent's
          bet sizing is, over time. Self-fetches + self-titles; renders
          nothing until it has a gradeable read (no orphan section header).
          Reconciled with the scouting economy: shown only when the
          `sizing_polarization` read is unlocked (grind OR informant) — the
          dossier computes that authoritatively server-side. Outside the
          Circuit there's no scouting block, so it's ungated (as the rest of
          the dossier is). When locked, the scouting strip's "Sizing tell"
          teaser already advertises it as earnable. */}
      {character.name &&
        (!fetched?.scouting || fetched.scouting.unlocked.includes('sizing_polarization')) && (
          <OpponentSizingTell opponent={character.name} />
        )}

      {hasStanding && fetched?.relationship && (
        <>
          <SectionRule>STANDING</SectionRule>
          <section className="dossier__standing">
            <TallyStrip
              value={fetched.relationship.heat}
              label="Heat"
              readout={fetched.relationship.heat > 0 ? 'rivalry' : '—'}
            />
            <TallyStrip value={fetched.relationship.respect} label="Respect" />
            <TallyStrip value={fetched.relationship.likability} label="Likability" />
            {fetched.relationship.hint && (
              <div className="dossier__standing-hint">
                <span className="dossier__standing-mark" aria-hidden="true">
                  ›
                </span>
                <em>{fetched.relationship.hint}</em>
              </div>
            )}
          </section>
        </>
      )}

      {hasTrackRecord && (
        <>
          <SectionRule>TRACK RECORD</SectionRule>
          <section className="dossier__track">
            {fetched?.cash_pair_stats && (
              <>
                <DataRow
                  label="Lifetime PnL"
                  value={
                    <span
                      className={
                        'dossier__money dossier__money--' +
                        (fetched.cash_pair_stats.cumulative_pnl >= 0 ? 'pos' : 'neg')
                      }
                    >
                      {fetched.cash_pair_stats.cumulative_pnl >= 0 ? '+' : '−'}$
                      {Math.abs(fetched.cash_pair_stats.cumulative_pnl).toLocaleString()}
                    </span>
                  }
                />
                <DataRow
                  label="Cash hands"
                  value={fetched.cash_pair_stats.hands_played_cash.toLocaleString()}
                />
              </>
            )}
            {pressureRows.map(([label, value]) => (
              <DataRow key={label} label={label} value={value} />
            ))}
            {hasMemorable && (
              <ul className="dossier__memorable-list" aria-label="Memorable hands">
                {memorable.map((h) => (
                  <li key={h.hand_id} className="dossier__memorable">
                    <div className="dossier__memorable-head">
                      <span className="dossier__memorable-tag">{h.event.replace(/_/g, ' ')}</span>
                      <span className="dossier__memorable-impact" title="impact score">
                        {Math.round(h.impact_score * 100)}
                      </span>
                    </div>
                    <p className="dossier__memorable-narrative">{h.narrative}</p>
                    {h.hand_summary && (
                      <p className="dossier__memorable-summary">
                        <span aria-hidden="true">›</span> {h.hand_summary}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      {showNotes && (
        <>
          <SectionRule>FIELD NOTES</SectionRule>
          <section className="dossier__notes">
            <textarea
              className="dossier__notes-input"
              value={note.draft}
              onChange={note.onChange}
              placeholder="Tells, tendencies, anything worth remembering…"
              rows={4}
              maxLength={2000}
              spellCheck
            />
            <div className="dossier__notes-footer">
              <span
                className={`dossier__notes-status dossier__notes-status--${note.state}`}
                aria-live="polite"
              >
                {note.state === 'saving'
                  ? 'Saving…'
                  : note.state === 'saved'
                    ? '✓ Saved'
                    : note.state === 'error'
                      ? 'Couldn’t save'
                      : note.draft.length > 1800
                        ? `${note.draft.length} / 2000`
                        : ''}
              </span>
              <span className="dossier__notes-hint">autosaves · persists across sessions</span>
            </div>
          </section>
        </>
      )}

      {(hasChips || hasObserved || fetched?.ai_bankroll != null) && (
        <>
          <SectionRule>TABLE POSTURE</SectionRule>
          <section className="dossier__posture">
            {character.chips?.atTable !== undefined && (
              <DataRow
                label="Chips at table"
                value={
                  <span className="dossier__money">
                    ${character.chips.atTable.toLocaleString()}
                  </span>
                }
              />
            )}
            {fetched?.ai_bankroll != null && (
              <DataRow
                label="Total bankroll"
                value={
                  <span className="dossier__money">${fetched.ai_bankroll.toLocaleString()}</span>
                }
              />
            )}
            {character.chips?.bankroll !== undefined && (
              <DataRow
                label="Bankroll"
                value={
                  <span className="dossier__money">
                    ${character.chips.bankroll.toLocaleString()}
                  </span>
                }
              />
            )}
            {fetched?.stake_summary?.as_staker.total_owed_to_them ? (
              <DataRow
                label="Owed to them"
                value={
                  <span className="dossier__money">
                    ${fetched.stake_summary.as_staker.total_owed_to_them.toLocaleString()}
                    <span className="dossier__money-note">
                      {' '}
                      across {fetched.stake_summary.as_staker.carry_count}{' '}
                      {fetched.stake_summary.as_staker.carry_count === 1 ? 'carry' : 'carries'}
                    </span>
                  </span>
                }
              />
            ) : null}
            {fetched?.stake_summary?.as_borrower.total_carried ? (
              <DataRow
                label="They owe"
                value={
                  <span className="dossier__money">
                    ${fetched.stake_summary.as_borrower.total_carried.toLocaleString()}
                    <span className="dossier__money-note">
                      {' '}
                      across {fetched.stake_summary.as_borrower.carry_count}{' '}
                      {fetched.stake_summary.as_borrower.carry_count === 1 ? 'carry' : 'carries'}
                    </span>
                  </span>
                }
              />
            ) : null}
            {creditHistory}
            {hasObserved && merged.observed?.handsObserved !== undefined && (
              <DataRow
                label="Hands observed"
                value={merged.observed.handsObserved.toLocaleString()}
              />
            )}
            {merged.observed?.vpip != null && (
              <DataRow label="VPIP" value={`${Math.round(merged.observed.vpip * 100)}%`} />
            )}
            {merged.observed?.pfr != null && (
              <DataRow label="PFR" value={`${Math.round(merged.observed.pfr * 100)}%`} />
            )}
            {merged.observed?.aggressionFactor != null && (
              <DataRow
                label="Aggression factor"
                value={merged.observed.aggressionFactor.toFixed(1)}
              />
            )}
            {merged.observed?.playStyleLabel && (
              <DataRow label="Read" value={merged.observed.playStyleLabel} />
            )}
          </section>
        </>
      )}

      {hasDeeperReads && deeperReads && (
        <>
          <SectionRule>DEEP READ</SectionRule>
          <section className="dossier__posture">
            {deeperReads.fold_to_cbet != null && (
              <DataRow
                label="Fold to c-bet"
                value={`${Math.round(deeperReads.fold_to_cbet * 100)}%`}
              />
            )}
            {deeperReads.cbet_attempt_rate != null && (
              <DataRow
                label="C-bet frequency"
                value={`${Math.round(deeperReads.cbet_attempt_rate * 100)}%`}
              />
            )}
            {deeperReads.barrel_frequency != null && (
              <DataRow
                label="Barrel (turn)"
                value={`${Math.round(deeperReads.barrel_frequency * 100)}%`}
              />
            )}
            {deeperReads.third_barrel_frequency != null && (
              <DataRow
                label="Barrel (river)"
                value={`${Math.round(deeperReads.third_barrel_frequency * 100)}%`}
              />
            )}
            {deeperReads.aggression_factor_postflop != null && (
              <DataRow
                label="Postflop aggression"
                value={deeperReads.aggression_factor_postflop.toFixed(1)}
              />
            )}
            {deeperReads.all_in_frequency != null && (
              <DataRow
                label="All-in frequency"
                value={`${(deeperReads.all_in_frequency * 100).toFixed(1)}%`}
              />
            )}
            {deeperReads.equity_when_betting != null && (
              <DataRow
                label="Equity when betting"
                value={`${Math.round(deeperReads.equity_when_betting * 100)}%`}
              />
            )}
            {deeperReads.equity_when_raising != null && (
              <DataRow
                label="Equity when raising"
                value={`${Math.round(deeperReads.equity_when_raising * 100)}%`}
              />
            )}
            {deeperReads.equity_when_calling != null && (
              <DataRow
                label="Equity when calling"
                value={`${Math.round(deeperReads.equity_when_calling * 100)}%`}
              />
            )}
          </section>
        </>
      )}

      {hasRead && (
        <>
          <SectionRule>THE READ</SectionRule>
          <section className="dossier__read">
            {archetype && (
              <div className="dossier__read-badge">
                <span className="dossier__archetype">{archetype.label}</span>
              </div>
            )}
            {theRead.length > 0 ? (
              <ul className="dossier__read-tips">
                {theRead.map((tip) => (
                  <li key={tip.pattern} className="dossier__read-tip">
                    {tip.text}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="dossier__read-empty">No clear exploit yet — keep watching them play.</p>
            )}
          </section>
        </>
      )}

      {hasTemperament && temperament && (
        <>
          <SectionRule>TEMPERAMENT</SectionRule>
          <section className="dossier__posture">
            {temperament.tilt_label && (
              <DataRow
                label="Tilt"
                value={
                  temperament.tilt_score != null
                    ? `${temperament.tilt_label} (${Math.round(temperament.tilt_score * 100)}%)`
                    : temperament.tilt_label
                }
              />
            )}
            {temperament.poise != null && (
              <DataRow label="Composure" value={`${Math.round(temperament.poise * 100)}%`} />
            )}
            {temperament.expressiveness != null && (
              <DataRow
                label="Readability"
                value={`${Math.round(temperament.expressiveness * 100)}%`}
              />
            )}
            {temperament.lines.length > 0 && (
              <ul className="dossier__read-tips">
                {temperament.lines.map((line, i) => (
                  <li key={i} className="dossier__read-tip">
                    {line}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      {hasFieldPos && fieldPos && (
        <>
          <SectionRule>FIELD STANDING</SectionRule>
          <section className="dossier__read">
            <ul className="dossier__read-tips">
              {fieldPos.vpip_label && <li className="dossier__read-tip">{fieldPos.vpip_label}</li>}
              {fieldPos.af_label && <li className="dossier__read-tip">{fieldPos.af_label}</li>}
            </ul>
          </section>
        </>
      )}

      {hasHistory && history && (
        <>
          <SectionRule>THE HISTORY</SectionRule>
          <section className="dossier__history">
            <p className="dossier__history-line">{history.line}</p>
            {history.defining && (
              <div className="dossier__history-defining">
                <div className="dossier__history-defining-head">
                  <span className="dossier__history-defining-tag">{history.defining.label}</span>
                  <span className="dossier__history-defining-impact" title="impact score">
                    {Math.round(history.defining.impact_score * 100)}
                  </span>
                </div>
                {history.defining.narrative && (
                  <p className="dossier__history-defining-narrative">
                    {history.defining.narrative}
                  </p>
                )}
              </div>
            )}
            {history.clash.length > 0 && (
              <div className="dossier__history-tallies">
                {history.clash.map((c) => (
                  <span key={c.event} className="dossier__history-chip">
                    {c.label}
                    {c.count > 1 && (
                      <span className="dossier__history-chip-count"> ×{c.count}</span>
                    )}
                  </span>
                ))}
              </div>
            )}
            {history.banter.length > 0 && (
              <div className="dossier__history-tallies dossier__history-tallies--banter">
                {history.banter.map((c) => (
                  <span
                    key={c.event}
                    className="dossier__history-chip dossier__history-chip--banter"
                  >
                    {c.label}
                    {c.count > 1 && (
                      <span className="dossier__history-chip-count"> ×{c.count}</span>
                    )}
                  </span>
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {hasAffiliation && (
        <>
          <SectionRule>AFFILIATIONS</SectionRule>
          <section className="dossier__affiliation">
            {character.affiliation?.sponsor && (
              <DataRow label="Sponsor" value={character.affiliation.sponsor.toUpperCase()} />
            )}
            {character.affiliation?.relationship &&
              (() => {
                const relMeta = RELATIONSHIP_COPY[character.affiliation.relationship];
                return (
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
                );
              })()}
          </section>
        </>
      )}

      {character.remark && (
        <>
          <SectionRule>OBSERVED REMARK</SectionRule>
          <blockquote className="dossier__remark">
            <span className="dossier__remark-flourish" aria-hidden="true">
              ¶
            </span>
            <span className="dossier__remark-text">{character.remark}</span>
            <footer className="dossier__remark-attrib">
              — table mic, hand №&nbsp;{fileNumber.split('-')[1] ?? '0000'}
            </footer>
          </blockquote>
        </>
      )}
    </>
  );
}
