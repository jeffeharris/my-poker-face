#!/bin/bash
set -e

SERVER="root@178.156.202.136"
APP_DIR="/opt/poker"
AGE_KEY="/root/.config/age/key.txt"
COMPOSE="docker compose -f docker-compose.prod.yml"

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

echo "==> Tagging current images for rollback..."
ssh ${SERVER} "cd ${APP_DIR} && ${COMPOSE} images -q 2>/dev/null | xargs -r docker tag 2>/dev/null || true"
ssh ${SERVER} "cd ${APP_DIR} && for svc in backend frontend; do
  img=\$(${COMPOSE} images \$svc -q 2>/dev/null)
  if [ -n \"\$img\" ]; then
    docker tag \$img \${svc}:rollback 2>/dev/null || true
  fi
done"

echo "==> Backing up database..."
ssh ${SERVER} "cd ${APP_DIR} && if [ -f data/poker_games.db ]; then
  cp data/poker_games.db data/poker_games.db.bak.\$(date +%Y%m%d-%H%M%S)
  ls -t data/poker_games.db.bak.* 2>/dev/null | tail -n +6 | xargs rm -f
  echo 'Database backed up (keeping last 5)'
fi"

echo "==> Building and starting containers..."
ssh ${SERVER} "cd ${APP_DIR} && ${COMPOSE} up -d --build"

echo "==> Waiting for services to start..."
sleep 15

echo "==> Running database migrations..."
ssh ${SERVER} "cd ${APP_DIR} && ${COMPOSE} run --rm backend python scripts/migrate_avatars_to_db.py"

echo "==> Checking health..."
if ! ssh ${SERVER} "curl -sf http://localhost/health"; then
    echo ""
    echo "!!! Health check failed — rolling back..."
    ssh ${SERVER} "cd ${APP_DIR} && for svc in backend frontend; do
      if docker image inspect \${svc}:rollback >/dev/null 2>&1; then
        docker tag \${svc}:rollback \$(${COMPOSE} images \$svc --format '{{.Repository}}:{{.Tag}}') 2>/dev/null || true
      fi
    done && ${COMPOSE} up -d"
    echo "!!! Rolled back to previous images. Check logs with:"
    echo "    ssh ${SERVER} 'docker logs poker-backend-1'"
    exit 1
fi

echo ""
echo "==> Deployment complete!"
echo "==> Access your app at: https://mypokerfacegame.com"
