---
purpose: Operations runbook for deploying, launching, and running My Poker Face in production — activation checklist, env knobs, deploy/rollback, backups, monitoring, and incident playbooks
type: guide
created: 2026-05-29
last_updated: 2026-05-29
---

# Ops Runbook

The single place to look when **deploying, launching, or firefighting** production.
Security *posture* (what controls exist + why) lives in
[`../technical/SECURITY_POSTURE.md`](../technical/SECURITY_POSTURE.md); this doc is
the operational *how*. The hardening items referenced as `PRH-*` are tracked in
[`../PUBLIC_RELEASE_HARDENING.md`](../PUBLIC_RELEASE_HARDENING.md).

> Infra (host provisioning, Caddy/DNS, the Hetzner box) is documented separately in
> `~/projects/hetzner-infra/README.md`. This runbook covers the application.

## Quick reference

| | |
|---|---|
| Site | https://mypokerfacegame.com |
| Health | https://mypokerfacegame.com/health (compose healthcheck curls `:5000/health`) |
| Server | `ssh root@178.156.202.136` · app dir `/opt/poker` |
| Compose | `docker-compose.prod.yml` (backend = gunicorn + gevent-websocket, `-w 1`) |
| Deploy | `./deploy.sh` (from a clean local checkout) |
| Secrets | `.env.prod` (age-encrypted to `.env.prod.age`; decrypted on the server at deploy) |
| Logs | `ssh root@178.156.202.136 "docker logs -f poker-backend-1"` |
| Restart | `ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml restart"` |

---

## 1. Pre-launch activation checklist

Most controls ship **armed by default** in `docker-compose.prod.yml`. The items below
are the ones that need an operator action — a secret, an external URL, or a cron — before
or right after launch. Set everything in `.env.prod` (then `age`-encrypt to `.env.prod.age`)
unless noted.

- [ ] **Secrets set + strong** — `SECRET_KEY` (startup *fails* in prod without it),
      `JWT_SECRET_KEY`, and the provider keys actually used (`OPENAI_API_KEY` at minimum;
      the in-game default + moderation + images all use OpenAI). Generate a key with
      `python -c "import secrets; print(secrets.token_hex(32))"`.
- [ ] **Google OAuth** — `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` set; the OAuth
      redirect URI is `https://mypokerfacegame.com/api/auth/google/callback`.
- [ ] **`INITIAL_ADMIN_EMAIL`** — set to a **Google/OAuth email** (a `guest_…` value is
      *refused* in production — PRH-38). This is how the first admin is granted.
- [ ] **`ALERT_WEBHOOK_URL`** — set it, or configure it live via **Admin → Settings →
      Alerting** (no redeploy). Until set, the alert handler is a **no-op** (PRH-28) and
      every safety signal is log-only. Slack-compatible; Discord works via the `/slack`
      suffix. **Verify it pages** (see §5).
- [ ] **Budget ceilings confirmed** — `LLM_GLOBAL_DAILY_BUDGET_USD` (default **$50**) is
      the only ceiling a guest can't reset; `LLM_PER_OWNER_DAILY_BUDGET_USD` (default
      **$5**) caps a signed-in user. Confirm they suit launch traffic (PRH-25). Keep the
      **provider-side billing cap low** for launch week as the ultimate backstop.
- [ ] **Off-box backups (PRH-29)** — add the daily cron (see §4) with an off-box
      `--remote-cmd` and provision the remote (Storage Box / S3). Deploy-time backup is
      **on-box only**; the cron is what survives a disk failure.
- [ ] **`CORS_ORIGINS`** — the explicit prod allowlist (already set to the two
      mypokerfacegame.com hosts). Wildcard is *refused* in prod.
- [ ] **`REDIS_URL`** — set (compose points it at the `redis` service). Prod **fails
      startup** if set-but-unreachable (PRH-10). Keep **`-w 1`** until presence + the world
      ticker have a shared store.

After a deploy, confirm the boot log shows the controls armed (see §6).

---

## 2. Environment knobs (shipped defaults)

These are armed in `docker-compose.prod.yml`; override via host env only with reason.

| Var | Prod default | What it does | PRH |
|---|---|---|---|
| `LLM_GLOBAL_DAILY_BUDGET_USD` | `50` | Rolling-24h global spend kill-switch | 25 |
| `LLM_PER_OWNER_DAILY_BUDGET_USD` | `5` | Per-owner daily spend ceiling | 25 |
| `LLM_BUDGET_VELOCITY_WARN_FRACTION` | `0.8` | Page when a scope crosses this fraction of its cap (early warning) | 41 |
| `LLM_PROMPT_RETENTION_DAYS` | `30` | Daily sweep purges older `prompt_captures` | 32 |
| `API_USAGE_RETENTION_DAYS` | `90` | Daily sweep purges older `api_usage` | 32 |
| `DECISION_ANALYSIS_ITERATIONS` | `500` | Per-decision equity MC iterations (analytics; lower = less CPU) | 30 |
| `LLM_INGAME_TIMEOUT` | `30` | Per-call timeout (s) for in-game decision/narration | 18 |
| `LLM_TICKER_TIMEOUT` | `10` | Tighter per-call timeout (s) for world-ticker narration | 21 |
| `LOG_FORMAT` | `json` | Structured logs (set empty for human-readable) | 35 |
| `DROP_PRIVILEGES` | `1` | Entrypoint chowns the data volume + runs as non-root `appuser` | 40 |
| `CSRF_PROTECTION_ENABLED` | _(auto when `FLASK_ENV=production`)_ | Double-submit CSRF on mutating `/api/*` | 36 |
| `SOCKETIO_ASYNC_MODE` | `threading` | Socket.IO async model (safe under the gevent worker; see §7) | 24 |
| `IMAGE_PROVIDER` | `openai` | Image gen provider (prefer `openai` — dall-e-2 moderates output) | — |

---

## 3. Deploy & rollback

```bash
# From a clean local checkout on the default branch:
./deploy.sh
```

`deploy.sh` does, in order: rsync the tree (excluding `.git`, `node_modules`,
`data/*.db`, secrets) → decrypt `.env.prod.age` → **tag current images as
`:rollback`** → **WAL-safe DB backup** (`scripts/backup_db.py`, PRH-29; aborts the
deploy if the backup is corrupt) → `docker compose up -d --build` → run migrations →
**health check** → **auto-rollback** to the `:rollback` images if `/health` fails.

**Manual rollback** (if needed after the window): re-tag the `:rollback` image and
`docker compose -f docker-compose.prod.yml up -d --no-build` — see the rollback block in
`deploy.sh` for the exact commands. Check logs with
`ssh root@178.156.202.136 "docker logs poker-backend-1"`.

---

## 4. Backups & restore (PRH-29)

**Schedule the daily off-box backup** (on the prod box, e.g. `crontab -e`):

```cron
30 3 * * * cd /opt/poker && python3 scripts/backup_db.py data/poker_games.db \
    --keep 7 --remote-cmd 'rclone copy {path} storagebox:poker-backups/' \
    >> /var/log/poker-backup.log 2>&1
```

`scripts/backup_db.py` uses the SQLite **online backup API** (consistent snapshot of
the live WAL DB), runs `PRAGMA integrity_check` (deletes + non-zero-exits on
corruption), keeps `--keep` daily snapshots, and ships off-box via `--remote-cmd`
(`{path}` substituted; rclone/rsync/aws — or set `BACKUP_REMOTE_CMD`). **Wire a non-zero
exit to the PRH-28 webhook** (exit 1 = backup failed, 2 = integrity failed, 3 = off-box
copy failed) so a silent backup failure pages.

**Restore**: stop the backend, replace `data/poker_games.db` with a snapshot
(`*.backup_YYYYmmdd-HHMMSS`), remove any stale `-wal`/`-shm` sidecars, `integrity_check`
it, then start. Never `cp` a live WAL DB — that's the corruption trap PRH-29 fixed.

---

## 5. Monitoring & alerting

- **Alert webhook (PRH-28)** — once `ALERT_WEBHOOK_URL` is set, the handler POSTs
  **ERROR+** logs and the prefixed **WARNING** signals `[LEDGER]`, `[LLM BUDGET]`,
  `[CASH LIFECYCLE]`, `[ASYNC]`(error case). Each alert carries the request id
  (`req=<id>`, PRH-35). De-duped + non-blocking.
- **Verify it pages**: set the URL, then trigger a harmless ERROR (e.g. a deliberate
  bad admin action, or watch the boot log — a disabled budget logs
  `[LLM BUDGET] … DISABLED`). Confirm a message arrives.
- **What to watch for in the feed**:
  - `[LLM BUDGET] velocity …` → a scope is at ≥80% of its daily cap (spend spike *building*).
  - `[LLM BUDGET] … exceeded` → cap hit; cosmetic LLM calls vanish, decisions fall back to the deterministic engine (no hang).
  - `[LEDGER] DRIFT RISK` → a chip-ledger write failed after a chip move — reconcile.
  - `[ASYNC] … threading WITHOUT gevent monkey-patching` → the worker isn't yielding cooperatively (misconfigured deploy — see §7).
- **Logs**: `LOG_FORMAT=json` emits one JSON object per line with `request_id`; ship
  stdout to an aggregator (the remaining PRH-35 ops follow-up). Correlate a user report
  to its server logs via the `X-Request-ID` response header.

---

## 6. Post-deploy verification

```bash
# Health
curl -sf https://mypokerfacegame.com/health && echo OK

# Boot log confirms the controls armed (run shortly after deploy):
ssh root@178.156.202.136 "docker logs poker-backend-1 2>&1 | tail -50" | grep -E \
  "LLM BUDGET|ASYNC|RETENTION|TICKER|alert"
```

Expect to see: `[LLM BUDGET] spend kill-switch ARMED …`, `[ASYNC] … async_mode=threading …
monkey-patch active=True`, and no `DISABLED` / drift warnings. A request to the site should
return an `X-Request-ID` header.

---

## 7. Incident playbook

| Symptom | Likely cause | Action |
|---|---|---|
| "Game frozen", un-sticks ~10–30s later | A stalled provider; bounded by the per-call timeout (PRH-18/21) — *not* a hang | Check `[LLM BUDGET]`/provider status; nothing to do, it self-recovers. If chronic, lower `LLM_INGAME_TIMEOUT`. |
| "Frozen" + 429s in logs | State-poll rate-limited | Per-IP/per-user limiter; bump `RATE_LIMIT_POLLING` if legitimate. |
| Cosmetic flavor (avatars/chatter) vanishing | Daily budget hit (PRH-25) | Check `[LLM BUDGET] … exceeded`; raise the cap or wait for the 24h window to roll. |
| `[LEDGER] DRIFT RISK` paged | A ledger write failed post chip-move | Run the chip-economy audit; reconcile by deltas. **Never hand-settle a reachable cash session.** |
| Startup `[ASYNC] … WITHOUT gevent monkey-patching` (ERROR) | Backend not running under the gevent-websocket worker | Confirm the prod `command:` (gunicorn `-k geventwebsocket…`); a stray plain-`flask run` deploy is the footgun PRH-40 guards. |
| Backend won't boot, `CORS_ORIGINS='*' … not allowed` | Missing explicit `CORS_ORIGINS` in prod | Set the allowlist (this is a deliberate prod guard). |
| Backend won't boot, missing provider key | A configured provider's key is unset | Set the key or remove that provider from the tier config. |
| DB "malformed" after a manual copy | `cp` of a live WAL DB | Restore from a `scripts/backup_db.py` snapshot (§4). |
| Disk filling | Capture/usage growth | Confirm the retention sweep ran (`[RETENTION] purged …`); lower `LLM_PROMPT_RETENTION_DAYS`. |

---

## 8. Known constraints

- **Single worker (`-w 1`)** — presence + the world ticker assume one elected worker.
  Scaling to `-w 2+` needs a shared presence/ticker store first (PRH-10).
- **Mobile-web / PWA only** — no native app store presence (drops the simulated-gambling
  store-review surface; revisit for a native build).
- **Same-origin SPA** — CSRF (PRH-36) relies on the SPA being served same-origin as the
  API (it is, via nginx). A cross-origin frontend would need the token delivered in a
  response body instead of via `document.cookie`.
