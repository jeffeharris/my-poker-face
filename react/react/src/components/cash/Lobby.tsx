/**
 * Cash mode lobby — multi-table view, seat picker, idle staking panel.
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
 * Player-as-staker (Phase 5) lives in a separate panel below the
 * table grid — `<IdleStakablePanel>` shows AIs willing to be staked
 * up to the next tier. Tapping "Stake" opens `<StakeOfferModal>`
 * pre-targeted to that AI at their +1 tier.
 *
 * Active-session redirect: if `/api/cash/lobby` is reached while the
 * user has an active session, the page redirects to /game/:id via
 * `/api/cash/state` (separate endpoint, kept).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createAuthedSocket } from '../../utils/socket';
import { ChevronDown, ChevronRight, Lock, Spade, Dices, Clock, Play, Trophy } from 'lucide-react';
import { PageLayout, MenuBar, ShuffleLoading } from '../shared';
import type { TickerLine } from '../shared/ShuffleLoading';
import { getLobby, getState, leaveTable, releaseSeat, sitAtTable, setWorldPace } from './api';
import { SponsorModal } from './SponsorModal';
import { TableCard } from './TableCard';
import { ActivityTicker } from './ActivityTicker';
import { MainEventCard } from './MainEventCard';
import {
  getInvite,
  getTournamentLobby,
  sitTournament,
  type TournamentInvite,
} from './tournamentApi';
import { feedEventKey, renderEventIcon } from './tickerEvents';
import { selectInterhandTicker } from './interhandTicker';
import { CareerHero } from './CareerHero';
import { ReputationPanel } from './ReputationPanel';
import { NetWorthDrawer } from './NetWorthDrawer';
import { IntelHub } from './IntelHub';
import type { FileCabinetPerson } from './types';
import { StakeOfferModal } from './StakeOfferModal';
import { IdleStakablePanel } from './IdleStakablePanel';
import type {
  BankrollPoint,
  LobbyEvent,
  LobbyTable,
  ReputationData,
  StakableAiCandidate,
  StakeLabel,
  WorldEvent,
  WorldPace,
} from './types';
import { STAKES } from './types';
import { rememberAdminOrigin } from '../admin/adminOrigin';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { CharacterDetailCard, type CharacterDossierData } from '../character';
import './CashMode.css';

/** What TableCard fires up to the Lobby on AI-seat click. */
export interface AiSeatClick {
  dossier: CharacterDossierData;
  origin: { x: number; y: number };
  /** Stable id for the dossier route's lookup (personality_id or name). */
  identifier?: string;
}

// Fallback poll. The realtime ticker pushes `lobby_tick` over the
// socket as the primary driver now, so this only backstops a dropped
// websocket — much slower than the old 8s read-driven cadence.
const LOBBY_REFRESH_INTERVAL_MS = 25000;

// Coalesce bursts of `lobby_tick` pushes into one refetch.
const LOBBY_TICK_DEBOUNCE_MS = 400;

// In dev, pin the socket to long-polling (matches useSocket.ts — the
// Werkzeug + threading combo mis-negotiates the WS upgrade). Prod lets
// socket.io negotiate normally behind Caddy + GeventWebSocketWorker.
const SOCKET_TRANSPORTS = import.meta.env.PROD ? undefined : ['polling'];

/** Mobile-first breakpoint. On widths at or below this, tier sections
 *  collapse by default so the player only sees one tier at a time;
 *  desktop renders all tiers expanded. The query runs once at mount —
 *  we don't subscribe to resize since the lobby is a transient view.
 */
const MOBILE_BREAKPOINT_PX = 640;

/** Flavor nicknames shown beside each Cardroom stake label in the tier
 *  header. Cosmetic only. */
const TIER_NICKNAMES: Record<StakeLabel, string> = {
  $2: 'Micros',
  $10: 'The grind',
  $50: 'High limit',
  $200: 'The big game',
  $1000: 'Nosebleeds',
};

/** Group lobby tables by stake_label, preserving STAKES order. Tables
 *  inside each tier sort by table_id for determinism (matches the
 *  backend's per-tier sort in CashTableRepository.list_all_tables). */
function groupTablesByStake(tables: LobbyTable[]): Map<StakeLabel, LobbyTable[]> {
  const grouped = new Map<StakeLabel, LobbyTable[]>();
  for (const stake of STAKES) grouped.set(stake, []);
  for (const t of tables) {
    const bucket = grouped.get(t.stake_label);
    if (bucket) bucket.push(t);
  }
  for (const bucket of grouped.values()) {
    bucket.sort((a, b) => a.table_id.localeCompare(b.table_id));
  }
  return grouped;
}

/** Player access to a tier, derived from its tables' affordability:
 *  `open` = can sit now, `stakeable` = can't self-afford but backing is
 *  available, `locked` = earn more first. */
type TierAccess = 'open' | 'stakeable' | 'locked';

/** Per-tier rollup for the smart tier header: open-seat count, the
 *  player's access, and the buy-in to clear (same across a stake). */
function tierMeta(tierTables: LobbyTable[]): {
  count: number;
  openSeats: number;
  access: TierAccess;
  minBuyIn: number;
} {
  let openSeats = 0;
  let anyAfford = false;
  let anySponsor = false;
  let minBuyIn = 0;
  for (const t of tierTables) {
    openSeats += t.seats.filter((s) => s.kind === 'open').length;
    if (t.affordability === 'affordable') anyAfford = true;
    else if (t.affordability === 'sponsor_eligible') anySponsor = true;
    minBuyIn = t.min_buy_in;
  }
  return {
    count: tierTables.length,
    openSeats,
    access: anyAfford ? 'open' : anySponsor ? 'stakeable' : 'locked',
    minBuyIn,
  };
}

/** Cap on the rolling activity feed. The server snapshot is short (so the
 *  payload stays small); the client accumulates beyond it so the user can
 *  scroll back through more history than any single poll returns. */
const MAX_FEED_EVENTS = 60;

/** Merge incoming events into the rolling feed: keep the newest copy of
 *  each key, sort newest-first, cap the buffer. Accumulating (rather than
 *  replacing with the server's short snapshot) is what lets the user
 *  scroll back; the cap keeps the buffer from growing without bound. */
function mergeEvents(existing: LobbyEvent[], incoming: LobbyEvent[]): LobbyEvent[] {
  const byKey = new Map<string, LobbyEvent>();
  for (const e of [...incoming, ...existing]) {
    const k = feedEventKey(e);
    const cur = byKey.get(k);
    if (!cur || e.created_at > cur.created_at) byKey.set(k, e);
  }
  return Array.from(byKey.values())
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
    .slice(0, MAX_FEED_EVENTS);
}

/** Coarse "paused Xm/Xh/Xd ago" for the Resume bar. Returns null for a
 *  missing/just-now/unparseable timestamp so the caller can omit the hint. */
function formatPausedAgo(iso: string | null): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return null;
  if (mins < 60) return `paused ${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `paused ${hrs}h ago`;
  return `paused ${Math.floor(hrs / 24)}d ago`;
}

export function Lobby() {
  const navigate = useNavigate();
  const [bankroll, setBankroll] = useState<number | null>(null);
  const [bankrollHistory, setBankrollHistory] = useState<BankrollPoint[]>([]);
  const [lastSessionDelta, setLastSessionDelta] = useState<number | null>(null);
  const [reputation, setReputation] = useState<ReputationData | null>(null);
  const [tables, setTables] = useState<LobbyTable[]>([]);
  /** The table the player currently has a live session at, or null. Drives
   *  the "you're here" pin + Resume on the matching TableCard. Only ever
   *  set when the lobby is reachable while seated (see the mount redirect). */
  const [seatedTableId, setSeatedTableId] = useState<string | null>(null);
  /** DB-aware: the player has an active cash session (live OR a cold,
   *  DB-only one not in memory). Drives the Resume bar independently of
   *  `seatedTableId`, which is null for a cold session and would otherwise
   *  hide the only path back into / out of a wedged game. */
  const [hasActiveSession, setHasActiveSession] = useState(false);
  /** Stake label for the Resume bar when the seated table isn't in the
   *  rendered lobby list (cold / cross-sandbox session). */
  const [seatedStakeLabelFromServer, setSeatedStakeLabelFromServer] = useState<string | null>(null);
  /** ISO start time of the active session, for the Resume bar's age hint. */
  const [seatedSince, setSeatedSince] = useState<string | null>(null);
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [endingSession, setEndingSession] = useState(false);
  const [sitError, setSitError] = useState<string | null>(null);
  /** Non-null while a seat tap is in flight: shows the shuffle transition
   *  ("Taking your seat…") immediately on press so the join feels responsive
   *  instead of a frozen lobby. Stays up through the navigate() on success so
   *  the game page's own "Setting up the table" shuffle hands off seamlessly;
   *  cleared if we bail (sponsor modal / error) and stay on the lobby. */
  const [sittingDown, setSittingDown] = useState<{ submessage?: string } | null>(null);
  const [sponsorState, setSponsorState] = useState<{
    stakeLabel: StakeLabel;
    tableId: string;
    seatIndex: number;
  } | null>(null);
  const [dossier, setDossier] = useState<AiSeatClick | null>(null);
  const [netWorthOpen, setNetWorthOpen] = useState(false);
  const [intelHubOpen, setIntelHubOpen] = useState(false);
  const [pendingForgivenessCount, setPendingForgivenessCount] = useState(0);
  /** How fast the background world ticks. Null until the first lobby
   *  load resolves the server-stored preference. */
  const [worldPace, setWorldPaceState] = useState<WorldPace | null>(null);
  /** Tick incremented on every lobby reload so the IdleStakablePanel's
   *  useEffect re-fetches its own data in lockstep. The two endpoints
   *  return overlapping state (seated AIs disappear from stakable,
   *  successful stakes seat AIs); keeping them in sync visually keeps
   *  the UX honest. */
  const [stakablePanelTick, setStakablePanelTick] = useState(0);
  const [stakeTarget, setStakeTarget] = useState<{
    candidate: StakableAiCandidate;
    stakeLabel: StakeLabel;
    minBuyIn: number;
    maxBuyIn: number;
  } | null>(null);

  /** The open circuit Main Event invite, or null. Fetched alongside the lobby
   *  (mount + tick + fallback poll); the GET also lets the chairman offer /
   *  expire one server-side, so polling keeps the card fresh without a
   *  scheduler. */
  const [mainEventInvite, setMainEventInvite] = useState<TournamentInvite | null>(null);
  const loadInviteRef = useRef<() => Promise<void>>(async () => {});
  /** The tournament_id of a Main Event the player is currently IN (active), so
   *  the lobby can show a "Resume Main Event" bar — the offer card vanishes once
   *  accepted, so without this there's no way back to an in-progress event. */
  const [activeTournamentId, setActiveTournamentId] = useState<string | null>(null);
  const loadActiveTournamentRef = useRef<() => Promise<void>>(async () => {});

  /** Set of stake labels whose tier section is currently collapsed.
   *  Initialized once per mount: mobile → all collapsed except the
   *  cheapest tier (so the user sees at least one section on first
   *  paint); desktop → none collapsed. After mount, the set tracks
   *  user toggles only. */
  const [collapsedTiers, setCollapsedTiers] = useState<Set<StakeLabel>>(() => {
    if (typeof window === 'undefined') return new Set();
    const isMobile = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT_PX}px)`).matches;
    if (!isMobile) return new Set();
    return new Set<StakeLabel>(STAKES.filter((s) => s !== STAKES[0]));
  });

  const toggleTier = useCallback((stake: StakeLabel) => {
    setCollapsedTiers((prev) => {
      const next = new Set(prev);
      if (next.has(stake)) next.delete(stake);
      else next.add(stake);
      return next;
    });
  }, []);

  const [activeVenue, setActiveVenue] = useState<'cardroom' | 'casino'>('cardroom');

  // Split tables by venue. Cardroom = the career ladder (everything that
  // isn't a casino table); Casino = the ephemeral $2 fish floor, its own
  // tab. Casino tables are pulled out of the ladder so they don't double
  // up in the $2 tier.
  const casinoTables = useMemo(() => tables.filter((t) => t.table_type === 'casino'), [tables]);
  const cardroomTables = useMemo(() => tables.filter((t) => t.table_type !== 'casino'), [tables]);
  const casinoClosingCount = useMemo(
    () => casinoTables.filter((t) => t.closing_hand_countdown != null).length,
    [casinoTables]
  );
  const tablesByStake = useMemo(() => groupTablesByStake(cardroomTables), [cardroomTables]);

  // One-shot once tables first load: auto-expand the highest tier the
  // player can self-afford (their "current" tier) rather than just the
  // cheapest. Mobile only — desktop renders all tiers expanded.
  const tiersAutoExpandedRef = useRef(false);
  useEffect(() => {
    if (tiersAutoExpandedRef.current || cardroomTables.length === 0) return;
    tiersAutoExpandedRef.current = true;
    if (typeof window === 'undefined') return;
    if (!window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT_PX}px)`).matches) {
      return;
    }
    let target: StakeLabel = STAKES[0];
    for (const stake of STAKES) {
      if (cardroomTables.some((t) => t.stake_label === stake && t.affordability === 'affordable')) {
        target = stake;
      }
    }
    setCollapsedTiers(new Set<StakeLabel>(STAKES.filter((s) => s !== target)));
  }, [cardroomTables]);

  // Mutable ref so the drawer's `onPayoff` callback can re-fetch the
  // lobby without re-rendering on every interval tick. The interval
  // captures `load` once via the dep-free useEffect below.
  const reloadLobbyRef = useRef<() => Promise<void>>(async () => {});

  // On mount: check active session first (redirect if so), then load
  // the lobby. Pulling /state before /lobby avoids a flash of the
  // lobby UI for users who are already in a game.
  //
  // Polling every 8s keeps the activity ticker + roster fresh.
  // The world now advances server-side in the realtime ticker; this
  // read is a pure snapshot (it no longer drives `refresh_unseated_tables`
  // when the ticker is enabled). The socket's `lobby_tick` is the primary
  // refresh trigger — see the socket effect below — and this interval is
  // just a slow fallback for a dropped websocket. Stop both on unmount.
  useEffect(() => {
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | null = null;

    const load = async () => {
      try {
        const lobby = await getLobby();
        if (cancelled) return;
        setBankroll(lobby.bankroll);
        setBankrollHistory(lobby.bankroll_history ?? []);
        setLastSessionDelta(lobby.last_session_delta ?? null);
        setReputation(lobby.reputation ?? null);
        setTables(lobby.tables);
        setSeatedTableId(lobby.seated_table_id ?? null);
        setHasActiveSession(lobby.has_active_session ?? false);
        setSeatedStakeLabelFromServer(lobby.seated_stake_label ?? null);
        setSeatedSince(lobby.seated_since ?? null);
        // Merge into the rolling feed rather than replace, so history the
        // server snapshot no longer carries stays scrollable. Drop any
        // prior self last-stand line first so the poll snapshot stays
        // authoritative for it (it clears when the condition lifts).
        setEvents((prev) =>
          mergeEvents(
            prev.filter((e) => !(e.type === 'last_stand' && e.reason === 'self')),
            lobby.events ?? []
          )
        );
        setPendingForgivenessCount(lobby.pending_forgiveness_count ?? 0);
        // Adopt the server pace only on first load; once set, the local
        // (optimistic) value wins so a refetch can't clobber a pace the
        // user just changed. Single writer per sandbox makes this safe.
        setWorldPaceState((cur) => cur ?? lobby.world_pace ?? 'lively');
        setStakablePanelTick((t) => t + 1);
      } catch (e) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Failed to load lobby:', msg);
        setLoadError(msg);
      }
    };
    reloadLobbyRef.current = load;

    // The Main Event invite rides its own endpoint (outside /api/cash). Fetched
    // alongside the lobby; best-effort so a tournament-route hiccup never breaks
    // the lobby. The GET also runs the chairman's offer/expire sweep server-side.
    const loadInvite = async () => {
      try {
        const { invite } = await getInvite();
        if (cancelled) return;
        setMainEventInvite(invite);
      } catch (e) {
        if (cancelled) return;
        logger.warn(
          'Failed to load Main Event invite:',
          e instanceof Error ? e.message : String(e)
        );
      }
    };
    loadInviteRef.current = loadInvite;

    // Whether the player is currently IN an active Main Event (for the Resume
    // bar). Same low cadence as the invite — NOT tick frequency (it changes only
    // on accept/decline/bust) and the endpoint isn't on the polling rate-limit.
    const loadActiveTournament = async () => {
      try {
        const lobby = await getTournamentLobby();
        if (cancelled) return;
        setActiveTournamentId(lobby.has_active ? (lobby.active?.tournament_id ?? null) : null);
      } catch (e) {
        if (cancelled) return;
        logger.warn(
          'Failed to load active tournament:',
          e instanceof Error ? e.message : String(e)
        );
      }
    };
    loadActiveTournamentRef.current = loadActiveTournament;

    // The lobby is an always-browsable hub: we no longer bounce a player
    // with a live session straight back into their game. Instead the
    // `seated_table_id` from the load drives a "you're here" pin on that
    // table card + a persistent Resume bar — so resume is always one tap
    // away, survives refresh, and is consistent from every entry point
    // (the old auto-redirect only ever "helped" the Career menu button,
    // at the cost of never showing the player which table they were at).
    (async () => {
      await Promise.all([load(), loadInvite(), loadActiveTournament()]);
      if (cancelled) return;
      interval = setInterval(() => {
        void load();
        void loadInvite();
        void loadActiveTournament();
      }, LOBBY_REFRESH_INTERVAL_MS);
    })();

    return () => {
      cancelled = true;
      if (interval !== null) clearInterval(interval);
    };
    // Mount-only: sets up the load + fallback poll. `load` is defined
    // inline and the lobby no longer reads any reactive value here.
  }, []);

  // Realtime push. The backend's `connect` handler joins this user's
  // lobby room (auth comes from the session cookie via withCredentials),
  // and the world ticker emits `lobby_tick` / `world_event` to it. We
  // debounce-refetch on tick and merge pushed events into the feed for
  // instant motion ahead of the refetch. No-op gracefully if the socket
  // can't connect — the fallback poll above still refreshes.
  useEffect(() => {
    const socket = createAuthedSocket(config.SOCKET_URL, {
      withCredentials: true,
      ...(SOCKET_TRANSPORTS ? { transports: SOCKET_TRANSPORTS } : {}),
    });

    let debounce: ReturnType<typeof setTimeout> | null = null;
    const onTick = () => {
      if (debounce) return;
      debounce = setTimeout(() => {
        debounce = null;
        void reloadLobbyRef.current();
        // NB: the Main Event invite is intentionally NOT refetched on every tick.
        // `GET /api/tournament/invite` is an expensive call (sandbox lock + the
        // chairman's offer/expire sweep) and the offer changes rarely (cooldowns
        // of minutes, a 10-min window). It refreshes on mount + the slow interval
        // below, and immediately after a user action (accept/decline). Polling it
        // at tick frequency once the world ticker is live overran its rate limit.
      }, LOBBY_TICK_DEBOUNCE_MS);
    };
    const onWorldEvent = (event: WorldEvent) => {
      // Merge for immediate motion; the debounced refetch reconciles to
      // server truth. Shared merge de-dupes on the natural key so a
      // tick-refetch landing right after doesn't double-show.
      setEvents((prev) => mergeEvents(prev, [event]));
      // Future: curated "signal" toasts (whale arrived / on a heater /
      // on tilt) hang off this same channel — see CASH_MODE_REALTIME_TICKER.md.
    };

    socket.on('lobby_tick', onTick);
    socket.on('world_event', onWorldEvent);

    return () => {
      if (debounce) clearTimeout(debounce);
      socket.off('lobby_tick', onTick);
      socket.off('world_event', onWorldEvent);
      socket.disconnect();
    };
  }, []);

  const handlePaceChange = useCallback(
    async (pace: WorldPace) => {
      const prev = worldPace;
      setWorldPaceState(pace); // optimistic
      try {
        await setWorldPace(pace);
      } catch (e) {
        setWorldPaceState(prev); // revert on failure
        logger.error('Failed to set world pace:', e instanceof Error ? e.message : String(e));
      }
    },
    [worldPace]
  );

  const handleSeatTap = useCallback(
    async (table: LobbyTable, seatIndex: number) => {
      if (busy) return;
      setSitError(null);
      setBusy(true);
      // Optimistic transition: drop the shuffle screen in immediately so the
      // press registers, rather than waiting on the /sit round-trip.
      setSittingDown({ submessage: table.table_name ?? `${table.stake_label} table` });
      try {
        const result = await sitAtTable(table.table_id, seatIndex);
        if ('kind' in result) {
          // Sponsor flow takes over the screen — drop the transition so the
          // modal is visible and we stay on the lobby.
          setSittingDown(null);
          // Open sponsor modal scoped to the seat the backend actually
          // reserved. It echoes table_id + seat_index because live-fill
          // may have taken the tapped seat and the server fell back to
          // another open one — targeting the original index would then
          // reserve the wrong (or a taken) seat.
          setSponsorState({
            stakeLabel: result.data.stake_label,
            tableId: result.data.table_id ?? table.table_id,
            seatIndex: result.data.seat_index ?? seatIndex,
          });
          return;
        }
        navigate(`/game/${result.game_id}`);
      } catch (e) {
        const err = e as Error & { status?: number; gameId?: string };
        const status = err.status;
        const msg = e instanceof Error ? e.message : String(e);
        logger.error('Sit failed:', msg);
        // The "a cash session is already active" 409 carries the existing
        // game_id (the seat-race / full-table 409s carry seat_kind instead).
        // It isn't a failure — the player just can't open a second seat — so
        // route them into the game they're already in (Resume) and keep the
        // transition up for the handoff, rather than the confusing "seat
        // filled up" message.
        if (status === 409 && err.gameId) {
          navigate(`/game/${err.gameId}`);
          return;
        }
        // Any other failure: drop the transition so the lobby (and the error /
        // refresh) is visible again instead of a stuck shuffle screen.
        setSittingDown(null);
        if (status === 409) {
          // Seat-race or full table: the lobby snapshot was stale. Refresh
          // it so the seat state reconciles instead of leaving a silently
          // dead button, and tell the player to try again.
          setSitError('That seat just filled up — refreshing the lobby. Try again.');
          void reloadLobbyRef.current();
        } else {
          setSitError(msg);
        }
      } finally {
        setBusy(false);
      }
    },
    [busy, navigate]
  );

  /** Dismiss the SponsorModal, releasing the seat-hold the /sit 402
   *  placed so an AI can't be cut out of taking it (and so the player
   *  isn't shown as parked there). Fire-and-forget + idempotent
   *  server-side: a successful sponsor-and-sit already converted the
   *  hold to a human seat, so release is a harmless no-op there. */
  const handleSponsorClose = useCallback(() => {
    const held = sponsorState;
    setSponsorState(null);
    if (held) {
      releaseSeat(held.tableId, held.seatIndex).catch((e) => {
        logger.warn(
          'Failed to release sponsorship seat-hold:',
          e instanceof Error ? e.message : String(e)
        );
      });
    }
  }, [sponsorState]);

  /** Resume the player's in-progress game. The lobby knows the seated
   *  table_id but not the game_id, so we resolve it via /api/cash/state
   *  (same source the mount redirect uses) and navigate. */
  const handleResume = useCallback(async () => {
    try {
      const state = await getState();
      if (state.state?.game_id) navigate(`/game/${state.state.game_id}`);
    } catch (e) {
      logger.error('Resume failed:', e instanceof Error ? e.message : String(e));
    }
  }, [navigate]);

  /** End the in-progress session from the lobby without sitting back
   *  down. Hits /api/cash/leave, which (post-hardening) cold-loads a
   *  DB-only session and settles it properly before tearing it down —
   *  the escape valve for a session that got wedged after a restart.
   *  On success we clear the Resume bar locally and reload the lobby so
   *  the seat/Resume state reconciles. */
  const handleEndSession = useCallback(async () => {
    if (endingSession) return;
    if (
      !window.confirm(
        'End your current session? Your table chips will be cashed out and any active stake settled.'
      )
    ) {
      return;
    }
    setEndingSession(true);
    setSitError(null);
    try {
      await leaveTable();
      setHasActiveSession(false);
      setSeatedTableId(null);
      setSeatedStakeLabelFromServer(null);
      await reloadLobbyRef.current();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('End session failed:', msg);
      setSitError(msg);
    } finally {
      setEndingSession(false);
    }
  }, [endingSession]);

  /** Open the StakeOfferModal pre-targeted to a candidate. Looks up
   *  the target tier's [min, max] window from the lobby's tables so
   *  the modal doesn't need its own fetch. */
  const handleStakeClick = useCallback(
    (candidate: StakableAiCandidate, targetStakeLabel: string) => {
      const table = tables.find((t) => t.stake_label === targetStakeLabel);
      if (!table) {
        // The lobby and stakable-AI endpoints normally agree, but
        // race conditions can produce a candidate whose target tier
        // isn't in the current lobby snapshot. Fall back to ignoring
        // the click — the next poll will reconcile.
        logger.warn('Stake target tier missing from lobby tables:', targetStakeLabel);
        return;
      }
      setStakeTarget({
        candidate,
        stakeLabel: targetStakeLabel as StakeLabel,
        minBuyIn: table.min_buy_in,
        maxBuyIn: table.max_buy_in,
      });
    },
    [tables]
  );

  /** Register accepted → the human's live tournament table is built; route into
   *  it through the normal game UI (back-nav returns to /tournament). */
  const handleEnterTournament = useCallback(
    (gameId: string) => {
      setSittingDown({ submessage: 'Main Event' });
      navigate(`/game/${gameId}`);
    },
    [navigate]
  );

  /** The invite was declined (or otherwise resolved) — clear the card and
   *  refetch so a freshly-spawned autonomous run doesn't leave a stale offer. */
  const handleInviteResolved = useCallback(() => {
    setMainEventInvite(null);
    void loadInviteRef.current();
  }, []);

  /** Resume an in-progress Main Event: resolve its live game (sit is idempotent —
   *  returns the existing game when one's already built) and navigate in. */
  const handleResumeTournament = useCallback(async () => {
    if (!activeTournamentId) return;
    setSittingDown({ submessage: 'Main Event' });
    try {
      const { game_id } = await sitTournament(activeTournamentId);
      navigate(`/game/${game_id}`);
    } catch (e) {
      setSittingDown(null);
      logger.error('Resume Main Event failed:', e instanceof Error ? e.message : String(e));
      // The tournament likely ended (busted / complete) — clear the bar.
      void loadActiveTournamentRef.current();
    }
  }, [activeTournamentId, navigate]);

  // Stake label of the table the player is seated at (for the Resume bar
  // text). Prefer the live lobby snapshot (stays in sync as the session
  // ends); fall back to the server-provided label for a cold session whose
  // table isn't in the rendered list.
  const seatedStakeLabel =
    (seatedTableId
      ? (tables.find((t) => t.table_id === seatedTableId)?.stake_label ?? null)
      : null) ?? seatedStakeLabelFromServer;

  // The "meanwhile, elsewhere" strip for the join transition — the same
  // rare-events digest the interhand shuffle uses, built from the lobby's
  // live world feed so the wait shows the room is alive.
  const sitTicker = useMemo<TickerLine[]>(
    () =>
      selectInterhandTicker(events, 3).map((e) => ({
        key: feedEventKey(e),
        icon: renderEventIcon(e.type),
        message: e.message,
      })),
    [events]
  );

  return (
    <>
      <ShuffleLoading
        isVisible={!!sittingDown}
        message="Taking your seat"
        submessage={sittingDown?.submessage}
        ticker={sitTicker}
        exitStyle="slide"
      />
      <MenuBar
        onBack={() => navigate('/menu')}
        title="The Circuit"
        showUserInfo
        onMainMenu={() => navigate('/menu')}
        onAdminTools={() => {
          rememberAdminOrigin('/cash');
          navigate('/admin');
        }}
      />
      <PageLayout variant="top" glowColor="gold" hasMenuBar className="cash-lobby-layout">
        <div className="cash-entry">
          {bankroll !== null && (
            <CareerHero
              bankroll={bankroll}
              lastSessionDelta={lastSessionDelta}
              bankrollHistory={bankrollHistory}
              pendingForgivenessCount={pendingForgivenessCount}
              onOpenNetWorth={() => setNetWorthOpen(true)}
            />
          )}

          {reputation && <ReputationPanel reputation={reputation} />}

          {(hasActiveSession || seatedTableId) && (
            <div className="cash-entry__resume-row">
              <button type="button" className="cash-entry__resume" onClick={handleResume}>
                <Play size={18} aria-hidden="true" />
                <span className="cash-entry__resume-text">
                  Resume your{seatedStakeLabel ? ` ${seatedStakeLabel}` : ''} session
                  {(() => {
                    const ago = formatPausedAgo(seatedSince);
                    return ago ? <span className="cash-entry__resume-age"> · {ago}</span> : null;
                  })()}
                </span>
                <ChevronRight size={18} className="cash-entry__resume-arrow" aria-hidden="true" />
              </button>
              {/* Escape valve: a session that wedged after a restart can
                  be ended here without first having to resume into it. */}
              <button
                type="button"
                className="cash-entry__end-session"
                onClick={handleEndSession}
                disabled={endingSession}
              >
                {endingSession ? 'Ending…' : 'End session'}
              </button>
            </div>
          )}

          {activeTournamentId && (
            <button type="button" className="main-event-resume" onClick={handleResumeTournament}>
              <Trophy size={18} aria-hidden="true" />
              <span className="main-event-resume__text">Resume the Main Event</span>
              <ChevronRight size={18} className="main-event-resume__arrow" aria-hidden="true" />
            </button>
          )}

          {!activeTournamentId && mainEventInvite && mainEventInvite.status === 'offered' && (
            <MainEventCard
              invite={mainEventInvite}
              onEnter={handleEnterTournament}
              onResolved={handleInviteResolved}
              onRegisterStart={() => setSittingDown({ submessage: 'Main Event' })}
              onRegisterError={() => setSittingDown(null)}
            />
          )}

          <ActivityTicker
            events={events}
            worldPace={worldPace}
            onPaceChange={handlePaceChange}
            onOpenIntel={() => setIntelHubOpen(true)}
          />

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

          <div className="cash-entry__venues">
            <div className="cash-entry__tabs" role="tablist" aria-label="Table venues">
              <button
                type="button"
                role="tab"
                aria-selected={activeVenue === 'cardroom'}
                className={`cash-entry__tab${activeVenue === 'cardroom' ? ' is-active' : ''}`}
                onClick={() => setActiveVenue('cardroom')}
              >
                <Spade size={15} aria-hidden="true" />
                Cardroom
                <span className="cash-entry__tab-count">{cardroomTables.length}</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeVenue === 'casino'}
                className={`cash-entry__tab${activeVenue === 'casino' ? ' is-active' : ''}`}
                onClick={() => setActiveVenue('casino')}
              >
                <Dices size={15} aria-hidden="true" />
                Casino
                <span className="cash-entry__tab-count">{casinoTables.length}</span>
                {casinoClosingCount > 0 && (
                  <Clock
                    size={13}
                    className="cash-entry__tab-closing"
                    aria-label={`${casinoClosingCount} table${casinoClosingCount === 1 ? '' : 's'} closing`}
                  />
                )}
              </button>
            </div>

            {activeVenue === 'cardroom' ? (
              <section className="cash-entry__stakes">
                {cardroomTables.length === 0 ? (
                  <p className="cash-entry__venue-empty">No tables open right now.</p>
                ) : (
                  STAKES.map((stake) => {
                    const tierTables = tablesByStake.get(stake) ?? [];
                    if (tierTables.length === 0) return null;
                    const isCollapsed = collapsedTiers.has(stake);
                    const meta = tierMeta(tierTables);
                    const gap = Math.max(0, meta.minBuyIn - (bankroll ?? 0));
                    return (
                      <div
                        key={stake}
                        className={`cash-entry__tier cash-entry__tier--${meta.access}${isCollapsed ? ' cash-entry__tier--collapsed' : ''}`}
                      >
                        <button
                          type="button"
                          className="cash-entry__tier-header"
                          onClick={() => toggleTier(stake)}
                          aria-expanded={!isCollapsed}
                        >
                          <span className="cash-entry__tier-label">{stake}</span>
                          <span className="cash-entry__tier-name">{TIER_NICKNAMES[stake]}</span>
                          <span className="cash-entry__tier-spacer" />
                          {meta.access === 'open' && (
                            <span className="cash-entry__tier-summary">
                              <i className="cash-entry__tier-dot" aria-hidden="true" />
                              {meta.openSeats} open · {meta.count}{' '}
                              {meta.count === 1 ? 'table' : 'tables'}
                            </span>
                          )}
                          {meta.access === 'stakeable' && (
                            <span className="cash-entry__tier-badge cash-entry__tier-badge--stake">
                              Get staked
                            </span>
                          )}
                          {meta.access === 'locked' && (
                            <span className="cash-entry__tier-badge cash-entry__tier-badge--locked">
                              <Lock size={11} aria-hidden="true" />
                              earn ${gap.toLocaleString()}
                            </span>
                          )}
                          <ChevronDown
                            size={18}
                            className="cash-entry__tier-chevron"
                            aria-hidden="true"
                          />
                        </button>
                        {meta.access === 'locked' && bankroll != null && meta.minBuyIn > 0 && (
                          <div
                            className="cash-entry__tier-progress"
                            title={`$${bankroll.toLocaleString()} / $${meta.minBuyIn.toLocaleString()} toward the ${stake} buy-in`}
                          >
                            <i
                              style={{
                                width: `${Math.min(100, Math.round((bankroll / meta.minBuyIn) * 100))}%`,
                              }}
                            />
                          </div>
                        )}
                        {!isCollapsed && (
                          <div className="cash-entry__stake-grid">
                            {tierTables.map((t) => (
                              <TableCard
                                key={t.table_id}
                                table={t}
                                busy={busy}
                                onSeatTap={(seatIndex) => handleSeatTap(t, seatIndex)}
                                onAiSeatClick={setDossier}
                                isSeated={seatedTableId === t.table_id}
                                onResume={handleResume}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </section>
            ) : (
              <section className="cash-entry__stakes cash-entry__stakes--casino">
                <p className="cash-entry__venue-intro">
                  The $2 floor — soft games packed with tourists. Tables fill and break fast, so
                  grab a seat while the fish are biting.
                </p>
                {casinoTables.length === 0 ? (
                  <div className="cash-entry__casino-empty">
                    The floor’s quiet right now. New casino tables open as the pool fills — check
                    back soon.
                  </div>
                ) : (
                  <div className="cash-entry__stake-grid">
                    {casinoTables.map((t) => (
                      <TableCard
                        key={t.table_id}
                        table={t}
                        busy={busy}
                        onSeatTap={(seatIndex) => handleSeatTap(t, seatIndex)}
                        onAiSeatClick={setDossier}
                        isSeated={seatedTableId === t.table_id}
                        onResume={handleResume}
                      />
                    ))}
                  </div>
                )}
              </section>
            )}
          </div>

          <IdleStakablePanel
            refreshKey={stakablePanelTick}
            onStake={handleStakeClick}
            onOpenDossier={setDossier}
          />
        </div>
        <SponsorModal
          isOpen={sponsorState !== null}
          stakeLabel={sponsorState?.stakeLabel ?? null}
          origin={
            sponsorState
              ? { tableId: sponsorState.tableId, seatIndex: sponsorState.seatIndex }
              : null
          }
          tableName={
            sponsorState
              ? (tables.find((t) => t.table_id === sponsorState.tableId)?.table_name ?? null)
              : null
          }
          onClose={handleSponsorClose}
        />
        <CharacterDetailCard
          isOpen={dossier !== null}
          onClose={() => setDossier(null)}
          character={dossier?.dossier ?? { name: '' }}
          origin={dossier?.origin}
          identifier={dossier?.identifier}
          circuitContext /* the lobby is always the Circuit */
          // Refresh the lobby intel surfaces (incl. the file cabinet behind
          // this card) after an informant purchase so unlock state updates.
          onIntelChanged={() => setStakablePanelTick((t) => t + 1)}
        />
        <NetWorthDrawer
          isOpen={netWorthOpen}
          onClose={() => setNetWorthOpen(false)}
          onPayoff={() => {
            void reloadLobbyRef.current();
          }}
        />
        <IntelHub
          isOpen={intelHubOpen}
          onClose={() => setIntelHubOpen(false)}
          events={events}
          refreshTick={stakablePanelTick}
          onOpenDossier={(person: FileCabinetPerson) => {
            // Open the dossier ON TOP of the hub (it has a higher z-index) and
            // leave the hub open behind it — so closing the dossier returns
            // you to the file cabinet you came from, on the same tab, rather
            // than dropping you back to the lobby. Circuit context, so the
            // informant buy buttons show.
            setDossier({
              dossier: { name: person.name },
              origin: { x: window.innerWidth / 2, y: window.innerHeight / 2 },
              identifier: person.personality_id,
            });
          }}
        />
        <StakeOfferModal
          target={stakeTarget}
          bankroll={bankroll ?? 0}
          onClose={() => setStakeTarget(null)}
          onAccepted={() => {
            // Don't close immediately — modal shows the "accepted" notice
            // and the player taps Close. But reload lobby so the AI's
            // new seat + in_active_stake glyph appear in the background.
            void reloadLobbyRef.current();
          }}
        />
      </PageLayout>
    </>
  );
}
