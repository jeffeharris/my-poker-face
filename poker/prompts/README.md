# Prompt Templates

This directory contains externalized YAML prompt templates for the poker AI system.

## Directory Structure

```
poker/prompts/
├── __init__.py           # Validation and security functions
├── README.md             # This file
├── poker_player.yaml     # Main AI persona template (5 sections)
├── decision.yaml         # Decision-making prompt
├── end_of_hand_commentary.yaml  # Post-hand reflection
├── quick_chat_*.yaml     # Targeted chat templates (7 files)
└── post_round_*.yaml     # Post-round reaction templates (4 files)
```

## YAML Format

Each template file follows this structure:

```yaml
name: template_name
version: "1.0.0"
sections:
  section_name: |-
    Template content with {variable} placeholders.
    Multiple lines supported using YAML literal block style.
```

### Variables

Variables are defined using single curly braces: `{variable_name}`

Common variables include:
- `{name}` - Player name
- `{attitude}` - Player attitude (confident, nervous, etc.)
- `{confidence}` - Confidence level
- `{money}` - Starting stack
- `{message}` - Context message for decisions

## Hot-Reload (Development Only)

In development mode (`FLASK_DEBUG=1`), templates are watched for changes and automatically reloaded:

1. Edit any `.yaml` file in this directory
2. Changes are detected within 500ms (debounced)
3. The affected template is reloaded
4. New AI calls will use the updated template

Hot-reload is disabled in production for security and performance.

## Security Features

1. **Path traversal prevention**: Template names must match `^[a-z_]+$`
2. **Safe YAML loading**: Uses `yaml.safe_load()` exclusively
3. **Thread safety**: Template access is protected by locks
4. **Atomic writes**: Saves use temp file + rename pattern
5. **Dev-only editing**: API endpoints are blocked in production

## Editing Templates

### Via UI (Recommended)

1. Open the Prompt Playground in the admin console
2. Click the "Templates" tab
3. Select a template to edit
4. Modify sections as needed
5. Click "Save" to persist changes

### Via File System

1. Edit the YAML file directly
2. Hot-reload will pick up changes (dev mode)
3. Or restart the server (production)

## Template Types

### poker_player.yaml
The main AI persona template with 5 sections:
- `persona_details`: Character background and traits
- `strategy`: Playing strategy instructions
- `direction`: Communication guidelines
- `response_format`: JSON format specification
- `reminder`: Context reminder

### decision.yaml
Simple decision-making prompt wrapper:
- `instruction`: Instructions for responding to game state

### end_of_hand_commentary.yaml
Post-hand analysis and commentary:
- `instruction`: How to reflect on the hand
- `response_format`: Commentary format specification

### quick_chat_*.yaml
Targeted chat for psychological tactics:
- `instruction`: Specific tactic instructions
- `response_format`: Response format

Templates: tilt, false_confidence, doubt, goad, mislead, befriend, table

### post_round_*.yaml
Post-round reactions based on outcome:
- `instruction`: Emotional response instructions
- `response_format`: Response format

Templates: gloat, humble, salty, gracious

## Testing Templates

The Template Editor includes a "Test on Past Calls" feature:

1. Select a template
2. Related captured prompts are displayed
3. Click "Test" to replay with current LLM
4. Compare original vs new responses
5. Click "Replay" to open full replay view

## Versioning

Templates are versioned via Git. Each template includes:
- `version`: Semantic version string
- `hash`: Content hash for change detection

The UI displays version and hash for each template.
