# Persistence System Improvements

## Current State Analysis

### What's Working
1. **Game State Persistence**: Basic game mechanics (cards, pot, stacks, phases) are properly saved
2. **Automatic Saving**: Games save after each action
3. **Message History**: Basic chat/game messages are stored

### Critical Issues

#### 1. AI Player State Loss
The most significant issue is that AI players lose all their context when games are loaded:
- `OpenAILLMAssistant.memory` (conversation history) is not persisted
- AI personalities reset to defaults on game load
- No tracking of AI decision patterns or learning

#### 2. Card Serialization Inconsistency
- Cards have proper `to_dict()`/`from_dict()` methods in `core/card.py`
- However, defensive checks in persistence.py suggest cards sometimes remain as dicts
- This causes the "Unexpected card format" warnings

#### 3. Incomplete State Reconstruction
When loading games, AI controllers are recreated from scratch:
```python
# Current approach loses all AI context
ai_controllers[player.name] = AIPlayerController(player.name, state_machine)
```

## Proposed Improvements

### Phase 1: Immediate Fixes (1 week)

#### 1.1 Fix Card Serialization
```python
# In persistence.py, ensure consistent Card object usage
def _serialize_card(card):
    """Ensure card is properly serialized."""
    if hasattr(card, 'to_dict'):
        return card.to_dict()
    elif isinstance(card, dict):
        return card
    else:
        raise ValueError(f"Unknown card format: {card}")

def _deserialize_card(card_data):
    """Ensure card is properly deserialized to Card object."""
    if isinstance(card_data, dict):
        return Card.from_dict(card_data)
    elif hasattr(card_data, 'rank'):  # Already a Card object
        return card_data
    else:
        raise ValueError(f"Cannot deserialize card: {card_data}")
```

#### 1.2 Add AI Memory Persistence
Extend the current schema to include AI conversation memory:

```sql
-- Add to existing schema
CREATE TABLE IF NOT EXISTS ai_player_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    conversation_history TEXT,  -- JSON array of messages
    personality_state TEXT,     -- JSON of current personality modifiers
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(id)
);

CREATE INDEX idx_ai_player_game ON ai_player_state(game_id, player_name);
```

### Phase 2: Enhanced AI Persistence (2-3 weeks)

#### 2.1 Comprehensive AI State Schema
```sql
-- AI decision history for learning
CREATE TABLE IF NOT EXISTS ai_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    hand_number INTEGER,
    phase TEXT,
    game_context TEXT,      -- JSON: pot size, position, stack sizes
    decision TEXT,          -- fold/check/call/raise
    amount INTEGER,
    reasoning TEXT,         -- AI's explanation
    outcome TEXT,           -- won/lost/folded
    profit_loss INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(id)
);

-- Personality evolution tracking
CREATE TABLE IF NOT EXISTS personality_evolution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT NOT NULL,
    trait TEXT NOT NULL,              -- bluff_tendency, aggression, etc.
    original_value REAL,
    current_value REAL,
    change_reason TEXT,
    game_id TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Enhanced message storage
CREATE TABLE IF NOT EXISTS game_messages_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    message_id TEXT UNIQUE,
    sender TEXT NOT NULL,
    content TEXT NOT NULL,
    message_type TEXT,
    game_phase TEXT,          -- PRE_FLOP, FLOP, etc.
    hand_number INTEGER,
    metadata TEXT,            -- JSON for additional context
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(id)
);
```

#### 2.2 AI State Manager Class
```python
# poker/ai_persistence.py
from typing import Dict, List, Optional, Any
import json
from dataclasses import dataclass, asdict
from datetime import datetime

@dataclass
class AIMemoryState:
    conversation_history: List[Dict[str, str]]
    personality_modifiers: Dict[str, float]
    decision_count: int
    last_bluff_hand: Optional[int]
    winning_streak: int
    total_winnings: int

class AIStateManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()
    
    def save_ai_state(self, game_id: str, player_name: str, 
                      assistant: 'OpenAILLMAssistant', 
                      controller: 'AIPlayerController'):
        """Save complete AI state including memory and personality."""
        memory_state = AIMemoryState(
            conversation_history=assistant.memory,
            personality_modifiers=controller.personality_modifiers,
            decision_count=controller.decision_count,
            last_bluff_hand=controller.last_bluff_hand,
            winning_streak=controller.winning_streak,
            total_winnings=controller.total_winnings
        )
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO ai_player_state 
                (game_id, player_name, conversation_history, personality_state)
                VALUES (?, ?, ?, ?)
            """, (
                game_id,
                player_name,
                json.dumps(memory_state.conversation_history),
                json.dumps(asdict(memory_state))
            ))
    
    def load_ai_state(self, game_id: str, player_name: str) -> Optional[AIMemoryState]:
        """Load AI state from database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT conversation_history, personality_state
                FROM ai_player_state
                WHERE game_id = ? AND player_name = ?
                ORDER BY last_updated DESC
                LIMIT 1
            """, (game_id, player_name))
            
            row = cursor.fetchone()
            if row:
                history = json.loads(row[0])
                state_dict = json.loads(row[1])
                return AIMemoryState(**state_dict)
        return None
    
    def record_decision(self, game_id: str, player_name: str,
                       hand_number: int, phase: str,
                       context: Dict, decision: str, 
                       amount: int, reasoning: str):
        """Record AI decision for learning."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO ai_decisions
                (game_id, player_name, hand_number, phase, 
                 game_context, decision, amount, reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_id, player_name, hand_number, phase,
                json.dumps(context), decision, amount, reasoning
            ))
```

#### 2.3 Modified AIPlayerController
```python
# In controllers.py
class AIPlayerController(PlayerController):
    def __init__(self, player_name: str, game_adapter: GameAdapter, 
                 ai_state_manager: Optional[AIStateManager] = None,
                 game_id: Optional[str] = None):
        super().__init__(player_name, game_adapter)
        self.ai_state_manager = ai_state_manager
        self.game_id = game_id
        
        # Load or initialize state
        if ai_state_manager and game_id:
            saved_state = ai_state_manager.load_ai_state(game_id, player_name)
            if saved_state:
                self._restore_from_saved_state(saved_state)
            else:
                self._initialize_fresh_state()
        else:
            self._initialize_fresh_state()
    
    def _restore_from_saved_state(self, state: AIMemoryState):
        """Restore AI from saved state."""
        self.llm_assistant.memory = state.conversation_history
        self.personality_modifiers = state.personality_modifiers
        self.decision_count = state.decision_count
        self.last_bluff_hand = state.last_bluff_hand
        self.winning_streak = state.winning_streak
        self.total_winnings = state.total_winnings
    
    def decide_action(self, game_messages: List[Dict]) -> Dict:
        """Enhanced decision with persistence."""
        # Current decision logic...
        result = super().decide_action(game_messages)
        
        # Record the decision
        if self.ai_state_manager and self.game_id:
            context = {
                'pot_size': self.game_adapter.game_state.pot.get('total', 0),
                'stack': self.game_adapter.game_state.current_player.stack,
                'position': self._get_position(),
                'hand_strength': self._estimate_hand_strength()
            }
            self.ai_state_manager.record_decision(
                self.game_id, self.player_name,
                self.game_adapter.hand_number,
                str(self.game_adapter.current_phase),
                context, result['action'], 
                result.get('amount', 0),
                result.get('reasoning', '')
            )
            
            # Save current state
            self.ai_state_manager.save_ai_state(
                self.game_id, self.player_name,
                self.llm_assistant, self
            )
        
        return result
```

### Phase 3: Advanced Features (4-6 weeks)

#### 3.1 Learning System
```python
class AILearningEngine:
    """Analyze past decisions to improve AI play."""
    
    def analyze_player_history(self, player_name: str) -> Dict[str, Any]:
        """Analyze all decisions by a player across games."""
        # Calculate success rates for different actions
        # Identify patterns in winning/losing decisions
        # Suggest personality adjustments
        pass
    
    def generate_personality_adjustments(self, player_name: str, 
                                       recent_games: int = 10) -> Dict[str, float]:
        """Generate personality adjustments based on performance."""
        # If losing too much, reduce bluff_tendency
        # If winning with aggressive play, increase aggression
        # Adapt to table dynamics
        pass
```

#### 3.2 Personality Evolution
```python
class PersonalityEvolution:
    """Track and evolve AI personalities over time."""
    
    def evolve_personality(self, player_name: str, 
                          game_outcome: str, 
                          decisions: List[Dict]) -> Dict[str, float]:
        """Evolve personality based on game outcomes."""
        # Winning reinforces current traits
        # Losing causes trait adjustments
        # Major losses might cause "tilt"
        pass
```

### Migration Strategy

1. **Database Migration Script**:
```python
def migrate_to_v2(db_path: str):
    """Migrate existing database to support new features."""
    conn = sqlite3.connect(db_path)
    
    # Add new tables
    conn.executescript("""
        -- New AI tables...
    """)
    
    # Migrate existing data
    # Set default AI states for existing games
    
    conn.close()
```

2. **Backward Compatibility**:
- Keep existing persistence working
- Add feature flags for new persistence
- Gradual rollout of AI memory features

### Implementation Priority

1. **Week 1**: Fix Card serialization, add basic AI memory
2. **Week 2-3**: Implement AIStateManager and decision recording
3. **Week 4**: Add learning engine basics
4. **Week 5-6**: Personality evolution and analytics

This approach will transform your AI players from stateless bots into sophisticated opponents with memory, learning capabilities, and evolving personalities.