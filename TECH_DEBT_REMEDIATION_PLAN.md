# Tech Debt Remediation Plan

## Overview
This plan addresses the critical technical debt identified in the analysis report while incorporating recent developments (React frontend, persistence improvements). The goal is to create a solid foundation for the personality elasticity feature.

## Current State (Post-Merge)
1. **New React Frontend**: Full TypeScript React app with WebSocket support
2. **Persistence Improvements**: Documentation and partial fixes for card serialization
3. **Rich CLI**: Alternative console interface using Rich library
4. **Personality Testing Tools**: Web utilities for testing and managing personalities

## Priority Order (Based on Tech Debt Report)

### Week 1: Critical Foundation Fixes

#### 1. AI Error Handling (2-3 days)
**Why Critical**: Game crashes on any OpenAI API failure. With elasticity increasing API calls, this becomes more critical.

**Tasks**:
- [x] Create `poker/ai_resilience.py` module ✅
- [x] Implement retry decorator with exponential backoff ✅
- [x] Add fallback AI behaviors (random valid action, conservative play) ✅
- [x] Wrap all OpenAI calls in try/except blocks ✅
- [x] Add circuit breaker pattern for repeated failures ✅
- [x] Create monitoring/logging for AI failures ✅

**COMPLETED**: 2025-06-03

**Implementation**:
```python
# poker/ai_resilience.py
import functools
import random
import time
from typing import Any, Callable, Optional
import logging

logger = logging.getLogger(__name__)

class AIError(Exception):
    """Base exception for AI-related errors"""
    pass

def with_ai_fallback(fallback_fn: Optional[Callable] = None, max_retries: int = 3):
    """Decorator for AI operations with automatic retry and fallback"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.warning(f"AI operation failed (attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        time.sleep(wait_time)
            
            # All retries failed, use fallback
            if fallback_fn:
                logger.error(f"Using fallback after {max_retries} failures")
                return fallback_fn(*args, **kwargs)
            else:
                raise AIError(f"AI operation failed after {max_retries} attempts") from last_error
        return wrapper
    return decorator
```

#### 2. State Persistence Layer (3-4 days)
**Why Critical**: No persistence = no elasticity between sessions. Recent persistence work needs completion.

**Tasks**:
- [x] Complete AI memory persistence (from PERSISTENCE_IMPROVEMENTS.md) ✅
- [x] Fix card serialization inconsistencies ✅
- [ ] Implement repository pattern for clean separation
- [x] Add personality state persistence ✅
- [ ] Create migration system for schema updates
- [x] Add comprehensive tests for save/load cycles ✅

**COMPLETED**: 2025-06-03 (Core functionality done, repository pattern and migrations can be added later)

**New Tables** (from persistence improvements doc):
```sql
-- AI conversation memory
CREATE TABLE IF NOT EXISTS ai_player_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    conversation_history TEXT,  -- JSON array
    personality_state TEXT,     -- JSON with current modifiers
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(id)
);

-- For elasticity tracking
CREATE TABLE IF NOT EXISTS personality_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT NOT NULL,
    game_id TEXT NOT NULL,
    hand_number INTEGER,
    personality_traits TEXT,  -- JSON with all trait values
    pressure_levels TEXT,     -- JSON with pressure per trait
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Week 2: Architectural Fixes

#### 3. Refactor State Machine to Pure Functional (3 days)
**Why Important**: Current mutations violate functional principles, making elasticity harder to implement correctly.

**Tasks**:
- [ ] Remove all mutations from PokerStateMachine
- [ ] Make state machine return new instances
- [ ] Fix property methods that mutate lists
- [ ] Create immutable deck operations
- [ ] Add state transition validation

**Example Refactor**:
```python
# Before (mutating)
def advance_state(self):
    self.game_state = new_state  # BAD
    
# After (functional)
def advance_state(self) -> 'PokerStateMachine':
    """Returns new state machine instance with advanced state"""
    return PokerStateMachine(
        game_state=new_state,
        phase=new_phase
    )
```

#### 4. Comprehensive Test Suite (3-4 days)
**Why Important**: No tests = regressions with every change. Critical before elasticity.

**Tasks**:
- [ ] State machine transition tests
- [ ] Mock OpenAI framework
- [ ] WebSocket/async tests for React integration
- [ ] Error scenario coverage
- [ ] Persistence round-trip tests
- [ ] AI behavior regression tests

### Week 3: Code Quality & Performance

#### 5. Reduce Complexity (2 days)
**Tasks**:
- [ ] Break up `determine_winner()` (70 lines)
- [ ] Refactor `progress_game()` (61 lines)
- [ ] Extract pot calculation logic
- [ ] Create helper modules for common operations

#### 6. Configuration System (1-2 days)
**Tasks**:
- [ ] Create `config/` directory structure
- [ ] Externalize game parameters
- [ ] Add environment-based configuration
- [ ] Remove hardcoded values

#### 7. Performance Optimization for Elasticity (2 days)
**Tasks**:
- [ ] Profile state update performance
- [ ] Implement personality state caching
- [ ] Consider separate mutable container for frequently-changing traits
- [ ] Add metrics collection

## Integration with Recent Changes

### React Frontend Considerations
- Ensure WebSocket events include personality state changes
- Add API endpoints for personality evolution visualization
- Consider real-time personality trait indicators in UI

### Rich CLI Enhancements
- Add personality state display to Rich interface
- Show trait changes in real-time during play
- Add debug mode to see AI decision process

## Success Metrics
1. **Zero crashes** from OpenAI failures in 1000 hands
2. **100% persistence** of AI state across sessions
3. **<100ms** for personality state updates
4. **90%+ test coverage** for critical paths
5. **No regressions** in existing functionality

## Future Architecture Considerations

### Current Setup: React + Flask + SocketIO
The current architecture works but has some limitations:
- Two separate apps (React frontend, Flask backend)
- Manual state synchronization
- No type safety between frontend/backend
- Older patterns for real-time communication

### Modernization Options (Lower Priority)

1. **Incremental Modernization** (Least disruption)
   - Keep Flask but make it a pure REST/WebSocket API
   - Add proper state management to React (Zustand/Redux)
   - Add TypeScript interfaces matching Python models
   - Better separation of concerns

2. **Next.js + FastAPI** (Recommended for knowledge building)
   - Full-stack TypeScript with Next.js
   - FastAPI for Python backend (modern, async, auto-docs)
   - Keep Python for AI integration
   - Better developer experience
   - Natural upgrade path from current stack

3. **Phoenix LiveView** (Best for real-time at scale)
   - Excellent for real-time games
   - Would require Elixir rewrite
   - Harder AI integration

4. **SvelteKit** (If starting fresh)
   - Simpler than React
   - Smaller ecosystem

**Recommendation**: When ready, consider Next.js + FastAPI to stay current with both Node.js and modern Python patterns while preserving AI capabilities.

## Next Steps After Remediation
Once tech debt is addressed:
1. Implement personality elasticity system
2. Add tournament mode with narrative engine
3. Build mood and rivalry systems
4. Create XP/progression based on dramatic moments

## Risk Mitigation
- Create feature flags for gradual rollout
- Maintain backward compatibility for saved games
- Add comprehensive logging for debugging
- Set up monitoring for production issues

---
*Plan created: 6/3/2025*
*Estimated completion: 3-4 weeks*
*Prerequisites: Fix critical issues before starting elasticity*