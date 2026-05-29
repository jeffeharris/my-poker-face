#!/usr/bin/env bash
#
# prod_db_backup.sh — WAL-safe snapshot of the production poker DB + download.
#
# WHY a script and not `cp`: the live DB runs in WAL mode. A plain copy of
# poker_games.db (without -wal/-shm, atomically) is routinely malformed and
# fails integrity_check. We use SQLite's online backup API, which takes a
# consistent snapshot of a live DB — safe to run while the app is up.
#
# This script ONLY READS prod. It writes the snapshot to the prod data volume
# (a new file, never touching the live DB) and scp's it down to ./data/.
# It does NOT migrate, deploy, or restart anything.
#
# Usage:
#   scripts/prod_db_backup.sh
#
# Requires: ssh access to the prod box. You will be prompted to confirm before
# anything touches prod.

set -euo pipefail

PROD_HOST="${PROD_HOST:-root@178.156.202.136}"
PROD_CONTAINER="${PROD_CONTAINER:-poker-backend-1}"
PROD_DB_IN_CONTAINER="/app/data/poker_games.db"
PROD_DATA_ON_HOST="${PROD_DATA_ON_HOST:-/opt/poker/data}"

# UTC timestamp from the prod box itself (keeps naming consistent with server logs).
TS="$(ssh "$PROD_HOST" 'date -u +%Y%m%d_%H%M%S')"
BACKUP_NAME="poker_games.prodbackup_${TS}.db"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)/data"
LOCAL_PATH="${LOCAL_DIR}/${BACKUP_NAME}"

echo "=============================================================="
echo " PROD DB BACKUP (read-only snapshot)"
echo "   host:       $PROD_HOST"
echo "   container:  $PROD_CONTAINER"
echo "   source DB:  $PROD_DB_IN_CONTAINER"
echo "   snapshot →  ${PROD_DATA_ON_HOST}/${BACKUP_NAME}  (on prod)"
echo "   download →  ${LOCAL_PATH}  (local)"
echo "=============================================================="
read -r -p "This will SSH into prod and read the live DB. Continue? [y/N] " ok
[ "$ok" = "y" ] || [ "$ok" = "Y" ] || { echo "Aborted."; exit 1; }

echo
echo "[1/4] Taking WAL-safe snapshot inside the container via SQLite backup API…"
ssh "$PROD_HOST" "docker exec -i $PROD_CONTAINER python - <<'PY'
import sqlite3, sys
src_path = '${PROD_DB_IN_CONTAINER}'
dst_path = '/app/data/${BACKUP_NAME}'
src = sqlite3.connect(src_path)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)          # consistent online snapshot of the live WAL DB
ver = src.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]
ck  = dst.execute('PRAGMA integrity_check').fetchone()[0]
bver = dst.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]
src.close(); dst.close()
print(f'source schema_version : {ver}')
print(f'backup schema_version : {bver}')
print(f'backup integrity_check: {ck}')
if ck != 'ok':
    sys.exit('INTEGRITY CHECK FAILED — snapshot is not usable')
PY"

echo
echo "[2/4] Verifying snapshot exists on prod host…"
ssh "$PROD_HOST" "ls -lh ${PROD_DATA_ON_HOST}/${BACKUP_NAME}"

echo
echo "[3/4] Downloading snapshot to ${LOCAL_PATH}…"
mkdir -p "$LOCAL_DIR"
scp "${PROD_HOST}:${PROD_DATA_ON_HOST}/${BACKUP_NAME}" "$LOCAL_PATH"

echo
echo "[4/4] Local integrity re-check after transfer…"
# Run inside the local backend container so we use the same sqlite build.
docker compose exec -T backend python - "$BACKUP_NAME" <<'PY'
import sqlite3, sys
name = sys.argv[1]
db = f'/app/data/{name}'
c = sqlite3.connect(db)
ck = c.execute('PRAGMA integrity_check').fetchone()[0]
ver = c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]
print(f'downloaded copy schema_version : {ver}')
print(f'downloaded copy integrity_check: {ck}')
sys.exit(0 if ck == 'ok' else 'LOCAL INTEGRITY CHECK FAILED')
PY

echo
echo "=============================================================="
echo " DONE. Verified snapshot at: $LOCAL_PATH"
echo " Next: dry-run the migration on a COPY of this file:"
echo "   docker compose exec -T backend python scripts/migration_dryrun.py /app/data/${BACKUP_NAME}"
echo "=============================================================="
echo
echo " (Optional) keep one snapshot on prod for rollback, prune the rest:"
echo "   ssh $PROD_HOST 'ls -t ${PROD_DATA_ON_HOST}/poker_games.prodbackup_*.db | tail -n +4 | xargs -r rm'"
