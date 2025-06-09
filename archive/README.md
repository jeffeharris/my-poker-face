# Archived Components

This directory contains deprecated UI components that have been replaced by the modern React frontend.

## Archive Structure

- **console_ui/** - Original text-based console interface
- **rich_cli/** - Rich terminal UI with visual poker table
- **flask_ui/** - Flask HTML templates (replaced by React)
- **demo_scripts/** - Various demo and test scripts

## Current Architecture

The project now uses:
- **Frontend**: React with TypeScript (in `/react` directory)
- **Backend**: Flask API (routes in `flask_app/ui_web.py`)
- **Game Engine**: Core poker logic (in `/poker` directory)

## Note

These components are preserved for historical reference. They are no longer maintained or tested.

For the current UI, please refer to the React application.