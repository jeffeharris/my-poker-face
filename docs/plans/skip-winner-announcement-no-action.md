# Plan: Skip Winner Announcement for No-Action Fold-Wins

## Goal
Skip the winner overlay when a player wins via folds and there was no voluntary action (e.g., someone raises preflop and everyone folds - just stealing blinds).

## Definition of "Voluntary Action"
A hand has voluntary action if:
- At least one player **called** a bet, OR
- There was more than one **raise** (i.e., a re-raise happened)

If the only actions are a single raise followed by folds, there was no voluntary action worth announcing.

## Implementation

### 1. Add helper method to `HandInProgress` (poker/memory/hand_history.py)

```python
def had_voluntary_action(self) -> bool:
    """Check if there was voluntary action beyond a single raise + folds."""
    call_count = sum(1 for a in self.actions if a.action == 'call')
    raise_count = sum(1 for a in self.actions if a.action in ('raise', 'all_in'))
    return call_count > 0 or raise_count > 1
```

### 2. Pass flag to winner_announcement (flask_app/handlers/game_handler.py ~line 843-859)

Before emitting `winner_announcement`:
1. Check if it's NOT a showdown (`is_showdown == False`)
2. Get the hand recorder from memory_manager
3. Check `had_voluntary_action()` on current hand
4. Add `skip_announcement: true` to `winner_data` if no showdown AND no voluntary action

```python
# Around line 843-859
skip_announcement = False
if not is_showdown:
    memory_manager = game_data.get('memory_manager')
    if memory_manager and memory_manager.hand_recorder.current_hand:
        if not memory_manager.hand_recorder.current_hand.had_voluntary_action():
            skip_announcement = True

winner_data['skip_announcement'] = skip_announcement
```

### 3. Frontend: Respect the flag (2 files)

**Desktop: react/react/src/components/game/WinnerAnnouncement/WinnerAnnouncement.tsx**
```typescript
// Early return if skip_announcement is true
if (!winnerInfo || !show || winnerInfo.skip_announcement) return null;
```

**Mobile: react/react/src/components/mobile/MobileWinnerAnnouncement.tsx**
```typescript
if (!winnerInfo || winnerInfo.skip_announcement) return null;
```

### 4. Update TypeScript types (react/react/src/types/game.ts or inline)

Add `skip_announcement?: boolean` to `WinnerInfo` interface.

## Files to Modify

| File | Change |
|------|--------|
| `poker/memory/hand_history.py` | Add `had_voluntary_action()` method to `HandInProgress` |
| `flask_app/handlers/game_handler.py` | Check for voluntary action, set `skip_announcement` flag |
| `react/.../WinnerAnnouncement.tsx` | Skip render if `skip_announcement` is true |
| `react/.../MobileWinnerAnnouncement.tsx` | Skip render if `skip_announcement` is true |

## Verification

1. Start a game with AI players
2. Raise preflop to steal blinds - should see NO winner overlay
3. Call a raise, then fold to a bet - should see winner overlay (there was a call)
4. Go to showdown - should see full overlay with cards
5. Check that tournament final hands still show the overlay regardless
