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
  --exclude 'data/' --exclude '*.pyc' \
  --exclude '.ruff_cache' --exclude '.pytest_cache' --exclude '.mypy_cache' \
  --exclude 'generated_images/' --exclude '.env.prod' --exclude '.env' \
  -e ssh ./ ${SERVER}:${APP_DIR}/

echo "==> Decrypting secrets on server..."
ssh ${SERVER} "cd ${APP_DIR} && age -d -i ${AGE_KEY} .env.prod.age > .env"

echo "==> Tagging current images for rollback..."
ssh ${SERVER} "cd ${APP_DIR} && for svc in backend frontend; do
  img=\$(${COMPOSE} images \$svc --format '{{.ID}}' 2>/dev/null | head -1)
  repo=\$(${COMPOSE} images \$svc --format '{{.Repository}}' 2>/dev/null | head -1)
  if [ -n \"\$img\" ] && [ -n \"\$repo\" ]; then
    docker tag \$img \${repo}:rollback
    echo \"\$repo\" > /tmp/rollback-\$svc
    echo \"  Tagged \$svc (\$img) as \${repo}:rollback\"
  else
    echo \"  No existing image for \$svc (first deploy?)\"
  fi
done"

echo "==> Backing up database (WAL-safe, PRH-29)..."
# Use the SQLite online backup API (consistent snapshot of a live WAL DB) +
# integrity_check + retention, instead of a torn plain `cp`. Off-box shipping is
# the cron's job (set BACKUP_REMOTE_CMD on the box); deploy-time backup is the
# on-box safety net before a build. A failed/ corrupt backup aborts the deploy.
ssh ${SERVER} "cd ${APP_DIR} && if [ -f data/poker_games.db ]; then
  python3 scripts/backup_db.py data/poker_games.db --keep 7
else
  echo 'No database to back up (first deploy?)'
fi"

echo "==> Building and starting containers..."
ssh ${SERVER} "cd ${APP_DIR} && ${COMPOSE} up -d --build"

echo "==> Waiting for services to start..."
sleep 15

echo "==> Running database migrations..."
ssh ${SERVER} "cd ${APP_DIR} && ${COMPOSE} run --rm backend python -m poker.migrations.migrate_avatars_to_db"

echo "==> Checking health..."
if ! ssh ${SERVER} "curl -sf http://localhost/health"; then
    echo ""
    echo "!!! Health check failed — rolling back..."
    ssh ${SERVER} "cd ${APP_DIR} && ROLLBACK_OK=1
for svc in backend frontend; do
  if [ -f /tmp/rollback-\$svc ]; then
    REPO=\$(cat /tmp/rollback-\$svc)
    if docker image inspect \${REPO}:rollback >/dev/null 2>&1; then
      docker tag \${REPO}:rollback \${REPO}:latest
      echo \"  Restored \$svc from \${REPO}:rollback\"
    else
      echo \"  WARNING: rollback image not found for \$svc\"
      ROLLBACK_OK=0
    fi
  else
    echo \"  WARNING: No rollback ref for \$svc\"
    ROLLBACK_OK=0
  fi
done
${COMPOSE} up -d --no-build
if [ \"\$ROLLBACK_OK\" = \"0\" ]; then
  echo 'WARNING: Rollback may be incomplete — check manually'
fi"
    echo "!!! Check logs with:"
    echo "    ssh ${SERVER} 'docker logs poker-backend-1'"
    exit 1
fi

echo ""
echo "==> Deployment complete!"
echo "==> Access your app at: https://mypokerfacegame.com"
