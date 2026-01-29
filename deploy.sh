#!/bin/bash
set -e

SERVER="root@178.156.202.136"
APP_DIR="/opt/poker"
AGE_KEY="/root/.config/age/key.txt"

# Check for encrypted secrets file
if [ ! -f ".env.prod.age" ]; then
    echo "ERROR: .env.prod.age not found!"
    echo ""
    echo "To create it:"
    echo "  1. Create .env.prod with your secrets"
    echo "  2. Get the server's public key: ssh ${SERVER} 'grep public ${AGE_KEY}'"
    echo "  3. Encrypt: age -r <PUBLIC_KEY> .env.prod -o .env.prod.age"
    exit 1
fi

echo "==> Syncing files to server..."
rsync -avz --exclude '.git' --exclude 'node_modules' --exclude '__pycache__' \
  --exclude '.venv' --exclude 'my_poker_face_venv' --exclude '.dev' \
  --exclude 'data/*.db' --exclude '*.pyc' \
  --exclude 'generated_images/' --exclude '.env.prod' \
  -e ssh ./ ${SERVER}:${APP_DIR}/

echo "==> Decrypting secrets on server..."
ssh ${SERVER} "cd ${APP_DIR} && age -d -i ${AGE_KEY} .env.prod.age > .env"

echo "==> Building and starting containers..."
ssh ${SERVER} "cd ${APP_DIR} && docker compose -f docker-compose.prod.yml up -d --build"

echo "==> Waiting for services to start..."
sleep 10

echo "==> Checking health..."
ssh ${SERVER} "curl -s http://localhost/health || echo 'Health check pending...'"

echo "==> Running database migrations..."
ssh ${SERVER} "cd ${APP_DIR} && docker compose -f docker-compose.prod.yml run --rm backend python scripts/migrate_avatars_to_db.py"

echo ""
echo "==> Deployment complete!"
echo "==> Access your app at: https://mypokerfacegame.com"
