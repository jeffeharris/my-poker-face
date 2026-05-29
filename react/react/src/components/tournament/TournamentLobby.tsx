/**
 * Tournament lobby entry — register a new multi-table tournament or resume the
 * active one. Deliberately compact; the standings screen is the main surface.
 */

import { useState } from 'react';
import type { RegisterRequest, TournamentLobbyActive } from './types';
import './tournament.css';

const FIELD_PRESETS = [18, 24, 45];
const TABLE_PRESETS = [6, 9];

interface Props {
  active: TournamentLobbyActive | null;
  busy?: boolean;
  error?: string | null;
  onRegister: (body: RegisterRequest) => void;
  onResume: () => void;
}

export function TournamentLobby({ active, busy, error, onRegister, onResume }: Props) {
  const [fieldSize, setFieldSize] = useState(18);
  const [tableSize, setTableSize] = useState(6);

  return (
    <div className="tourney">
      <div className="tourney__inner tlobby">
        <div className="tlobby__kicker">Multi-Table Event</div>
        <h1 className="tlobby__title">The Main Event</h1>
        <p className="tlobby__sub">
          A full field across many tables. Survive as the crowd thins, tables break, and
          you&apos;re moved toward the final table. Last stack standing takes it.
        </p>

        {active ? (
          <div className="tlobby__resume">
            <div className="tlobby__resume-meta">
              In progress
              <strong>
                {active.standings.players_remaining} / {active.standings.field_size} left
              </strong>
              Level {active.standings.level.level} · you&apos;re{' '}
              {active.standings.human.out
                ? 'out'
                : `${active.standings.human.rank ?? '—'}${rankSuffix(active.standings.human.rank)}`}
            </div>
            <button className="dock__btn dock__btn--primary" onClick={onResume} disabled={busy}>
              Resume ›
            </button>
          </div>
        ) : (
          <div className="tlobby__card">
            <div className="field">
              <label className="field__label">Field Size</label>
              <div className="chips">
                {FIELD_PRESETS.map((n) => (
                  <button
                    key={n}
                    className={`chip${fieldSize === n ? ' chip--on' : ''}`}
                    onClick={() => setFieldSize(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label className="field__label">Seats / Table</label>
              <div className="chips">
                {TABLE_PRESETS.map((n) => (
                  <button
                    key={n}
                    className={`chip${tableSize === n ? ' chip--on' : ''}`}
                    onClick={() => setTableSize(n)}
                  >
                    {n}-max
                  </button>
                ))}
              </div>
            </div>
            <button
              className="dock__btn dock__btn--primary"
              style={{ width: '100%', marginTop: 6 }}
              disabled={busy}
              onClick={() => onRegister({ field_size: fieldSize, table_size: tableSize })}
            >
              {busy ? 'Seating…' : 'Take My Seat'}
            </button>
            {error && <div className="tlobby__error">{error}</div>}
          </div>
        )}
      </div>
    </div>
  );
}

function rankSuffix(n: number | null): string {
  if (n == null) return '';
  const v = n % 100;
  if (v >= 11 && v <= 13) return 'th';
  return ['th', 'st', 'nd', 'rd'][n % 10] || 'th';
}
