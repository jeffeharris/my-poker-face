/**
 * Shared types + copy constants for the CharacterDetailCard ("Dossier 1972").
 * Extracted from CharacterDetailCard.tsx so the section components, hook, and
 * the orchestrator can all share one definition.
 */

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
   * Whether the dossier is being viewed from a Circuit (cash) context.
   * The scouting unlock state shows everywhere (your Circuit-earned reads
   * carry over), but the informant's pay-to-unlock buttons only appear in
   * the Circuit — that's where the bankroll lives. Elsewhere (e.g. a
   * tournament table) locked sections show an "unlock in the Circuit" hint
   * instead of chip-cost buttons. Defaults to false.
   */
  circuitContext?: boolean;
  /**
   * Fired after a successful informant purchase, so a caller showing this
   * dossier over another intel surface (e.g. the file cabinet) can refresh
   * that surface to reflect the new unlock state.
   */
  onIntelChanged?: () => void;
  /**
   * Optional handler for the "Send chat" affordance. Receives the
   * dossier subject's name so the caller can open the chat sheet
   * pre-targeted to that player. When omitted the button is hidden.
   */
  onSendChat?: (targetName: string) => void;
}

export const RELATIONSHIP_COPY: Record<RelationshipKind, { label: string; tone: string }> = {
  rival: { label: 'RIVALRY', tone: 'crimson' },
  friend: { label: 'TRUSTED', tone: 'emerald' },
  sponsor: { label: 'BACKED BY', tone: 'gold' },
  neutral: { label: 'NEUTRAL', tone: 'ink' },
  admirer: { label: 'ADMIRER', tone: 'gold' },
  antagonist: { label: 'ANTAGONIST', tone: 'crimson' },
};

export type NoteSaveState = 'idle' | 'saving' | 'saved' | 'error';
