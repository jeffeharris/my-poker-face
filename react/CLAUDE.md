# React Poker App - CLAUDE.md

This document guides Claude Code in building a modern React-based poker application that leverages the existing Python poker engine while providing a rich, mobile-friendly user experience.

## Project Vision

Create a modern, responsive poker application that:
- Works seamlessly on desktop and mobile devices
- Provides real-time multiplayer gameplay
- Showcases AI player personalities with engaging UI
- Maintains the functional poker engine's integrity
- Offers a delightful user experience with smooth animations

## Architecture Overview

### Frontend (React)
- **Framework**: React 18+ with TypeScript
- **Build Tool**: Vite for fast development
- **Styling**: Tailwind CSS for responsive design
- **State Management**: Zustand or Context API
- **Real-time**: Socket.IO client
- **Animations**: Framer Motion
- **Mobile**: PWA capabilities

### Backend (Python)
- **API**: FastAPI or Flask-RESTful
- **WebSocket**: Socket.IO for real-time updates
- **Game Engine**: Existing poker module (unchanged)
- **Persistence**: SQLite with game state storage
- **AI**: OpenAI integration for personalities

### Key Design Decisions

1. **Separation of Concerns**
   - React handles all UI/UX
   - Python handles all game logic
   - Clear API contract between them

2. **Mobile-First Design**
   - Touch-friendly controls
   - Responsive poker table layout
   - Portrait and landscape support
   - Gesture support for actions

3. **Real-Time Updates**
   - WebSocket for game state changes
   - Optimistic UI updates
   - Smooth animations for state transitions

## UI/UX Features to Implement

### Core Game Features
1. **Poker Table Component**
   - Visual representation of players around table
   - Animated card dealing
   - Chip animations for bets
   - Pot display with smooth updates

2. **Player Positions**
   - Dynamic layout based on player count
   - Speech bubbles for AI personalities
   - Status indicators (thinking, folded, all-in)
   - Stack and bet displays

3. **Action Interface**
   - Swipe gestures for fold/check/call
   - Slider for bet/raise amounts
   - Quick bet buttons (1/2 pot, pot, all-in)
   - Visual feedback for actions

4. **Cards Display**
   - Smooth flip animations
   - Peek gesture for hole cards
   - Community cards with reveal animation
   - Hand strength indicator

### Enhanced Features (from CLI learnings)
1. **AI Personality Display**
   - Speech bubbles with personality responses
   - Typing indicators
   - Emotion indicators based on game state
   - Chat history in sidebar

2. **Game Information**
   - Pot odds calculator
   - Hand strength meter
   - Action history timeline
   - Position indicator

3. **Mobile Optimizations**
   - Landscape mode for full table view
   - Portrait mode with stacked layout
   - Touch-and-hold for more info
   - Haptic feedback for actions

## Development Approach

### Phase 1: Foundation
- Set up React app with TypeScript
- Create basic poker table component
- Implement responsive layout
- Connect to Python backend via REST API

### Phase 2: Core Gameplay
- Real-time updates via WebSocket
- Player actions and animations
- Card dealing and community cards
- Basic AI player integration

### Phase 3: Polish
- AI personality speech bubbles
- Advanced animations
- Sound effects
- Mobile optimizations

### Phase 4: Enhancement
- Tournament mode
- Statistics tracking
- Social features
- PWA capabilities

## API Design

### REST Endpoints
```
POST   /api/games          - Create new game
GET    /api/games/:id      - Get game state
POST   /api/games/:id/join - Join existing game
POST   /api/games/:id/action - Player action
GET    /api/games/:id/history - Get game history
```

### WebSocket Events
```
Client -> Server:
- join_game
- player_action
- send_message

Server -> Client:
- game_state_update
- player_joined
- player_action
- ai_message
- game_over
```

## Component Structure
```
src/
├── components/
│   ├── PokerTable/
│   │   ├── Table.tsx
│   │   ├── PlayerPosition.tsx
│   │   ├── CommunityCards.tsx
│   │   └── Pot.tsx
│   ├── Player/
│   │   ├── PlayerCard.tsx
│   │   ├── PlayerCards.tsx
│   │   ├── SpeechBubble.tsx
│   │   └── StatusIndicator.tsx
│   ├── Actions/
│   │   ├── ActionButtons.tsx
│   │   ├── BetSlider.tsx
│   │   └── QuickBets.tsx
│   └── UI/
│       ├── GameInfo.tsx
│       ├── ChatPanel.tsx
│       └── Statistics.tsx
├── hooks/
│   ├── useGameState.ts
│   ├── useWebSocket.ts
│   └── useAnimations.ts
├── services/
│   ├── api.ts
│   ├── websocket.ts
│   └── gameLogic.ts
└── utils/
    ├── poker.ts
    ├── animations.ts
    └── mobile.ts
```

## Mobile-Specific Considerations

1. **Touch Interactions**
   - Swipe to fold
   - Tap to check/call
   - Drag slider for bets
   - Long press for player info

2. **Layout Adaptations**
   - Stack players vertically in portrait
   - Show full table in landscape
   - Collapsible panels for chat/info
   - Bottom sheet for actions

3. **Performance**
   - Optimize animations for mobile
   - Lazy load non-critical components
   - Use CSS transforms for smooth animations
   - Minimize re-renders

## Testing Strategy

1. **Unit Tests**
   - Component testing with React Testing Library
   - Hook testing
   - Utility function tests

2. **Integration Tests**
   - API integration tests
   - WebSocket connection tests
   - Game flow tests

3. **E2E Tests**
   - Full game scenarios
   - Mobile gesture tests
   - Multi-player synchronization

## Deployment Considerations

1. **Frontend**: Vercel/Netlify for React app
2. **Backend**: Railway/Fly.io for Python API
3. **Database**: Managed PostgreSQL
4. **WebSocket**: Dedicated WebSocket server
5. **CDN**: CloudFlare for assets

## Next Steps

1. Initialize React app with Vite and TypeScript
2. Set up Tailwind CSS for styling
3. Create basic poker table component
4. Implement responsive layout
5. Connect to existing Python backend
6. Add WebSocket for real-time updates

This architecture provides a solid foundation for a modern, mobile-friendly poker application that showcases the AI personalities while maintaining the integrity of the existing game engine.