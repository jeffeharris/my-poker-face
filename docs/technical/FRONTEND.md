---
purpose: Frontend architecture — React app structure, state management, and component layout
type: architecture
created: 2025-06-07
last_updated: 2026-06-03
---

# Frontend Architecture

The React frontend for My Poker Face. TypeScript + Vite, real-time over Socket.IO,
state held in Zustand. This doc maps the directory layout, the state-flow seam
(socket → store → components), and the cross-cutting providers — the parts you'd
otherwise reverse-engineer. It is not a per-component catalog; read the tree for that.

Code lives in `react/react/src/`. All paths below are relative to that root.

## Stack

Source of truth: `react/react/package.json`.

| Concern | Choice |
|---|---|
| Framework | React 19 (`react` / `react-dom` `^19`) |
| Build / dev | Vite 7 (`@vitejs/plugin-react`) |
| Language | TypeScript ~5.9 |
| State | Zustand 5 (`stores/`) + React Context providers (auth, usage, deck packs) |
| Routing | React Router 7 (`react-router-dom`) |
| Real-time | `socket.io-client` 4 |
| Animation | Framer Motion 12 |
| Icons / toasts | `lucide-react`, `react-hot-toast` |
| PWA | `vite-plugin-pwa` |
| Tests | Vitest + Testing Library (unit), Playwright (E2E) |

There is **no Redux and no `contexts/` directory** — providers live in `hooks/`
(e.g. `AuthProvider` is exported from `hooks/useAuth.tsx`).

## Build & dev (`package.json` scripts)

| Script | Does |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run build` | `tsc -b && vite build` |
| `npm run typecheck` | `tsc -b --noEmit` (also run in Docker via `python3 scripts/test.py --ts`) |
| `npm run lint` | ESLint (flat config, `typescript-eslint`) |
| `npm run format` / `format:check` | Prettier |
| `npm test` / `test:watch` | Vitest |

Environment config is centralized in `config.ts`. API and socket URLs default to
the current host in production (relative `''` / `window.location.origin`) and to
`http://<hostname>:<VITE_BACKEND_PORT|5000>` in dev. Two debug flags
(`ENABLE_DEBUG`, `ENABLE_AI_DEBUG`) read `VITE_*` env vars. Backend is Flask + Socket.IO
(not FastAPI), served behind Caddy in Docker (not Vercel/Netlify).

## State management

The app uses **Zustand stores** plus a small set of **Context providers** for
cross-cutting app state. There is no global game Context.

### Game state: the socket → store → component seam

```
Socket.IO event ──► usePokerGame ──► useGameStore.applyGameState() ──► components
   (backend)         (hooks/)            (stores/gameStore.ts)        (selectors)
```

- **`stores/gameStore.ts`** — the single source of truth for live game state
  (`useGameStore`, created via `create<GameStore>`). Holds granular slices: `players`,
  `phase`, `pot`, `communityCards`, betting context, blinds/dealer indices, `cashMode`,
  buffered `worldEvents`, run-out director flags, and an optimistic-action rollback
  snapshot. Components subscribe to only the slices they need.
- **`hooks/usePokerGame.ts`** — owns the socket lifecycle. It receives backend game
  state and writes it into the store via `applyGameState(...)` (`usePokerGame.ts:120`,
  `:294`, `:776`), and exposes actions (`handleAction`, `sendMessage`, fast-forward)
  plus non-store state (winners, tournament info). It composes a backward-compatible
  `gameState` object via `selectGameState` (`gameStore.ts:343`).

**Read conventions** (per `react/CLAUDE.md`):

- **Mobile** components (`mobile/MobilePokerTable` and children) read store selectors
  directly for granular re-render control.
- **Desktop** (`game/PokerTable`) reads the composed `gameState` object from
  `usePokerGame` for backward compatibility.

`selectGameState` and the store use stable empty/zero references
(`EMPTY_MESSAGES`, `ZERO_POT`) so selector identity stays stable across updates.

### Other stores

- **`stores/nicknameOverridesStore.ts`** (`useNicknameOverridesStore`) — local
  overrides for AI-assigned nicknames, hydrated from the backend on app load
  (`App.tsx` → `fetchNicknameOverrides`).

### Context providers (`main.tsx`)

Wrapped around the router in `main.tsx`, outermost first:
`BrowserRouter → AuthProvider → UsageStatsProvider → DeckPackProvider`.

| Provider | Defined in | Purpose |
|---|---|---|
| `AuthProvider` / `useAuth` | `hooks/useAuth.tsx` | Current user (guest or Google), `login`/`logout`/`checkAuth`, permissions; `hasPermission(user, perm)` helper |
| `UsageStatsProvider` | `hooks/UsageStatsProvider.tsx` | API usage/cost stats |
| `DeckPackProvider` / `useDeckPack` | `hooks/useDeckPack.tsx` | Active card-deck pack |

## Routing

`App.tsx` is the root: it defines routes with React Router 7 and **lazy-loads** most
secondary/admin routes via `React.lazy` + `Suspense` (game selectors, personality
manager, admin routes, landing page, cash `Lobby`, training screens, settings, PWA
install prompt). Eagerly imported: `HomeMenu`, `TournamentMenu`, auth (`LoginForm`,
`ProtectedRoute`), and `GamePage`. `ErrorBoundary` (`components/ErrorBoundary.tsx`)
wraps the tree for graceful failure.

## Directory map

Regenerated from `react/react/src/` (2026-06-03). Counts are approximate `.tsx` files
per directory and will drift — treat the list of directories as the durable part.

```
src/
├── App.tsx              # Root routing + lazy routes
├── main.tsx             # Provider stack + BrowserRouter mount
├── config.ts            # API/socket URLs, debug flags
├── components/
│   ├── ErrorBoundary.tsx
│   ├── admin/           # Admin dashboard, sidebar, routes; model/personality/prompt
│   │                    #   managers, chip ledger, relationship matrix, replay designer,
│   │                    #   cash whereabouts, coach effectiveness, range explorer
│   ├── auth/            # LoginForm, ProtectedRoute
│   ├── cards/           # Card.tsx + debug hole card
│   ├── cash/            # Cash/career mode: Lobby, TableCard, sponsor/stake/bust/solo
│   │                    #   modals, net-worth drawer, reputation/intel, activity ticker
│   ├── character/       # Character/dossier cards, sizing tells, api.ts (+ index.ts)
│   ├── chat/            # Chat panel / quick-chat
│   ├── debug/           # Dev-only debug panels
│   ├── dev/             # Layout/runout sandboxes (RunoutCommitSandbox, WinnerLayoutSandbox)
│   ├── game/            # Core game UI (subfolders below)
│   ├── landing/         # Marketing/landing page
│   ├── legal/           # Terms, privacy
│   ├── menus/           # HomeMenu, TournamentMenu, GameSelector, ThemedGameSelector,
│   │                    #   CustomGameConfig, PlayerNameEntry
│   ├── mobile/          # Mobile table, action buttons, coach bubble/panel, floating chat,
│   │                    #   heads-up panel, progression strip, stats bar
│   ├── profile/         # ProfilePage.css only (component lives elsewhere — see note)
│   ├── pwa/             # InstallPrompt
│   ├── settings/        # SettingsPage + coach/gameplay/profile settings
│   ├── shared/          # Reusable UI: PageLayout/PageHeader, BottomSheet, ShuffleLoading,
│   │                    #   GuestLimitModal, MenuBar, ThemedSelect, UserBadge/Dropdown, …
│   ├── stats/           # CareerStats
│   └── training/        # TrainingMenu, PreflopLeaks, PreflopDrill, SizingReadability
├── hooks/               # Custom hooks + the three Context providers (see below)
├── stores/              # Zustand: gameStore.ts, nicknameOverridesStore.ts (+ tests)
├── types/               # TS interfaces (game, player, chat, coach, llm, runout, …)
├── utils/               # api.ts, cards, formatters, logger, csrf, storage, playerOrdering, …
├── constants/           # gameModes, gamePhases, gameStructure, interhand/runout timing
├── config/              # timing.ts
├── styles/              # design-tokens.css, animations.css, action-badges.css
├── assets/
└── test/, __tests__/    # test setup + suites
```

**Note on `profile/`:** the directory currently holds only `ProfilePage.css`; the
profile UI component is `settings/ProfileSettings.tsx`. (Possible stray CSS — unverified
whether the page component was moved or removed.)

### `game/` subfolders

Each is a folder of related components (most with an `index.ts` barrel):

| Folder | Role |
|---|---|
| `GamePage.tsx` | Main game view (eager-imported in `App.tsx`) |
| `PokerTable/` | Desktop table layout + player positions |
| `StadiumLayout/` | Alternative desktop stadium layout |
| `ActionButtons/` | Fold/check/call/raise controls |
| `PlayerCommandCenter/` | Player action area |
| `GameHeader/` | Top bar (game info, coach toggle) |
| `ActivityFeed/` | Game event log |
| `StatsPanel/` | In-game statistics |
| `CoachDock/` | Docked coach panel (desktop) |
| `PlayerThinking/` | AI thinking indicator |
| `SeatSpeechBubble/` | Per-seat speech bubbles |
| `WinnerAnnouncement/` | Hand-winner overlay (+ quote flavor) |
| `TournamentComplete/` | End-of-tournament screen |
| `LoadingIndicator/` | Loading states |

## Key hooks

In `hooks/`. The list is large; these are the load-bearing ones.

| Hook | Role |
|---|---|
| `usePokerGame` | Socket lifecycle → writes Zustand store; exposes game actions |
| `useSocket` | Low-level Socket.IO connection management |
| `useGameState` | Game-state fetch/update helpers |
| `usePolling` | Fallback polling when the socket is down |
| `useAuth` | Auth context consumer (provider also defined here) |
| `useCoach`, `useCareerStats` | Coaching feedback / career-mode stats |
| `useRunoutDirector`, `useInterhandDirector` | Client-owned reveal/interhand pacing |
| `useCardAnimation`, `useCommunityCardAnimation` | Card reveal animations |
| `useBettingCalculations` | Raise/bet sizing math for the action UI |
| `useMediaQuery`, `useViewport` | Responsive layout (mobile vs desktop split) |
| `useOnlineStatus` | Offline detection |
| `useLLMProviders`, `useUsageStats`, `useAdminResource` | Admin/usage data |
| `useGuestChatLimit` | Guest chat-rate gating |

## API integration

REST calls are centralized in `utils/api.ts`. The primary export is `gameAPI`
(`createGame`, `loadGame`, `sendAction`, `fastForward`, `sendMessage`,
`getPressureStats`, chat-suggestion endpoints, …). An `adminAPI` object exists for
backward compatibility. CSRF handling lives in `utils/csrf.ts`.

```typescript
import { gameAPI } from '../utils/api';

const { game_id } = await gameAPI.createGame(playerName);
await gameAPI.sendAction(gameId, 'raise', 100);
await gameAPI.sendMessage(gameId, message, sender);
```

## Conventions

- **Functional components + hooks** throughout; no class components except
  `ErrorBoundary`.
- **`React.memo` on leaves**, with reference-stable props: wrap callbacks in
  `useCallback`, derived objects/arrays in `useMemo`. Zustand selectors return new
  references on each change, so derived values must be memoized. (Full rationale:
  `react/CLAUDE.md`.)
- **`import type`** for type-only imports.
- **Route-level code splitting** for admin and secondary pages (see Routing).

## Related docs

- `react/CLAUDE.md` — working guide for this app (state-management contract,
  memoization rules, key-file table).
- Backend: Flask + Socket.IO API (`flask_app/`).
</content>
