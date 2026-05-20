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

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Wallet } from 'lucide-react';
import { PageLayout, PageHeader } from '../shared';
import { getLobby, getState, sitAtTable } from './api';
import { SponsorModal } from './SponsorModal';
import { TableCard } from './TableCard';
import { ActivityTicker } from './ActivityTicker';
import { NetWorthDrawer } from './NetWorthDrawer';
import type { LobbyEvent, LobbyTable, StakeLabel } from './types';
import { logger } from '../../utils/logger';
import {
  CharacterDetailCard,
  type CharacterDossierData,
} from '../character';
import './CashMode.css';

/** What TableCard fires up to the Lobby on AI-seat click. */
export interface AiSeatClick {
  dossier: CharacterDossierData;
  origin: { x: number; y: number };
  /** Stable id for the dossier route's lookup (personality_id or name). */
  identifier?: string;
}

const LOBBY_REFRESH_INTERVAL_MS = 8000;

export function Lobby() {
  const navigate = useNavigate();
  const [bankroll, setBankroll] = useState<number | null>(null);
  const [tables, setTables] = useState<LobbyTable[]>([]);
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sitError, setSitError] = useState<string | null>(null);
  const [sponsorState, setSponsorState] = useState<{
    stakeLabel: StakeLabel;
    tableId: string;
  } | null>(null);
  const [dossier, setDossier] = useState<AiSeatClick | null>(null);
  const [netWorthOpen, setNetWorthOpen] = useState(false);
  // Mutable ref so the drawer's `onPayoff` callback can re-fetch the
  // lobby without re-rendering on every interval tick. The interval
  // captures `load` once via the dep-free useEffect below.
  const reloadLobbyRef = useRef<() => Promise<void>>(async () => {});

  // On mount: check active session first (redirect if so), then load
  // the lobby. Pulling /state before /lobby avoids a flash of the
  // lobby UI for users who are already in a game.
  //
  // Polling every 8s keeps the activity ticker + roster fresh.
  // Important: the lobby read itself drives `refresh_unseated_tables`
  // server-side, so polling is ALSO what keeps the world moving in
  // v1.5 (there's no background daemon). Stop the poll when the
  // component unmounts.
  useEffect(() => {
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | null = null;

    const load = async () => {
      try {
        const lobby = await getLobby();
        if (cancelled) return;
        setBankroll(lobby.bankroll);
        setTables(lobby.tables);
        setEvents(lobby.events ?? []);
      } catch (e) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Failed to load lobby:', msg);
        setLoadError(msg);
      }
    };
    reloadLobbyRef.current = load;

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
      await load();
      if (cancelled) return;
      interval = setInterval(load, LOBBY_REFRESH_INTERVAL_MS);
    })();

    return () => {
      cancelled = true;
      if (interval !== null) clearInterval(interval);
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
            <button
              type="button"
              className="cash-entry__net-worth-trigger"
              onClick={() => setNetWorthOpen(true)}
              aria-label="Open net worth"
              title="View net worth"
            >
              <Wallet size={16} aria-hidden="true" />
            </button>
          </div>
        )}

        <ActivityTicker events={events} />

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
                onAiSeatClick={setDossier}
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
      <CharacterDetailCard
        isOpen={dossier !== null}
        onClose={() => setDossier(null)}
        character={dossier?.dossier ?? { name: '' }}
        origin={dossier?.origin}
        identifier={dossier?.identifier}
      />
      <NetWorthDrawer
        isOpen={netWorthOpen}
        onClose={() => setNetWorthOpen(false)}
        onPayoff={() => {
          void reloadLobbyRef.current();
        }}
      />
    </PageLayout>
  );
}
