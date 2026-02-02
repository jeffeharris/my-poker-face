# Milestone 4: Frontend Evolution

## Background

This is the **Coach Progression System** for a poker game (My Poker Face). It teaches players poker incrementally through play by tracking skill development across a gated skill tree.

**Milestones 1-3 are complete** (on branch `poker-coach-progression`). The backend is fully built:
- 11 skills across 4 gates with triggers, evaluators, and state machine
- Gate unlock chain (1 -> 2 -> 3 -> 4)
- Mode-aware LLM prompts (learn/compete/review) with practicing split at 60%
- Onboarding (beginner/intermediate/experienced) + silent downgrade
- Post-action evaluation hook in both REST and Socket paths
- 7 API endpoints including `/progression` and `/onboarding`
- Session memory with hand evaluations, coaching cadence, repetition avoidance

**The frontend has zero progression UI.** The existing coach components (CoachButton, CoachBubble, CoachPanel, StatsBar) work but are unaware of skill progression. The backend `/stats` endpoint already embeds progression data in the response — it just isn't consumed.

**Key reference documents:**
- Requirements: `docs/technical/COACH_PROGRESSION_REQUIREMENTS.md` (Milestone 4, sections 4, 5, 12.2, 12.3)
- Architecture: `docs/technical/COACH_PROGRESSION_ARCHITECTURE.md` (section 2.6)
- M3 Plan: `docs/plans/M3_PLAN.md`

**Files to read before implementing (the frontend):**
- `react/react/src/hooks/useCoach.ts` — central coach hook (stats, tips, review, mode)
- `react/react/src/types/coach.ts` — CoachStats, CoachMessage, CoachMode
- `react/react/src/components/mobile/CoachPanel.tsx` — bottom sheet with drag-to-dismiss, StatsBar, messages, Q&A
- `react/react/src/components/mobile/CoachBubble.tsx` — floating tip with framer-motion, auto-dismiss
- `react/react/src/components/mobile/StatsBar.tsx` — 4-column stats grid + recommendation strip
- `react/react/src/components/mobile/CoachButton.tsx` — draggable FAB with badge
- `react/react/src/components/mobile/MobilePokerTable.tsx` — orchestrates all coach components (lines 202-246, 605-632)
- `react/react/src/styles/design-tokens.css` — full design system (colors, spacing, typography, shadows)

**Backend endpoints already available:**
- `GET /api/coach/<game_id>/stats` — returns coaching data with `progression` field:
  ```json
  {
    "equity": 0.45, "pot_odds": 2.5, "...",
    "progression": {
      "coaching_mode": "learn",
      "primary_skill": "fold_trash_hands",
      "relevant_skills": ["fold_trash_hands", "position_matters"],
      "coaching_prompt": "...",
      "situation_tags": ["preflop", "trash_hand"],
      "skill_states": {
        "fold_trash_hands": { "state": "practicing", "window_accuracy": 0.75, "total_opportunities": 12, "name": "Fold Trash Hands", "description": "Fold the bottom ~30% of hands preflop.", "gate": 1 },
        "position_matters": { "state": "introduced", "window_accuracy": 0.0, "total_opportunities": 3, "name": "Position Matters", "description": "Play tighter from early position, wider from late.", "gate": 1 }
      }
    }
  }
  ```
- `GET /api/coach/<game_id>/progression` — returns full progression state:
  ```json
  {
    "skill_states": {
      "fold_trash_hands": { "state": "practicing", "window_accuracy": 0.75, "total_opportunities": 12, "total_correct": 9, "streak_correct": 3, "name": "Fold Trash Hands", "description": "Fold the bottom ~30% of hands preflop.", "gate": 1 }
    },
    "gate_progress": { "1": { "unlocked": true, "unlocked_at": "2025-...", "name": "Preflop Fundamentals", "description": "Core preflop decision-making." }, "2": { "unlocked": false, "unlocked_at": null, "name": "Post-Flop Basics", "description": "Fold when you miss, bet when you hit." } },
    "profile": { "self_reported_level": "beginner", "effective_level": "beginner" }
  }
  ```
- `POST /api/coach/<game_id>/onboarding` — body: `{ "level": "beginner"|"intermediate"|"experienced" }`

**Backend skill state values:** `introduced`, `practicing`, `reliable`, `automatic`
**Backend coaching mode values:** `learn`, `compete`, `silent`

**Codebase conventions:**
- Plain CSS with CSS custom properties from `design-tokens.css`. BEM-like naming (`.component__element--modifier`).
- DM Sans (display/body) + JetBrains Mono (monospace). Colors: gold (`#d4a574`), emerald (`#34d399`), sapphire (`#3b82f6`), ruby (`#f43f5e`).
- Components: early return when not open, `useRef` for perf-critical state, `useState` for UI state.
- Framer Motion installed but used sparingly (only CoachBubble). Most animations are CSS keyframes.
- Icons from `lucide-react`. Toast notifications via `react-hot-toast`.
- No UI library (custom components). No global state management (hooks + props).

---

## M4 Scope (from Requirements Milestone 4)

**What ships:**
1. Frontend coaching mode reflects skill-driven cadence
2. Progression indicators (current gate, skill status)
3. Enhanced CoachPanel with skill context
4. Coach bubble adapts content to mode (stat line in Compete, nudge in Learn)
5. Skill introduction cards when new skills activate

---

## Design Decisions

1. **No blocking onboarding modal.** Auto-default everyone to beginner (backend already does this via `get_or_initialize_player()`). Show an inline "skip ahead?" prompt on first CoachPanel visit for experienced players to opt up. Dismissible via localStorage flag.

2. **Progression header in CoachPanel.** Compact strip between the panel header and StatsBar showing current gate name + active skill + accuracy %. Tappable to expand into a full skill grid view (replaces messages section while expanded).

3. **Skill unlock notifications via toast.** When skill states transition (new skill introduced, or gate unlocked), show a `react-hot-toast` notification. Non-blocking, auto-dismisses. Can be upgraded to a richer celebration component later.

4. **Bubble mode differentiation via color accent + label.** Learn mode: emerald border/icon accent with "Coach Tip" label. Compete mode: gold border/icon accent with "Your Stats" label. Same bubble shape and layout.

5. **Skill metadata embedded in API responses.** Instead of duplicating skill definitions in the frontend, the backend includes `name`, `description`, and `gate` fields in the `/stats` and `/progression` responses alongside each skill's state data. Also includes gate metadata (name, description) in gate_progress. This eliminates sync risk with ~15 lines of backend change.

6. **Progression data flows through existing stats.** The `/stats` endpoint already returns `progression` embedded in the response. No new polling or extra API calls for per-turn data. The full `/progression` endpoint is only called once when the CoachPanel opens (for the detail view).

7. **StatsBar unchanged.** Keep it clean — progression lives in the header above it.

---

## Implementation Plan

### Step 0: Enrich backend API responses with skill/gate metadata

**Why**: The frontend needs skill names, descriptions, and gate assignments to display progression UI. Instead of duplicating this data in a static frontend registry, embed it in the existing API responses from `skill_definitions.py` (the single source of truth).

**Modify**: `flask_app/services/coach_engine.py` — `compute_coaching_data_with_progression()`

Enrich `progression.skill_states` to include metadata from `SkillDefinition`:
```python
from .skill_definitions import ALL_SKILLS, ALL_GATES

data['progression'] = {
    # ... existing fields ...
    'skill_states': {
        sid: {
            'state': ss.state.value,
            'window_accuracy': round(ss.window_accuracy, 2),
            'total_opportunities': ss.total_opportunities,
            # NEW: metadata from SkillDefinition
            'name': ALL_SKILLS[sid].name if sid in ALL_SKILLS else sid,
            'description': ALL_SKILLS[sid].description if sid in ALL_SKILLS else '',
            'gate': ALL_SKILLS[sid].gate if sid in ALL_SKILLS else 0,
        }
        for sid, ss in skill_states.items()
    },
}
```

**Modify**: `flask_app/routes/coach_routes.py` — `coach_progression()`

Enrich the `/progression` response similarly:
```python
from flask_app.services.skill_definitions import ALL_SKILLS, ALL_GATES

return jsonify({
    'skill_states': {
        sid: {
            'state': ss.state.value,
            'total_opportunities': ss.total_opportunities,
            'total_correct': ss.total_correct,
            'window_accuracy': round(ss.window_accuracy, 2),
            'streak_correct': ss.streak_correct,
            # NEW: metadata
            'name': ALL_SKILLS[sid].name if sid in ALL_SKILLS else sid,
            'description': ALL_SKILLS[sid].description if sid in ALL_SKILLS else '',
            'gate': ALL_SKILLS[sid].gate if sid in ALL_SKILLS else 0,
        }
        for sid, ss in state['skill_states'].items()
    },
    'gate_progress': {
        str(gn): {
            'unlocked': gp.unlocked,
            'unlocked_at': gp.unlocked_at,
            # NEW: metadata
            'name': ALL_GATES[gn].name if gn in ALL_GATES else f'Gate {gn}',
            'description': ALL_GATES[gn].description if gn in ALL_GATES else '',
        }
        for gn, gp in state['gate_progress'].items()
    },
    'profile': state['profile'],
})
```

### Step 1: Type additions to `types/coach.ts`

**Why**: All new components need progression types. This is the foundation.

**Modify**: `react/react/src/types/coach.ts`

Add:
```typescript
// Skill state values from backend
export type SkillStateValue = 'introduced' | 'practicing' | 'reliable' | 'automatic';

// Coaching mode from progression system (distinct from CoachMode proactive/reactive/off)
export type CoachingModeValue = 'learn' | 'compete' | 'silent';

// Per-skill progress with metadata (from /stats progression.skill_states)
export interface SkillProgress {
  state: SkillStateValue;
  window_accuracy: number;
  total_opportunities: number;
  name: string;         // From backend SkillDefinition
  description: string;  // From backend SkillDefinition
  gate: number;         // Gate number this skill belongs to
}

// Progression context embedded in stats response
export interface CoachProgression {
  coaching_mode: CoachingModeValue;
  primary_skill: string | null;
  relevant_skills: string[];
  coaching_prompt: string;
  situation_tags: string[];
  skill_states: Record<string, SkillProgress>;
}

// Full progression state (from /progression endpoint)
export interface FullSkillProgress extends SkillProgress {
  total_correct: number;
  streak_correct: number;
}

export interface GateProgressInfo {
  unlocked: boolean;
  unlocked_at: string | null;
  name: string;         // From backend GateDefinition
  description: string;  // From backend GateDefinition
}

export interface CoachProfile {
  self_reported_level: string;
  effective_level: string;
}

export interface ProgressionState {
  skill_states: Record<string, FullSkillProgress>;
  gate_progress: Record<string, GateProgressInfo>;
  profile: CoachProfile;
}
```

Extend existing `CoachStats` interface:
```typescript
export interface CoachStats {
  // ... existing fields unchanged ...
  progression?: CoachProgression;
}
```

### Step 2: Extend `useCoach` hook

**Why**: The hook is the single source of truth for coach state. All new UI components consume data from it.

**Modify**: `react/react/src/hooks/useCoach.ts`

**New state:**
```typescript
const [progression, setProgression] = useState<CoachProgression | null>(null);
const [progressionFull, setProgressionFull] = useState<ProgressionState | null>(null);
const [skillUnlockQueue, setSkillUnlockQueue] = useState<string[]>([]);
const prevSkillStatesRef = useRef<Record<string, SkillProgress>>({});
```

**Modify `refreshStats()`:** After `setStats(data)`, extract and store progression:
```typescript
if (data.progression) {
  setProgression(data.progression);

  // Detect new skills appearing (gate unlock introduces new skill IDs)
  const prev = prevSkillStatesRef.current;
  const curr = data.progression.skill_states;
  const newUnlocks: string[] = [];

  for (const sid of Object.keys(curr)) {
    // New skill appeared that wasn't tracked before (gate just unlocked)
    if (!(sid in prev)) {
      newUnlocks.push(sid);
    }
  }

  // Only notify after the first load (prevSkillStatesRef starts empty)
  if (Object.keys(prev).length > 0 && newUnlocks.length > 0) {
    setSkillUnlockQueue(q => [...q, ...newUnlocks]);
  }
  prevSkillStatesRef.current = curr;
}
```

**New callback `fetchProgression()`:**
```typescript
const fetchProgression = useCallback(async () => {
  if (!gameId) return;
  try {
    const res = await fetch(`${config.API_URL}/api/coach/${gameId}/progression`, {
      credentials: 'include',
    });
    if (res.ok) {
      const data = await res.json();
      setProgressionFull(data);
    }
  } catch { /* non-critical */ }
}, [gameId]);
```

**New callback `skipAhead(level)`:**
```typescript
const skipAhead = useCallback(async (level: 'intermediate' | 'experienced') => {
  if (!gameId) return;
  try {
    const res = await fetch(`${config.API_URL}/api/coach/${gameId}/onboarding`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level }),
    });
    if (res.ok) {
      await fetchProgression();
      await refreshStats();
    }
  } catch { /* non-critical */ }
}, [gameId, fetchProgression, refreshStats]);
```

**New callback `dismissSkillUnlock(skillId)`:**
```typescript
const dismissSkillUnlock = useCallback((skillId: string) => {
  setSkillUnlockQueue(q => q.filter(id => id !== skillId));
}, []);
```

**Extend `UseCoachResult` interface:**
```typescript
interface UseCoachResult {
  // ... existing fields ...
  progression: CoachProgression | null;
  progressionFull: ProgressionState | null;
  fetchProgression: () => Promise<void>;
  skipAhead: (level: 'intermediate' | 'experienced') => Promise<void>;
  skillUnlockQueue: string[];
  dismissSkillUnlock: (skillId: string) => void;
}
```

### Step 3: Progression header strip component

**Create**: `react/react/src/components/mobile/ProgressionStrip.tsx`
**Create**: `react/react/src/components/mobile/ProgressionStrip.css`

A compact, tappable strip showing the current gate and active skill focus.

**Props:**
```typescript
interface ProgressionStripProps {
  progression: CoachProgression;
  isExpanded: boolean;
  onToggle: () => void;
}
```

**Rendering logic:**
1. Look up `primary_skill` in `progression.skill_states` for display name (`skill_states[primary_skill].name`)
2. Look up gate from `skill_states[primary_skill].gate` for gate number
3. Show accuracy from `progression.skill_states[primary_skill].window_accuracy`
4. Color-code accuracy: >= 0.70 emerald, 0.40-0.69 gold, < 0.40 ruby
5. Show chevron (down when collapsed, up when expanded)
6. 2px progress bar at bottom showing gate completion (count of reliable/automatic skills in gate / total gate skills)

**When `primary_skill` is null** (all skills automatic or no active skill): show "All skills mastered" or hide the strip.

**CSS follows the `stats-bar` border/padding pattern** — see Design Decisions section above for full CSS.

### Step 4: Progression detail view component

**Create**: `react/react/src/components/mobile/ProgressionDetail.tsx`
**Create**: `react/react/src/components/mobile/ProgressionDetail.css`

A scrollable skill grid organized by gates. Shown when the progression strip is expanded (replaces message list).

**Props:**
```typescript
interface ProgressionDetailProps {
  progressionFull: ProgressionState | null;
  progressionLite: CoachProgression | null;  // Fallback if full not loaded yet
}
```

**Rendering logic:**
1. Iterate gates from `progressionFull.gate_progress` (keyed by gate number, includes `name` and `description`)
2. For each gate, show header with gate name + dots summarizing skill states
3. For each skill in gate (grouped by `skill.gate` from skill_states), show a card with:
   - Skill name
   - Mini progress bar (width = `window_accuracy * 100%`)
   - State label (INTRODUCED / PRACTICING / RELIABLE / AUTOMATIC / LOCKED)
   - Accuracy % (if total_opportunities > 0)
4. Use `progressionFull` if available, fall back to `progressionLite.skill_states`
5. Skills not in either source are shown as "locked"

**Gate dot logic:**
- Count skills in each state. Show filled dots for reliable/automatic, outlined gold for practicing, outlined grey for introduced, dim for locked.

### Step 5: Mode-aware CoachBubble

**Modify**: `react/react/src/components/mobile/CoachBubble.tsx`
**Modify**: `react/react/src/components/mobile/CoachBubble.css`

**Add prop:**
```typescript
interface CoachBubbleProps {
  // ... existing props ...
  coachingMode?: CoachingModeValue;
}
```

**Changes to JSX:**
1. Determine accent class: `coachingMode === 'learn'` -> `coach-bubble--learn`, else `coach-bubble--compete`
2. Add mode label above tip text: `coachingMode === 'learn'` -> "Coach Tip", compete -> "Your Stats"
3. Apply accent class to root `.coach-bubble` div

**CSS additions:**
- `.coach-bubble--learn`: emerald border, emerald icon background
- `.coach-bubble--compete`: gold border, gold icon background (makes current default explicit)
- `.coach-bubble__mode-label`: tiny uppercase label (9px, `--letter-spacing-widest`)

### Step 6: CoachPanel integration

**Modify**: `react/react/src/components/mobile/CoachPanel.tsx`
**Modify**: `react/react/src/components/mobile/CoachPanel.css`

**Add props:**
```typescript
interface CoachPanelProps {
  // ... existing props ...
  progression: CoachProgression | null;
  progressionFull: ProgressionState | null;
  onFetchProgression: () => void;
  onSkipAhead: (level: 'intermediate' | 'experienced') => void;
}
```

**Add state:**
```typescript
const [showDetail, setShowDetail] = useState(false);
const [showOnboarding, setShowOnboarding] = useState(false);
```

**Layout changes (insert between header and StatsBar):**
```tsx
{/* Progression strip */}
{progression && (
  <ProgressionStrip
    progression={progression}
    isExpanded={showDetail}
    onToggle={() => setShowDetail(!showDetail)}
  />
)}

{/* Onboarding prompt (first visit, beginner only) */}
{showOnboarding && (
  <div className="coach-onboarding">
    <p className="coach-onboarding__text">
      Already know your way around the table? <strong>Skip ahead</strong> to match your experience.
    </p>
    <div className="coach-onboarding__actions">
      <button className="coach-onboarding__btn coach-onboarding__btn--skip"
        onClick={() => { onSkipAhead('intermediate'); handleDismissOnboarding(); }}>
        Intermediate
      </button>
      <button className="coach-onboarding__btn coach-onboarding__btn--skip"
        onClick={() => { onSkipAhead('experienced'); handleDismissOnboarding(); }}>
        Experienced
      </button>
      <button className="coach-onboarding__btn coach-onboarding__btn--dismiss"
        onClick={handleDismissOnboarding}>
        I'm New
      </button>
    </div>
  </div>
)}
```

**Conditional message area:**
```tsx
{showDetail ? (
  <ProgressionDetail
    progressionFull={progressionFull}
    progressionLite={progression}
  />
) : (
  <div className="coach-messages">
    {/* ... existing messages rendering ... */}
  </div>
)}
```

**Onboarding logic:**
```typescript
// Show onboarding prompt on first open (beginner only, not previously dismissed)
const hasCheckedOnboardingRef = useRef(false);

useEffect(() => {
  if (isOpen && progressionFull && !showDetail && !hasCheckedOnboardingRef.current) {
    hasCheckedOnboardingRef.current = true;
    const isDismissed = localStorage.getItem('coach_onboarding_dismissed');
    const isBeginner = progressionFull.profile?.self_reported_level === 'beginner';
    setShowOnboarding(!isDismissed && isBeginner);
  }
}, [isOpen, progressionFull, showDetail]);

const handleDismissOnboarding = useCallback(() => {
  setShowOnboarding(false);
  try { localStorage.setItem('coach_onboarding_dismissed', 'true'); } catch { /* ignore */ }
}, []);
```

**Fetch progression on first panel open:**
```typescript
const hasFetchedRef = useRef(false);

useEffect(() => {
  if (isOpen && !hasFetchedRef.current) {
    hasFetchedRef.current = true;
    onFetchProgression();
  }
}, [isOpen, onFetchProgression]);
```

### Step 7: MobilePokerTable wiring

**Modify**: `react/react/src/components/mobile/MobilePokerTable.tsx`

**Changes:**

1. Import `toast` from `react-hot-toast`
2. Pass new props to `CoachBubble`:
   ```tsx
   <CoachBubble
     isVisible={...}
     tip={coach.proactiveTip}
     stats={coach.stats}
     onTap={() => setShowCoachPanel(true)}
     onDismiss={coach.clearProactiveTip}
     coachingMode={coach.progression?.coaching_mode}
   />
   ```
3. Pass new props to `CoachPanel`:
   ```tsx
   <CoachPanel
     isOpen={showCoachPanel}
     onClose={() => setShowCoachPanel(false)}
     stats={coach.stats}
     messages={coach.messages}
     onSendQuestion={coach.sendQuestion}
     isThinking={coach.isThinking}
     mode={coach.mode}
     onModeChange={coach.setMode}
     progression={coach.progression}
     progressionFull={coach.progressionFull}
     onFetchProgression={coach.fetchProgression}
     onSkipAhead={coach.skipAhead}
   />
   ```
4. Add skill unlock toast effect (processes one at a time with stagger):
   ```typescript
   const unlockToastTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);

   useEffect(() => {
     if (coach.skillUnlockQueue.length > 0 && !unlockToastTimeoutRef.current) {
       const skillId = coach.skillUnlockQueue[0];
       const skillName = coach.progression?.skill_states[skillId]?.name ?? skillId;
       toast(`New skill: ${skillName}`, {
         duration: 4000,
         position: 'top-center',
         style: {
           background: 'rgba(20, 22, 30, 0.95)',
           color: '#f8fafc',
           border: '1px solid rgba(52, 211, 153, 0.3)',
           backdropFilter: 'blur(16px)',
         },
       });
       // Stagger dismissals so multiple toasts don't fire simultaneously
       unlockToastTimeoutRef.current = setTimeout(() => {
         unlockToastTimeoutRef.current = undefined;
         coach.dismissSkillUnlock(skillId);
       }, 600);
     }
   }, [coach.skillUnlockQueue]);
   ```

### Step 8: Tests

**TypeScript check:**
- `python3 scripts/test.py --ts` — ensure all new types compile, no regressions

**Manual testing plan:**
1. Launch game as guest — verify coach works normally, no progression UI visible
2. Launch game as authenticated user — verify progression strip appears in CoachPanel
3. Play 5+ hands — verify progression data updates (accuracy, opportunities)
4. Verify bubble shows emerald accent during learn mode tips
5. Verify bubble shows gold accent during compete mode tips
6. Tap progression strip — verify detail view expands with skill grid
7. Verify locked gates show correctly (dimmed, locked state)
8. Verify onboarding prompt shows on first panel open (beginner)
9. Tap "Intermediate" — verify skip ahead works (backend re-initializes, UI updates)
10. Dismiss onboarding — verify it doesn't reappear
11. Play enough hands to advance a skill — verify toast notification appears

**Regression tests:**
- Existing coach features work unchanged (stats, tips, Q&A, hand review, mode toggle)
- Guests never see progression UI
- Coach works identically when progression data is missing from stats response

---

## Files Summary

### New Files

| File | Purpose |
|------|---------|
| `react/react/src/components/mobile/ProgressionStrip.tsx` | Compact gate/skill header strip (~60 lines) |
| `react/react/src/components/mobile/ProgressionStrip.css` | Strip styling (~70 lines) |
| `react/react/src/components/mobile/ProgressionDetail.tsx` | Expandable skill grid by gate (~90 lines) |
| `react/react/src/components/mobile/ProgressionDetail.css` | Skill card and gate section styling (~120 lines) |

### Modified Files

| File | Changes |
|------|---------|
| `react/react/src/types/coach.ts` | Add progression types, extend CoachStats (~60 lines added) |
| `react/react/src/hooks/useCoach.ts` | Add progression state, fetchProgression, skipAhead, skill unlock detection (~70 lines added) |
| `react/react/src/components/mobile/CoachPanel.tsx` | Add progression strip, detail toggle, onboarding prompt, new props (~50 lines added) |
| `react/react/src/components/mobile/CoachPanel.css` | Onboarding prompt styles (~40 lines added) |
| `react/react/src/components/mobile/CoachBubble.tsx` | Add coachingMode prop, mode label, accent class (~15 lines added) |
| `react/react/src/components/mobile/CoachBubble.css` | Learn/compete accent styles, mode label (~30 lines added) |
| `react/react/src/components/mobile/MobilePokerTable.tsx` | Wire new props, skill unlock toasts (~25 lines added) |

### Backend Files (Small Changes)

| File | Changes |
|------|---------|
| `flask_app/services/coach_engine.py` | Add `name`, `description`, `gate` to `progression.skill_states` serialization (~5 lines) |
| `flask_app/routes/coach_routes.py` | Add `name`, `description` to skill_states and gate_progress in `/progression` response (~10 lines) |

### Unchanged Files

| File | Why |
|------|-----|
| `react/react/src/components/mobile/StatsBar.tsx` | Keep clean — progression lives in strip above |
| `react/react/src/components/mobile/CoachButton.tsx` | No changes needed |

---

## Implementation Batches

Implement in this order, verifying TypeScript compiles after each batch:

| Batch | Steps | What | Key Files |
|-------|-------|------|-----------|
| 0 | 0 | Backend metadata enrichment | `coach_engine.py`, `coach_routes.py` |
| 1 | 1 | Type foundation | `types/coach.ts` |
| 2 | 2 | Hook extensions | `useCoach.ts` |
| 3 | 3-4 | New components (strip + detail) | `ProgressionStrip.tsx`, `ProgressionDetail.tsx` + CSS |
| 4 | 5 | Bubble mode awareness | `CoachBubble.tsx` + CSS |
| 5 | 6 | CoachPanel integration | `CoachPanel.tsx` + CSS |
| 6 | 7 | MobilePokerTable wiring + toasts | `MobilePokerTable.tsx` |
| 7 | 8 | Testing | TypeScript check + manual |

---

## Design Specification

### Color Coding for Skill States

| State | Color | Token | Usage |
|-------|-------|-------|-------|
| Introduced | Neutral grey | `--color-text-muted` | Border, label, dot outline |
| Practicing | Gold | `--color-gold` | Border, label, progress bar, dot outline |
| Reliable | Emerald | `--color-emerald` | Border, label, progress bar, filled dot |
| Automatic | Sapphire | `--color-sapphire` | Border, label, progress bar, filled dot |
| Locked | Disabled | `--color-text-disabled` | Dimmed card (opacity 0.4) |

### Coaching Mode Bubble Accents

| Mode | Border | Icon Background | Label Color | Label Text |
|------|--------|----------------|-------------|------------|
| Learn | `rgba(52, 211, 153, 0.25)` | `rgba(52, 211, 153, 0.15)` | `--color-emerald` | "Coach Tip" |
| Compete | `rgba(212, 165, 116, 0.25)` | `rgba(212, 165, 116, 0.15)` | `--color-gold` | "Your Stats" |

### Component Layout in CoachPanel

```
┌──────────────────────────────────────┐
│ ═══ (drag handle)                     │
│ POKER COACH          [Auto] [×]       │
├──────────────────────────────────────┤
│ ◆ GATE 1 · Preflop Fundamentals  [▾] │  ← ProgressionStrip
│   ► Fold Trash Hands · 75%            │
│ ▓▓▓▓▓▓▓▓░░░░ (gate progress bar)     │
├──────────────────────────────────────┤
│ [Already know the basics? Skip...]    │  ← Onboarding (conditional)
├──────────────────────────────────────┤
│ Equity  Pot Odds  Hand    Outs        │  ← StatsBar (unchanged)
│ 45%     2.5:1     Pair    8           │
│ [Recommendation: CALL]                │
├──────────────────────────────────────┤
│                                       │
│ Messages / ProgressionDetail (toggle) │
│                                       │
├──────────────────────────────────────┤
│ [Ask your coach...          ] [Send]  │
└──────────────────────────────────────┘
```

---

## Key Design Decisions

1. **Progression data source**: `stats.progression` (from existing `/stats` call) is the primary source for per-turn data. The full `/progression` endpoint is only called once when the panel opens. This avoids extra API calls.

2. **Skill metadata from backend**: Skill names, descriptions, and gate assignments are embedded in the API responses (from `skill_definitions.py`). No static frontend registry needed — the backend is the single source of truth. ~15 lines of backend change.

3. **Skill unlock detection**: Compare `prevSkillStatesRef` with new `skill_states` from stats. A new key appearing with state `introduced` means a gate just unlocked and new skills appeared. This is more reliable than comparing state transitions (which require tracking all skills across turns).

4. **Graceful degradation**: All progression UI is optional. If `stats.progression` is missing (guest user, API error, server restart), the coach works identically to pre-M4. Components check `progression != null` before rendering.

5. **No new API calls per turn**: The progression data piggybacks on the existing `/stats` fetch. The only new fetch is `/progression` (once, on panel open) for the detailed view.

6. **localStorage for onboarding dismissal**: Stores `coach_onboarding_dismissed` flag. If localStorage is unavailable, the prompt shows every time the panel opens — acceptable degradation.

7. **Toast for skill unlocks**: Simple, uses existing `react-hot-toast` infrastructure. Styled to match the poker aesthetic (dark glass background, emerald border). One toast per new skill, auto-dismisses after 4 seconds.

8. **Silent coaching mode and bubbles**: When `coaching_mode: "silent"` (all skills Automatic), the backend returns no proactive tip — so the bubble naturally won't appear (no tip text). No special frontend handling needed.

9. **Post-action evaluation is server-side**: The backend evaluates player actions in `game_routes.py:_evaluate_coach_progression()` on both REST and Socket paths. No frontend API call for evaluation is needed — progression updates happen automatically.

10. **Notification coexistence**: Three notification zones exist — AI chat bubbles (top, z-100), coach bubble (bottom, z-160), and toasts (top-right, z-9999). They occupy different screen regions and are brief. Ship as-is; add a priority queue if playtesting reveals noise issues.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Backend response shape changes break frontend types | Types are straightforward. TypeScript check (`--ts`) catches mismatches immediately. |
| CoachPanel gets visually crowded (strip + onboarding + stats + messages) | Onboarding is one-time, dismissible. Strip is 1 line (compact). Detail view replaces messages. |
| Skill unlock toast fires multiple times | `dismissSkillUnlock` removes from queue immediately after showing. `useRef` prevents re-triggering on re-renders. |
| progressionFull not loaded when user expands detail view | Show `progressionLite` (from stats) as fallback — has skill states but not gate_progress. Detail view works with partial data. |
| Bubble mode accent not visible (subtle difference) | The mode label text ("Coach Tip" vs "Your Stats") provides a textual cue in addition to color change. |
| Notification overlap on mobile (AI chat bubble at top z-100, coach bubble at bottom z-160, toast at top-right z-9999) | Ship as-is. The three occupy different screen zones and are brief. If playtesting shows it's too noisy, add a queue-based priority system (coach > AI chat during player turns, toasts between hands only). |

---

## Verification

1. `python3 scripts/test.py --ts` — TypeScript compiles cleanly
2. `python3 scripts/test.py --all` — no Python test regressions
3. Manual: play as guest — coach works, no progression UI
4. Manual: play as authenticated beginner — progression strip shows, onboarding prompt appears
5. Manual: skip ahead to intermediate — verify skills re-initialize, strip updates
6. Manual: play 25+ hands — verify skill accuracy updates, skill state changes
7. Manual: verify learn mode bubble = emerald, compete mode bubble = gold
8. Manual: tap progression strip — detail view shows all gates with skill cards
9. Manual: verify toast appears when new skill is introduced
10. Manual: verify panel works after server restart (session memory cleared, persisted state intact)
