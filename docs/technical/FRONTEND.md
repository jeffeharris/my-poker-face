# Frontend Architecture Documentation

This document describes the organization and architecture of the React frontend for My Poker Face.

## Overview

The frontend is a modern React application built with TypeScript and Vite, featuring:
- Organized component structure by feature/domain
- Centralized state management with React Context
- Custom hooks for reusable logic
- Type-safe development with TypeScript
- Real-time updates via WebSocket

**Status**: ✅ Migration Complete (as of January 2025)

## Project Structure

```
src/
├── components/
│   ├── cards/          # Card display components
│   ├── game/           # Core game components
│   ├── chat/           # Chat functionality
│   ├── stats/          # Statistics and pressure tracking
│   ├── debug/          # Debug tools (excluded in production)
│   ├── menus/          # Menu and game setup screens
│   └── admin/          # Admin tools (personality manager)
├── contexts/
│   └── ThemeContext.tsx # Theme/appearance context
├── hooks/
│   ├── usePokerGame.ts # Central game state, socket events, messages
│   ├── useSocket.ts    # WebSocket connection management
│   ├── useGameState.ts # Game state fetching and updates
│   └── usePolling.ts   # Fallback polling for game updates
├── types/
│   ├── game.ts         # Game state interfaces
│   ├── player.ts       # Player interfaces
│   ├── chat.ts         # Chat message interfaces
│   └── index.ts        # Barrel exports
├── utils/
│   ├── api.ts          # Centralized API calls
│   └── cards.ts        # Card parsing utilities
└── config.ts           # Environment configuration
```

## Component Organization

### Cards (`/components/cards/`)
- `Card.tsx` - Base card component with three variants:
  - `Card` - Generic card display
  - `CommunityCard` - Community cards on the table
  - `HoleCard` - Player's hole cards (with visibility control)

### Game Components (`/components/game/`)
Core gameplay components organized in subfolders:
- `PokerTable/` - Main game table component
- `PokerTableLayout/` - Layout wrapper for game screen
- `ActionButtons/` - Player action controls (fold, check, raise)
- `PlayerThinking/` - AI thinking indicator
- `WinnerAnnouncement/` - Game winner overlay
- `LoadingIndicator/` - Loading states

### Chat Components (`/components/chat/`)
- `Chat/` - Chat message display
- `ChatSidebar/` - Collapsible chat panel

### Stats Components (`/components/stats/`)
- `PressureStats.tsx` - Real-time pressure statistics and player personalities

### Menu Components (`/components/menus/`)
- `PlayerNameEntry.tsx` - Initial player name input
- `GameMenu.tsx` - Main menu after name entry
- `GameSelector.tsx` - Load saved games
- `ThemedGameSelector.tsx` - Select themed games
- `CustomGameConfig.tsx` - Configure custom games

### Debug Components (`/components/debug/`)
Development tools that should be excluded from production:
- `ElasticityDebugPanel.tsx` - Personality elasticity testing
- `DebugPanel.tsx` - Game state debugging
- `CSSDebugger.tsx` - CSS layout debugging
- `CardDemo.tsx` - Card rendering demo

## State Management

### usePokerGame Hook
The `usePokerGame` hook (`src/hooks/usePokerGame.ts`) is the central game state manager:
- Game state (players, cards, pot, etc.)
- Socket.IO event handling
- Chat messages and deduplication
- AI thinking states
- Winner announcements and tournaments

### Usage Example
```typescript
import { usePokerGame } from '../hooks/usePokerGame';

function MyComponent() {
  const { gameState, sendAction, loading } = usePokerGame({ gameId });

  if (loading) return <LoadingIndicator />;

  return <div>{gameState?.pot.total}</div>;
}
```

## Custom Hooks

### useSocket
Manages WebSocket connections with auto-reconnect:
```typescript
const { socket, connect, disconnect } = useSocket(url, {
  onConnect: () => console.log('Connected'),
  onDisconnect: () => console.log('Disconnected')
});
```

### useGameState
Handles game state fetching and updates:
```typescript
const { 
  gameState, 
  loading, 
  error, 
  playerPositions,
  fetchGameState,
  updateGameState 
} = useGameState(gameId);
```

### usePolling
Provides polling functionality for fallback updates:
```typescript
const { start, stop } = usePolling(
  () => fetchGameState(),
  2000, // interval in ms
  { enabled: !socket.connected }
);
```

## API Integration

All API calls are centralized in `utils/api.ts`:

```typescript
import { gameAPI } from '../utils/api';

// Create new game
const { game_id } = await gameAPI.createGame(playerName);

// Send player action
await gameAPI.sendAction(gameId, 'raise', 100);

// Send chat message
await gameAPI.sendMessage(gameId, message, sender);
```

## Type Safety

All major data structures have TypeScript interfaces:

```typescript
// types/game.ts
export interface GameState {
  players: Player[];
  community_cards: string[];
  pot: { total: number };
  current_player_idx: number;
  // ... etc
}

// types/player.ts
export interface Player {
  name: string;
  stack: number;
  bet: number;
  is_folded: boolean;
  is_human: boolean;
  hand?: string[];
}
```

## Component Patterns

### Index Exports
Each component folder has an `index.ts` for clean imports:
```typescript
// components/cards/index.ts
export { Card, CommunityCard, HoleCard } from './Card';
```

### Props Interfaces
All components have clearly defined prop interfaces:
```typescript
interface ComponentProps {
  requiredProp: string;
  optionalProp?: boolean;
  onAction?: (value: string) => void;
}
```

### Functional Components
All components use functional component syntax with hooks:
```typescript
export function ComponentName({ prop1, prop2 }: ComponentProps) {
  const [state, setState] = useState(initialValue);
  // component logic
  return <div>...</div>;
}
```

## Development Guidelines

### Import Paths
- Use relative imports within the same feature folder
- Use absolute imports from `src/` for cross-feature imports
- Always use `type` imports for TypeScript types

### Component Responsibilities
- **Container components** handle state and side effects
- **Presentational components** focus on UI rendering
- **Keep components focused** on a single responsibility

### Testing Strategy
- Unit test hooks and utilities
- Integration test component interactions
- E2E test full game flows

### Performance Considerations
- Use React.memo for expensive renders
- Implement lazy loading for heavy components
- Minimize re-renders with proper dependencies

## Production Considerations

### Excluding Debug Components
Debug components should be conditionally imported:
```typescript
const DebugPanel = import.meta.env.DEV 
  ? lazy(() => import('./components/debug/DebugPanel'))
  : null;
```

### Environment Configuration
All environment-specific values come from `config.ts`:
```typescript
export const config = {
  API_URL: import.meta.env.VITE_API_URL || 'http://localhost:5000',
  SOCKET_URL: import.meta.env.VITE_SOCKET_URL || 'ws://localhost:5000',
  ENABLE_DEBUG: import.meta.env.DEV
};
```

### Build Optimization
- Tree-shake unused code
- Chunk vendor dependencies
- Optimize images and assets
- Enable gzip compression

## Future Enhancements

1. **State Management**
   - Consider Redux Toolkit for more complex state
   - Add optimistic updates for better UX

2. **Testing**
   - Add React Testing Library tests
   - Implement Storybook for component documentation

3. **Performance**
   - Implement virtual scrolling for chat
   - Add service worker for offline support

4. **Features**
   - Add sound effects and haptic feedback
   - Implement gesture controls for mobile
   - Add player statistics dashboard

## Troubleshooting

### Common Issues

1. **Import Errors After Reorganization**
   - Update all import paths to reflect new structure
   - Check that index.ts files export all components

2. **TypeScript Errors**
   - Ensure all type imports use `import type`
   - Check that interfaces match actual data

3. **WebSocket Connection Issues**
   - Verify SOCKET_URL in config
   - Check that backend is running
   - Look for CORS issues

4. **State Not Updating**
   - Verify GameProvider wraps components
   - Check that hooks are used within provider
   - Ensure proper dependency arrays

## Migration History

The frontend was successfully migrated from a flat component structure to the organized architecture described above in January 2025. The migration preserved all CSS styling and functionality while improving code organization and maintainability.

### Key Improvements from Migration:
- Components organized by feature/domain
- Type definitions centralized in `/types/`
- Custom hooks extracted to `/hooks/`
- Utility functions in `/utils/`
- Clean import paths with barrel exports
- Resolved CSS naming conflicts (e.g., `.player-cards`)

This architecture provides a solid foundation for maintaining and extending the poker application while keeping the codebase organized and scalable.