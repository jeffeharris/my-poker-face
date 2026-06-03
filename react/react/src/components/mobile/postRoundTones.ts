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
import type { PostRoundTone } from '../../types/chat';

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

/**
 * Pick the post-round tones that actually fit what happened this hand, so the
 * options are never awkward (no "vow revenge" over a hand you folded).
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
  return ids.map((id) => TONE_META[id]);
}
