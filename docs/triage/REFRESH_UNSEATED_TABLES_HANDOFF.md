---
purpose: Handoff for the in-progress refresh_unseated_tables god-function extraction (T2-75)
type: guide
created: 2026-05-29
last_updated: 2026-05-29
---

# Handoff — `refresh_unseated_tables` extraction (T2-75)

Companion to the plan in [`REFRESH_UNSEATED_TABLES_GOD_FUNCTION.md`](./REFRESH_UNSEATED_TABLES_GOD_FUNCTION.md).
This doc records **exactly what landed, what's left, and the mechanical recipe**
to continue, so the next session can pick up cold.

## TL;DR

- Target: `cash_mode/lobby.py` :: `refresh_unseated_tables` (module-level free
  function; was ~1,642 lines, `def` at line 782).
- **Landed & validated green (514 passed, exit 0):** Phase 1 + Phase 2.
- **Not landed:** Phases 3–7. (Phases 3/4 were written + were passing in a prior
  run but the apply was interrupted before it persisted — they are NOT on disk.)
- Refactor is **behavior-preserving**: each phase is a pure extraction; the
  `tests/test_cash_mode/` suite (~9s) is the oracle and is green after each step.
- Working tree is currently **clean and green** — safe to branch from or continue.

## Current verified state

```bash
# from repo root, containers up (docker compose up -d)
docker compose exec -T backend python -m pytest tests/test_cash_mode/ -q -p no:cacheprovider
# => 514 passed, exit 0   (~9 seconds)
```

What's on disk now (verify with the grep below):

| Item | Status | Location |
|---|---|---|
| `_ticker_name_for` hoisted to module level (Phase 1) | ✅ landed | `cash_mode/lobby.py:2449` |
| `_settle_table_stakes` extracted (Phase 2) | ✅ landed | helper at `:2233`, called in-loop at `:1607` |
| `_apply_bankroll_transfers` (Phase 3) | ❌ still inline | transfers block in the per-table loop |
| `_apply_stake_creations` (Phase 4) | ❌ still inline | creations block in the per-table loop |

```bash
# Confirm landed state at a glance:
grep -n "^def _settle_table_stakes\|^def _apply_bankroll_transfers\|^def _apply_stake_creations\|^def _ticker_name_for\|^def refresh_unseated_tables" cash_mode/lobby.py
grep -n "settled_from_seat_indices = _settle_table_stakes\|_apply_bankroll_transfers(\|_apply_stake_creations(" cash_mode/lobby.py
python3 -c "import ast; ast.parse(open('cash_mode/lobby.py').read()); print('parse OK')"
```

Backups of intermediate states (ephemeral `/tmp`, may be gone — don't rely on
them; git is the source of truth): `/tmp/lobby.bak.p1`, `/tmp/lobby.bak.p2`,
`/tmp/lobby.bak.p4`.

## The per-table loop pipeline (what the body looks like / should look like)

Inside `for table in tables:` (the ~890-line body), the post-burst sequence is:

```
result = RosterRefreshResult(...)              # synthesized from the burst
_process_aspiration_asks(result, ...)          # already a helper
cash_table_repo.save_table(result.new_table)   # persist table
for change in result.idle_changes: ...          # persist idle pool
settled_from_seat_indices = _settle_table_stakes(result, ...)   # ✅ Phase 2 DONE
<bankroll ↔ seat transfers block>               # ⬅ Phase 3: extract to _apply_bankroll_transfers
<AI-borrow stake creations block>               # ⬅ Phase 4: extract to _apply_stake_creations
_emit_activity_events(...)                       # already a helper
_emit_burst_events(...)                          # already a helper
<predator/last-stand scan> ... out[table_id] = result
```

The ordering invariant **settlement → transfers → creations → events** and the
`settled_from_seat_indices` spine (built by settlement, consumed by transfers to
skip exactly the from_seat entries it already drained) MUST be preserved. Do not
reorder.

## How Phases 1–2 were done (the recipe to reuse)

The extractions are done with a small Python transform script (NOT hand-editing
190-line blocks — too error-prone for the deep re-indent). The script:
1. locates the block by **content markers** (asserts exactly one match each),
2. dedents the block body (the in-loop blocks are at 8-space indent → dedent by
   4 to make a module-level helper body at 4-space; the settlement block's body
   was under an extra `if` at 12-space → dedented by 8),
3. splices a call to the new helper in place of the block,
4. inserts the helper `def` just before `def _ticker_name_for(`,
5. writes back; then `ast.parse` + the suite validate it.

### ⚠️ Critical gotcha (import coupling)

The **creations** block calls `debit_bankroll_for_seat`, but in the original that
name is imported inside the **transfers** block (`from cash_mode.bankroll import
credit_ai_cash_out, debit_bankroll_for_seat`). So:

- Extract **creations FIRST** (Phase 4 before Phase 3), giving its helper its own
  `from cash_mode.bankroll import debit_bankroll_for_seat` (the script's
  `prelude`), so the still-inline transfers block keeps working; **or**
- extract both in one go before running the suite.

If you extract transfers first and run the suite, the inline creations block
NameErrors on `debit_bankroll_for_seat`.

### The reusable extractor (generic) — re-create at `/tmp/extract_block.py`

This is the exact tool used for Phases 3/4 (`which = 'creations' | 'transfers'`).
The markers below are current as of this handoff:

```python
import sys
PATH = 'cash_mode/lobby.py'
CONFIG = {
  'creations': dict(
    start="# Phase 4: apply AI-borrow stake creations. The seat refill",
    end="# Emit lobby activity events from the refresh result.",
    name="_apply_stake_creations",
    params=["result","*","stake_repo","relationship_repo","personality_repo",
            "bankroll_repo","chip_ledger_repo","sandbox_id","now"],
    callargs=[("result",None),("stake_repo","stake_repo"),
              ("relationship_repo","relationship_repo"),
              ("personality_repo","personality_repo"),
              ("bankroll_repo","bankroll_repo"),
              ("chip_ledger_repo","chip_ledger_repo"),
              ("sandbox_id","sandbox_id"),("now","now")],
    prelude=["from cash_mode.bankroll import debit_bankroll_for_seat"],  # see gotcha
    doc=["Apply AI-borrow stake creations recorded on `result`."],
    ret="None",
  ),
  'transfers': dict(
    start="# Apply bankroll ↔ seat transfers (closes the v1.5 lobby-seed",
    end="# Phase 4: apply AI-borrow stake creations. The seat refill",
    name="_apply_bankroll_transfers",
    params=["result","*","settled_from_seat_indices","bankroll_repo",
            "chip_ledger_repo","sandbox_id","now"],
    callargs=[("result",None),
              ("settled_from_seat_indices","settled_from_seat_indices"),
              ("bankroll_repo","bankroll_repo"),
              ("chip_ledger_repo","chip_ledger_repo"),
              ("sandbox_id","sandbox_id"),("now","now")],
    prelude=[],
    doc=["Apply bankroll <-> seat transfers for one table's refresh result."],
    ret="None",
  ),
}
which = sys.argv[1]; cfg = CONFIG[which]
lines = open(PATH).read().split('\n')
def find_unique(s):
    h=[i for i,l in enumerate(lines) if s in l]
    assert len(h)==1, f"{s!r}: {len(h)} matches {h}"; return h[0]
s,e = find_unique(cfg['start']), find_unique(cfg['end'])
assert lines[s].startswith("        ") and e>s
block = lines[s:e]
while block and block[-1].strip()=="" : block.pop()
ded=[("" if l.strip()=="" else l[4:]) for l in block]   # 8->4 dedent
for l in block:
    assert l.strip()=="" or l.startswith("        "), f"bad indent: {l!r}"
helper=[f"def {cfg['name']}("]+[f"    {p}," for p in cfg['params']]+[f") -> {cfg['ret']}:"]
helper+=['    """'+cfg['doc'][0]+'"""']+["    "+p for p in cfg['prelude']]
if cfg['prelude']: helper+=[""]
helper+=ded+["",""]
call=[f"        {cfg['name']}("]+[f"            {n}," if v is None else f"            {n}={v}," for n,v in cfg['callargs']]+["        )",""]
new=lines[:s]+call+lines[e:]
t=[i for i,l in enumerate(new) if l.startswith("def _ticker_name_for(")][0]
new=new[:t]+helper+new[t:]
open(PATH,'w').write('\n'.join(new))
print(f"OK {which}: block {e-s} lines -> helper {len(helper)} lines")
```

Run + validate each phase:

```bash
cp cash_mode/lobby.py /tmp/lobby.bak.$(date +%s)         # belt-and-suspenders
python3 /tmp/extract_block.py creations                  # Phase 4 first (see gotcha)
python3 -c "import ast; ast.parse(open('cash_mode/lobby.py').read()); print('AST OK')"
docker compose exec -T backend python -m pytest tests/test_cash_mode/ -q -p no:cacheprovider
python3 /tmp/extract_block.py transfers                  # Phase 3
python3 -c "import ast; ast.parse(open('cash_mode/lobby.py').read()); print('AST OK')"
docker compose exec -T backend python -m pytest tests/test_cash_mode/ -q -p no:cacheprovider
```

## Remaining phases

- **Phase 4 — `_apply_stake_creations`** (do first; see gotcha). Marker-delimited
  block; needs its own `debit_bankroll_for_seat` import.
- **Phase 3 — `_apply_bankroll_transfers`**. Consumes `settled_from_seat_indices`.
- **Phase 5 — emission consolidation.** Mostly already done: activity/burst
  events go through the existing `_emit_*` family (`cash_mode/lobby.py:2449+`).
  The two *inline* ticker emissions (`EVENT_AI_DEFAULT` inside settlement,
  `EVENT_AI_STAKE` inside creations) currently travel **inside** the extracted
  helpers — that is behavior-preserving and acceptable. Only pursue the
  "return events as data" purity refactor if you want settlement/creation to be
  side-effect-free; it is optional and not required for the god-function win.
- **Phase 6 — slim + hygiene.** After 3/4 land, the loop body is a short
  pipeline. Then: hoist the scattered deferred `from x import y` to module top
  where no circular-import forces them deep; audit the ~dozen best-effort
  `try/except` (narrow or comment each). Run
  `pr-review-toolkit:silent-failure-hunter` on the settlement diff specifically.
- **Phase 7 — optional** post-loop helper cleanup (already mostly helpers).

After all phases: also run the broader cash tests that import this function
(`tests/test_cash_lobby_route.py`, `tests/test_ticker_service.py`) and a
chip-conservation check (`tests/test_cash_mode/test_lobby_seat_chip_conservation.py`)
before opening a PR. Ship **one phase per PR**.

## Notes / environment

- The `tests/test_cash_mode/` bucket = 514 tests, ~9s. Fast enough to run after
  every edit — use it as the oracle. (The plan's "golden IO-log" Phase 0 test was
  judged redundant given this suite's coverage; if you want belt-and-suspenders,
  add a recording-fakes characterization test, but it's not blocking.)
- There is **no** dedicated `test_refresh_unseated*.py`; the function is covered
  across ~9 files (`test_global_greedy_fill`, `test_take_stake`, `test_player_staking`,
  `test_lobby_seat_chip_conservation`, `test_movement`, `test_seating`, …).
- This session hit an intermittent **tool-output delivery lag** in the harness
  (results arriving several calls late). If you see that, issue ONE call and wait
  — do not fire filler commands to "flush" it.

## Status checklist (mirror of the plan doc)

- [x] Phase 1 — hoist `_ticker_name_for` (green)
- [x] Phase 2 — extract `_settle_table_stakes` (green)
- [x] Phase 4 — extract `_apply_stake_creations` (green; done before P3 per gotcha)
- [x] Phase 3 — extract `_apply_bankroll_transfers` (green)
- [x] Phase 5 — emission: satisfied by acceptance (inline ticker emits ride in helpers)
- [x] Phase 6 — loop slimmed; deferred imports KEPT (no circular dep — verified;
      they're conditional feature-gated lazy loads); try/except are intentional
      fail-soft (left as-is)
- [~] Phase 7 — skipped (post-loop passes already helpers)

**Done 2026-05-29.** `refresh_unseated_tables` 1,642 → 1,311 lines; three stage
helpers extracted (`_settle_table_stakes`, `_apply_bankroll_transfers`,
`_apply_stake_creations`) + `_ticker_name_for` hoisted. All green (pytest rc=0),
uncommitted on `development`. Diff +459/−358.

### Note on Phase 3 (transfers) — script vs hand-edit

The generic extractor script failed on Phase 3 because Phase 4 had **consumed the
end-marker comment** (`# Phase 4: apply AI-borrow stake creations…`) into the new
helper, so the marker no longer existed in the loop and the search ran into the
helper at the wrong indent (clean `AssertionError`, nothing written). Phase 3 was
then done as a precise hand-`Edit` instead. Lesson: when chaining marker-driven
extractions, a later extraction can delete an earlier one's markers — re-derive
markers against the current file between steps, or extract adjacent blocks in one
pass.
