# React Poker App - CLAUDE.md

Guide for working with the React frontend of My Poker Face.

## Tech Stack

- **Framework**: React 18 with TypeScript
- **Build Tool**: Vite
- **Styling**: Tailwind CSS + component CSS files
- **State Management**: Zustand store (`gameStore.ts`) + custom hooks (`usePokerGame`, `useGameState`)
- **Real-time**: Socket.IO client
- **Animations**: Framer Motion
- **Routing**: React Router v7
- **Notifications**: react-hot-toast
- **Icons**: Lucide React
- **Testing**: Vitest + React Testing Library + Playwright (E2E)

## Architecture

### Backend Integration
- **API**: Flask REST backend (not FastAPI)
- **WebSocket**: Socket.IO for real-time game state updates
- **Deployment**: Docker + Caddy reverse proxy (not Vercel/Netlify)

### Key Patterns
- Route-level code splitting via `React.lazy` for admin and secondary pages
- `ErrorBoundary` component for graceful error handling
- `useOnlineStatus` hook for offline detection

### State Management

Two-layer architecture:

1. **Zustand store** (`src/stores/gameStore.ts`) — the single source of truth for game state. Provides granular selectors so components subscribe only to the slices they need (e.g., `state.players`, `state.phase`, `state.pot`).
2. **`usePokerGame` hook** (`src/hooks/usePokerGame.ts`) — manages socket lifecycle, receives game state from the backend, and writes it into the Zustand store via `applyGameState()`. Also exposes actions (`handleAction`, `sendMessage`, etc.) and non-store state (winners, tournament info).

**How components should read game state:**
- **Mobile components** (`MobilePokerTable` and children): read directly from Zustand store selectors for granular re-render control.
- **Desktop components** (`PokerTable`): use the composed `gameState` object returned by `usePokerGame` for backward compatibility.

### Performance: React.memo & Memoization

Leaf components are wrapped with `React.memo` to prevent unnecessary re-renders. When adding or modifying memoized components, ensure props are reference-stable:

- **Callbacks**: wrap with `useCallback` in the parent. Never pass inline arrow functions as props to memoized children.
- **Derived objects/arrays**: wrap with `useMemo` in the parent. Zustand store selectors return new references on every state change, so derived values (e.g., filtering players, computing opponents) must be memoized.
- **JSX as props**: if passing JSX as a prop (e.g., `centerContent`), wrap it in `useMemo`.
- **Primitives**: strings, numbers, booleans are always stable — no action needed.

Pure utility functions with no dependency on props or state (e.g., parsers, formatters) should be defined at module level, not wrapped in `useCallback`.

## Project Structure

```
react/react/src/
├── components/
│   ├── admin/          # Admin dashboard, model/personality/prompt management
│   ├── auth/           # Login, registration
│   ├── cards/          # Card rendering (Card.tsx, DebugHoleCard)
│   ├── chat/           # Chat panel, quick chat suggestions
│   ├── debug/          # Debug tools
│   ├── game/           # Core game UI
│   │   ├── ActionButtons/     # Fold/check/call/raise controls
│   │   ├── ActivityFeed/      # Game event log
│   │   ├── GameHeader/        # Top bar with game info
│   │   ├── GamePage.tsx       # Main game page component
│   │   ├── PlayerCommandCenter/ # Player action area
│   │   ├── PokerTable/        # Table layout and player positions
│   │   ├── StadiumLayout/     # Alternative table layout
│   │   ├── StatsPanel/        # Game statistics
│   │   ├── TournamentComplete/ # End-of-tournament screen
│   │   └── WinnerAnnouncement/ # Hand winner display
│   ├── landing/        # Landing page
│   ├── legal/          # Terms, privacy
│   ├── menus/          # Game setup (GameMenu, GameSelector, CustomGameConfig)
│   ├── mobile/         # Mobile-specific components
│   ├── pwa/            # PWA install prompt
│   └── shared/         # Reusable UI components
├── contexts/           # React contexts (auth, theme)
├── hooks/              # Custom hooks (usePokerGame, useSocket, useAuth, etc.)
├── types/              # TypeScript type definitions
├── utils/              # Utility functions
├── config.ts           # App configuration
└── App.tsx             # Root component with routing
```

## Commands

```bash
# Development
cd react/react && npm run dev

# Build
cd react/react && npm run build

# Type checking (runs in Docker via test script)
python3 scripts/test.py --ts

# Unit tests
cd react/react && npm test

# Lint
cd react/react && npm run lint
```

## Key Files

| File | Purpose |
|------|---------|
| `src/App.tsx` | Root routing, lazy-loaded routes |
| `src/stores/gameStore.ts` | Zustand store — single source of truth for game state |
| `src/hooks/usePokerGame.ts` | Socket lifecycle, writes to Zustand store, exposes actions |
| `src/hooks/useSocket.ts` | Socket.IO connection management |
| `src/components/game/GamePage.tsx` | Main game view |
| `src/types/game.ts` | Game state TypeScript types |
| `src/config.ts` | API URL, environment config |
