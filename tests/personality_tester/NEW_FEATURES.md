# New Features Added

## 1. Column View Toggle

Switch between two display modes:
- **Stacked View**: Traditional vertical layout (default)
- **Column View**: Side-by-side comparison in a grid

The column view is perfect for comparing how different personalities respond to the same scenario. Each personality gets equal space and you can see decisions at a glance.

## 2. Show Prompt Details

Click the "Show Prompt Details" button to see:

### What's Actually Sent to OpenAI:

**System Prompt Structure:**
```
Persona: {PersonalityName}
Attitude: {from personalities.json}
Confidence: {from personalities.json}
Starting money: $10000

Full prompt includes:
- Personality details and play style
- Strategy instructions  
- Response format requirements (JSON)
- Example responses
```

**User Message (Your Scenario):**
```
You have 7♥ 7♦ in your hand.
Community Cards: K♠ Q♠ J♣
Pot Total: $500
Your cost to call: $100
You must select from these options: ['fold', 'call', 'raise']
What is your move?
```

**Personality Traits Applied:**
```
Eeyore:
- Play Style: tight and passive
- Bluff Tendency: 10%
- Aggression: 20%

Donald Trump:
- Play Style: aggressive and boastful
- Bluff Tendency: 80%
- Aggression: 90%
```

This debug view helps understand:
- How the prompt management system works
- What personality data influences decisions
- The exact format OpenAI receives

## Usage Tips

1. **Column View** is best when testing 2-3 personalities to compare decisions
2. **Stacked View** is better for reading full responses and inner thoughts
3. **Show Prompt Details** helps developers understand the AI system
4. Toggle between views without losing your results