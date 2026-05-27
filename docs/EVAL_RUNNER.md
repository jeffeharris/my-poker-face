---
purpose: How to burst eval/experiment runs on a dedicated Hetzner box (the poker-bot-optimization project)
type: guide
created: 2026-05-27
last_updated: 2026-05-27
---

# Eval runner on Hetzner

For running the sim matrix (e.g. `experiments/exploit_bb100.py`, `sng_runner.py`)
on a fat dedicated box instead of pegging a laptop/prod for hours. Scoped to the
**lookup-tables** eval work.

## ⚠️ SAFETY RAILS — read first

1. **ONLY the `poker-bot-optimization` Hetzner project.** Run
   `hcloud context active` and confirm it prints `poker-bot-optimization`
   before ANY `server create`/`delete`. **Never** operate in the `poker-prod`
   context, and **never** run experiments on the prod server
   (`178.156.202.136` / `poker-prod`) — these runs peg every core for hours and
   have OOM-killed containers; that would jeopardize the live game.
2. **Creating a server is billed.** Confirm with the user before provisioning
   unless they've durably authorized it for the session.
3. **Always tear down after** (`hcloud server delete <name>`). Don't leave idle
   billed boxes. A small box test costs ~€0.005; a full matrix batch ~€1–5.

## One-time project setup (already done 2026-05-27)

- The project `poker-bot-optimization` exists with its own R/W API token, wired
  into a local `hcloud` context of the same name (`hcloud context create ...`,
  token pasted by the user — never put the token in chat/logs).
- SSH key `jeffh` is on the project (matches local `~/.ssh/id_ed25519`,
  fingerprint `4a:9f:...:c2:92`). So `ssh -i ~/.ssh/id_ed25519 root@<ip>` works
  on any box created with `--ssh-key jeffh`.

## Recipe

```bash
export PATH="$HOME/.local/bin:$PATH"
hcloud context use poker-bot-optimization
[ "$(hcloud context active)" = "poker-bot-optimization" ] || { echo ABORT; exit 1; }

# Create. cpx31 (4 shared vCPU) for a flow test; ccx53/ccx63 (32/48 DEDICATED
# cores) for the matrix. docker-ce image ships Docker preinstalled; ash = same
# region as prod. #seeds usable per run = #cores (~200 MiB RAM per worker).
hcloud server create --name poker-eval-<tag> --type ccx63 --image docker-ce \
  --location ash --ssh-key jeffh

IP=<ipv4-from-create-output>
SSH="ssh -i $HOME/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new root@$IP"
until $SSH 'echo up' 2>/dev/null; do sleep 5; done   # wait for boot

# Ship the code. ROOT-ANCHOR the /data/ exclude (a bare `data/` ALSO excludes
# poker/strategy/data/ — the charts — and the eval silently has no tables).
rsync -az --delete \
  --exclude='.git/' --exclude='node_modules/' --exclude='*.db' --exclude='*.db-*' \
  --exclude='my_poker_face_venv/' --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='.pytest_cache/' --exclude='*.out' --exclude='/data/' \
  -e "ssh -i $HOME/.ssh/id_ed25519" ./ root@$IP:/root/poker/

$SSH 'cd /root/poker && docker compose build backend'   # ~47s

# Run (DB-free sim; --no-deps avoids spinning redis/db). Background long matrices.
$SSH 'cd /root/poker && docker compose run --rm --no-deps backend \
  python -m experiments.exploit_bb100 --change exploitation --archetype TAG \
  --backdrop CallStation,CallStation,FoldyBot,FoldyBot --hands 8000 --seeds 42,142,242'

hcloud server delete poker-eval-<tag>   # TEAR DOWN when done
```

## Notes

- **Faithfulness verified**: box results are bit-identical to local (same image
  build from the same `requirements.txt`), so box numbers are trustworthy.
- **Parallelism**: each `exploit_bb100` invocation uses `#seeds` cores. To fan a
  matrix across a fat box, launch one invocation per `--change`×`--backdrop`
  (each grabs its seeds); concurrency cap ≈ `cores / seeds`. ccx63 (48c) runs
  ~16 three-seed jobs at once.
- **Code delivery**: rsync (above) or `git clone` the branch if pushed.
- Sizing/cost detail and the parallelism math are in the captain's log
  (`docs/captains-log/lookup-tables/`).
