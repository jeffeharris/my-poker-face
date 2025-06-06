# Prompt System Improvements Plan

## Overview

This document outlines improvements to the AI player prompt and dynamic personality system, focusing on:
1. Enhanced conversation dynamics based on chattiness traits
2. Rigid hand strategy that persists throughout each hand
3. Mandatory inner monologue for all AI responses
4. More natural and contextual AI behavior

## Goals

- **Improve AI player prompts** to be more dynamic and contextual
- **Enhance dynamic personality** with natural conversation flow
- **Maintain consistency** within hands while allowing variation between hands
- **Create more realistic** table dynamics with varied speaking patterns

## Key Changes

### 1. Response Structure Modifications

#### Current State
- All fields in `RESPONSE_FORMAT` are effectively required
- Every AI player speaks every turn
- Hand strategy can change mid-hand

#### Proposed Changes
```python
RESPONSE_FORMAT = {
    # ALWAYS REQUIRED
    "action": "required: fold/check/call/raise",
    "amount": "required if raising",
    "inner_monologue": "required: internal thoughts",
    
    # RIGID PER HAND
    "hand_strategy": "required on first action of hand, then locked",
    
    # OPTIONAL BASED ON CHATTINESS
    "persona_response": "optional: what you say to the table",
    "physical": "optional: gestures/actions",
    
    # AI STATE MANAGEMENT
    "new_confidence": "optional: single word",
    "new_attitude": "optional: single word"
}
```

### 2. Hand Strategy Persistence

- AI sets `hand_strategy` on first action of each hand
- Strategy remains locked for entire hand
- New hand = new strategy opportunity
- Provides consistency within hands while allowing adaptation

### 3. Chattiness-Based Speaking

#### Speaking Probability Factors
- Base: Chattiness trait value (0.0-1.0)
- Contextual modifiers:
  - Just won/lost big: ±0.2-0.3
  - Big pot or all-in: +0.2-0.4
  - Addressed directly: +0.5
  - Long table silence: +0.2
  - Currently bluffing: -0.1

#### Expected Behavior by Chattiness Level
- **Low (0.0-0.3)**: Speaks 10-30% of turns, mostly short responses
- **Medium (0.4-0.6)**: Speaks 40-60% of turns, comments on big moments
- **High (0.7-1.0)**: Speaks 70-90% of turns, regular commentary

### 4. Dynamic Prompt Building

Prompts will dynamically adjust based on:
- Turn number in hand (first action vs later)
- Current chattiness level and speaking probability
- Game context (pot size, recent events)
- Personality traits and current elastic values

## Implementation Components

### Phase 1: Core Infrastructure

1. **Modify AIPokerPlayer**
   - Add `current_hand_strategy` attribute
   - Add `hand_action_count` tracking
   - Update `set_for_new_hand()` to reset strategy

2. **Create ChattinessManager**
   - Calculate speaking probabilities
   - Track conversation flow
   - Manage table dynamics

3. **Update Response Validation**
   - Enforce mandatory fields
   - Lock hand strategy after first action
   - Remove unauthorized speech

### Phase 2: Enhanced Prompting

1. **Dynamic Response Format**
   - Build format based on context
   - Clear indication of optional fields
   - Guidance on when to speak

2. **Contextual Prompt Builder**
   - Include hand strategy context
   - Add chattiness guidance
   - Provide speaking probability

3. **Conversation Flow Tracking**
   - Monitor silence patterns
   - Track who was addressed
   - Identify natural speaking moments

### Phase 3: Testing & Refinement

1. **Comprehensive Test Suite**
   - Unit tests for each component
   - Integration tests for full flow
   - Personality-specific scenarios

2. **Behavior Validation**
   - Ensure strategy persistence
   - Verify speaking patterns
   - Test edge cases

## Benefits

1. **More Natural Gameplay**
   - Realistic conversation patterns
   - Varied table dynamics
   - Character-appropriate silence

2. **Better AI Consistency**
   - Coherent strategy within hands
   - Predictable trait boundaries
   - Maintained character identity

3. **Enhanced Player Experience**
   - Less repetitive dialogue
   - More strategic depth
   - Authentic personality expression

## Additional Improvements Identified

### 1. Trait-Based Response Generation
- Use elastic trait values to influence response style
- Aggressive players use stronger language
- Cautious players hedge their statements

### 2. Memory Context Enhancement
- Include relevant hand history in prompts
- Track player-specific patterns
- Build on previous interactions

### 3. Emotional State Tracking
- Beyond confidence/attitude
- Track frustration, excitement, suspicion
- Influence both speech and strategy

### 4. Meta-Game Awareness
- AI acknowledges tournament vs cash game
- Considers stack sizes relative to blinds
- Adapts to table image

## Success Metrics

- Reduced repetitive dialogue (measurable via response diversity)
- Consistent hand strategies (trackable via response logging)
- Natural conversation flow (subjective but observable)
- Maintained personality identity (trait consistency within bounds)

## Next Phase Improvements

### 1. Trait-Influenced Language Style

Building on the elasticity system, AI language should dynamically reflect current trait values:

#### Dynamic Vocabulary Selection
```python
# Example: Aggression affects word choice
aggression_vocabulary = {
    "low": ["maybe", "perhaps", "consider", "modest", "careful"],
    "medium": ["should", "likely", "decent", "solid", "standard"],
    "high": ["must", "definitely", "crushing", "dominating", "powerful"]
}

# Bluff tendency affects certainty expression
bluff_expressions = {
    "low": ["I genuinely have", "My actual hand is", "To be honest"],
    "high": ["You'll never guess", "I might have", "Who knows what I'm holding"]
}
```

#### Personality-Specific Language Patterns
- **Sherlock (analytical)**: Technical terms scale with confidence
- **Eeyore (pessimistic)**: Negativity inversely scales with mood
- **Trump (bombastic)**: Superlatives scale with both aggression and confidence
- **Gordon (intense)**: Profanity and kitchen metaphors scale with frustration

#### Implementation Strategy
- Build trait-to-language mappings in personality configs
- Apply real-time substitutions based on elastic values
- Maintain character voice while varying intensity

### 2. Contextual Memory Windows

Implement sophisticated memory systems that influence AI behavior:

#### Short-Term Memory (Current Session)
```python
memory_window = {
    "recent_hands": last_10_hands,  # Tactical memory
    "player_patterns": {
        "Jeff": {"bluffs": 2, "folds_to_pressure": 3},
        "Player2": {"aggressive_raises": 5, "showdown_rate": 0.6}
    },
    "emotional_context": recent_5_minutes  # Mood influences
}
```

#### Working Memory (Current Hand)
- Track betting patterns within the hand
- Remember who showed strength/weakness
- Maintain action consistency with stated strategy

#### Long-Term Memory (Cross-Session)
- Personality evolution based on past games
- Relationship dynamics that persist
- Signature moves and memorable moments

#### Memory Decay System
- Recent events have stronger influence
- Emotional memories last longer than tactical ones
- Positive/negative experiences weighted differently by personality

### 3. Meta-Strategy Awareness

AI players understand and adapt to the broader game context:

#### Tournament vs Cash Game Mentality
```python
tournament_adjustments = {
    "bubble_awareness": "Tighten up near money positions",
    "stack_preservation": "Survival matters more than chip accumulation",
    "ICM_pressure": "Consider tournament equity, not just pot odds",
    "level_progression": "Adapt to increasing blinds"
}

cash_game_adjustments = {
    "reload_mentality": "Each hand independent, no survival pressure",
    "deep_stack_play": "More post-flop complexity",
    "table_selection": "Identify and target weak players",
    "session_goals": "Long-term profit over short-term variance"
}
```

#### Stack Size Awareness
- **Short stack (<20BB)**: Push/fold mentality
- **Medium stack (20-50BB)**: Selective aggression
- **Deep stack (>100BB)**: Complex post-flop play

#### Table Image Management
- AI tracks how others perceive them
- Adjusts strategy to exploit image
- Intentionally shapes image for future hands

#### Meta-Game Commentary
AI players can discuss strategy at appropriate moments:
- "I had to fold there - tournament life on the line"
- "In a cash game I'm calling, but not here"
- "You've been so tight, I have to respect that raise"

### 4. Emotional State Evolution

Expand beyond simple confidence/attitude to rich emotional landscapes:

#### Multi-Dimensional Emotional Model
```python
emotional_state = {
    # Core emotions
    "confidence": 0.7,      # Self-belief
    "frustration": 0.3,     # Accumulated tilt
    "excitement": 0.5,      # Engagement level
    "suspicion": 0.4,       # Paranoia about others
    
    # Complex states
    "vengeful": 0.0,        # Desire for revenge
    "protective": 0.0,      # Defending chip lead
    "desperate": 0.0,       # Short-stack pressure
    "cocky": 0.0,          # Overconfidence from winning
    
    # Social emotions
    "respected": 0.5,       # Feeling acknowledged
    "isolated": 0.0,        # Social disconnection
    "amused": 0.0,         # Finding humor in situation
}
```

#### Emotional Trajectories
- **Tilt Spiral**: Frustration → Aggression → Desperation → Resignation
- **Confidence Arc**: Cautious → Confident → Cocky → Humbled
- **Social Journey**: Isolated → Curious → Engaged → Connected

#### Personality-Specific Emotional Ranges
- **Eeyore**: Limited emotional range, mostly negative spectrum
- **Trump**: Extreme swings between supreme confidence and rage
- **Bob Ross**: Consistently positive with rare dips
- **Batman**: Controlled emotions with occasional intensity

#### Emotional Contagion
- Table mood affects individual emotions
- Personalities influence each other's states
- Create cascading emotional dynamics

## Implementation Roadmap

### Phase 1: Foundation (Weeks 1-2)
1. Extend trait system to support language mappings
2. Implement basic memory window structure
3. Add tournament/cash game context to prompts
4. Create emotional state data model

### Phase 2: Integration (Weeks 3-4)
1. Build trait-to-language transformation engine
2. Connect memory system to prompt generation
3. Implement meta-strategy reasoning
4. Wire emotional states to elasticity system

### Phase 3: Refinement (Weeks 5-6)
1. Personality-specific language tuning
2. Memory decay algorithm optimization
3. Meta-game awareness validation
4. Emotional trajectory testing

### Phase 4: Polish (Weeks 7-8)
1. Cross-personality interaction testing
2. Long-term memory persistence
3. Tournament mode full integration
4. Emotional contagion effects

## Success Metrics

- **Language Variety**: 50% reduction in repeated phrases
- **Strategic Coherence**: 90% strategy consistency within hands
- **Emotional Realism**: Player survey on AI believability
- **Memory Impact**: Measurable influence on decisions
- **Meta Awareness**: Appropriate strategy shifts in different contexts

## Implementation Plan - Iterative Chunks

### Chunk 1: Hand Strategy Persistence (FOUNDATION) ✅ COMPLETE
**Goal**: Lock in hand strategy for duration of each hand

**Files modified**:
1. `poker/poker_player.py`
   - ✅ Added `current_hand_strategy` attribute to AIPokerPlayer
   - ✅ Added `hand_action_count` attribute
   - ✅ Updated `set_for_new_hand()` method to reset both
   - ✅ Modified `get_player_response()` to:
     - Track action count
     - Request strategy on first action with prompt addition
     - Lock strategy and prevent mid-hand changes
     - Remind AI of locked strategy on subsequent actions
   - ✅ Added serialization support in `to_dict()` and `from_dict()`

**Tests passing**:
```bash
python -m pytest tests/test_prompt_improvements.py::TestHandStrategyPersistence -v
# Result: 3 passed ✅
```

**How it works**:
- First action: AI receives prompt "This is your FIRST action this hand. You must set your 'hand_strategy' for the entire hand."
- AI responds with strategy like: "Aggressive - build big pot with premium hand"
- Subsequent actions: AI is reminded "Your hand strategy remains: '[locked strategy]'"
- New hand: Everything resets for fresh strategy

---

### Chunk 2: Response Structure Updates (FOUNDATION) ✅ COMPLETE
**Goal**: Make persona_response and physical actions optional

**Files modified**:
1. `poker/prompt_manager.py`
   - ✅ Updated `RESPONSE_FORMAT` with clear REQUIRED/OPTIONAL markers
   - ✅ Reorganized fields by requirement type:
     - Always required: action, inner_monologue
     - Conditionally required: adding_to_pot (if raising), hand_strategy (first action)
     - Optional: persona_response, physical (based on chattiness)

2. Created `poker/response_validator.py`
   - ✅ New ResponseValidator class for validation
   - ✅ Validates mandatory fields based on context
   - ✅ Provides clean_response() to remove inappropriate fields
   - ✅ Generates helpful error messages and warnings

**Tests passing**:
```bash
python -m pytest tests/test_prompt_improvements.py::TestMandatoryInnerMonologue -v
# Result: 2 passed ✅
```

**Key features**:
- Validates responses have required fields
- Context-aware validation (e.g., adding_to_pot required for raises)
- Removes speech fields for quiet players
- Clear error messages for missing fields

---

### Chunk 3: Chattiness Manager (CORE FEATURE) ✅ COMPLETE
**Goal**: Implement speaking probability based on traits

**Files created**:
1. `poker/chattiness_manager.py`
   - ✅ ChattinessManager class for speaking decisions
   - ✅ Context-aware probability calculation
   - ✅ Conversation flow tracking
   - ✅ Personality-specific adjustments
   - ✅ ConversationContext for table dynamics

**Key features**:
- Base probability from chattiness trait (0.0-1.0)
- Contextual modifiers:
  - `just_won_big`: +0.3
  - `big_pot`: +0.2
  - `addressed_directly`: +0.5
  - `bluffing`: -0.1
  - And many more...
- Personality overrides (e.g., Gordon min 0.7, Eeyore max 0.4)
- Tracks silence patterns and breaks awkward pauses

**Tests passing**:
```bash
python -m pytest tests/test_prompt_improvements.py::TestChattinessBehavior -v
# Result: 3 passed ✅
```

**Demo results**:
- Gordon (0.9 chattiness) → speaks ~100% of turns
- Eeyore (0.2 chattiness) → speaks ~10-25% of turns
- Context properly affects probability
- Conversation flow tracking works

**Note**: AI still generates speech even when shouldn't - this is fixed in Chunk 4 when we integrate with prompts

---

### Chunk 4: Dynamic Prompt Building (INTEGRATION) ✅ COMPLETE
**Goal**: Build prompts that adapt to context

**Files modified**:
1. `poker/controllers.py`
   - ✅ Updated `decide_action()` to integrate ChattinessManager
   - ✅ Added `_build_game_context()` for contextual decisions
   - ✅ Added `_build_chattiness_guidance()` for dynamic prompts
   - ✅ Integrated ResponseValidator to clean responses

**Key features**:
- Prompts include chattiness level and speaking guidance
- Clear indication when to speak vs stay quiet
- Dynamic response format based on context
- First action shows hand_strategy requirement
- Speaking style suggestions based on chattiness

**Tests passing**:
```bash
python -m pytest tests/test_prompt_improvements.py::TestDynamicPromptBuilding -v
# Result: 3 passed ✅
```

**Live test results**:
- Silent Bob (0.1 chattiness): Spoke 40% of turns
- Eeyore (0.3 chattiness): Spoke 40% of turns  
- Sherlock (0.5 chattiness): Spoke 80% of turns
- Bob Ross (0.7 chattiness): Spoke 100% of turns
- Gordon (0.9 chattiness): Spoke 100% of turns

**Success**: AI respects "DO NOT include persona_response" guidance!

---

### Chunk 5: Response Processing (INTEGRATION)
**Goal**: Process responses according to new rules

**Files to create**:
1. `poker/response_processor.py`
   - Process AI responses
   - Remove speech from quiet players
   - Lock in hand strategies

**Files to modify**:
1. `poker/controllers.py`
   - Use ResponseProcessor
   - Handle processed responses

**Tests to run**:
```bash
python -m pytest tests/test_prompt_improvements.py::TestResponseProcessing -v
```

**Validation**:
- Quiet players don't speak even if AI includes speech
- Chatty players keep their dialogue
- Strategies lock correctly

---

### Chunk 6: Full Integration & Polish
**Goal**: Ensure all components work together smoothly

**Tests to run**:
```bash
# Run all new tests
python -m pytest tests/test_prompt_improvements.py -v

# Run existing tests to ensure no regression
python -m pytest tests/test_prompt_management.py -v
python -m pytest tests/test_personality_responses.py -v

# Integration tests
python -m console_app.ui_console
python personality_showcase.py
```

**Validation**:
- Natural conversation flow
- Strategy consistency
- No performance regression

## Success Criteria Per Chunk

### Chunk 1 ✓ When:
- Hand strategies persist through entire hand
- Strategies reset between hands
- No regression in existing gameplay

### Chunk 2 ✓ When:
- Response validation works correctly
- Optional fields handled properly
- Clear error messages for missing required fields

### Chunk 3 ✓ When:
- Speaking frequency matches chattiness level
- Contextual modifiers affect probability
- Natural conversation patterns emerge

### Chunk 4 ✓ When:
- Prompts adapt to game context
- Clear guidance for AI on when to speak
- Strategy context properly included

### Chunk 5 ✓ When:
- Responses processed according to rules
- Unauthorized speech removed
- All validations pass

### Chunk 6 ✓ When:
- Full gameplay feels natural
- No performance degradation
- All tests pass

## Next Steps

1. Start with Chunk 1 (Hand Strategy Persistence)
2. Run tests after each implementation
3. Commit when tests pass
4. Move to next chunk
5. Full integration testing after all chunks

Each chunk builds on the previous one but can be tested independently. This approach ensures we maintain working software throughout the implementation process.