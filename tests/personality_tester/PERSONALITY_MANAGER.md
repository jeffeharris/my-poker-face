# Personality Manager

A web interface for editing AI poker personalities stored in `poker/personalities.json`.

## Features

### 1. **Edit Existing Personalities**
- Modify play style descriptions
- Adjust personality traits with sliders:
  - Bluff Tendency (0-100%)
  - Aggression (0-100%)
  - Chattiness (0-100%)
  - Emoji Usage (0-100%)
- Edit verbal and physical tics
- Change default confidence and attitude

### 2. **Create New Personalities**
- Click "+ Create New Personality"
- Enter a name
- Set all traits and behaviors
- Save to add to the game

### 3. **Delete Personalities**
- Select a personality
- Click "Delete Personality"
- Confirms before deletion

### 4. **Automatic Backups**
- Every save creates a timestamped backup
- Backups stored in `poker/personality_backups/`
- Format: `personalities_backup_YYYYMMDD_HHMMSS.json`

## Running the Manager

```bash
cd tests/personality_tester
python personality_manager.py
```

Then open http://localhost:5002

## How It Works

1. **Select a Personality**: Click on any name in the left panel
2. **Edit Values**: 
   - Text fields for descriptions
   - Sliders for personality traits (0-100%)
   - Add/remove verbal and physical tics
3. **Save Changes**: Click "Save Changes" to update the JSON file
4. **Test Changes**: Go back to the Personality Tester to see effects

## Personality Trait Guidelines

- **Bluff Tendency**: How likely to bluff (0% = never, 100% = always)
- **Aggression**: How likely to raise vs call (0% = passive, 100% = very aggressive)
- **Chattiness**: How much they talk (0% = silent, 100% = very talkative)
- **Emoji Usage**: How often they use emojis (0% = never, 100% = lots)

## Example Personalities

### Conservative Player
- Bluff: 10-20%
- Aggression: 20-30%
- Play Style: "tight and cautious"

### Aggressive Bluffer
- Bluff: 70-90%
- Aggression: 80-95%
- Play Style: "loose and aggressive"

### Balanced Player
- Bluff: 40-50%
- Aggression: 50-60%
- Play Style: "adaptable and strategic"

## Safety Features

- Automatic backups before any changes
- Confirmation required for deletions
- JSON validation before saving
- Error messages for invalid data