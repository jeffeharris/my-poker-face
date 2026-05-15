---
purpose: Evaluate whether detect_fold_events and detect_chat_events add value post-refactor and how to handle each
type: design
created: 2026-05-15
last_updated: 2026-05-15
---

# Pressure detector orphan method evaluation (T1-36 / T3-66)

Two methods in `poker/pressure_detector.py` are either uncalled or produce zero effect. Different verdicts: one is fully superseded, the other is live-wired but silently broken.

## `detect_fold_events` (lines 197-228)

### What it did

Fired `successful_bluff` when all other players folded and the pot exceeded 50% of average stack. Two detection branches:
1. Self-reported `winner_bluff_likelihood >= 50` from the winner's LLM responses (primary)
2. Fallback: `winner.elastic_personality.get_trait_value('aggression') > 0.7` (aggression heuristic)

### Call sites

**None in production.** Direct unit test only. Not invoked by `PsychologyPipeline._detect_events()`.

### Successor analysis

`detect_showdown_events` (lines 106-113) fully covers this case:

```python
if winner_name and is_big_pot and len(active_players) == 1:
    if self._was_bluffing(winner_name, winner_hand_rank, player_bluff_likelihoods):
        events.append(("successful_bluff", [winner_name]))
```

- `len(active_players) == 1` fires when all others have folded — same trigger.
- `winner_hand_rank` defaults to `10` when cards are not shown (`winner_info.get('hand_rank', 10)`, line 87).
- `_was_bluffing` returns True immediately for `rank >= 9` (line 49) — so all no-showdown wins fire `successful_bluff`.
- `player_bluff_likelihoods` from `get_hand_bluff_likelihood()` is collected and passed in by `psychology_pipeline._detect_events()` (lines 243-251) — the self-reported signal is preserved.

The `elastic_personality` fallback at lines 223-226 is permanently dead. `hasattr(winner, 'elastic_personality')` always returns False post-refactor.

### Gap assessment

**No functional gap.** The showdown path provides equivalent or better coverage. Timing differs (real-time vs post-hand) but is immaterial — events are batched and resolved at hand-end regardless.

### Recommendation — DELETE

Delete lines 197-228. Remove direct unit tests calling this method. Resolves T1-36 and T3-66.

## `detect_chat_events` (lines 244-260)

### What it does

Keyword-scans incoming chat messages and returns:
- `("friendly_chat", recipients)` — keywords: nice, good, great, love, thanks, appreciate
- `("rivalry_trigger", recipients)` — keywords: scared, weak, donkey, fool, terrible, stupid

### Call sites

**IS called in production** at `flask_app/routes/game_routes.py:1645` inside the `send_message` socket handler. Events are routed through `controller.psychology.apply_pressure_event(event_name, sender)` for each affected AI controller.

### Gap analysis — silent no-op

Wiring is correct but all three impact layers are missing registrations:

| Layer | Location | Issue |
|---|---|---|
| Axis impacts | `player_psychology.py:349` `_get_pressure_impacts()` | No entry for either event → returns `{}` → no conf/comp/energy change |
| Event severity | `zone_config.py:160` `EVENT_SEVERITY` | No entry → defaults to `'normal'` (floor applied to zero delta, harmless) |
| Composure tracking | `psychology_model.py:164` `update_from_event()` | Hardcoded 8-event allowlist; neither chat event appears |

A human can trash-talk AI opponents all session with zero behavioral consequence.

### Recommendation — DELETE (revised 2026-05-15)

**Original recommendation was "fix in place" (add the two missing registrations).** User overrode after reviewing the design:

> "remove it, lets clean up the dead code. i don't think the idea was going to work which is why it was abandoned"

The keyword-match approach has too many false positives to be useful (e.g. "good draw" → friendly_chat, "weak hand" → rivalry_trigger when said as neutral poker commentary). The feature was scaffolded but never finished, and the registrations are missing across three layers — strong signal that it was abandoned rather than overlooked. Delete it cleanly.

### Cleanup steps

**1. Delete `detect_chat_events` method** — `poker/pressure_detector.py:244-260`

**2. Delete the caller** — `flask_app/routes/game_routes.py:1638-1652`, the entire post-`send_message` block:
```python
# Remove these lines:
if game_data and content:
    if 'pressure_detector' in game_data and 'ai_controllers' in game_data:
        pressure_detector = game_data['pressure_detector']
        ai_controllers = game_data['ai_controllers']
        ai_player_names = list(ai_controllers.keys())
        chat_events = pressure_detector.detect_chat_events(sender, content, ai_player_names)
        for event_name, affected_players in chat_events:
            for player_name in affected_players:
                if player_name in ai_controllers:
                    controller = ai_controllers[player_name]
                    if controller.psychology is not None:
                        controller.psychology.apply_pressure_event(event_name, sender)
```

**3. Verify no other call sites** — `grep -r 'detect_chat_events\|friendly_chat\|rivalry_trigger' poker/ flask_app/ tests/` should return only test files and the deletion targets.

**4. Remove any tests** that exercise `detect_chat_events` directly or assert on `friendly_chat`/`rivalry_trigger` event names.

If the chat-tilts-AI idea is ever revisited, it should use a cheap LLM categorization (`CallType.CATEGORIZATION`) rather than keyword matching, and the impact registrations need to be designed alongside the detection.

## Test plan

### `detect_fold_events` removal

- Delete `poker/pressure_detector.py:197-228`
- Remove direct calls from `tests/test_pressure_system.py`
- Verify `detect_showdown_events` covers the no-showdown bluff:
  - `winner_info` with no `hand_rank` key, all others folded, big pot → expects `("successful_bluff", [winner])` in events
  - Self-reported `get_hand_bluff_likelihood() >= 50` also fires `successful_bluff`

### `detect_chat_events` removal

- Delete method at `poker/pressure_detector.py:244-260`
- Delete caller block at `flask_app/routes/game_routes.py:1638-1652`
- Remove any tests in `tests/test_pressure_system.py` that exercise `detect_chat_events`, `friendly_chat`, or `rivalry_trigger`
- `grep -r 'detect_chat_events\|friendly_chat\|rivalry_trigger' poker/ flask_app/ tests/` should return zero hits in production code after cleanup

## Disposition summary

| Method | Production status | Recommendation |
|---|---|---|
| `detect_fold_events` | Uncalled; dead fallback; superseded by `detect_showdown_events` | **DELETE** (lines 197-228) |
| `detect_chat_events` | Called but silent no-op; design abandoned | **DELETE** (lines 244-260 + caller at `game_routes.py:1638-1652`) |

## Key files

- `poker/pressure_detector.py` — delete lines 197-228 (`detect_fold_events`) and lines 244-260 (`detect_chat_events`)
- `flask_app/routes/game_routes.py:1638-1652` — delete the chat-event detection block inside `send_message` socket handler
- `poker/psychology_pipeline.py:243-251` — shows how `bluff_likelihoods` flow into `detect_showdown_events` (the surviving successor for fold-bluff detection)
- `tests/test_pressure_system.py` — remove direct tests for `detect_fold_events` and `detect_chat_events`
