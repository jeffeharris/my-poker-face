# Chat Phase 2 Features Plan

## Overview
This document outlines the implementation plan for Phase 2 chat features. All features are behind feature flags that can be toggled in the Debug Panel.

## Feature Flags Setup

### Configuration
Features are controlled via the Debug Panel > Feature Flags tab:
- `quickSuggestions` - AI-powered quick chat suggestions
- `playerFilter` - Filter messages by specific player
- `messageGrouping` - Group consecutive messages from same player
- `eventIndicators` - Special visual indicators for events

### Usage
```typescript
import { useFeatureFlags } from '../../debug/FeatureFlags';

// In component
const featureFlags = useFeatureFlags();
if (featureFlags.playerFilter) {
  // Show player filter dropdown
}
```

## Features

### 1. Player Filter Dropdown ✅ (Implemented)
**Status**: Complete

**Implementation**:
- Dropdown shows all players who have sent messages
- Filters both regular messages and action messages
- Maintains player colors in dropdown
- Only visible when feature flag is enabled

**Usage**:
1. Enable "Player Filter Dropdown" in Debug > Feature Flags
2. Select a player from the dropdown
3. Only messages from that player will be shown

### 2. Quick Chat Suggestions ✅ (Implemented)
**Status**: Complete

**Implementation**:
- AI-powered context-aware suggestions using OpenAILLMAssistant
- Shows 3 suggestions when it's player's turn
- Categories: reaction, strategic, social
- Refreshes based on game events (raises, all-ins)
- 30-second cooldown to prevent API spam
- Fallback suggestions if AI fails

**Backend API**:
```
POST /api/game/{gameId}/chat-suggestions
{
  "playerName": "Jeff",
  "lastAction": {
    "type": "raise",
    "player": "Mike",
    "amount": 200
  },
  "chipPosition": "chip leader"
}
```

**Features**:
- Click suggestion to populate input field
- Refresh button for new suggestions
- Type-specific styling (reaction/strategic/social)
- Loading state while fetching
- Only shows during player's turn

**Usage**:
1. Enable "Quick Chat Suggestions" in Debug > Feature Flags
2. Suggestions appear automatically on your turn
3. Click to use or refresh for new ones

### 3. Message Grouping ✅ (Implemented)
**Status**: Complete

**Implementation**:
- Consecutive messages from same player are visually grouped
- Header (sender name, icon, timestamp) only shown on first message
- Reduced spacing between grouped messages
- Softer border radius for middle messages
- Works with both regular messages and action messages

**Visual Changes**:
- First message in group: Normal styling
- Middle messages: Reduced top padding, squared corners
- Last message in group: Reduced bottom margin
- Single message: Normal styling (not grouped)

**Usage**:
1. Enable "Message Grouping" in Debug > Feature Flags
2. Send multiple messages in a row
3. Messages will automatically group with cleaner appearance

### 4. Event Indicators ✅ (Implemented)
**Status**: Complete

**Implementation**:
- Detects and highlights special game events:
  - 🏆 **Wins** - Gold gradient background with pulse animation
  - 📢 **All-ins** - Orange gradient with pulse animation  
  - 💰 **Big pots** ($500+) - Green gradient with pulse
  - 🎭 **Showdowns** - Purple gradient background
  - 💀 **Eliminations** - Red gradient with shake animation

**Visual Effects**:
- Event-specific emoji appears before message
- Gradient backgrounds matching event type
- Border color override for emphasis
- Animations: pulse, bounce, and shake effects
- Works with existing message types (system, table, etc.)

**Usage**:
1. Enable "Special Event Indicators" in Debug > Feature Flags
2. Events are automatically detected from message content
3. Visual effects highlight important moments

## Testing Instructions

1. Start the app and open a game
2. Click "Show Debug" button
3. Navigate to "Feature Flags" tab
4. Toggle features on/off to test
5. Settings persist across refreshes

## Implementation Progress

- [x] Feature flags system
- [x] Player filter dropdown
- [x] Quick chat suggestions
- [x] Message grouping
- [x] Event indicators

All Phase 2 features are now complete! 🎉

## Known Limitations

- **Message Grouping**: Currently groups consecutive chat messages from the same player. Action messages may not group consistently due to their different sender structure. This is acceptable for now as actions are important events that benefit from standing out.

## Next Steps

1. Implement quick chat suggestions:
   - Add API endpoint for AI suggestions
   - Create suggestion pills UI
   - Hook up to chat input

2. Implement message grouping:
   - Modify message rendering logic
   - Add visual grouping indicators
   - Handle timestamp display

3. Implement event indicators:
   - Create event detection system
   - Design special message styles
   - Add animations