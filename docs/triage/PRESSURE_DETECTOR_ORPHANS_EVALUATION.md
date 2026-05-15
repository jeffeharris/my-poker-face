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

### Recommendation — FIX IN PLACE

The feature is architecturally sound and already wired. Two small additions complete it:

**1. Add to `_get_pressure_impacts()` in `poker/player_psychology.py` (line ~384, end of `pressure_events` dict):**

```python
# Chat events (applied real-time via apply_pressure_event)
'rivalry_trigger': {'composure': -0.05, 'energy': 0.03},
'friendly_chat':   {'composure':  0.03, 'energy': 0.01},
```

Rationale: a taunt rattles composure and spikes alertness (energy); warmth has a minor calming effect. Both weaker than `fold_under_pressure` (`composure: 0.05`). Poise anchor scales composure impact via `_calculate_sensitivity`.

**2. Add to `EVENT_SEVERITY` in `poker/zone_config.py` (line ~192, end of dict):**

```python
'rivalry_trigger': 'minor',
'friendly_chat':   'minor',
```

No change to `ComposureState.update_from_event()` is needed — neither event warrants pressure-source tracking.

### Optional — intrusive thoughts for rivalry_trigger

Add to `INTRUSIVE_THOUGHTS` in `poker/zone_effects.py` so TILTED-zone players see taunt-specific thoughts:

```python
'rivalry_trigger': [
    "They're trying to get in your head.",
    "Prove them wrong. Make them pay.",
    "Don't let their words change your game.",
],
```

### Known limitation

Keyword matching is naive — "nice hand" fires `friendly_chat`; "weak draw" fires `rivalry_trigger` as neutral commentary. Given the small impact magnitudes, false positives are acceptable noise. A higher-quality version would use `CallType.CATEGORIZATION` (fast LLM) but is out of scope.

## Test plan

### `detect_fold_events` removal

- Delete `poker/pressure_detector.py:197-228`
- Remove direct calls from `tests/test_pressure_system.py`
- Verify `detect_showdown_events` covers the no-showdown bluff:
  - `winner_info` with no `hand_rank` key, all others folded, big pot → expects `("successful_bluff", [winner])` in events
  - Self-reported `get_hand_bluff_likelihood() >= 50` also fires `successful_bluff`

### `detect_chat_events` fix

```python
def test_rivalry_trigger_reduces_composure():
    psych = PlayerPsychology.from_personality(name="Test", ...)
    comp_before = psych.axes.composure
    psych.apply_pressure_event('rivalry_trigger', 'Alice')
    assert psych.axes.composure < comp_before

def test_friendly_chat_raises_composure():
    psych = PlayerPsychology.from_personality(name="Test", ...)
    comp_before = psych.axes.composure
    psych.apply_pressure_event('friendly_chat', 'Alice')
    assert psych.axes.composure > comp_before

def test_detect_chat_events_rivalry_keyword():
    detector = PressureEventDetector()
    events = detector.detect_chat_events('Alice', "you're scared aren't you", ['Bob'])
    assert ('rivalry_trigger', ['Bob']) in events

def test_detect_chat_events_friendly_keyword():
    detector = PressureEventDetector()
    events = detector.detect_chat_events('Alice', "nice hand!", ['Bob'])
    assert ('friendly_chat', ['Bob']) in events
```

## Disposition summary

| Method | Production status | Recommendation |
|---|---|---|
| `detect_fold_events` | Uncalled; dead fallback; superseded by `detect_showdown_events` | **DELETE** (lines 197-228) |
| `detect_chat_events` | Called but silent no-op due to missing impact registration | **FIX IN PLACE** — 2 impact entries + 2 severity entries |

## Key files

- `poker/pressure_detector.py` (lines 197-228 to delete; lines 244-260 correct as-is)
- `poker/player_psychology.py:349-386` `_get_pressure_impacts` — add 2 entries
- `poker/zone_config.py:160-192` `EVENT_SEVERITY` — add 2 entries
- `flask_app/routes/game_routes.py:1640-1653` — existing wiring is correct, no change needed
- `poker/psychology_pipeline.py:243-251` — shows how bluff_likelihoods flow into `detect_showdown_events`
- `poker/psychology_model.py:164-177` `update_from_event` — no change needed for chat fix
