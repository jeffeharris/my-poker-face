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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'framer-motion';
import {
  fetchCharacterDossier,
  saveCharacterNote,
  saveCharacterNicknameOverride,
  NICKNAME_OVERRIDE_MAX_LEN,
  type DossierResponse,
} from './api';
import { useNicknameOverridesStore } from '../../stores/nicknameOverridesStore';
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
  /** Observed-at-table stats (only shown if handsObserved > 0). */
  observed?: {
    handsObserved?: number;
    vpip?: number; // 0–1
    pfr?: number; // 0–1
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
  /**
   * Personality id OR display name. When provided, the card fetches
   * /api/character/<identifier>/dossier on open to enrich the static
   * `character` data with the relationship axes, cash pair stats,
   * recent hands, and the player-authored note (which becomes
   * editable with debounced autosave).
   */
  identifier?: string;
  /**
   * Optional handler for the "Send chat" affordance. Receives the
   * dossier subject's name so the caller can open the chat sheet
   * pre-targeted to that player. When omitted the button is hidden.
   */
  onSendChat?: (targetName: string) => void;
}

const RELATIONSHIP_COPY: Record<RelationshipKind, { label: string; tone: string }> = {
  rival: { label: 'RIVALRY', tone: 'crimson' },
  friend: { label: 'TRUSTED', tone: 'emerald' },
  sponsor: { label: 'BACKED BY', tone: 'gold' },
  neutral: { label: 'NEUTRAL', tone: 'ink' },
  admirer: { label: 'ADMIRER', tone: 'gold' },
  antagonist: { label: 'ANTAGONIST', tone: 'crimson' },
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
      <div className="dossier__tally-readout">{readout ?? `${Math.round(value * 100)}%`}</div>
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

type NoteSaveState = 'idle' | 'saving' | 'saved' | 'error';

export function CharacterDetailCard({
  isOpen,
  onClose,
  character,
  origin,
  identifier,
  onSendChat,
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

  // ─── Server-side enrichment ─────────────────────────────────
  // Fetched on open when `identifier` is provided. Sections derived
  // from this fall in below the prop-driven ones — the static prop
  // gives an instant render, the server fetch hydrates the rest.
  const [fetched, setFetched] = useState<DossierResponse | null>(null);
  const [noteDraft, setNoteDraft] = useState('');
  const [noteState, setNoteState] = useState<NoteSaveState>('idle');
  const lastSavedNote = useRef<string>('');
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Nickname override: separate draft/state/last-saved triple so its
  // autosave can't collide with the note autosave. `nicknameEditing`
  // toggles the inline input vs. the static display chip.
  const [nicknameEditing, setNicknameEditing] = useState(false);
  const [nicknameDraft, setNicknameDraft] = useState('');
  const [nicknameState, setNicknameState] = useState<NoteSaveState>('idle');
  const lastSavedNickname = useRef<string>('');
  const nicknameSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nicknameInputRef = useRef<HTMLInputElement | null>(null);

  // After every successful save we push the result into the global
  // overrides store, so table seats / chat targets / heads-up etc.
  // re-render with the new alias without a separate refetch.
  const setNicknameInStore = useNicknameOverridesStore((s) => s.setOne);

  // Ref so the save callbacks can look up the dossier subject's
  // canonical name without taking a dependency on `fetched` (which
  // would force every save closure to recreate on hydration).
  const storeKeyNameRef = useRef<string>(character.name);
  useEffect(() => {
    storeKeyNameRef.current = fetched?.personality?.name ?? character.name;
  }, [fetched, character.name]);

  useEffect(() => {
    if (!isOpen || !identifier) {
      setFetched(null);
      return;
    }
    let cancelled = false;
    fetchCharacterDossier(identifier)
      .then((data) => {
        if (cancelled) return;
        setFetched(data);
        const initialNote = data.note ?? '';
        setNoteDraft(initialNote);
        lastSavedNote.current = initialNote;
        setNoteState('idle');
        const initialNick = data.personality?.nickname_override ?? '';
        setNicknameDraft(initialNick);
        lastSavedNickname.current = initialNick;
        setNicknameState('idle');
        setNicknameEditing(false);
      })
      .catch((e) => {
        // Anonymous reads return 200 with null fields, so this is
        // genuinely an error case — log but don't block the render.
        // The card still shows whatever static `character` carries.
        console.error('[dossier] fetch failed:', e);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, identifier]);

  // Debounced autosave: 600ms after the last keystroke. Cancels any
  // pending save when a new keystroke comes in or the card closes.
  const scheduleNoteSave = useCallback(
    (next: string) => {
      if (!identifier) return;
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => {
        if (next === lastSavedNote.current) return;
        setNoteState('saving');
        saveCharacterNote(identifier, next)
          .then((res) => {
            lastSavedNote.current = res.note ?? '';
            setNoteState('saved');
            // Quietly drop the "saved" indicator after a beat so it
            // doesn't linger as the player keeps reading.
            setTimeout(() => setNoteState('idle'), 1400);
          })
          .catch((e) => {
            console.error('[dossier] note save failed:', e);
            setNoteState('error');
          });
      }, 600);
    },
    [identifier]
  );

  // Flush on close: if there's an unsaved draft when the card closes,
  // fire one final save synchronously (no debounce). Without this
  // you'd lose the last few keystrokes when dismissing fast.
  useEffect(() => {
    if (isOpen) return;
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    if (identifier && noteDraft !== lastSavedNote.current) {
      saveCharacterNote(identifier, noteDraft)
        .then((res) => {
          lastSavedNote.current = res.note ?? '';
        })
        .catch(() => {
          // Silent — the card is gone, no UI surface to report into.
        });
    }
    // Intentionally only depends on isOpen — we want flush on
    // close, not on every draft keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const handleNoteChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const next = e.target.value.slice(0, 2000);
      setNoteDraft(next);
      scheduleNoteSave(next);
    },
    [scheduleNoteSave]
  );

  // Nickname autosave mirrors the note autosave but uses its own
  // debounce timer + last-saved ref so the two can save in parallel
  // without stepping on each other's status indicators.
  const scheduleNicknameSave = useCallback(
    (next: string) => {
      if (!identifier) return;
      if (nicknameSaveTimerRef.current) clearTimeout(nicknameSaveTimerRef.current);
      nicknameSaveTimerRef.current = setTimeout(() => {
        if (next === lastSavedNickname.current) return;
        setNicknameState('saving');
        saveCharacterNicknameOverride(identifier, next)
          .then((res) => {
            lastSavedNickname.current = res.nickname_override ?? '';
            setNicknameInStore(storeKeyNameRef.current, res.nickname_override);
            setNicknameState('saved');
            setTimeout(() => setNicknameState('idle'), 1400);
          })
          .catch((e) => {
            console.error('[dossier] nickname save failed:', e);
            setNicknameState('error');
          });
      }, 600);
    },
    [identifier, setNicknameInStore]
  );

  // Flush nickname draft on close, same as notes. Independent effect
  // so the two flushes can both fire if both fields are dirty.
  useEffect(() => {
    if (isOpen) return;
    if (nicknameSaveTimerRef.current) {
      clearTimeout(nicknameSaveTimerRef.current);
      nicknameSaveTimerRef.current = null;
    }
    if (identifier && nicknameDraft !== lastSavedNickname.current) {
      saveCharacterNicknameOverride(identifier, nicknameDraft)
        .then((res) => {
          lastSavedNickname.current = res.nickname_override ?? '';
          setNicknameInStore(storeKeyNameRef.current, res.nickname_override);
        })
        .catch(() => {
          // Silent — the card is gone.
        });
    }
    // Intentionally only depends on isOpen; we want flush on close.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const handleNicknameChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const next = e.target.value.slice(0, NICKNAME_OVERRIDE_MAX_LEN);
      setNicknameDraft(next);
      scheduleNicknameSave(next);
    },
    [scheduleNicknameSave]
  );

  const commitNicknameEdit = useCallback(() => {
    // Force-fire the debounced save instead of waiting out the
    // 600ms — the user just hit Enter or blurred away, the draft
    // is "done" by their lights.
    if (nicknameSaveTimerRef.current) {
      clearTimeout(nicknameSaveTimerRef.current);
      nicknameSaveTimerRef.current = null;
    }
    if (identifier && nicknameDraft !== lastSavedNickname.current) {
      setNicknameState('saving');
      saveCharacterNicknameOverride(identifier, nicknameDraft)
        .then((res) => {
          lastSavedNickname.current = res.nickname_override ?? '';
          setNicknameInStore(storeKeyNameRef.current, res.nickname_override);
          setNicknameState('saved');
          setTimeout(() => setNicknameState('idle'), 1400);
        })
        .catch((e) => {
          console.error('[dossier] nickname save failed:', e);
          setNicknameState('error');
        });
    }
    setNicknameEditing(false);
  }, [identifier, nicknameDraft, setNicknameInStore]);

  const handleNicknameKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        commitNicknameEdit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        // Cancel: revert the draft to the last-saved value and exit.
        setNicknameDraft(lastSavedNickname.current);
        setNicknameEditing(false);
        // Don't bubble — the overlay's own Escape handler would
        // close the entire card otherwise, which is jarring when
        // the user just wanted to back out of the input.
        e.stopPropagation();
      }
    },
    [commitNicknameEdit]
  );

  // Autofocus the input the moment we flip into edit mode so the
  // user can type immediately without an extra click.
  useEffect(() => {
    if (nicknameEditing && nicknameInputRef.current) {
      nicknameInputRef.current.focus();
      nicknameInputRef.current.select();
    }
  }, [nicknameEditing]);

  // ─── Section-presence flags (server-fetched overlays prop data) ───
  // Prefer the freshly-fetched personality fields, but fall back to
  // whatever the caller passed in `character` so the card still
  // renders instantly before the fetch resolves.
  const merged = useMemo(() => {
    const p = fetched?.personality;
    const obs = fetched?.observation;
    return {
      name: p?.name ?? character.name,
      // `nickname` is the *displayed* alias — the server already
      // baked in the viewer's override on top of the canonical
      // value, so we just trust whichever is freshest. The
      // canonical fallback is exposed separately for the editor.
      nickname: p?.nickname ?? character.nickname ?? undefined,
      canonicalNickname: p?.canonical_nickname ?? character.nickname ?? undefined,
      playStyle: p?.play_style ?? character.playStyle,
      attitude: p?.attitude ?? character.attitude,
      confidence: p?.confidence ?? character.confidence,
      // Prefer the server-fetched live emotion (always-set with a
      // 'confident' default for RuleBots) over whatever was on the
      // initial click payload, which can be undefined for table-side
      // opens before the first socket update lands.
      emotion: fetched?.emotion ?? character.emotion,
      remark: character.remark ?? p?.signature_line ?? undefined,
      // Server-side observation wins; the static prop's `observed` is
      // legacy and only fires for callers who pre-populate.
      observed: obs
        ? {
            handsObserved: obs.hands_observed,
            vpip: obs.vpip,
            pfr: obs.pfr,
            aggressionFactor: obs.aggression_factor,
            playStyleLabel: obs.play_style,
          }
        : character.observed && {
            handsObserved: character.observed.handsObserved,
            vpip: character.observed.vpip,
            pfr: character.observed.pfr,
            aggressionFactor: character.observed.aggressionFactor,
            playStyleLabel: undefined as string | undefined,
          },
    };
  }, [fetched, character]);

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

  // BEHAVIORAL INDEX reads the curated anchor subset from the
  // server fetch. Static-prop fallback is intentionally absent —
  // the anchors live on the personality config, which only the
  // dossier endpoint resolves.
  const anchors = fetched?.personality?.anchors ?? null;
  const hasAnchors = !!anchors && Object.values(anchors).some((v) => v != null);
  const hasObserved = !!merged.observed && (merged.observed.handsObserved ?? 0) > 0;
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
  const showNotes = !!identifier;

  const relationship = character.affiliation?.relationship;
  const relMeta = relationship ? RELATIONSHIP_COPY[relationship] : null;

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

            {onSendChat && (
              <button
                type="button"
                className="dossier__chat-btn"
                onClick={() => onSendChat(merged.name)}
                aria-label={`Send a message to ${merged.name}`}
                title={`Send a message to ${merged.name}`}
              >
                <span aria-hidden="true">✉</span>
                <span className="dossier__chat-btn-label">Send chat</span>
              </button>
            )}

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
                {merged.emotion && (
                  <div className="dossier__wax-seal" title={`current state: ${merged.emotion}`}>
                    <span className="dossier__wax-text">{merged.emotion}</span>
                  </div>
                )}
              </div>

              <div className="dossier__subject-text">
                <div className="dossier__eyebrow">SUBJECT</div>
                <h2 className="dossier__name">{merged.name}</h2>
                {(() => {
                  // The nickname row has three rendering modes:
                  //   1. Editing (input visible)
                  //   2. Display with an override or canonical value (chip + pencil)
                  //   3. No nickname at all but editor allowed — just a pencil
                  //      affordance so the player can add one from scratch.
                  // The editor is gated on `identifier` (no auth → no override).
                  const editorAllowed = !!identifier;
                  const hasOverride = !!fetched?.personality?.nickname_override;
                  if (nicknameEditing) {
                    return (
                      <div className="dossier__nickname dossier__nickname--editing">
                        <span className="dossier__quote-marks" aria-hidden="true">
                          &ldquo;
                        </span>
                        <input
                          ref={nicknameInputRef}
                          type="text"
                          className="dossier__nickname-input"
                          value={nicknameDraft}
                          onChange={handleNicknameChange}
                          onKeyDown={handleNicknameKeyDown}
                          onBlur={commitNicknameEdit}
                          placeholder={merged.canonicalNickname ?? 'alias'}
                          maxLength={NICKNAME_OVERRIDE_MAX_LEN}
                          aria-label="Edit nickname for this opponent"
                          spellCheck
                        />
                        <span className="dossier__quote-marks" aria-hidden="true">
                          &rdquo;
                        </span>
                        <span
                          className={`dossier__nickname-status dossier__nickname-status--${nicknameState}`}
                          aria-live="polite"
                        >
                          {nicknameState === 'saving'
                            ? 'Saving…'
                            : nicknameState === 'saved'
                              ? '✓'
                              : nicknameState === 'error'
                                ? '!'
                                : ''}
                        </span>
                      </div>
                    );
                  }
                  if (merged.nickname) {
                    return (
                      <div
                        className={
                          'dossier__nickname' +
                          (hasOverride ? ' dossier__nickname--overridden' : '')
                        }
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
                            onClick={() => setNicknameEditing(true)}
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
                    );
                  }
                  if (editorAllowed) {
                    return (
                      <button
                        type="button"
                        className="dossier__nickname-add"
                        onClick={() => setNicknameEditing(true)}
                      >
                        + add your own nickname
                      </button>
                    );
                  }
                  return null;
                })()}
                {merged.playStyle && <div className="dossier__archetype">{merged.playStyle}</div>}
              </div>
            </section>

            <SectionRule>PROFILE</SectionRule>
            <section className="dossier__profile">
              {merged.attitude && <DataRow label="Attitude" value={merged.attitude} />}
              {merged.confidence && <DataRow label="Confidence" value={merged.confidence} />}
            </section>

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
                            <span className="dossier__memorable-tag">
                              {h.event.replace(/_/g, ' ')}
                            </span>
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
                    value={noteDraft}
                    onChange={handleNoteChange}
                    placeholder="Tells, tendencies, anything worth remembering…"
                    rows={4}
                    maxLength={2000}
                    spellCheck
                  />
                  <div className="dossier__notes-footer">
                    <span
                      className={`dossier__notes-status dossier__notes-status--${noteState}`}
                      aria-live="polite"
                    >
                      {noteState === 'saving'
                        ? 'Saving…'
                        : noteState === 'saved'
                          ? '✓ Saved'
                          : noteState === 'error'
                            ? 'Couldn’t save'
                            : noteDraft.length > 1800
                              ? `${noteDraft.length} / 2000`
                              : ''}
                    </span>
                    <span className="dossier__notes-hint">
                      autosaves · persists across sessions
                    </span>
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
                        <span className="dossier__money">
                          ${fetched.ai_bankroll.toLocaleString()}
                        </span>
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
                            {fetched.stake_summary.as_staker.carry_count === 1
                              ? 'carry'
                              : 'carries'}
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
                            {fetched.stake_summary.as_borrower.carry_count === 1
                              ? 'carry'
                              : 'carries'}
                          </span>
                        </span>
                      }
                    />
                  ) : null}
                  {hasObserved && merged.observed?.handsObserved !== undefined && (
                    <DataRow
                      label="Hands observed"
                      value={merged.observed.handsObserved.toLocaleString()}
                    />
                  )}
                  {merged.observed?.vpip !== undefined && (
                    <DataRow label="VPIP" value={`${Math.round(merged.observed.vpip * 100)}%`} />
                  )}
                  {merged.observed?.pfr !== undefined && (
                    <DataRow label="PFR" value={`${Math.round(merged.observed.pfr * 100)}%`} />
                  )}
                  {merged.observed?.aggressionFactor !== undefined && (
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

            {hasAffiliation && (
              <>
                <SectionRule>AFFILIATIONS</SectionRule>
                <section className="dossier__affiliation">
                  {character.affiliation?.sponsor && (
                    <DataRow label="Sponsor" value={character.affiliation.sponsor.toUpperCase()} />
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
