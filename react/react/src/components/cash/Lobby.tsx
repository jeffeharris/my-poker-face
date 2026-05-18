/**
 * Cash mode lobby — multi-table view, seat picker.
 *
 * Replaces `CashModeEntry`'s stake-picker UI. Fetches `/api/cash/lobby`
 * on mount; renders one `<TableCard>` per stake with the 4-AI roster
 * + 2 open-seat tap targets.
 *
 * Tap an open seat:
 *   - Affordable → POST /api/cash/sit → navigate to /game/:id.
 *   - Sponsor-required → SitResponse comes back as 402 with the
 *     `requires_sponsor` body → open `<SponsorModal>` with this
 *     table's id (so sponsor offers are narrowed to seated AIs).
 *   - Locked → tap ignored (button is disabled).
 *
 * Active-session redirect: if `/api/cash/lobby` is reached while the
 * user has an active session, the page redirects to /game/:id via
 * `/api/cash/state` (separate endpoint, kept).
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout, PageHeader } from '../shared';
import { getLobby, getState, sitAtTable } from './api';
import { SponsorModal } from './SponsorModal';
import { TableCard } from './TableCard';
import type { LobbyTable, StakeLabel } from './types';
import { logger } from '../../utils/logger';
import './CashMode.css';

export function Lobby() {
  const navigate = useNavigate();
  const [bankroll, setBankroll] = useState<number | null>(null);
  const [tables, setTables] = useState<LobbyTable[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sitError, setSitError] = useState<string | null>(null);
  const [sponsorState, setSponsorState] = useState<{
    stakeLabel: StakeLabel;
    tableId: string;
  } | null>(null);

  // On mount: check active session first (redirect if so), then load
  // the lobby. Pulling /state before /lobby avoids a flash of the
  // lobby UI for users who are already in a game.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const state = await getState();
        if (cancelled) return;
        if (state.state?.game_id) {
          navigate(`/game/${state.state.game_id}`, { replace: true });
          return;
        }
      } catch (e) {
        if (cancelled) return;
        logger.warn('Failed to read cash state:', e);
      }
      try {
        const lobby = await getLobby();
        if (cancelled) return;
        setBankroll(lobby.bankroll);
        setTables(lobby.tables);
      } catch (e) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Failed to load lobby:', msg);
        setLoadError(msg);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  const handleSeatTap = useCallback(
    async (table: LobbyTable, seatIndex: number) => {
      if (busy) return;
      setSitError(null);
      setBusy(true);
      try {
        const result = await sitAtTable(table.table_id, seatIndex);
        if ('kind' in result && result.kind === 'requires_sponsor') {
          // Open sponsor modal scoped to this table.
          setSponsorState({
            stakeLabel: result.data.stake_label,
            tableId: table.table_id,
          });
          return;
        }
        navigate(`/game/${result.game_id}`);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Sit failed:', msg);
        setSitError(msg);
      } finally {
        setBusy(false);
      }
    },
    [busy, navigate],
  );

  return (
    <PageLayout>
      <PageHeader
        title="Cash Game"
        onBack={() => navigate('/menu')}
        subtitle="Pick a table, tap an open seat, play hands."
      />
      <div className="cash-entry">
        {bankroll !== null && (
          <div className="cash-entry__bankroll">
            <span className="cash-entry__bankroll-label">Your bankroll</span>
            <span className="cash-entry__bankroll-value">
              ${bankroll.toLocaleString()}
            </span>
          </div>
        )}

        {loadError && (
          <div className="cash-entry__error" role="alert">
            {loadError}
          </div>
        )}
        {sitError && (
          <div className="cash-entry__error" role="alert">
            {sitError}
          </div>
        )}

        <section className="cash-entry__stakes">
          <h2>Tables</h2>
          <div className="cash-entry__stake-grid">
            {tables.map((t) => (
              <TableCard
                key={t.table_id}
                table={t}
                busy={busy}
                onSeatTap={(seatIndex) => handleSeatTap(t, seatIndex)}
              />
            ))}
          </div>
        </section>
      </div>
      <SponsorModal
        isOpen={sponsorState !== null}
        stakeLabel={sponsorState?.stakeLabel ?? null}
        onClose={() => setSponsorState(null)}
      />
    </PageLayout>
  );
}
