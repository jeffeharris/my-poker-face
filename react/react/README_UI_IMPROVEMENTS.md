# UI Layout Improvements

This document describes the new UI layout improvements implemented for the React poker application.

## Overview

The poker table UI has been redesigned with a cleaner, more organized layout that addresses previous issues:
- Less crowding on the table
- Better organization of UI elements
- Resizable chat sidebar
- Optional debug panel
- Support for up to 6 players

## New Components

### 1. PokerTableLayout
A grid-based layout wrapper that organizes the game into distinct areas:
- **Main area**: Poker table and action buttons
- **Right sidebar**: Chat panel
- **Bottom panel**: Debug information (optional)

### 2. ChatSidebar
A docked chat panel on the right side:
- Full height for better message visibility
- Clear message organization with icons
- Smooth scrolling
- Better timestamp formatting

### 3. DebugPanel
A collapsible bottom panel for debug information:
- **Elasticity tab**: Shows personality trait changes
- **Pressure stats tab**: Shows game pressure events
- Auto-refresh toggle
- Only visible when debug mode is enabled

### 4. GameMenu
New game configuration menu after login:
- **Quick Play**: Jump into a random game
- **Custom Game**: Choose specific opponents
- **Themed Game**: AI-generated personality groups
- **Continue Game**: Resume saved games

### 5. ThemedGameSelector
Theme selection interface with 8 predefined themes:
- Science Masters
- Hollywood Legends
- Sports Champions
- Historical Figures
- Music Icons
- Comedy Legends
- Famous Villains
- Surprise Me!

### 6. CustomGameConfig
Manual opponent selection:
- Search functionality
- Personality traits display
- Support for up to 5 AI opponents
- Difficulty settings (coming soon)

## Environment Variables

### Debug Mode
Enable the debug panel by setting:
```bash
VITE_ENABLE_DEBUG=true
```

For Docker:
```bash
ENABLE_DEBUG=true docker compose up
```

For local development:
```bash
./run_react_debug.sh  # Runs with debug enabled
```

## Layout Structure

```
┌─────────────────────────────────────────────┐
│  Main Game Area                │  Chat      │
│                                │  Sidebar   │
│  ┌─────────────────────────┐  │            │
│  │                         │  │            │
│  │     Poker Table         │  │            │
│  │                         │  │            │
│  └─────────────────────────┘  │            │
│                                │            │
│  [Action Buttons]              │            │
├────────────────────────────────┴────────────┤
│  Debug Panel (optional, collapsible)        │
└─────────────────────────────────────────────┘
```

## Key Improvements

1. **Reduced Crowding**: Players and UI elements have more breathing room
2. **Better Organization**: Clear separation of game, chat, and debug areas
3. **Responsive Design**: Works well on different screen sizes
4. **Configurable**: Debug panel can be hidden for cleaner gameplay
5. **6-Player Support**: Layout accommodates up to 6 players comfortably

## API Enhancements

### New Endpoints

1. **Generate Theme**
   ```
   POST /api/generate-theme
   Body: { theme, themeName, description }
   ```
   Uses OpenAI to select personalities matching a theme.

2. **Create Game with Personalities**
   ```
   POST /api/new-game
   Body: { playerName, personalities: string[] }
   ```
   Creates a game with specific AI opponents.

## Future Enhancements

1. **Resizable Panels**: Make chat and debug panels resizable
2. **Mobile Optimization**: Improve layout for mobile devices
3. **Animation Polish**: Add smooth transitions between views
4. **Keyboard Shortcuts**: Quick actions for power users
5. **Theme Persistence**: Remember user's preferred layout settings