# UI Component Archival Plan

## Overview
This document outlines the plan to archive deprecated UI components and consolidate the codebase around the React frontend with Flask API backend.

## Deprecated Components to Archive

### 1. Console UI (`console_app/`)
- **Files:**
  - `console_app/__init__.py`
  - `console_app/ui_console.py`
- **Usage:** Basic text-based interface
- **Dependencies:** Direct imports from poker module
- **References to update:**
  - README.md (remove console app instructions)
  - CLAUDE.md (remove console testing commands)

### 2. Rich CLI (`fresh_ui/`)
- **Files:**
  - `fresh_ui/` entire directory including:
    - `__init__.py`, `__main__.py`
    - `display/` (animations, cards, hand_strength, pot_odds, table)
    - `menus/` (main_menu, personality_selector)
    - `utils/` (game_adapter, game_adapter_v2, input_handler, mock_ai)
    - `tests/` (test files)
    - `game_runner.py`
- **Related files:**
  - `working_game.py` (main entry point)
  - `run_rich_cli.sh` (startup script)
  - `README_RICH_CLI.md` (documentation)
- **Dependencies:** Rich library, custom display modules
- **References to update:**
  - README.md (remove Rich CLI section)
  - requirements.txt (consider removing Rich dependency if unused elsewhere)

### 3. Flask UI Templates (`flask_app/templates/`)
- **Files:**
  - `flask_app/templates/` including:
    - `home.html`
    - `poker_game.html` 
    - `settings.html`
    - `messages.html`
    - `advantages.html`
- **Related routes in `flask_app/ui_web.py`:**
  - Routes that render templates (not API endpoints)
  - Template-specific logic
- **Static files:**
  - May need to review `flask_app/static/` for Flask-UI specific assets
- **Dependencies:** Jinja2 templates, Flask render_template
- **References to update:**
  - Remove template rendering routes
  - Keep all API endpoints for React

### 4. Other Deprecated Files
- **Old files already in `old_files/`:**
  - Keep as-is, already archived
- **Demo/test scripts to consider archiving:**
  - `simple_ai_demo.py`
  - `simple_elasticity_demo.py`
  - `elasticity_demo.py`
  - `personality_showcase.py`
  - `interactive_demo.py`
  - Various test scripts in root directory

## Archival Structure

```
archive/
├── deprecated_ui/
│   ├── console_ui/
│   │   └── [console_app files]
│   ├── rich_cli/
│   │   ├── fresh_ui/
│   │   ├── working_game.py
│   │   ├── run_rich_cli.sh
│   │   └── README_RICH_CLI.md
│   ├── flask_ui/
│   │   ├── templates/
│   │   └── static/ (Flask-specific assets)
│   └── demo_scripts/
│       ├── simple_ai_demo.py
│       ├── simple_elasticity_demo.py
│       ├── elasticity_demo.py
│       ├── personality_showcase.py
│       └── interactive_demo.py
└── README.md (explaining the archive)
```

## Migration Steps

### Phase 1: Preparation
1. Create new branch: `feature/archive-deprecated-ui`
2. Create archive directory structure
3. Document current state

### Phase 2: Move Console UI
1. Move `console_app/` to `archive/deprecated_ui/console_ui/`
2. Update imports in any remaining files
3. Remove console UI references from documentation

### Phase 3: Move Rich CLI
1. Move `fresh_ui/` directory
2. Move `working_game.py` and related files
3. Move `README_RICH_CLI.md`
4. Update main README

### Phase 4: Move Flask UI Templates
1. Move `flask_app/templates/` directory
2. Move Flask-specific static assets
3. Update `ui_web.py` to remove template routes
4. Keep all API endpoints for React frontend

### Phase 5: Clean Up
1. Move demo scripts to archive
2. Update all documentation
3. Clean up dependencies in requirements.txt
4. Update .gitignore if needed
5. Update CLAUDE.md instructions

### Phase 6: Testing
1. Verify Flask UI still works
2. Check all imports
3. Run test suite
4. Update CI/CD if applicable

## Post-Archive Improvements

### Clean Flask API
- Remove all template rendering routes
- Keep only API endpoints
- Remove Jinja2/template dependencies if unused
- Focus on being a pure API backend

### Documentation Updates
- Update README to focus on React + Flask API architecture
- Clear setup instructions for frontend/backend
- Update developer documentation

### Dependencies
- Review and minimize requirements.txt
- Remove unused JavaScript dependencies
- Clean up Docker configurations

## Rollback Plan
If issues arise:
1. All files are moved, not deleted
2. Can easily restore from archive/
3. Git history preserves everything

## Notes
- The personality tester in `tests/personality_tester/` should remain as it's a development tool
- Keep the core poker engine intact
- Preserve all game logic and AI functionality
- Focus on simplifying the UI layer to just Flask

## Questions to Clarify
1. Should we keep any Flask UI templates for reference?
2. Are all features from Flask UI already implemented in React?
3. Should demo scripts be archived or deleted entirely?
4. Do we need to update nginx.conf to remove Flask UI routes?