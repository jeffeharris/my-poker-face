# React Poker App - CLAUDE.md

Guide for working with the React frontend of My Poker Face.

## Tech Stack

- **Framework**: React 18 with TypeScript
- **Build Tool**: Vite
- **Styling**: Tailwind CSS + component CSS files
- **State Management**: React Context API (`GameContext`)
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
- `GameContext` (`src/contexts/GameContext.tsx`) is the central state manager — handles WebSocket, HTTP API, state, and message dedup
- `usePokerGame` hook (`src/hooks/usePokerGame.ts`) manages socket events, game state, messages, winners, and tournaments
- Route-level code splitting via `React.lazy` for admin and secondary pages
- `ErrorBoundary` component for graceful error handling
- `useOnlineStatus` hook for offline detection

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
├── contexts/
│   └── GameContext.tsx  # Central game state + WebSocket management
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
| `src/contexts/GameContext.tsx` | Game state, WebSocket, API calls |
| `src/hooks/usePokerGame.ts` | Socket events, game flow management |
| `src/hooks/useSocket.ts` | Socket.IO connection management |
| `src/components/game/GamePage.tsx` | Main game view |
| `src/types/game.ts` | Game state TypeScript types |
| `src/config.ts` | API URL, environment config |
