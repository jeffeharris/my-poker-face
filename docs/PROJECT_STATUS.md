# My Poker Face - Project Status

*Last Updated: June 7, 2025*

## 🎯 Project Overview

My Poker Face is an AI-powered poker game where players compete against dynamic personalities powered by LLMs. The game emphasizes emergent storytelling, personality evolution, and social dynamics over pure poker strategy.

## 🏗️ Current Architecture

### Frontend
- **Technology**: React 18 with TypeScript
- **Build Tool**: Vite
- **Real-time**: Socket.IO client
- **UI Components**: Custom poker table visualization
- **Component Organization**: Feature-based folder structure (cards/, game/, chat/, stats/, menus/, debug/, admin/)
- **State Management**: React Context with custom hooks
- **Status**: ✅ Production-ready (reorganized June 2025)

### Backend
- **API**: Flask (Python) - pure API, no templates
- **WebSocket**: Socket.IO for real-time updates
- **Database**: SQLite with automatic game persistence
- **AI Integration**: OpenAI API for personality responses
- **Status**: ✅ Production-ready

### Deployment
- **Platform**: Docker Compose
- **Services**: Frontend, Backend, Redis, Nginx (optional)
- **Development**: Hot reloading, volume mounts
- **Status**: ✅ Ready for local deployment

## ✅ Completed Features

### Core Gameplay
- ✅ Texas Hold'em poker engine with immutable state
- ✅ Multi-player support (human + AI)
- ✅ Full game flow (blinds, betting rounds, showdown)
- ✅ Side pot calculations for all-ins
- ✅ Game persistence and resume functionality

### AI & Personalities
- ✅ 30+ unique AI personalities (Gordon Ramsay, Eeyore, Batman, etc.)
- ✅ Personality-driven chat responses
- ✅ Physical gestures and verbal tics
- ✅ AI-powered personality generation
- ✅ Personality manager UI (CRUD operations)

### Dynamic Personality System (Elasticity)
- ✅ Traits change based on game events
- ✅ Per-personality elasticity configuration
- ✅ Pressure events (wins, losses, bluffs)
- ✅ Automatic trait recovery
- ✅ Mood system reflecting emotional state
- ✅ Full persistence of personality state

### Pressure Stats System
- ✅ Real-time tracking of dramatic game events
- ✅ Player statistics and leaderboards
- ✅ Database persistence for historical data
- ✅ Fun facts and signature move detection
- ✅ Tilt and aggression scoring
- ✅ Frontend stats panel with live updates

### User Interface
- ✅ Modern React frontend with real-time updates
- ✅ Interactive poker table visualization
- ✅ Chat system with AI responses
- ✅ Action buttons and bet slider
- ✅ Elasticity debug panel
- ✅ Mobile-responsive design

### Technical Infrastructure
- ✅ Immutable state machine architecture
- ✅ Functional programming approach
- ✅ Docker Compose setup
- ✅ API-only backend design
- ✅ Comprehensive test suite
- ✅ Development documentation

## 🚧 In Progress

Currently, no features are actively in development. The project is in a stable state.

## 📋 Planned Features

### Near Term (Quick Wins)
1. **Personality Mixer** - Combine two personalities
2. **Emoji Quick Chat** - Quick emoji responses
3. **Basic Tell System** - Physical tells based on hand strength
4. **Rivalry Tracker** - Track conflicts between players

### Medium Term
1. **Relationship System** - AI players remember each other
2. **Emotional Contagion** - Moods spread between players
3. **Table Talk Analysis** - AI responds to chat patterns
4. **Tournament Mode** - Multi-table tournaments

### Long Term
1. **Story Mode** - Campaign with persistent world
2. **Visual Personalities** - AI-generated character images
3. **Voice Integration** - Text-to-speech for AI players
4. **Multi-Model Support** - Different AI providers

## 📅 Recent Updates (June 2025)

### Frontend Reorganization
- ✅ Migrated from flat component structure to feature-based organization
- ✅ Components now organized by domain: cards/, game/, chat/, stats/, menus/, debug/, admin/
- ✅ Added TypeScript interfaces in centralized /types/ directory
- ✅ Created custom React hooks (useSocket, useGameState, usePolling)
- ✅ Added GameContext for centralized state management
- ✅ Created utility functions in /utils/ (api.ts, cards.ts)
- ✅ Fixed CSS naming conflicts (e.g., .player-cards)
- ✅ All functionality and styling preserved during migration

### Pressure Stats Persistence
- ✅ Added database schema for pressure event storage
- ✅ Implemented PressureEventRepository following repository pattern
- ✅ Updated PressureStatsTracker to support optional persistence
- ✅ Stats now persist across server restarts and game sessions
- ✅ Maintains backward compatibility for non-persistent usage
- ✅ Added comprehensive documentation in docs/PRESSURE_STATS_SYSTEM.md

## 📊 Technical Metrics

### Codebase
- **Languages**: TypeScript, Python, CSS
- **React Components**: 15+ (now organized by feature)
- **Python Modules**: 20+
- **Test Coverage**: Core engine fully tested
- **Documentation**: Comprehensive (including architecture docs)

### Performance
- **Game State Updates**: Real-time via WebSocket
- **AI Response Time**: 1-3 seconds
- **Persistence**: Automatic after each action
- **Memory Usage**: Minimal (< 100MB per game)

## 🛠️ Development Setup

```bash
# Quick Start
make up

# Access
Frontend: http://localhost:5173
API: http://localhost:5000
```

## 📁 Project Structure

```
my-poker-face/
├── react/          # React frontend
├── flask_app/      # Flask API backend
├── poker/          # Core game engine
├── tests/          # Test suites
├── docs/           # Documentation
├── archive/        # Deprecated components
└── docker-compose.yml
```

## 🎮 How to Play

1. Start the game with `make up`
2. Open http://localhost:5173
3. Click "New Game"
4. Play poker against AI personalities
5. Watch as their moods and traits evolve!

## 🔗 Key Resources

- **Repository**: [GitHub](https://github.com/jeffeharris/my-poker-face)
- **Documentation**: `/docs` directory
- **Vision**: `/docs/vision/GAME_VISION.md`
- **Architecture**: `/CLAUDE.md`

## 🎯 Next Steps

1. Deploy to production environment
2. Implement quick win features
3. Gather player feedback
4. Expand personality library
5. Add tournament mode

---

*This project demonstrates the potential of LLMs to create dynamic, personality-driven gaming experiences that go beyond traditional AI opponents.*