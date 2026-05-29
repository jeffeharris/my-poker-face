/**
 * Tournament standings — the "back out to the standings" screen. The whole
 * field is paused while this is up (player-gated time), so the chrome reads like
 * a frozen broadcast tournament clock + leaderboard.
 */

import { motion, useReducedMotion } from 'framer-motion';
import type { TournamentSeat, TournamentStandings, TournamentTable } from './types';
import './tournament.css';

const fmt = (n: number | null | undefined): string =>
  n == null ? '—' : n.toLocaleString('en-US');

const ordinalSuffix = (n: number): string => {
  const v = n % 100;
  if (v >= 11 && v <= 13) return 'th';
  return ['th', 'st', 'nd', 'rd'][n % 10] || 'th';
};

interface Props {
  standings: TournamentStandings;
  busy?: boolean;
  /** Drop into the live poker table to play the human's current table. */
  onReturnToTable: () => void;
  /** Spectate the rest of the field after busting (fast-forward to the end). */
  onWatch: () => void;
  onLeave: () => void;
  onBack: () => void;
}

export function TournamentStandings({
  standings,
  busy,
  onReturnToTable,
  onWatch,
  onLeave,
  onBack,
}: Props) {
  const reduce = useReducedMotion();
  const { level, human, complete, winner } = standings;

  const fade = (i: number) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, y: 12 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.32, delay: 0.04 * i, ease: [0.22, 1, 0.36, 1] as const },
        };

  return (
    <div className="tourney">
      <div className="tourney__inner">
        <div className="tourney__topbar">
          <button className="tourney__back" onClick={onBack}>
            ‹ Lobby
          </button>
          <span className="tourney__paused">
            <span className="tourney__paused-dot" />
            {complete ? 'Final' : 'Field paused'}
          </span>
        </div>

        {/* ── clock band ── */}
        <motion.div className="clock" {...fade(0)}>
          <div className="clock__row">
            <div>
              <div className="clock__level-label">Blind Level</div>
              <div className="clock__level-num">
                <span>{level.level}</span>
              </div>
              <div className="clock__blinds">
                {fmt(level.small_blind)}
                <span className="slash">/</span>
                {fmt(level.big_blind)}
              </div>
              {level.ante > 0 && <div className="clock__ante">ante {fmt(level.ante)}</div>}
            </div>
            <div className="clock__remaining">
              <div className="clock__stat-label">Players Left</div>
              <div className="clock__remaining-num">
                {standings.players_remaining}
                <em> / {standings.field_size}</em>
              </div>
            </div>
          </div>
        </motion.div>

        {/* ── you ── */}
        <motion.div
          className={`you-strip${human.out ? ' you-strip--out' : ''}`}
          {...fade(1)}
        >
          <div>
            <div className="you-strip__tag">{human.out ? 'Busted' : 'Your Standing'}</div>
            <div className="you-strip__rank">
              {human.rank != null ? (
                <>
                  {human.rank}
                  <sup>{ordinalSuffix(human.rank)}</sup>
                </>
              ) : (
                '—'
              )}
            </div>
          </div>
          <div />
          <div className="you-strip__stack">
            {human.out ? 'OUT' : fmt(human.stack)}
            <small>{human.out ? 'finished' : 'your stack'}</small>
          </div>
        </motion.div>

        {/* ── champion ── */}
        {complete && winner && (
          <motion.div className="champion" {...fade(2)}>
            <div className="champion__label">Champion</div>
            <div className="champion__name">
              {winner === human.player_id ? 'You' : winner}
            </div>
          </motion.div>
        )}

        {/* ── tables ── */}
        {standings.tables.length > 0 && (
          <>
            <div className="tourney__heading">Tables</div>
            <div className="tables">
              {standings.tables.map((t, i) => (
                <motion.div key={t.table_id} {...fade(3 + i)}>
                  <TableCard table={t} />
                </motion.div>
              ))}
            </div>
          </>
        )}

        {/* ── knockouts ── */}
        <div className="tourney__heading">Recent Knockouts</div>
        <div className="kos">
          {standings.recent_eliminations.length === 0 && (
            <div className="kos__empty">No one has busted yet.</div>
          )}
          {standings.recent_eliminations.map((ko) => (
            <motion.div
              key={`${ko.player_id}-${ko.finishing_position}`}
              className="ko"
              {...(reduce
                ? {}
                : { initial: { opacity: 0, x: -10 }, animate: { opacity: 1, x: 0 } })}
            >
              <div className="ko__pos">
                {ko.finishing_position}
                <sup>{ordinalSuffix(ko.finishing_position)}</sup>
              </div>
              <div className="ko__who">
                {ko.player_id === human.player_id ? 'You' : ko.player_id}
              </div>
              <div className="ko__by">{ko.eliminator ? `KO by ${ko.eliminator}` : 'eliminated'}</div>
            </motion.div>
          ))}
        </div>
      </div>

      {/* ── control dock ── */}
      <div className="dock">
        {!complete && !human.out && (
          <button
            className="dock__btn dock__btn--primary"
            onClick={onReturnToTable}
            disabled={busy}
          >
            {busy ? '…' : 'Return to Table'}
          </button>
        )}
        {!complete && human.out && (
          <button className="dock__btn dock__btn--primary" onClick={onWatch} disabled={busy}>
            {busy ? '…' : 'Watch to the End'}
          </button>
        )}
        <button className="dock__btn dock__btn--ghost" onClick={onLeave} disabled={busy}>
          Leave
        </button>
      </div>
    </div>
  );
}

function TableCard({ table }: { table: TournamentTable }) {
  return (
    <div className={`table-card${table.is_human_table ? ' table-card--human' : ''}`}>
      <div className="table-card__head">
        <span className="table-card__name">Table {table.table_id}</span>
        {table.is_human_table ? (
          <span className="table-card__yours">Your Table</span>
        ) : (
          <span className="table-card__count">{table.size} left</span>
        )}
      </div>
      {table.seats.map((s) => (
        <SeatRow key={s.seat} seat={s} />
      ))}
    </div>
  );
}

function SeatRow({ seat }: { seat: TournamentSeat }) {
  if (!seat.player_id) {
    return (
      <div className="seat seat--empty">
        <span className="seat__btn seat__btn--off" />
        <span className="seat__empty">empty</span>
        <span className="seat__stack">—</span>
      </div>
    );
  }
  return (
    <div className={`seat${seat.is_human ? ' seat--human' : ''}`}>
      <span className={`seat__btn${seat.is_button ? '' : ' seat__btn--off'}`}>
        {seat.is_button ? 'D' : ''}
      </span>
      <span className="seat__who">
        <span className="seat__id">
          {seat.is_human ? 'You' : seat.player_id}
          {seat.is_human && <span className="you">YOU</span>}
        </span>
        {seat.archetype && !seat.is_human && <span className="seat__arch">{seat.archetype}</span>}
      </span>
      <span className="seat__stack">{fmt(seat.stack)}</span>
    </div>
  );
}
