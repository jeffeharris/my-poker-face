# AI Personality Generation Plan

## Overview
Add AI-powered personality generation to the Personality Manager, allowing users to create complete poker personalities by entering just a character name. The system will be built in phases, starting with simple generation and adding refinement capabilities.

## Development Phases

### Phase 1: Single-Shot Generation (MVP)
- User enters name → AI generates complete personality
- One API call, simple implementation
- Manual editing only after generation

### Phase 2: Targeted Refinement
- Add refinement capabilities after initial generation
- Specific buttons for common adjustments
- Custom refinement prompt option
- Preserves conversation context

### Phase 3: Full Chat Interface (Future)
- Complete conversational interface
- Iterative refinement through dialogue
- AI explains its choices
- Full collaborative experience

## User Flow

### Phase 1 Flow (Initial Implementation)
1. User clicks "Create New Personality" in manager
2. Enters a name (e.g., "Sherlock Holmes", "Marie Curie", "Darth Vader")
3. Clicks "Generate with AI" button
4. System generates complete personality profile
5. User reviews and can adjust any values manually
6. User saves the personality

### Phase 2 Flow (With Refinements)
1. Same initial generation as Phase 1
2. After generation, refinement options appear:
   - Quick refinement buttons (Make More Aggressive, Add Humor, etc.)
   - Custom refinement text input
3. User can refine multiple times
4. Each refinement updates the personality
5. Manual editing still available
6. User saves when satisfied

## Technical Implementation

### Architecture Choice: Dedicated PersonalityGenerator Class

Create `poker/personality_generator.py`:

```python
class PersonalityGenerator:
    def __init__(self):
        self.assistant = OpenAILLMAssistant(
            ai_temp=0.8,  # Some creativity
            system_message=self._get_system_prompt()
        )
        self.current_name = None
        self.current_personality = None
        # For Phase 2: Track generation history
        self.generation_history = []
    
    # Phase 1: Basic Generation
    def generate_from_name(self, name: str) -> dict:
        """Generate complete personality from just a name"""
        self.current_name = name
        prompt = self._build_generation_prompt(name)
        response = self.assistant.chat(prompt, json_format=True)
        
        personality = self._validate_and_clean(json.loads(response))
        self.current_personality = personality
        self.generation_history.append({
            'action': 'generate',
            'name': name,
            'result': personality
        })
        
        return personality
    
    # Phase 2: Refinement Methods (to be implemented later)
    def refine_personality(self, instruction: str) -> dict:
        """Refine the current personality based on instruction"""
        if not self.current_personality:
            raise ValueError("No personality to refine")
        
        prompt = f"""
        Current personality for {self.current_name}:
        {json.dumps(self.current_personality, indent=2)}
        
        Refinement instruction: {instruction}
        
        Update the personality based on this instruction and return 
        the complete updated personality in the same JSON format.
        """
        
        response = self.assistant.chat(prompt, json_format=True)
        personality = self._validate_and_clean(json.loads(response))
        
        self.current_personality = personality
        self.generation_history.append({
            'action': 'refine',
            'instruction': instruction,
            'result': personality
        })
        
        return personality
    
    def apply_preset_refinement(self, preset: str) -> dict:
        """Apply a preset refinement (Phase 2)"""
        refinements = {
            'more_aggressive': "Make this character more aggressive in their poker play. Increase their aggression and bluff tendency.",
            'more_passive': "Make this character more passive and cautious. Decrease aggression and bluff tendency.",
            'add_humor': "Add more humor to their verbal tics and make them more entertaining.",
            'more_realistic': "Make the personality more realistic and less exaggerated.",
            'more_talkative': "Increase their chattiness and add more verbal tics.",
            'less_talkative': "Make them quieter with fewer verbal expressions."
        }
        
        if preset not in refinements:
            raise ValueError(f"Unknown preset: {preset}")
            
        return self.refine_personality(refinements[preset])
    
    def _get_system_prompt(self) -> str:
        """System prompt for personality generation"""
        return """You are an expert at creating poker player personalities based on 
        character names. You understand how personality traits translate to poker 
        playing styles and behaviors. You create believable, interesting personalities
        that are fun to play against in poker."""
    
    def _validate_and_clean(self, data: dict) -> dict:
        """Ensure all required fields exist with valid values"""
        # Validate ranges, required fields, etc.
        required_fields = ['play_style', 'default_confidence', 'default_attitude', 
                          'personality_traits', 'verbal_tics', 'physical_tics']
        
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")
        
        # Ensure traits are in valid range
        traits = data['personality_traits']
        for trait in ['bluff_tendency', 'aggression', 'chattiness', 'emoji_usage']:
            if trait in traits:
                traits[trait] = max(0.0, min(1.0, float(traits[trait])))
        
        return data
```

### API Endpoint in personality_manager.py

```python
@app.route('/api/generate_personality', methods=['POST'])
def generate_personality():
    try:
        name = request.json['name']
        generator = PersonalityGenerator()
        personality_data = generator.generate_from_name(name)
        
        return jsonify({
            'success': True,
            'personality': personality_data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })
```

## Prompt Engineering

### Main Generation Prompt Template

```
Create a poker player personality for "{name}". 

First, consider who this character is - their background, temperament, and known behaviors. Then translate these traits into poker playing behavior.

Generate the following:

1. play_style: 2-4 words describing their poker approach (e.g., "aggressive and calculated", "tight and passive")

2. default_confidence: One word describing their usual confidence level (e.g., "supreme", "anxious", "steady")

3. default_attitude: One word describing their general demeanor (e.g., "friendly", "intimidating", "mysterious")

4. personality_traits: Rate each 0.0 to 1.0
   - bluff_tendency: How often they bluff (0=never, 1=constantly)
   - aggression: How likely to raise vs call (0=very passive, 1=very aggressive)
   - chattiness: How talkative they are (0=silent, 1=never stops talking)
   - emoji_usage: How often they'd use emojis (0=never, 1=constantly)

5. verbal_tics: 3-5 characteristic phrases they would say at the poker table

6. physical_tics: 3-5 physical actions or gestures they would make

Consider:
- How would this character's personality translate to poker?
- What would their tells be?
- How would they try to intimidate or deceive opponents?
- Make them interesting but believable

Respond in this exact JSON format:
{
  "play_style": "...",
  "default_confidence": "...",
  "default_attitude": "...",
  "personality_traits": {
    "bluff_tendency": 0.0,
    "aggression": 0.0,
    "chattiness": 0.0,
    "emoji_usage": 0.0
  },
  "verbal_tics": ["...", "...", "..."],
  "physical_tics": ["...", "...", "..."]
}
```

## UI Updates

### Phase 1: Basic Generation UI

```javascript
function createNewPersonality() {
    const name = prompt('Enter name for new personality:');
    if (!name) return;
    
    // Ask if they want AI generation
    if (confirm('Would you like AI to generate this personality?')) {
        generateWithAI(name);
    } else {
        createBlankPersonality(name);
    }
}

async function generateWithAI(name) {
    showLoading('Generating personality...');
    
    try {
        const response = await fetch('/api/generate_personality', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name })
        });
        
        const data = await response.json();
        if (data.success) {
            personalities[name] = data.personality;
            displayPersonalityList();
            selectPersonality(name);
            showAlert('success', 'Personality generated! Review and save.');
        }
    } catch (error) {
        showAlert('error', 'Generation failed: ' + error.message);
    }
}
```

### Phase 2: Refinement UI Additions

```javascript
// Add refinement section to editor
function displayRefinementOptions(name) {
    return `
        <div class="refinement-section">
            <h3>AI Refinements</h3>
            <div class="preset-refinements">
                <button onclick="refinePersonality('more_aggressive')">More Aggressive</button>
                <button onclick="refinePersonality('more_passive')">More Passive</button>
                <button onclick="refinePersonality('add_humor')">Add Humor</button>
                <button onclick="refinePersonality('more_realistic')">More Realistic</button>
            </div>
            <div class="custom-refinement">
                <input type="text" id="custom-refinement" placeholder="Custom refinement...">
                <button onclick="refineCustom()">Apply</button>
            </div>
        </div>
    `;
}

async function refinePersonality(preset) {
    showLoading('Refining personality...');
    
    const response = await fetch('/api/refine_personality', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ 
            name: currentPersonality,
            preset: preset 
        })
    });
    
    // Update display with refined personality
}
```

### Visual Indicators

1. **Loading States**: Show spinner during generation/refinement
2. **Success Feedback**: Green alert when complete
3. **Generation Badge**: Mark AI-generated personalities
4. **History Indicator**: Show if personality has been refined

## Success Metrics

### 1. Character Accuracy
- **Test**: Generate "Sherlock Holmes"
- **Expected**: High aggression, low emoji usage, analytical play style
- **Verbal tics**: Include deduction-related phrases

### 2. Trait Diversity
- **Test**: Generate 10 different characters
- **Expected**: Wide range of trait values, not clustered
- **No two personalities should be too similar

### 3. Poker Appropriateness
- **Test**: Generate non-poker characters (e.g., "Gandhi", "Mike Tyson")
- **Expected**: Traits make sense for poker context
- **Gandhi**: Low aggression, high chattiness (peaceful dialogue)
- **Tyson**: High aggression, high bluff tendency

### 4. Creative Consistency
- **Test**: Generate same name 3 times
- **Expected**: Similar core traits (±0.2), different tics
- **Should feel like same character interpreted slightly differently

### 5. Edge Case Handling
- Single names: "Madonna", "Cher"
- Titles: "The Queen", "Captain America"  
- Non-English: "永井" (Nagai)
- Fictional: "Gandalf", "HAL 9000"

## Testing Plan

### Phase 1: Core Functionality
1. Create PersonalityGenerator class
2. Test with 5 well-known characters
3. Verify JSON structure and value ranges
4. Check that all fields are populated

### Phase 2: Integration
1. Add API endpoint to manager
2. Update UI with generation button
3. Test full flow: name → generate → edit → save
4. Verify saved personalities work in game

### Phase 3: Quality Assurance
1. Test 20+ diverse characters
2. Check trait distribution
3. Verify tics are appropriate
4. Test error handling (API failures, etc.)

### Phase 4: User Experience
1. Add loading states
2. Allow regeneration
3. Show generation tips
4. Add examples gallery?

## Potential Enhancements (Future)

1. **Generation Options**:
   - "Make more aggressive"
   - "Make funnier"
   - "Make more realistic"

2. **Batch Generation**:
   - Generate multiple at once
   - "Generate cast of Star Wars"

3. **Trait Suggestions**:
   - AI suggests adjustments
   - "This seems too passive for a warrior"

4. **Personality Variants**:
   - "Young Sherlock" vs "Old Sherlock"
   - Mood variants: "Angry Gordon Ramsay"

## Implementation Priority

1. **Core Generator** (Phase 1)
2. **Basic UI Integration** (Phase 2)
3. **Error Handling & Validation**
4. **Polish & Loading States**
5. **Enhanced Features** (if time)

## Risk Mitigation

1. **API Failures**: Graceful fallback, clear error messages
2. **Invalid JSON**: Validation and cleaning layer
3. **Inappropriate Content**: Basic filtering for offensive terms
4. **Rate Limits**: Cache recent generations
5. **Inconsistent Quality**: Allow easy regeneration/editing

## Implementation Timeline

### Phase 1: Basic Generation (Current Focus)
**Goal**: Get single-shot generation working end-to-end

1. **Create PersonalityGenerator class** (1-2 hours)
   - Basic generation method
   - Prompt template
   - Validation logic
   
2. **Test via CLI** (30 min)
   - Generate 5-10 test personalities
   - Verify JSON structure
   - Check quality
   
3. **Add API endpoint** (30 min)
   - `/api/generate_personality`
   - Error handling
   
4. **Update UI** (1 hour)
   - Modify create flow
   - Add loading state
   - Test full integration

**Total Phase 1**: ~3-4 hours

### Phase 2: Refinement Features (Future)
**Goal**: Add iterative improvement capabilities

1. **Extend PersonalityGenerator** (1 hour)
   - Add refinement methods
   - Track conversation history
   - Preset refinements
   
2. **New API endpoints** (30 min)
   - `/api/refine_personality`
   - Support presets and custom
   
3. **UI enhancements** (1-2 hours)
   - Refinement section
   - Quick action buttons
   - Visual feedback

**Total Phase 2**: ~3-4 hours

### Phase 3: Full Chat (Future)
- Implement if needed based on user feedback
- Estimated 4-6 hours for full chat UI

## Definition of Done (Phase 1)

- [ ] PersonalityGenerator class created and tested
- [ ] Can generate personalities for 10 different character types
- [ ] All generated personalities have valid JSON structure
- [ ] API endpoint integrated into personality manager
- [ ] UI shows loading state during generation
- [ ] Generated personality appears in editor
- [ ] User can save generated personality
- [ ] Error handling for API failures
- [ ] Basic documentation updated