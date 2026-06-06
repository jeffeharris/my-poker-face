---
purpose: Implementation plan to decouple vice/side-hustle duration from the LLM (system-side, tunable) and make the flavor narration async off the ticker's hot path, inserting into the feed when it returns
type: guide
created: 2026-06-06
last_updated: 2026-06-06
---

# Async ticker narration + system-side duration

## Goal & design (approved)
Today the off-screen world ticker narrates vice / side-hustle events with a
**synchronous** LLM call, and that call **also picks the duration** the AI goes
off-grid. Two problems: it blocks the single ticker greenlet (PRH-21: "a stall
pauses the lobby for ALL users"), and it couples economics to an LLM call (bad for
the headless sim).

**Approved approach (Option A + bucket-in-prompt):**
1. **System picks the duration bucket** (`short`/`medium`/`long`), deterministically
   and **tunably** — not the LLM. Economics no longer wait on (or depend on) an LLM.
2. **LLM narration becomes flavor-only and async** — fired off the tick in a
   background greenlet; the bucket is passed **into** the prompt so the line is
   consistent with the chosen duration. When it returns, the world event is recorded
   and the next ticker poll inserts it into the feed.
3. **Sim win:** with duration decoupled, the headless sim runs vice/side-hustle
   economics with **zero LLM calls** (no narration, or templated).

No templated narration in the live feed — the LLM flavor stays; it just moves off
the hot path.

## Why this is safe to stage
- **Step 1 (decouple)** is shippable on its own: system-picks-bucket + flavor-only
  narration (still synchronous). Immediately fixes the sim coupling and is the
  prerequisite for async. Low blast radius.
- **Step 2 (async)** moves the (now flavor-only) narration off the tick.

## Current seam (code-grounded, vice path)
- `cash_mode/vice_narration.py:51` `narrate_vice(...) -> Tuple[str, str]` — returns
  `(narration, duration_bucket)`. `_narrate_inner` (`:95`) builds the prompt
  (`_build_user_prompt` `:146`), calls the FAST-tier LLM (`json_format`,
  `CallType.VICE_NARRATION`), and `_parse_response` (`:217`) extracts **both**
  `narration` and `duration` from the JSON.
- `cash_mode/ai_vice_spending.py`:
  - `NarrateFn` type (`:371`) = `Callable[[str, int, Optional[Dict]], Tuple[str, str]]`.
  - `_templated_narrate_fn` (`:381`) returns `(line, DEFAULT_DURATION_BUCKET)`.
  - `DURATION_RANGES` (`:118`), `DEFAULT_DURATION_BUCKET` (`:128`),
    `duration_for_bucket(bucket, rng)` (`:350`).
  - `resolve_ai_vice_spending` (`:697`) commit loop (`:868-902`): calls
    `narrate_fn(pid, amount, psych)` → `(narration, bucket)` →
    `duration_for_bucket(bucket, rng)` → `ends_at` → `_commit_vice_start(...,
    duration_bucket=, narration=)`. `pressure` is already computed per candidate.
  - `commit_leave_vice` (`:905`, narrate at `~:987`): same pattern for the
    seated→leave path.
- `cash_mode/lobby.py`:
  - `_vice_narrate` (`:2456`) → `narrate_vice(...)`; passed as `narrate_fn` into
    `resolve_ai_vice_spending` (`:2471`) and `commit_leave_vice` (`:2503`), gated by
    `vice_use_llm_narration`.
  - `_emit_vice_spending_events` (`:4090`) records `LobbyEvent`s to the activity feed
    via `cash_mode.activity.record_event` using the narration as the message.
- `flask_app/services/ticker_service.py`:
  - calls `refresh_unseated_tables(...)` (`:451`).
  - the feed is poll-based: `recent_events(...)` (`:417`, `:493`) →
    `socketio.emit("world_event", serialize_event(event), to=room)` (`:499`).
  - background-task pattern already present: `socketio.start_background_task(...)` (`:149`).

**Side-hustle is the parallel path** — `_hustle_narrate` (`lobby.py:2584`), the
side-hustle resolver + `narrate_hustle`, and the hustle emit. Apply the identical
treatment (own `pick_*_bucket` if the buckets differ; confirm the hustle narration
also returns a duration).

**`refresh_unseated_tables` has 4 callers** — `ticker_service.py:451` (async wanted),
`cash_mode/sim_runner.py:282` (sim — no LLM), `flask_app/routes/cash_routes.py:5026`
and `:5448` (web request paths). The decouple is internal to the resolver, so all 4
benefit uniformly; the async wiring is ticker-specific.

## Step 1 — decouple duration to a system-side picker
1. **Add `pick_duration_bucket(pressure, rng) -> str`** in `ai_vice_spending.py`
   (near `duration_for_bucket`) with tunable weights, e.g.:
   ```
   VICE_BUCKET_WEIGHTS = {'short': 0.45, 'medium': 0.40, 'long': 0.15}
   ```
   Higher `pressure` (low composure/energy) skews toward `long` (escapism).
   Deterministic given `rng` so the sim is reproducible.
2. **Change `NarrateFn` → flavor-only:** `Callable[[str, int, Optional[Dict], str], str]`
   (now receives the bucket, returns just the line).
3. **`_templated_narrate_fn(pid, amount, psych, duration_bucket=DEFAULT) -> str`** —
   return just the line.
4. **`resolve_ai_vice_spending` loop:** replace the `narrate_fn` tuple-call with
   `duration_bucket = pick_duration_bucket(pressure, rng)` then
   `narration = narrate_fn(pid, amount, psych, duration_bucket)` (flavor only).
   Same edit in `commit_leave_vice` (use a sensible pressure, or recompute).
5. **`narrate_vice(pid, amount, snapshot, duration_bucket, ...) -> str`** — add the
   bucket to `_build_user_prompt` ("this is a SHORT/MEDIUM/LONG escape, write a line
   that fits"), and make `_parse_response` return **just `narration`** (drop the
   duration extraction). Keep the fail-soft fallback (now flavor-only).
6. **`_vice_narrate` (lobby.py)** — thread `duration_bucket` through, return `str`.
7. **Tests:** update vice tests asserting the `(narration, bucket)` tuple /
   `narrate_fn` arity; add a `pick_duration_bucket` distribution test (weights +
   pressure skew, seeded rng).
8. Repeat 1–7 for the **side-hustle** path.

Ship Step 1 (still synchronous narration, but flavor-only + system duration). Sim now
runs vice/hustle with no LLM.

## Step 2 — make the flavor narration async (off the tick)
The feed is already record→poll→emit, so we don't need to push through SocketIO by
hand — record the event when the LLM returns and the next tick emits it.
1. **Resolver commits economics in-tick with narration deferred** — `_commit_vice_start`
   writes the state row (duration already chosen system-side) with `narration=None`/
   empty; the resolver returns the events carrying what narration needs
   (`pid, amount, psych, duration_bucket`).
2. **Don't call `_emit_vice_spending_events` inline** for the live path. Instead the
   **ticker** (which has `socketio`) spawns `socketio.start_background_task(...)` that,
   per event: calls the flavor LLM (`narrate_vice(..., duration_bucket)`), then
   `_emit_vice_spending_events([event_with_narration])` (records the LobbyEvent).
   The next `_tick_sandbox` `recent_events` scan emits it as a `world_event`.
   - Inject this as a `narration_scheduler` callback into `refresh_unseated_tables`
     (new optional kwarg). Ticker passes the greenlet-spawning scheduler; **sim passes
     `None`** (no narration); web routes can pass `None` or a sync scheduler.
3. **State-row narration:** decide — leave empty (feed carries the flavor) OR have the
   async path also update the vice/hustle state row's narration for consistency
   (only matters if the row's narration is shown when the AI returns; verify).
4. **Error/timeout:** the greenlet must be fail-soft (already true of `narrate_vice`);
   on failure, either skip the feed event or record a minimal line — but **never block
   or crash the ticker**. Keep the tight `TICKER_LLM_TIMEOUT_SECONDS`.
5. **Tests:** a test that a vice fire records the economics in-tick and that the feed
   event appears on a later poll (simulate the scheduler running synchronously in test).

## Risks / watch-items
- **Greenlet DB access:** the async narrate+record runs in the same process; it writes
  the activity feed (and maybe the state row) — ensure repo/DB access is valid off the
  request path (the ticker already does repo writes, so this should hold).
- **Ordering:** deferred events appear a tick or two after the economics — fine for a
  casual feed, but don't rely on feed order matching economic order.
- **Pressure for `commit_leave_vice`:** the leave path may not have `pressure` handy;
  compute it or pass a neutral default into `pick_duration_bucket`.
- **Don't regress the sim:** `sim_runner.py:282` must end up with **zero** LLM calls
  (scheduler `None` + system bucket). Add a sim assertion if practical.

## Status
- Already landed (Stage 0, branch `scaling-stage1`): `DECISION_ANALYSIS_ITERATIONS`
  500→250, env-tunable ticker pacing constants. The earlier *templated-narration*
  attempt was reverted (`e56a31ca`) in favor of this async design.
- Not started: Step 1 / Step 2 above.
