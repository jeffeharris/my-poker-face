/**
 * DossierSubject — the card's identity block: portrait (avatar or monogram)
 * with the current-emotion wax seal, the subject name, the inline nickname
 * editor (three modes: editing / display-with-pencil / add-from-scratch),
 * the play-style archetype, and the Renown-v2 standing badge.
 *
 * Extracted from CharacterDetailCard.tsx. Purely presentational — all the
 * nickname autosave wiring is owned by useDossierState and passed in via the
 * `nickname` controller.
 */

import { NICKNAME_OVERRIDE_MAX_LEN, type DossierReputation } from '../api';
import { formatEmotion, monogram, renownBadgeStyle } from './helpers';
import type { DossierMergedView, DossierNicknameController } from './useDossierState';
import type { CharacterDossierData } from './types';

export function DossierSubject({
  character,
  merged,
  reputation,
  identifier,
  hasOverride,
  nickname,
}: {
  character: CharacterDossierData;
  merged: DossierMergedView;
  reputation: DossierReputation | null;
  identifier?: string;
  hasOverride: boolean;
  nickname: DossierNicknameController;
}) {
  // The editor is gated on `identifier` (no auth → no override).
  const editorAllowed = !!identifier;

  return (
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
        {merged.emotion && (
          <div
            className="dossier__wax-seal"
            title={`current state: ${formatEmotion(merged.emotion)}`}
          >
            <span className="dossier__wax-text">{formatEmotion(merged.emotion)}</span>
          </div>
        )}
      </div>

      <div className="dossier__subject-text">
        <div className="dossier__eyebrow">SUBJECT</div>
        <h2 className="dossier__name">{merged.name}</h2>
        {/* The nickname row has three rendering modes:
              1. Editing (input visible)
              2. Display with an override or canonical value (chip + pencil)
              3. No nickname at all but editor allowed — just a pencil
                 affordance so the player can add one from scratch. */}
        {nickname.editing ? (
          <div className="dossier__nickname dossier__nickname--editing">
            <span className="dossier__quote-marks" aria-hidden="true">
              &ldquo;
            </span>
            <input
              ref={nickname.inputRef}
              type="text"
              className="dossier__nickname-input"
              value={nickname.draft}
              onChange={nickname.onChange}
              onKeyDown={nickname.onKeyDown}
              onBlur={nickname.commit}
              placeholder={merged.canonicalNickname ?? 'alias'}
              maxLength={NICKNAME_OVERRIDE_MAX_LEN}
              aria-label="Edit nickname for this opponent"
              spellCheck
            />
            <span className="dossier__quote-marks" aria-hidden="true">
              &rdquo;
            </span>
            <span
              className={`dossier__nickname-status dossier__nickname-status--${nickname.state}`}
              aria-live="polite"
            >
              {nickname.state === 'saving'
                ? 'Saving…'
                : nickname.state === 'saved'
                  ? '✓'
                  : nickname.state === 'error'
                    ? '!'
                    : ''}
            </span>
          </div>
        ) : merged.nickname ? (
          <div
            className={'dossier__nickname' + (hasOverride ? ' dossier__nickname--overridden' : '')}
          >
            <span className="dossier__quote-marks" aria-hidden="true">
              &ldquo;
            </span>
            {merged.nickname}
            <span className="dossier__quote-marks" aria-hidden="true">
              &rdquo;
            </span>
            {editorAllowed && (
              <button
                type="button"
                className="dossier__nickname-edit"
                onClick={nickname.startEditing}
                aria-label={
                  hasOverride
                    ? 'Edit your nickname for this opponent'
                    : 'Rename this opponent for your eyes only'
                }
                title={
                  hasOverride
                    ? `Your alias (canonical: "${merged.canonicalNickname ?? merged.name}")`
                    : 'Rename — only you see it'
                }
              >
                <span aria-hidden="true">✎</span>
              </button>
            )}
          </div>
        ) : editorAllowed ? (
          <button type="button" className="dossier__nickname-add" onClick={nickname.startEditing}>
            + add your own nickname
          </button>
        ) : null}
        {merged.playStyle && <div className="dossier__archetype">{merged.playStyle}</div>}
        {reputation &&
          (() => {
            const { glyph, mod } = renownBadgeStyle(reputation.quadrant);
            const pct =
              reputation.victim_percentile != null
                ? Math.round(reputation.victim_percentile * 100)
                : null;
            return (
              <div
                className={`dossier__renown dossier__renown--${mod}`}
                title={
                  pct != null
                    ? `Field-relative renown — ahead of ${pct}% of the field`
                    : 'Field-relative renown'
                }
              >
                <span className="dossier__renown-glyph" aria-hidden="true">
                  {glyph}
                </span>
                <span className="dossier__renown-quadrant">{reputation.quadrant}</span>
                <span className="dossier__renown-score">
                  renown {Math.round(reputation.renown_v2)}
                </span>
                {pct != null && (
                  <span className="dossier__renown-pct">ahead of {pct}% of the field</span>
                )}
              </div>
            );
          })()}
      </div>
    </section>
  );
}
