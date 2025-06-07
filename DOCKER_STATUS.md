# Docker Setup Status

## Current Configuration

All services are running correctly with the new UI improvements!

### Port Mappings
- **Frontend (React)**: http://localhost:3173 (maps to container port 3000)
- **Backend (Flask)**: http://localhost:5001 (maps to container port 5000)
- **Redis**: localhost:6380 (maps to container port 6379)

### Environment Variables
- `VITE_API_URL=http://localhost:5001` - Frontend knows where to find the backend
- `VITE_ENABLE_DEBUG=true` - Debug panel is enabled in the UI
- `ENABLE_DEBUG=true` - Set in .env file

### Quick Commands

Start all services:
```bash
docker compose up -d
```

View logs:
```bash
docker compose logs -f         # All services
docker compose logs frontend   # Just frontend
docker compose logs backend    # Just backend
```

Stop all services:
```bash
docker compose down
```

Rebuild after changes:
```bash
docker compose up -d --build
```

### Accessing the Application

1. Open http://localhost:3173 in your browser
2. Enter your name
3. You'll see the new Game Menu with options:
   - Quick Play (random opponents)
   - Custom Game (choose opponents)
   - Themed Game (AI-generated groups)
   - Continue Game (resume saved games)

### Debug Panel

The debug panel is enabled and can be toggled with the button at the bottom-left of the poker table. It shows:
- **Elasticity Tab**: Real-time personality trait changes
- **Pressure Stats Tab**: Game pressure events and statistics

### Troubleshooting

If the frontend can't connect to the backend:
1. Check that both containers are running: `docker compose ps`
2. Verify the backend is healthy: `curl http://localhost:5001/health`
3. Check frontend logs: `docker compose logs frontend`
4. Ensure VITE_API_URL is set correctly in the environment