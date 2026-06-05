import {
  PartyPopper,
  Smile,
  Angry,
  Handshake,
  Award,
  HeartHandshake,
  Clover,
  Swords,
  type LucideIcon,
} from 'lucide-react';
import type { PostRoundTone, PostRoundSuggestion } from '../../types/chat';

export interface ToneOption {
  id: PostRoundTone;
  icon: LucideIcon;
  label: string;
}

// Tone presentation, keyed by id. The *which tones show* decision is
// situational (see buildToneOptions) rather than a fixed win/loss list.
export const TONE_META: Record<PostRoundTone, ToneOption> = {
  gloat: { id: 'gloat', icon: PartyPopper, label: 'Gloat' },
  gracious: { id: 'gracious', icon: Handshake, label: 'Gracious' },
  humble: { id: 'humble', icon: Smile, label: 'Humble' },
  props: { id: 'props', icon: Award, label: 'Props' },
  salty: { id: 'salty', icon: Angry, label: 'Salty' },
  cry_luck: { id: 'cry_luck', icon: Clover, label: 'Cry Luck' },
  vow: { id: 'vow', icon: Swords, label: 'Vow' },
  commiserate: { id: 'commiserate', icon: HeartHandshake, label: 'Commiserate' },
};

// Post-round tones that take the sarcastic register (a warm surface to
// invert). gracious → fake-nice, humble → dry self-deprecation, commiserate →
// fake sympathy. The hostile/emotional tones are sincere-only.
export const SARCASM_ABLE_POST_ROUND: ReadonlySet<PostRoundTone> = new Set<PostRoundTone>([
  'gracious',
  'humble',
  'commiserate',
]);

// Keep the menu to at most four buttons on screen. The only situation that
// would otherwise overflow is a multiway showdown loss (salty/props/cry_luck/
// vow + commiserate = 5); when over, drop the least-essential tones first —
// Vow goes before Salty (its defiance overlaps Salty's, and Cry Luck already
// covers the needle-the-winner intent).
export const MAX_VISIBLE_TONES = 4;
const TONE_DROP_PRIORITY: PostRoundTone[] = ['vow', 'salty', 'humble', 'gracious'];

/**
 * Pick the post-round tones that actually fit what happened this hand, so the
 * options are never awkward (no "vow revenge" over a hand you folded), capped
 * at MAX_VISIBLE_TONES.
 *
 *  WIN  — always Gloat/Humble/Gracious; + Props only at showdown (a real play
 *         to respect; an uncontested win shows no cards).
 *  LOSS — always Salty/Props; + Cry Luck/Vow only if YOU were at showdown (you
 *         took a real beat to needle/avenge); + Commiserate only if there's a
 *         fellow loser to console (not heads-up vs. the winner).
 */
export function buildToneOptions(opts: {
  playerWon: boolean;
  isShowdown: boolean;
  humanAtShowdown: boolean;
  hasFellowLoser: boolean;
}): ToneOption[] {
  const ids: PostRoundTone[] = [];
  if (opts.playerWon) {
    ids.push('gloat', 'humble', 'gracious');
    if (opts.isShowdown) ids.push('props');
  } else {
    ids.push('salty', 'props');
    if (opts.humanAtShowdown) ids.push('cry_luck', 'vow');
    if (opts.hasFellowLoser) ids.push('commiserate');
  }

  // Trim to the on-screen max, dropping the least-essential tones first.
  for (const tone of TONE_DROP_PRIORITY) {
    if (ids.length <= MAX_VISIBLE_TONES) break;
    const i = ids.indexOf(tone);
    if (i >= 0) ids.splice(i, 1);
  }

  return ids.map((id) => TONE_META[id]);
}

/**
 * Who a post-round message is aimed at — the shared policy for both the desktop
 * and mobile winner overlays:
 *  - Commiserate → the fellow loser you're consoling.
 *  - Any other loss-side reaction → the winner who beat you.
 *  - Win-side tones → broadcast (undefined; the backend fans it out).
 */
export function addressingForTone(
  tone: PostRoundTone,
  ctx: { playerWon: boolean; winnerName?: string; fellowLoser?: string }
): string[] | undefined {
  if (tone === 'commiserate') return ctx.fellowLoser ? [ctx.fellowLoser] : undefined;
  if (!ctx.playerWon && ctx.winnerName) return [ctx.winnerName];
  return undefined;
}

// Offline fallbacks per tone, used when the suggestion API is unreachable or
// required params are missing. One source for both winner overlays and both
// error paths (network failure + missing params).
export const POST_ROUND_FALLBACKS: Record<PostRoundTone, PostRoundSuggestion[]> = {
  gloat: [
    { text: 'Too easy.', tone: 'gloat' },
    { text: 'Thanks for the chips!', tone: 'gloat' },
  ],
  gracious: [
    { text: 'Nice hand.', tone: 'gracious' },
    { text: 'Well played.', tone: 'gracious' },
  ],
  humble: [
    { text: 'Got lucky there.', tone: 'humble' },
    { text: 'Good game.', tone: 'humble' },
  ],
  commiserate: [
    { text: 'Tough beat, man.', tone: 'commiserate' },
    { text: 'Brutal spot — you played it fine.', tone: 'commiserate' },
  ],
  salty: [
    { text: 'Unreal.', tone: 'salty' },
    { text: 'Of course.', tone: 'salty' },
  ],
  props: [
    { text: 'Respect. Well played.', tone: 'props' },
    { text: 'That was a sharp read.', tone: 'props' },
  ],
  cry_luck: [
    { text: 'Needed that river, huh?', tone: 'cry_luck' },
    { text: 'Lucky catch.', tone: 'cry_luck' },
  ],
  vow: [
    { text: "I'm taking that stack back.", tone: 'vow' },
    { text: 'This isn’t over.', tone: 'vow' },
  ],
};
