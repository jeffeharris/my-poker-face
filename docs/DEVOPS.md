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

### Secrets Management (Age Encryption)

Production secrets are encrypted using [age](https://github.com/FiloSottile/age) and stored in `.env.prod.age`. The server decrypts them at deploy time using its private key.

**How it works:**
1. `.env.prod.age` (encrypted) is committed to the repo
2. On deploy, the server decrypts it to `.env` using its private key
3. Plaintext secrets never leave the server or enter git history

**Server key location:** `/root/.config/age/key.txt`

**Server public key:** `age1u53cznacvaqxa69vw6rwn9nz9f7w0dhvrmp9mj3yq2fgvjf2wcmqu9wqvx`

#### Updating Secrets

1. Create/edit `.env.prod` locally (it's gitignored):
   ```bash
   cat > .env.prod << 'EOF'
   OPENAI_API_KEY=sk-your-key-here
   SECRET_KEY=your-secret-key
   FLASK_ENV=production
   CORS_ORIGINS=https://mypokerfacegame.com
   EOF
   ```

2. Encrypt using the server (since age may not be installed locally):
   ```bash
   scp .env.prod root@178.156.202.136:/tmp/ && \
   ssh root@178.156.202.136 "age -r age1u53cznacvaqxa69vw6rwn9nz9f7w0dhvrmp9mj3yq2fgvjf2wcmqu9wqvx -o /tmp/.env.prod.age /tmp/.env.prod && rm /tmp/.env.prod" && \
   scp root@178.156.202.136:/tmp/.env.prod.age ./ && \
   ssh root@178.156.202.136 "rm /tmp/.env.prod.age"
   ```

3. Remove plaintext and commit:
   ```bash
   rm .env.prod
   git add .env.prod.age
   git commit -m "Update encrypted secrets"
   ```

4. Deploy:
   ```bash
   ./deploy.sh
   ```

#### Rotating the Age Key

If the server key is compromised:

```bash
# Generate new key on server
ssh root@178.156.202.136 "age-keygen -o /root/.config/age/key.txt"

# Get new public key
ssh root@178.156.202.136 "grep public /root/.config/age/key.txt"

# Re-encrypt secrets with new public key (update this doc with new key)
```

## Environment Variables

Production environment variables are stored in `/opt/poker/.env` on the server (decrypted from `.env.prod.age` at deploy time). See `.env.example` in the project root for the full list of available variables with descriptions.

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

## CI/CD with GitHub Actions

Deployments are automated via GitHub Actions. When you push to `main`:

1. **Test job** runs on GitHub-hosted runners (Python tests + React build/lint)
2. **Deploy job** runs on the self-hosted runner on the production server

### Workflow Overview

```
Push to main → Run Tests (GitHub) → Deploy (Self-hosted) → Health Check
```

### Triggering a Deployment

Simply push to the `main` branch:
```bash
git push origin main
```

Or merge a pull request into `main`.

### Viewing Deployment Status

Check the Actions tab in GitHub: `https://github.com/jeffeharris/my-poker-face/actions`

### Manual Deployment

If needed, you can still deploy manually:
```bash
./deploy.sh
```

## Self-Hosted GitHub Runner Setup

The production server runs a GitHub Actions self-hosted runner for deployments.

### Initial Setup (One-Time)

1. **Create a runner in GitHub**:
   - Go to: `Settings → Actions → Runners → New self-hosted runner`
   - Select: Linux, x64

2. **SSH to the server**:
   ```bash
   ssh root@178.156.202.136
   ```

3. **Create runner user** (runners shouldn't run as root):
   ```bash
   useradd -m -s /bin/bash github-runner
   usermod -aG docker github-runner
   ```

4. **Download and configure the runner**:
   ```bash
   su - github-runner
   mkdir actions-runner && cd actions-runner

   # Download latest runner (check GitHub for current version)
   curl -o actions-runner-linux-x64.tar.gz -L \
     https://github.com/actions/runner/releases/download/v2.321.0/actions-runner-linux-x64-2.321.0.tar.gz
   tar xzf actions-runner-linux-x64.tar.gz

   # Configure (use token from GitHub UI)
   ./config.sh --url https://github.com/jeffeharris/my-poker-face --token YOUR_TOKEN
   ```

5. **Install as a service**:
   ```bash
   exit  # Back to root
   cd /home/github-runner/actions-runner
   ./svc.sh install github-runner
   ./svc.sh start
   ```

6. **Grant access to age key**:
   ```bash
   # Copy age key for github-runner user
   mkdir -p /home/github-runner/.config/age
   cp /root/.config/age/key.txt /home/github-runner/.config/age/
   chown -R github-runner:github-runner /home/github-runner/.config
   chmod 600 /home/github-runner/.config/age/key.txt
   ```

7. **Set working directory**:
   ```bash
   # Ensure runner works from /opt/poker
   mkdir -p /opt/poker
   chown github-runner:github-runner /opt/poker
   ```

### Managing the Runner

```bash
# Check status
ssh root@178.156.202.136 "systemctl status actions.runner.jeffeharris-my-poker-face.poker-prod"

# Restart runner
ssh root@178.156.202.136 "systemctl restart actions.runner.jeffeharris-my-poker-face.poker-prod"

# View runner logs
ssh root@178.156.202.136 "journalctl -u actions.runner.jeffeharris-my-poker-face.poker-prod -f"
```

### Updating the Runner

GitHub will notify you when updates are available:

```bash
ssh root@178.156.202.136
su - github-runner
cd actions-runner
./svc.sh stop
# Download new version and extract
./svc.sh start
```

### Troubleshooting Runner Issues

**Runner offline:**
```bash
ssh root@178.156.202.136 "systemctl status actions.runner.* && systemctl restart actions.runner.*"
```

**Permission denied errors:**
```bash
# Ensure docker group membership
ssh root@178.156.202.136 "usermod -aG docker github-runner && systemctl restart actions.runner.*"
```

**Age decryption fails:**
```bash
# Check key exists and permissions
ssh root@178.156.202.136 "ls -la /home/github-runner/.config/age/"
```

## Scaling Considerations

Current setup is suitable for low-moderate traffic. For higher load:

1. **Upgrade server**: `~/.local/bin/hcloud server change-type poker-prod cpx21`
2. **Add workers**: Increase gunicorn workers in `docker-compose.prod.yml`
3. **External Redis**: Use Hetzner managed Redis or external service
4. **Load balancer**: Add Hetzner Load Balancer for horizontal scaling
