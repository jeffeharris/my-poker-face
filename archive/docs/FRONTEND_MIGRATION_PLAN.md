# Frontend Migration Plan

## Current State
- Working poker game with all components in `/src/components/` (flat structure)
- CSS files are properly styled and functional
- Game works correctly with all features

## Goal
Match the architecture described in FRONTEND.md:
- Organize components into logical folders by feature/domain
- Add TypeScript types in `/types/` folder
- Create custom hooks in `/hooks/` folder
- Add GameContext for state management
- Create utils folder with api.ts
- Preserve ALL existing CSS and functionality

## Target Structure (from FRONTEND.md)
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
│   └── GameContext.tsx # Centralized game state management
├── hooks/
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

## Migration Phases

### Phase 1: Infrastructure Setup
1. **Create type definitions** (`/types/`)
   - Extract interfaces from components
   - Create game.ts, player.ts, chat.ts
   - Add barrel export index.ts

2. **Create utility functions** (`/utils/`)
   - Move card parsing logic to cards.ts
   - Create api.ts with centralized API calls
   - Ensure config.ts exists

3. **Create custom hooks** (`/hooks/`)
   - Extract socket logic → useSocket.ts
   - Extract game state logic → useGameState.ts
   - Extract polling logic → usePolling.ts

4. **Create GameContext** (`/contexts/`)
   - Centralize game state management
   - Provide hooks for components

### Phase 2: Component Migration (by folder)

#### Cards (`/components/cards/`)
- Card.tsx (includes CommunityCard, HoleCard variants)
- Card.css
- index.ts (barrel export)

#### Game Components (`/components/game/`)
Each in its own subfolder:
- PokerTable/
- PokerTableLayout/
- ActionButtons/
- LoadingIndicator/
- PlayerThinking/
- WinnerAnnouncement/

#### Chat Components (`/components/chat/`)
- Chat/ (if exists separately)
- ChatSidebar/

#### Stats Components (`/components/stats/`)
- PressureStats.tsx
- PressureStats.css

#### Menu Components (`/components/menus/`)
- PlayerNameEntry.tsx
- GameMenu.tsx
- GameSelector.tsx
- ThemedGameSelector.tsx
- CustomGameConfig.tsx

#### Debug Components (`/components/debug/`)
- ElasticityDebugPanel.tsx
- DebugPanel.tsx
- CSSDebugger.tsx
- CardDemo.tsx
- DebugPanelTest.tsx

#### Admin Components (`/components/admin/`)
- PersonalityManagerHTML.tsx

### Phase 3: Integration
1. Update App.tsx to use GameProvider
2. Update all component imports
3. Replace direct API calls with gameAPI utility
4. Replace local state with GameContext hooks

## Migration Process for Each Component

### Step 1: Prepare Infrastructure
```bash
# Create all folders first
mkdir -p src/{components/{cards,game,chat,stats,debug,menus,admin},contexts,hooks,types,utils}
```

### Step 2: Extract Types
Before moving any components:
1. Identify all interfaces in components
2. Create corresponding type files
3. Export from types/index.ts

### Step 3: Component Migration Steps
For each component:

1. **Create destination folder**
   ```bash
   mkdir -p components/[category]/ComponentName
   ```

2. **Copy files (preserve originals)**
   ```bash
   cp ComponentName.tsx components/[category]/ComponentName/
   cp ComponentName.css components/[category]/ComponentName/
   ```

3. **Create index.ts**
   ```typescript
   export { ComponentName } from './ComponentName';
   ```

4. **Update imports in copied file**
   - Fix relative paths (../ → ../../ or ../../../)
   - Import types from types folder
   - Use utils/api instead of direct fetch

5. **Test the component**
   - Update ONE import to test
   - Run the app
   - Verify styling and functionality
   - Check console for errors

6. **Update all imports** (only after testing)
   ```typescript
   // Before
   import { ComponentName } from './ComponentName';
   
   // After
   import { ComponentName } from './category/ComponentName';
   ```

7. **Remove old files** (only after confirming)
   ```bash
   rm ComponentName.tsx ComponentName.css
   ```

8. **Commit the migration**
   ```bash
   git add .
   git commit -m "Migrate ComponentName to [category] folder"
   ```

## Component Dependencies Map

To avoid breaking imports, migrate in this order:

### No Dependencies (migrate first)
- Card → `/cards/`
- LoadingIndicator → `/game/`
- PlayerThinking → `/game/`
- WinnerAnnouncement → `/game/`

### UI Components (migrate second)
- ChatSidebar → `/chat/`
- PressureStats → `/stats/`
- ActionButtons → `/game/`

### Container Components (migrate third)
- PokerTableLayout → `/game/`
- PokerTable → `/game/` (depends on many components)

### Menu Components (migrate fourth)
- PlayerNameEntry → `/menus/`
- GameMenu → `/menus/`
- GameSelector → `/menus/`
- ThemedGameSelector → `/menus/`
- CustomGameConfig → `/menus/`

### Debug/Admin (migrate last)
- All debug components → `/debug/`
- PersonalityManagerHTML → `/admin/`

## Import Path Updates

### CSS Imports
```css
/* Before */
import './ComponentName.css';

/* After - same, CSS stays with component */
import './ComponentName.css';
```

### Component Imports
```typescript
// Before
import { Card } from './Card';
import { ActionButtons } from './ActionButtons';

// After
import { Card } from '../cards';
import { ActionButtons } from './ActionButtons'; // same folder
```

### Config Imports
```typescript
// Before (from components/)
import { config } from '../config';

// After (from nested folders)
import { config } from '../../config'; // from game/
import { config } from '../../../config'; // from game/PokerTable/
```

### Type Imports
```typescript
// After migration
import type { Player, GameState } from '../../types';
import type { ChatMessage } from '../../types/chat';
```

## Testing Checklist

After EACH component migration:
- [ ] App starts without errors
- [ ] Component renders correctly
- [ ] CSS styles are applied properly
- [ ] Interactive features work (buttons, inputs)
- [ ] No console errors
- [ ] No TypeScript errors
- [ ] Component appears in correct location
- [ ] All features using this component work

## Docker Considerations

After migrations:
1. Restart container: `docker restart poker-frontend`
2. Check logs: `docker logs poker-frontend --tail 50`
3. Clear cache if needed: `docker compose down && docker compose up -d`

## Rollback Plan

If something breaks:
```bash
# Option 1: Stash changes
git stash

# Option 2: Reset to last commit
git reset --hard HEAD

# Option 3: Checkout specific files
git checkout -- src/components/ComponentName.tsx
```

## Success Metrics

Migration is complete when:
- [ ] All components are in organized folders
- [ ] All CSS is preserved and working
- [ ] TypeScript types are extracted to /types/
- [ ] Custom hooks are in /hooks/
- [ ] GameContext provides centralized state
- [ ] API calls use utils/api.ts
- [ ] No functionality is lost
- [ ] No console errors
- [ ] All tests pass
- [ ] Docker build succeeds

## Common Pitfalls to Avoid

1. **Moving too fast** - Test after each component
2. **Forgetting CSS** - Always copy CSS with component
3. **Wrong import paths** - Count the ../ carefully
4. **Missing exports** - Always create index.ts
5. **Docker sync issues** - Restart container after moves
6. **Type mismatches** - Update interfaces when extracting

## Documentation Updates

After migration:
1. Update README with new structure
2. Document any path aliases added
3. Update component usage examples
4. Add troubleshooting section for common issues