# my-poker-face
A poker game with LLMs where you can define who you are playing against 
and have a conversation with them while you play.

## 🚀 Quick Start

**First time playing?** Check out our **[Quick Start Guide](QUICK_START.md)** - get playing in under 5 minutes!

For the v1.0 release notes and known issues, see [RELEASE_NOTES_v1.0.md](RELEASE_NOTES_v1.0.md).

## Key Features

- **AI-Powered Personalities**: Play against famous personalities (Gordon Ramsay, Eeyore, Batman, etc.) powered by OpenAI
- **Dynamic Personality System**: AI personalities now change during gameplay! Their traits (aggression, bluff tendency) adapt based on wins, losses, and game events
- **Modern Web Interface**: React frontend with Flask API backend
- **Persistent Games**: Save and resume games with full state preservation
- **Immersive Experience**: Each AI player has unique speech patterns, physical gestures, and playing styles
- **Personality Elasticity** *(NEW)*: AI moods and traits dynamically respond to game events, creating more realistic opponents

## Tech Stack
- **Frontend**: React with TypeScript, Vite, Socket.IO client
- **Backend**: Python Flask API with Socket.IO for real-time updates
- **AI**: OpenAI GPT models for personality-driven gameplay
- **Database**: SQLite for game persistence

## run the game locally
### optional: create a venv to run the game

- use the following commands to create a venv. first switch to the root 
directory of the project and then run the following. Depending on your 
python interpreter, you may need to switch 'python' to 'python3' below

`python -m venv my_poker_face_venv`

`source my_poker_face_venv/bin/activate`

`pip install -r requirements.txt`

### set up your `.env` file
Create a local `.env` file and add your `OPENAI_API_KEY` to it. 
This will be enabled to use the AI PLayers and Assistants.

Once the environment is configured:

### Running with Docker Compose (Recommended)

```bash
# Start all services
make up

# Stop all services
make down

# View logs
make logs

# With custom ports (if defaults are in use)
FRONTEND_PORT=3173 BACKEND_PORT=5001 make up
```

Access the game at http://localhost:5173

The Docker setup includes:
- React frontend (port 5173)
- Flask API backend (port 5000)
- Redis for session management and rate limiting (port 6379)
- Hot-reloading for development
- API rate limiting for cost control

### Admin Dashboard

The admin dashboard provides LLM usage analytics, cost tracking, and debugging tools. Access it at `/admin/` in development mode.

**Authentication:**
The admin dashboard requires token authentication. Set `ADMIN_TOKEN` in your `.env` file:

```bash
ADMIN_TOKEN=my-poker-face-admin
```

Then access the dashboard via:
- **Browser:** `http://localhost:5000/admin/?admin_token=my-poker-face-admin`
- **API:** `curl -H "Authorization: Bearer my-poker-face-admin" http://localhost:5000/admin/api/summary`

**Features:**
- Cost analysis by provider and model
- Performance metrics (latency percentiles, error rates)
- Prompt viewer with filtering and pagination
- Model management (enable/disable models)
- Pricing configuration

### Development Setup (Manual)

For developers who need to run services independently:

<details>
<summary>Click to expand manual setup instructions</summary>

1. **Backend API**:
```bash
python -m flask_app.ui_web
```

2. **Frontend** (in a new terminal):
```bash
cd react/react
npm install
npm run dev
```

3. **Access**:
- Frontend: http://localhost:5173
- API: http://localhost:5000
</details>
