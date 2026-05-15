---
purpose: Codex second-opinion review of the pre-main triage and the 7 agent plans
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# Codex review of pre-main plans

**Session:** `019e2a1c-8cd8-7220-b7c8-3659b35d840b` (resumed from earlier `pre-main-review` session).

**Caveat:** Codex's shell tool was sandboxed-out for this session (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`). Codex could not actually read the plan docs or referenced source files. The review below is directional pushback based on the prompt summary, **not** a line-by-line audit. Treat each point as a question to verify, not a finding.

## Pushback by item

### T1-29 — psychology persistence (schema migration)

Codex agrees with adding a new `psychology_json` column over reusing `elastic_personality_json`:

> I would not blindly reuse `elastic_personality_json` unless that column is already a versioned, general bot-runtime state blob. If it semantically stores elastic personality config/evolution, mixing psychology state into it risks schema confusion, accidental overwrites, and harder migrations. A new `psychology_json` column is probably cleaner if psychology has its own lifecycle.

**Aside:** Codex also raises a third option — a unified `bot_state_json` blob if you're intentionally consolidating all runtime bot state. We're not, so the agent's recommendation (new column) stands.

**Verdict:** Codex confirms the plan. Proceed.

### T1-32 — `_get_connection_with_retry` rewrite

This is where codex pushed back hardest:

> Be careful applying an existing unused `@retry_on_lock` decorator. It may have been left unused because retrying a context manager body is not equivalent to retrying a single DB operation. If the body performs side effects, emits events, mutates in-memory game state, or calls LLM/services, a retry can duplicate behavior. Also verify decorator ordering with `@contextmanager`; many retry decorators only retry entering the generator, not exceptions thrown after `yield`. I'd prefer the rewrite plan if it scopes retries to transaction acquisition/commit semantics and makes idempotency explicit.

**Action item:** Re-audit the 7 caller method bodies. The agent's audit looked at SQL idempotency but didn't ask about non-DB side effects in the same method. Specifically check each method for:
- LLM calls
- In-memory game state mutations
- Event/socket emissions
- File writes
- Logging that should not be duplicated

If any caller has non-DB side effects within the decorated method body, applying `@retry_on_lock` to the whole method would double those side effects on retry. The decorator should only wrap the DB operation, not the whole method.

This may explain why the decorator was implemented but never applied — someone may have realized this and backed out.

**Verdict:** Verify caller side-effect cleanliness before applying. The plan may need to scope the decorator to a narrower block.

### T1-34 — HU equity offsets

This is the strongest pushback. Codex thinks T1-34 should be demoted to T2 and gated:

> I'd push back hard on treating `+0.30/+0.20` as validated unless there is simulation evidence. Those are huge offsets and can easily make the HU button over-aggressive. This sounds like a tuning/spec hypothesis, not a correctness fix. Unless the current behavior is provably illegal or wildly broken, I'd demote to **T2** and gate behind simulation or a config flag.

Severity calibration he raised: "HU equity offsets sound like **T2**, not T1, unless they cause invalid actions o[r ...]" — output truncated, but the point is clear.

**Action items:**
1. Verify whether `+0.30 BTN` / `+0.20 BB` are based on solver data or hypothesis. The spec at `docs/technical/BOUNDED_OPTIONS_DECISION_FRAMEWORK.md:135-142` defines them — check whether that doc cites a source or just declares the constants.
2. Consider gating behind a config flag (`hu_equity_offset_enabled` on `PromptConfig`) so it can be A/B-tested against the no-offset baseline before becoming default.
3. Consider demoting from T1 to T2.

**Verdict:** Worth verifying. If the offsets are a hypothesis, they should not block the main merge.

### T2-42 — zone gravity

Codex agrees with DELETE:

> DELETE is likely right if it is dead/speculative and not wired into decisions. I would not implement it before main unless there is a concrete failing behavior it fixes. Shipping new strategy mechanics late is higher risk than deleting or deferring an unused concept.

**Verdict:** Confirms the plan. Proceed with delete.

### T2-54 — personality test ("stale test, not a bug")

Codex doesn't accept the verdict without evidence:

> "Stale test, not a bug" needs proof. If tight characters became less aggressive because psychology now suppresses aggression dynamically in believable spots, the new behavior may be correct. If the refactor globally flattened aggression or made tight personalities ignore intended baseline traits, that is a regression. I would not accept the stale-test verdict without comparing pre/post distributions for tight, loose, aggressive, and passive archetypes over fixed seeds.

**Action item:** Run a controlled comparison. The agent's evidence was: "Scrooge's `baseline_aggression` was tuned 0.2 → 0.45 in personalities.json during the refactor." That's the *data* change but doesn't prove the *behavioral* effect across archetypes is intentional.

Simple validation: run `tests/test_personality_responses.py` style scenarios across all 4 archetypes with fixed seeds, compare action distributions before and after the merge. If tight characters dropped aggression while loose characters held, that's the deliberate tuning. If aggression flattened across the board, it's a regression.

**Verdict:** Don't accept "stale test" verdict yet. Validate before fixing.

## Severity calibration challenges (truncated output)

Codex's final paragraph was cut off mid-sentence: "HU equity offsets sound like **T2**, not T1, unless they cause invalid actions o[...]"

Likely continuations:
- "...unless they cause invalid actions or violate explicit poker rules" — supports the demotion.
- The rest of the severity-recal section was lost. We could re-run codex but the actionable items above stand.

## Items codex did not address

These pre-main items got no comment from codex (no concern raised):
- T1-28 coach route ownership — mechanical fix, no design call.
- T1-30 c-bet detector all-in — 1-line fix.
- T1-31 OpponentModel serialization — 2-field fix.
- T1-33 recover_stuck_runout race — the lock-and-recheck pattern is standard.
- T1-37, T1-38 frontend crashes — null-guard pattern is standard.
- T1-39 experiment chat test mocks — test hygiene.
- All T2 items not above.
- All T3 items.

## Net recommendation

Three things to do before implementation kicks off:

1. **Verify retry-decorator caller side effects** (T1-32) — if any of the 7 methods do non-DB work inside the would-be-decorated body, the plan needs revision.
2. **Verify HU equity offsets are calibrated** (T1-34) — check the spec doc for a citation. If it's a hypothesis, demote to T2 and gate behind a flag.
3. **Validate personality test verdict empirically** (T2-54) — run the 4-archetype comparison before declaring "stale test."

Everything else in the plan set is consistent with codex's pushback (or codex didn't object).

## Re-run option

Codex's sandbox failure was on their end, not ours. If you want a real file-by-file review later, retry with:
```bash
codex-assist resume pre-main-review "..." -C /home/jeffh/projects/my-poker-face-tieredbot-messages
```
in a session where the sandbox is functional. The session id `019e2a1c-8cd8-7220-b7c8-3659b35d840b` preserves their context.
