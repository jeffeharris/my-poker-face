# Docker Setup for My Poker Face

This document explains how to run the poker game using Docker Compose.

## Prerequisites

- Docker and Docker Compose installed
- OpenAI API key

## Quick Start

1. **Copy environment file:**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` and add your OpenAI API key:**
   ```
   OPENAI_API_KEY=your_actual_api_key_here
   ```

3. **Start all services:**
   ```bash
   docker-compose up -d
   ```

4. **Access the application:**
   - React UI: http://localhost:5173
   - Flask Backend: http://localhost:5000
   - Health Check: http://localhost:5000/health

## Services

### Backend (Flask + SocketIO)
- **Port:** 5000
- **Features:** Game logic, AI players, WebSocket support
- **Health Check:** http://localhost:5000/health

### Frontend (React + Vite)
- **Port:** 5173
- **Features:** Modern UI, real-time updates, personality manager
- **Hot Reload:** Enabled in development

### Redis (Optional)
- **Port:** 6379
- **Purpose:** Session management, future scaling
- **Data:** Persisted in `redis-data` volume

### Nginx (Production Only)
- **Port:** 80
- **Purpose:** Reverse proxy, load balancing
- **Profile:** `production`

## Development Usage

### Start all services:
```bash
docker-compose up
```

### Start in background:
```bash
docker-compose up -d
```

### View logs:
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f backend
docker-compose logs -f frontend
```

### Stop services:
```bash
docker-compose down
```

### Rebuild after code changes:
```bash
docker-compose build
docker-compose up
```

## Production Usage

### Build and start production services:
```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### Enable nginx proxy:
```bash
docker-compose --profile production up -d
```

## Troubleshooting

### Backend not starting:
- Check OpenAI API key in `.env`
- Check logs: `docker-compose logs backend`

### Frontend build errors:
- Clear node_modules: `docker-compose exec frontend rm -rf node_modules`
- Rebuild: `docker-compose build frontend`

### Database issues:
- Data persisted in `./data` directory
- Reset database: `rm -rf ./data/poker_games.db`

### Port conflicts:
- Change ports in `.env` file
- Default ports: Backend=5000, Frontend=5173, Redis=6379

## Data Persistence

- **Database:** `./data/poker_games.db`
- **Personalities:** Stored in database
- **Redis:** `redis-data` Docker volume

## Environment Variables

See `.env.example` for all available options:
- `OPENAI_API_KEY`: Required for AI players
- `BACKEND_PORT`: Flask server port (default: 5000)
- `FRONTEND_PORT`: React dev server port (default: 5173)
- `REDIS_PORT`: Redis port (default: 6379)
- `NGINX_PORT`: Nginx proxy port (default: 80)

## Useful Commands

### Access container shell:
```bash
docker-compose exec backend bash
docker-compose exec frontend sh
```

### Run tests in container:
```bash
docker-compose exec backend python -m pytest
```

### Reset everything:
```bash
docker-compose down -v
rm -rf ./data/*
```

### View container resource usage:
```bash
docker stats
```