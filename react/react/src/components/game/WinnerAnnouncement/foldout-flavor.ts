export type FoldoutStreet = 'preflop' | 'flop' | 'turn' | 'river';

export const FOLDOUT_HEADLINES: Record<FoldoutStreet, string[]> = {
  preflop: ['Blinds Stolen', 'Never Saw the Flop', 'Took It Down Preflop', 'Won Without a Card'],
  flop: ['Folded on the Flop', 'Won on the Flop', 'Flop Pressure Worked', 'Three Cards Was Enough'],
  turn: ['Took It on the Turn', 'Folded on Fourth Street', 'Turn Bullied Them Out', 'Pressure on the Turn'],
  river: ['Won at the River', 'River Bluff Held', 'Last-Street Pressure', 'Folded on Fifth Street'],
};

export function getFoldoutStreet(communityCardCount: number): FoldoutStreet {
  if (communityCardCount === 0) return 'preflop';
  if (communityCardCount <= 3) return 'flop';
  if (communityCardCount === 4) return 'turn';
  return 'river';
}

export function pickRandom<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

interface PotWinner { name: string; amount: number }
interface PotBreakdownEntry { winners: PotWinner[] }
interface FoldoutWinnerInfo {
  winners: string[];
  showdown: boolean;
  pot_breakdown?: PotBreakdownEntry[];
  pot_contributions?: { [name: string]: number };
  community_cards?: unknown[];
}

interface FoldoutPlayer {
  name: string;
  stack: number;
  last_action?: string | null;
  avatar_emotion?: string;
  psychology?: {
    tilt_category?: 'none' | 'mild' | 'moderate' | 'severe';
    tilt_source?: string;
    losing_streak?: number;
    inner_voice?: string;
  };
  pressure_summary?: {
    successful_bluffs?: number;
    bluffs_caught?: number;
    bad_beats?: number;
    biggest_pot_won?: number;
    signature_move?: string;
    wins?: number;
  };
}

export interface StatRow {
  label: string;
  value: string;
}

export interface FoldoutStats {
  headline: string;
  rows: StatRow[];
}

const EMOJI_BY_EMOTION: Record<string, string> = {
  angry: '😠',
  frustrated: '😤',
  sad: '😞',
  confident: '😎',
  smug: '😏',
  happy: '🙂',
  tired: '😩',
  bored: '😐',
  shocked: '😲',
  thinking: '🤔',
  neutral: '😐',
};

const ACTION_LABEL: Record<string, string> = {
  raise: 'raised',
  call: 'called',
  check: 'checked',
  bet: 'bet',
  all_in: 'shoved',
  fold: 'folded',
};

function humanizeTiltSource(source?: string): string {
  if (!source) return '';
  return source.replace(/_/g, ' ');
}

function getEmoji(emotion?: string): string {
  if (!emotion) return '';
  return EMOJI_BY_EMOTION[emotion] ?? '';
}

/** Psychology readout: who broke down emotionally during this hand. */
function buildPsychologyStats(
  winnerInfo: FoldoutWinnerInfo,
  players: FoldoutPlayer[],
): StatRow[] | null {
  const folders = players.filter((p) => !winnerInfo.winners.includes(p.name));
  if (folders.length === 0) return null;

  const rows: StatRow[] = [];

  // Most-tilted folder (severe > moderate > mild)
  const tiltOrder = { severe: 3, moderate: 2, mild: 1, none: 0 } as const;
  const tilted = folders
    .map((p) => ({ p, lvl: tiltOrder[p.psychology?.tilt_category ?? 'none'] }))
    .filter((x) => x.lvl > 0)
    .sort((a, b) => b.lvl - a.lvl)[0];
  if (tilted) {
    const src = humanizeTiltSource(tilted.p.psychology?.tilt_source);
    rows.push({
      label: 'Tilt',
      value: `${tilted.p.name}: ${tilted.p.psychology?.tilt_category}${src ? ` (${src})` : ''}`,
    });
  }

  // Longest losing streak among folders
  const streaker = folders
    .filter((p) => (p.psychology?.losing_streak ?? 0) >= 2)
    .sort((a, b) => (b.psychology?.losing_streak ?? 0) - (a.psychology?.losing_streak ?? 0))[0];
  if (streaker) {
    const n = streaker.psychology?.losing_streak ?? 0;
    rows.push({ label: 'Streak', value: `${streaker.name}: ${n}-hand drought` });
  }

  // Notable inner voice from any folder
  const voiceFolder = folders.find(
    (p) => p.psychology?.inner_voice && p.psychology.inner_voice.length > 0,
  );
  if (voiceFolder?.psychology?.inner_voice) {
    const voice = voiceFolder.psychology.inner_voice;
    const truncated = voice.length > 48 ? voice.slice(0, 45) + '…' : voice;
    rows.push({ label: 'Inner', value: `"${truncated}"` });
  }

  return rows.length > 0 ? rows : null;
}

/** Session records: pulls from pressure_summary aggregates. */
function buildSessionStats(
  winnerInfo: FoldoutWinnerInfo,
  players: FoldoutPlayer[],
): StatRow[] | null {
  const winner = players.find((p) => p.name === winnerInfo.winners[0]);
  if (!winner?.pressure_summary) return null;

  const ps = winner.pressure_summary;
  const rows: StatRow[] = [];

  if ((ps.successful_bluffs ?? 0) > 0) {
    const n = ps.successful_bluffs!;
    const ordinal = n === 1 ? '1st' : n === 2 ? '2nd' : n === 3 ? '3rd' : `${n}th`;
    rows.push({ label: 'Bluff', value: `${winner.name}'s ${ordinal} steal this session` });
  }

  // Compare current pot to biggest pot the winner has taken this session
  const currentPot = winnerInfo.pot_breakdown
    ? winnerInfo.pot_breakdown.reduce(
        (s, p) => s + p.winners.reduce((sw, w) => sw + w.amount, 0),
        0,
      )
    : 0;
  if (currentPot > 0 && (ps.biggest_pot_won ?? 0) > 0) {
    if (currentPot >= (ps.biggest_pot_won ?? 0)) {
      rows.push({ label: 'Record', value: `Biggest pot this session ($${currentPot})` });
    } else {
      rows.push({
        label: 'Best',
        value: `Session best: $${ps.biggest_pot_won}`,
      });
    }
  }

  if (ps.signature_move && ps.signature_move.length > 0) {
    const sig = ps.signature_move.length > 40 ? ps.signature_move.slice(0, 37) + '…' : ps.signature_move;
    rows.push({ label: 'Move', value: `"${sig}"` });
  }

  // Folder with most bad beats this session — explains the fold
  const folders = players.filter((p) => !winnerInfo.winners.includes(p.name));
  const beatUp = folders
    .filter((p) => (p.pressure_summary?.bad_beats ?? 0) > 0)
    .sort((a, b) => (b.pressure_summary?.bad_beats ?? 0) - (a.pressure_summary?.bad_beats ?? 0))[0];
  if (beatUp) {
    const n = beatUp.pressure_summary?.bad_beats ?? 0;
    rows.push({ label: 'Note', value: `${beatUp.name}: ${n} bad beat${n === 1 ? '' : 's'} this session` });
  }

  return rows.length > 0 ? rows : null;
}

/** Behavioral: last action chain + folder emotions. */
function buildBehavioralStats(
  winnerInfo: FoldoutWinnerInfo,
  players: FoldoutPlayer[],
): StatRow[] | null {
  if (players.length === 0) return null;

  const rows: StatRow[] = [];

  // Action chain: winner's last action → N folds
  const winner = players.find((p) => p.name === winnerInfo.winners[0]);
  if (winner?.last_action && ACTION_LABEL[winner.last_action]) {
    const folders = players.filter((p) => !winnerInfo.winners.includes(p.name));
    const actionVerb = ACTION_LABEL[winner.last_action];
    rows.push({
      label: 'Action',
      value: `${winner.name} ${actionVerb} → ${folders.length} fold${folders.length === 1 ? '' : 's'}`,
    });
  }

  // Folder moods (most expressive emotion)
  const folders = players.filter((p) => !winnerInfo.winners.includes(p.name));
  const expressive = folders.filter(
    (p) => p.avatar_emotion && p.avatar_emotion !== 'neutral',
  );
  if (expressive.length > 0) {
    const first = expressive[0];
    const emoji = getEmoji(first.avatar_emotion);
    rows.push({
      label: 'Mood',
      value: `${first.name}: ${emoji ? emoji + ' ' : ''}${first.avatar_emotion}`,
    });
  }

  // Winner's emotion (often the most interesting — smug after a steal)
  if (winner?.avatar_emotion && winner.avatar_emotion !== 'neutral') {
    const emoji = getEmoji(winner.avatar_emotion);
    rows.push({
      label: 'Winner',
      value: `${emoji ? emoji + ' ' : ''}${winner.avatar_emotion}`,
    });
  }

  return rows.length > 0 ? rows : null;
}

export function computeFoldoutStats(
  winnerInfo: FoldoutWinnerInfo,
  players: FoldoutPlayer[] | number,
  bigBlind: number,
): FoldoutStats {
  const street = getFoldoutStreet(winnerInfo.community_cards?.length ?? 0);
  const headline = pickRandom(FOLDOUT_HEADLINES[street]);

  // Number fallback: caller didn't pass player objects, no rich stats possible
  const playerList = Array.isArray(players) ? players : [];

  // Try each builder; keep ones that returned at least one row
  void bigBlind; // reserved for future builders (pot/BB stats)
  const variants: StatRow[][] = [
    buildPsychologyStats(winnerInfo, playerList),
    buildSessionStats(winnerInfo, playerList),
    buildBehavioralStats(winnerInfo, playerList),
  ].filter((v): v is StatRow[] => v !== null && v.length > 0);

  // If nothing had data, return an empty scoreboard (headline still shows)
  if (variants.length === 0) {
    return { headline, rows: [] };
  }

  return { headline, rows: pickRandom(variants) };
}
