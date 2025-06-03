# AI Poker Personality Tester

A web utility for testing how different AI poker personalities respond to custom scenarios.

## Features

- Select up to 3 personalities to test at once
- 5 preset scenarios (Pocket Aces, Bluff Opportunity, etc.)
- Custom scenario builder
- Real-time AI responses using the actual prompt management system
- Shows personality traits and decision-making

## Setup

1. Make sure you have the OpenAI API key in your `.env` file
2. Install Flask if needed: `pip install flask`

## Running the App

From the project root:

```bash
cd tests/personality_tester
python app.py
```

Then open http://localhost:5001 in your browser.

## How It Works

1. **Select Personalities**: Choose 1-3 AI personalities from the list
2. **Configure Scenario**: Either use a preset or create custom:
   - Your hand (e.g., "A♥ A♦")
   - Community cards (leave empty for pre-flop)
   - Pot size
   - Cost to call
   - Available options (fold, call, raise, etc.)
3. **Test**: Click the button to see how each personality responds

The app uses the real `AIPokerPlayer` class and `PromptManager` to generate authentic AI responses based on personality traits.

## Example Scenarios

- **Pocket Aces Pre-flop**: How aggressive are they with the best starting hand?
- **Bluff Opportunity**: Who's willing to bluff with nothing?
- **Medium Pair vs Dangerous Board**: Conservative fold or aggressive play?
- **Flush Draw**: Who chases draws?
- **Monster Hand**: How do they play when they have the nuts?