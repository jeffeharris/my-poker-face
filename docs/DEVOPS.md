# DevOps Guide

This document covers production deployment and infrastructure management for My Poker Face.

## Production Infrastructure

| Component | Details |
|-----------|---------|
| **Provider** | Hetzner Cloud |
| **Server** | cpx11 (2 vCPU, 2GB RAM, 40GB SSD) |
| **Location** | Ashburn, VA (ash) |
| **Domain** | mypokerfacegame.com |
| **IP Address** | 178.156.202.136 |
| **Cost** | ~4.50 EUR/month |

## Architecture

```
                        Internet
                            |
                    [Hetzner Firewall]
                            |
                      :80 / :443
                            |
                    +-------+-------+
                    |     Caddy     |  (reverse proxy, auto-SSL)
                    +-------+-------+
                            |
          +-----------------+-----------------+
          |                 |                 |
    /api/*            /socket.io/*           /*
    /health                                   |
          |                 |                 |
    +-----+-----+     +-----+-----+     +-----+-----+
    |  Backend  |     |  Backend  |     | Frontend  |
    |  (Flask)  |     |  (Flask)  |     |  (nginx)  |
    |   :5000   |     |   :5000   |     |    :80    |
    +-----+-----+     +-----------+     +-----------+
          |
    +-----+-----+
    |   Redis   |
    |   :6379   |
    +-----------+
```

## Quick Reference

### Deploy Changes
```bash
./deploy.sh
```

### SSH to Server
```bash
ssh root@178.156.202.136
```

### View Logs
```bash
# All services
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml logs -f"

# Specific service
ssh root@178.156.202.136 "docker logs -f poker-backend-1"
ssh root@178.156.202.136 "docker logs -f poker-caddy-1"
```

### Restart Services
```bash
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml restart"
```

### Check Status
```bash
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml ps"
```

## Configuration Files

### Production Docker Compose
- **File**: `docker-compose.prod.yml`
- **Location on server**: `/opt/poker/docker-compose.prod.yml`

Key differences from development:
- Uses gunicorn with eventlet for production WSGI
- No volume mounts for code (baked into image)
- Caddy handles SSL termination
- Redis for session/rate limiting

### Caddyfile
- **File**: `Caddyfile`
- **Purpose**: Reverse proxy with automatic HTTPS

### Deploy Script
- **File**: `deploy.sh`
- Syncs code to server via rsync
- Rebuilds and restarts containers

## Hetzner CLI (hcloud)

The `hcloud` CLI is installed at `~/.local/bin/hcloud`.

### Common Commands
```bash
# List servers
~/.local/bin/hcloud server list

# Server info
~/.local/bin/hcloud server describe poker-prod

# Firewall rules
~/.local/bin/hcloud firewall list
~/.local/bin/hcloud firewall describe poker-firewall

# SSH keys
~/.local/bin/hcloud ssh-key list
```

### Context
The hcloud context `poker-prod` is configured with the API token.

## Security

### Firewall Rules

| Port | Protocol | Source | Description |
|------|----------|--------|-------------|
| 22 | TCP | Your IP only | SSH |
| 80 | TCP | 0.0.0.0/0 | HTTP (redirects to HTTPS) |
| 443 | TCP | 0.0.0.0/0 | HTTPS |

### Updating SSH Access

If your IP changes, update the firewall:

```bash
# Get your current IP
curl -s ifconfig.me

# Update firewall (replace YOUR_IP with your actual IP)
~/.local/bin/hcloud firewall remove-from-resource poker-firewall --type server --server poker-prod
~/.local/bin/hcloud firewall delete poker-firewall
~/.local/bin/hcloud firewall create --name poker-firewall --rules-file rules.json
~/.local/bin/hcloud firewall apply-to-resource poker-firewall --type server --server poker-prod
```

Where `rules.json` contains:
```json
[
  {"direction": "in", "protocol": "tcp", "port": "22", "source_ips": ["YOUR_IP/32"], "description": "SSH"},
  {"direction": "in", "protocol": "tcp", "port": "80", "source_ips": ["0.0.0.0/0", "::/0"], "description": "HTTP"},
  {"direction": "in", "protocol": "tcp", "port": "443", "source_ips": ["0.0.0.0/0", "::/0"], "description": "HTTPS"}
]
```

### SSL Certificates
- Managed automatically by Caddy via Let's Encrypt
- Auto-renews before expiration
- Current cert valid until April 3, 2026

## Environment Variables

Production environment variables are stored in `/opt/poker/.env` on the server:

| Variable | Description |
|----------|-------------|
| OPENAI_API_KEY | OpenAI API key for AI players |
| SECRET_KEY | Flask session secret |
| CORS_ORIGINS | Allowed CORS origins |
| FLASK_ENV | Set to production |
| REDIS_URL | Redis connection URL |

## Troubleshooting

### Backend Not Responding
```bash
# Check logs
ssh root@178.156.202.136 "docker logs poker-backend-1 --tail 50"

# Restart backend
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml restart backend"
```

### SSL Certificate Issues
```bash
# Check Caddy logs
ssh root@178.156.202.136 "docker logs poker-caddy-1 --tail 50"

# Force certificate renewal
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml restart caddy"
```

### Redis Connection Issues
```bash
# Check Redis health
ssh root@178.156.202.136 "docker exec poker-redis-1 redis-cli ping"

# Restart Redis
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml restart redis"
```

### Container Won't Start
```bash
# Check for errors
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml logs --tail 100"

# Rebuild from scratch
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml down"
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml up -d --build"
```

## Backup & Recovery

### Database Backup
```bash
# Copy SQLite database locally
scp root@178.156.202.136:/opt/poker/data/poker_games.db ./backup/
```

### Full Server Snapshot
```bash
~/.local/bin/hcloud server create-image poker-prod --type snapshot --description "Backup YYYY-MM-DD"
```

## Scaling Considerations

Current setup is suitable for low-moderate traffic. For higher load:

1. **Upgrade server**: `~/.local/bin/hcloud server change-type poker-prod cpx21`
2. **Add workers**: Increase gunicorn workers in `docker-compose.prod.yml`
3. **External Redis**: Use Hetzner managed Redis or external service
4. **Load balancer**: Add Hetzner Load Balancer for horizontal scaling
