---
purpose: How the React frontend keeps socket-driven re-renders cheap — the store update path, structural sharing, memoization contract, and the desktop/mobile consumption asymmetry
type: architecture
created: 2026-06-05
last_updated: 2026-06-05
---

# Frontend Rendering & Performance

The poker table is a real-time view driven by a stream of Socket.IO state pushes
(several per second while AIs act). This doc covers **how the app absorbs that
stream without re-rendering the world** — the parts a naive React + WebSocket app
gets wrong. For the general directory map, stack, and routing, see
[`FRONTEND.md`](FRONTEND.md); for the working rules, `react/CLAUDE.md`. This doc is
the "why it's fast," not the "what's where."

All paths are relative to `react/react/src/`.

## The update path

```
Socket.IO push ─► usePokerGame ─► useGameStore.applyGameState() ─► granular selectors ─► components
   (backend)       (hooks/)          (stores/gameStore.ts)           (per slice)
```

Every backend state push lands in `applyGameState` (`gameStore.ts:182`), which
writes the store. Components subscribe to **slices**, not the whole state, so a
change to `pot` doesn't wake a component that only reads `players`.

## The three mechanisms that keep it cheap

### 1. Granular slices, not one blob

`useGameStore` (`gameStore.ts:179`) holds ~25 independent slices — `players`,
`phase`, `pot`, `communityCards`, `currentPlayerIdx`, blinds, `cashMode`,
`bettingContext`, run-out director flags, etc. Components read exactly what they
use:

```ts
const pot = useGameStore((s) => s.pot);          // re-renders only when pot changes
const players = useGameStore((s) => s.players);  // only when players changes
```

This is "phase 2/3" of the old [T3-11](../TRIAGE.md) plan (split state + Zustand
selectors) — already in place.

### 2. Structural sharing in `applyGameState` — the key trick

A backend push delivers a **fresh `players` array with fresh `Player` objects every
time**, even for seats that didn't change. Handed to the store verbatim, every
memoized seat would see a new prop identity and re-render on every push.

`applyGameState` prevents this (`gameStore.ts:184-207`): it diffs each incoming
player against the previous one with a deep field compare (`arePlayersEqual`,
`gameStore.ts:119-177`) and **reuses the previous object reference when nothing
changed**:

```ts
players = state.players.map((incoming) => {
  const existing = prev.players?.find((p) => p.name === incoming.name);
  return existing && arePlayersEqual(existing, candidate) ? existing : candidate;
});
```

Net effect: after a push, only the seats whose data actually changed get new
references. `arePlayersEqual` covers primitives, the hole-card array, and the
nested `psychology` / `llm_debug` blocks — if you add a `Player` field that the UI
renders, **add it to `arePlayersEqual`** or its change won't propagate.

> Structural sharing currently applies to **`players` only**. The other
> object/array slices (`communityCards`, `playerOptions`, `pot`, `cashMode`,
> `bettingContext`) get a fresh reference on every push. That's fine today because
> their consumers are cheap, but it means memoizing a component keyed on those
> slices won't bail — see *Why we don't memo the table sub-components*.

### 3. `React.memo` on the expensive leaves

~23 components are wrapped in `React.memo`, notably the costly, high-instance ones:
`Card` (`components/cards/Card.tsx:25`) and `ActionBadge`
(`components/shared/ActionBadge.tsx:14`). Combined with structural sharing, an
unchanged seat's `Card`/`ActionBadge` skip rendering entirely even as the table
around them updates.

**Memo only works with reference-stable props.** The contract (also in
`react/CLAUDE.md`):

- Callbacks passed to memoized children must be `useCallback`'d.
- Derived objects/arrays must be `useMemo`'d (Zustand selectors return new
  references each change).
- Selector-derived helpers that are reused as props should be stable — e.g.
  `useDisplayNickname` returns a `useCallback`'d function
  (`stores/nicknameOverridesStore.ts:41`).

A single inline arrow defeats it. Live example of the failure mode:
`PokerTable.tsx:811` passes `onFadeComplete={() => setFadeKey((k) => k + 1)}` — a
fresh function each render — so `ActionBadge`'s memo busts for every desktop seat.
Mobile does it correctly with a `useCallback`. (Tracked in
[T3-11](../TRIAGE.md).)

## Desktop vs mobile: a deliberate asymmetry

The two tables consume the store differently (see also `react/CLAUDE.md`):

| | reads | re-render scope |
|---|---|---|
| **Mobile** (`mobile/MobilePokerTable`) | granular slices directly | only the components whose slice changed |
| **Desktop** (`game/PokerTable`) | the composed `gameState` object via `useGameStore(useShallow(selectGameState))` (`usePokerGame.ts:127`) | the whole `PokerTable` re-renders on essentially every push |

`selectGameState` (`gameStore.ts:343`) rebuilds a `GameState` object on each call
for backward compatibility; `useShallow` gates it to top-level slice changes, but
because `pot` (and other slices) arrive as fresh objects each push, desktop
`PokerTable` re-renders on nearly every push regardless. Its leaves still skip via
memo — so this is acceptable, not free. Converging desktop onto granular selectors
is possible but unforced; it pairs naturally with a future `PokerTable.tsx` split.

## Optimistic actions

When the human commits a chip action, the UI moves chips to the pot immediately
rather than waiting for the server round-trip. `applyOptimisticAction`
(`gameStore.ts:257`) mutates the chip-bearing slices and stashes a pre-action
`optimisticSnapshot`; `rollbackOptimisticAction` (`:309`) reverts if the server
rejects, and the next authoritative `applyGameState` clears the snapshot
(`gameStore.ts:234`). This keeps input feeling instant without diverging from
server truth.

## Measured baseline (2026-06-05, mobile)

Render counts were instrumented (a temporary `useRenderCount` hook incrementing per
render-function execution — memo bail-outs don't count) and a live guest tournament
was driven via Playwright. Over a ~12s window of active AI play (one human Call, AIs
acting through a street):

| Component | Renders | Note |
|---|---|---|
| `MobilePokerTable` | ~26 | ~2/sec, not 5–10/sec |
| `MobileOpponents` / `Hero` / `CommunityCards` / `ActionArea` | ~24 each | track parent 1:1 (not memoized) |
| `ActionBadge` (memo'd) | ~10 | vs ~120 possible (24 × 5 seats) → memo bails on most |
| `Card` (memo'd) | 0 steady state; ~18 during a flop deal | only renders when cards actually change/animate |

Conclusion: **no render storm.** The granular store + structural sharing + leaf
memoization already keep the hot path cheap. The instrument is not committed; to
re-run an audit, re-add a per-render counter to the components of interest, drive a
live game, and read `window.__renderCounts`.

## Why we don't memo the table sub-components

`MobileOpponents` / `MobileHero` / `MobileCommunityCards` / `MobileActionArea` are
**not** `React.memo`'d, on purpose. Their props are dominated by slices that get
fresh references every push (`communityCards`, `cashMode`, `playerOptions`,
`bettingContext`), so a memo would never bail — it would add a comparison cost for
no skipped renders. Each of these re-renders is cheap reconciliation whose
expensive children (`Card`, `ActionBadge`) already skip via their own memo. Making
these memos effective would require extending structural sharing (mechanism #2) to
the non-`players` slices first — only worth doing against a measured hotspot, which
the baseline above does not show.

## Rules of thumb when touching the table

- New rendered `Player` field → add it to `arePlayersEqual` (`gameStore.ts:119`).
- New prop to a memoized child → make it reference-stable (`useCallback` /
  `useMemo`); never an inline arrow or object literal.
- Adding a frequently-changing value to the table → put it in its own store slice
  and subscribe granularly, rather than threading it through a wide object.
- Before optimizing renders, **measure** (see baseline) — the structural-sharing
  groundwork means most speculative memoization yields ~0.

## Related docs

- [`FRONTEND.md`](FRONTEND.md) — directory map, stack, routing, providers.
- `react/CLAUDE.md` — the working state-management + memoization contract.
- [`../TRIAGE.md`](../TRIAGE.md) — T3-11 (this audit) and the desktop memo-defeat.
