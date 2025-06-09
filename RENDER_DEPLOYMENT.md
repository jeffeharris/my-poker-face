# Render Deployment Guide

This guide covers deploying My Poker Face to Render.com with automatic deployments via GitHub.

## Prerequisites

1. GitHub repository with your code
2. Render.com account (free tier works)
3. OpenAI API key for AI players

## Initial Setup

### 1. Connect GitHub to Render

1. Log in to [Render Dashboard](https://dashboard.render.com/)
2. Click "New +" → "Web Service"
3. Connect your GitHub account
4. Select your `my-poker-face` repository

### 2. Configure Services

The `render.yaml` file automatically configures:
- **Backend**: Flask/SocketIO server with gunicorn
- **Frontend**: Static React site
- **Database**: PostgreSQL (free tier)
- **Redis**: For session management

### 3. Environment Variables

In Render Dashboard, add these environment variables to your backend service:

```
OPENAI_API_KEY=your-openai-api-key
SECRET_KEY=generate-a-random-secret-key
```

### 4. Deploy

1. Push to your main branch
2. Render will automatically detect `render.yaml` and start deployment
3. Monitor progress in Render Dashboard

## Deployment Workflow

### Automatic Deployments

Every push to `main` or `release-candidate-*` branches triggers deployment via GitHub Actions.

### Manual Deployment

```bash
# Option 1: Push to trigger
git push origin main

# Option 2: Render Dashboard
# Click "Manual Deploy" → "Deploy latest commit"

# Option 3: Render CLI
render deploy
```

### Monitoring Deployments

1. Check deployment status: https://dashboard.render.com/
2. View logs: Click service → "Logs" tab
3. Monitor health: `/health` endpoint

## Update Process

### 1. Development Workflow

```bash
# Create feature branch
git checkout -b feature/new-feature

# Make changes and test locally
docker compose up

# Commit and push
git add .
git commit -m "Add new feature"
git push origin feature/new-feature
```

### 2. Deploy to Production

```bash
# Merge to main
git checkout main
git merge feature/new-feature
git push origin main

# Automatic deployment starts
```

### 3. Rollback if Needed

In Render Dashboard:
1. Go to your service
2. Click "Events" tab
3. Find previous successful deploy
4. Click "Rollback to this deploy"

## Configuration Details

### Backend Service

- **Runtime**: Docker with `Dockerfile.render`
- **Health Check**: `/health` endpoint
- **Scaling**: 1 instance (free tier)
- **Region**: Auto-selected by Render

### Frontend Service

- **Build Command**: `cd react/react && npm install && npm run build`
- **Publish Directory**: `./react/react/dist`
- **Routes**: SPA routing configured

### Database

- **Type**: PostgreSQL
- **Plan**: Free (1GB storage, 97% uptime)
- **Backups**: Daily (retained for 7 days on free tier)

## Troubleshooting

### WebSocket Issues

If WebSocket connections fail:
1. Ensure `FLASK_ENV=production` is set
2. Check CORS settings in backend
3. Verify frontend uses `wss://` in production

### Build Failures

Common fixes:
```bash
# Clear build cache in Render Dashboard
# Service → Settings → Clear build cache

# Check build logs for errors
# Service → Logs → Build logs
```

### Database Connection

If database errors occur:
1. Verify `DATABASE_URL` is set correctly
2. Check connection pool settings
3. Monitor database metrics in Render

## Cost Optimization

### Free Tier Limits

- **Web Services**: 750 hours/month
- **PostgreSQL**: 1GB storage
- **Bandwidth**: 100GB/month
- **Build Minutes**: 400/month

### Tips to Stay Free

1. Use single web service for backend
2. Host frontend as static site (free)
3. Enable auto-sleep for development instances
4. Monitor usage in Render Dashboard

## GitHub Actions Setup

To enable automatic deployments:

1. Get Render API key:
   - Account Settings → API Keys → Create API Key

2. Get Service ID:
   - Service Dashboard → Settings → Copy Service ID

3. Add GitHub Secrets:
   - Repository → Settings → Secrets → Actions
   - Add `RENDER_API_KEY` and `RENDER_SERVICE_ID`

## Next Steps

1. Set up custom domain (optional)
2. Configure monitoring alerts
3. Set up backup strategy
4. Consider CDN for static assets

---

For more details, see [Render Documentation](https://render.com/docs)