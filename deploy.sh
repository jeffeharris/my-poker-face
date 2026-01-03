#!/bin/bash
set -e

SERVER="root@178.156.202.136"
APP_DIR="/opt/poker"

echo "==> Syncing files to server..."
rsync -avz --exclude '.git' --exclude 'node_modules' --exclude '__pycache__' \
  --exclude '.venv' --exclude 'my_poker_face_venv' --exclude '.dev' \
  --exclude 'data/*.db' --exclude '*.pyc' \
  -e ssh ./ ${SERVER}:${APP_DIR}/

echo "==> Creating .env file on server..."
ssh ${SERVER} "cat > ${APP_DIR}/.env << 'ENVEOF'
OPENAI_API_KEY=${OPENAI_API_KEY}
SECRET_KEY=$(openssl rand -hex 32)
FLASK_ENV=production
CORS_ORIGINS=http://178.156.202.136
ENVEOF"

echo "==> Building and starting containers..."
ssh ${SERVER} "cd ${APP_DIR} && docker compose -f docker-compose.prod.yml up -d --build"

echo "==> Waiting for services to start..."
sleep 10

echo "==> Checking health..."
ssh ${SERVER} "curl -s http://localhost/health || echo 'Health check pending...'"

echo ""
echo "==> Deployment complete!"
echo "==> Access your app at: http://178.156.202.136"
