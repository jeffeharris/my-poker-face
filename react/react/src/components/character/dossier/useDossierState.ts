/**
 * useDossierState — all the stateful machinery behind the dossier card:
 *   • server-side enrichment fetch (on open, when an `identifier` is given)
 *   • informant purchase flow (+ refetch)
 *   • field-note autosave (debounced + flush-on-close)
 *   • nickname-override autosave (debounced + flush-on-close + inline editor)
 *   • the `merged` view that overlays the server fetch onto the static prop
 *
 * Lifted wholesale out of CharacterDetailCard.tsx so the component is left as a
 * thin renderer. The three concerns (fetch / note / nickname) live together
 * here because their effects are interdependent — the fetch effect seeds both
 * draft fields, and both flush-on-close effects key off the same `isOpen`
 * transition. Splitting them risks reordering those effects.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchCharacterDossier,
  buyInformantUnlock,
  saveCharacterNote,
  saveCharacterNicknameOverride,
  NICKNAME_OVERRIDE_MAX_LEN,
  type DossierResponse,
} from '../api';
import { useNicknameOverridesStore } from '../../../stores/nicknameOverridesStore';
import type { CharacterDossierData, NoteSaveState } from './types';

export interface DossierMergedView {
  name: string;
  nickname?: string;
  canonicalNickname?: string;
  playStyle?: string;
  attitude?: string;
  confidence?: string;
  emotion?: string;
  remark?: string;
  observed?:
    | {
        handsObserved?: number;
        vpip?: number;
        pfr?: number;
        aggressionFactor?: number;
        playStyleLabel?: string;
      }
    | undefined;
}

export interface DossierNoteController {
  draft: string;
  state: NoteSaveState;
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
}

export interface DossierNicknameController {
  editing: boolean;
  startEditing: () => void;
  draft: string;
  state: NoteSaveState;
  inputRef: React.RefObject<HTMLInputElement | null>;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  commit: () => void;
}

export interface UseDossierStateResult {
  fetched: DossierResponse | null;
  merged: DossierMergedView;
  buyingSection: string | null;
  buyError: string | null;
  handleBuyInformant: (sectionId: string) => Promise<void>;
  note: DossierNoteController;
  nickname: DossierNicknameController;
}

export function useDossierState(
  isOpen: boolean,
  identifier: string | undefined,
  character: CharacterDossierData,
  onIntelChanged?: () => void
): UseDossierStateResult {
  // ─── Server-side enrichment ─────────────────────────────────
  // Fetched on open when `identifier` is provided. Sections derived
  // from this fall in below the prop-driven ones — the static prop
  // gives an instant render, the server fetch hydrates the rest.
  const [fetched, setFetched] = useState<DossierResponse | null>(null);
  // Informant purchase (Phase 3): which section is in-flight + last error.
  const [buyingSection, setBuyingSection] = useState<string | null>(null);
  const [buyError, setBuyError] = useState<string | null>(null);
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

  // Pay the informant to reveal a locked section, then refetch the dossier
  // so every newly-declassified read populates (the gate reveals data, the
  // refetch pulls it in). Errors (e.g. insufficient bankroll) surface inline.
  const handleBuyInformant = useCallback(
    async (sectionId: string) => {
      if (!identifier || buyingSection) return;
      setBuyingSection(sectionId);
      setBuyError(null);
      try {
        await buyInformantUnlock(identifier, sectionId);
        const refreshed = await fetchCharacterDossier(identifier);
        setFetched(refreshed);
        // Let a host surface (the file cabinet behind this card) refresh so
        // the opponent's unlock state updates without waiting for a poll.
        onIntelChanged?.();
      } catch (e) {
        setBuyError(e instanceof Error ? e.message : 'Purchase failed');
      } finally {
        setBuyingSection(null);
      }
    },
    [identifier, buyingSection, onIntelChanged]
  );

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
  const merged = useMemo<DossierMergedView>(() => {
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
      // legacy and only fires for callers who pre-populate. When the dossier
      // was fetched in a gated (Circuit) context, the server's observation
      // is authoritative — a null means "classified", so we must NOT fall
      // back to the static prop (which carries live, ungated stats from the
      // table/lobby click) or the scouting gate would leak.
      observed: obs
        ? {
            handsObserved: obs.hands_observed,
            vpip: obs.vpip,
            pfr: obs.pfr,
            aggressionFactor: obs.aggression_factor,
            playStyleLabel: obs.play_style,
          }
        : fetched?.scouting
          ? undefined
          : character.observed
            ? {
                handsObserved: character.observed.handsObserved,
                vpip: character.observed.vpip,
                pfr: character.observed.pfr,
                aggressionFactor: character.observed.aggressionFactor,
                playStyleLabel: undefined as string | undefined,
              }
            : undefined,
    };
  }, [fetched, character]);

  return {
    fetched,
    merged,
    buyingSection,
    buyError,
    handleBuyInformant,
    note: {
      draft: noteDraft,
      state: noteState,
      onChange: handleNoteChange,
    },
    nickname: {
      editing: nicknameEditing,
      startEditing: () => setNicknameEditing(true),
      draft: nicknameDraft,
      state: nicknameState,
      inputRef: nicknameInputRef,
      onChange: handleNicknameChange,
      onKeyDown: handleNicknameKeyDown,
      commit: commitNicknameEdit,
    },
  };
}
