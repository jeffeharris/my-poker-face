# Quick Start Guide

Get playing in under 5 minutes.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- An API key from [OpenAI](https://platform.openai.com/api-keys), [Anthropic](https://console.anthropic.com/), or another supported provider

## Setup

```bash
# Clone the repository
git clone https://github.com/jeffeharris/my-poker-face.git
cd my-poker-face

# Configure environment
cp .env.example .env
```

Edit `.env` and add at least one API key:
```
OPENAI_API_KEY=sk-...
# Or use other providers:
# ANTHROPIC_API_KEY=sk-ant-...
# GROQ_API_KEY=gsk_...
```

## Start the Game

```bash
make up
```

Open [http://localhost:5173](http://localhost:5173)

## Playing

1. **Create a game** — Choose your opponents from the personality gallery
2. **Place bets** — Use the action buttons (Fold, Check, Call, Raise)
3. **Chat with AI** — Each opponent responds in character
4. **Games auto-save** — Come back anytime to continue

## Manual Setup (Without Docker)

For development or if you prefer not to use Docker:

**Backend** (Terminal 1):
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m flask_app.ui_web
```

**Frontend** (Terminal 2):
```bash
cd react/react
npm install
npm run dev
```

## Troubleshooting

**Port already in use**
```bash
# Use different ports
FRONTEND_PORT=3173 BACKEND_PORT=5001 make up
```

**API errors**
- Check your API key is set correctly in `.env`
- Verify you have credits with your provider

**Game seems frozen**
- AI responses can take a few seconds
- Check browser console for errors

## Next Steps

- [Game Vision](vision/GAME_VISION.md) — Design philosophy and roadmap
- [Troubleshooting](TROUBLESHOOTING.md) — More detailed help
- [DevOps Guide](DEVOPS.md) — Production deployment
