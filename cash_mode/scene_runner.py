"""Headless **scene runner** — drive a `TableScene` to completion in-process.

Scene-0 proved the rigged-narrated-poker primitive, but it could only be verified
by a human playing it: scene execution was entangled with the live Flask betting
loop, so the finale ("Sal busts Larry to 0") and every future scene shipped on
faith. This module closes that gap (Scene Engine vision, Pillar 1 — testability).

It does two things:

1. **Pure decision helpers** (`judge_hand`, `verdict_line`, `setup_lines`,
   `fish_street_line`, `holes_by_name`, `resolve_cast_action`) — the scene logic
   pulled out of `flask_app/handlers/game_handler.py` so the live handler and the
   headless runner call the **same** code. The live game keeps the Flask I/O
   (send_message / repos / socketio); these decide *what* happens. No divergence.

2. **`run_scene`** — owns a real `PokerStateMachine` and drives a scene exactly
   like `progress_game` does, minus Flask: deal the rigged decks, run the cast on
   the script, inject the hero's choices, fire per-street fish tells, judge each
   teaching hand at the boundary, and complete the scene — capturing every
   narration beat as a structured timeline and returning a `SceneResult`. This is
   what makes a scene assertable in CI (and every fork testable).

Plus `validate_scene` — authoring safety: a scene with a duplicate card, a bad
cast intent, or a judged hand missing its verdict line fails in CI, not a
playtest (Pillar 4, the slice the runner enables).

Flask-free on purpose: the runner reports the completion-effect *key*
(`on_complete`) as data — firing the real vouch (a DB write) stays the live
handler's job, already covered by its own tests. Cast funding is in-memory big
stacks (no bankroll), so conservation (chips transfer, never mint) is asserted
directly rather than mocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from cash_mode import career_scene

# ---------------------------------------------------------------------------
# Pure decision helpers — shared with the live game_handler (no divergence).
# ---------------------------------------------------------------------------

# Intents `resolve_scripted_action` understands (for validation + the hero API).
KNOWN_INTENTS = frozenset({"fold", "limp", "stay", "passive", "shove", "bluff", "bet"})


def judge_hand(hand, hero_folded: bool) -> Optional[bool]:
    """Did the hero pass the lesson? None for a hand that carries no lesson.

    'folded' lessons (discipline) pass when the hero laid it down; the rest
    (value / bluff-catch) pass when the hero stayed in. This is the binary
    "judge" today; it returns a value (not a void side-effect) so a future beat
    **router** can replace it without touching callers (Scene Engine, Pillar 2).
    """
    if not getattr(hand, "lesson", None):
        return None
    return hero_folded if hand.pass_when == "folded" else (not hero_folded)


def verdict_line(hand, passed: bool) -> str:
    """The mentor's post-hand line for a judged hand (sal_pass / sal_fail)."""
    return hand.sal_pass if passed else hand.sal_fail


# Outcome predicates a hand's `sal_verdict_branches` can route on — the first
# slice of the Scene Engine beat-router (Pillar 2). Each reads the finished-hand
# outcome dict from `hand_outcome`.
_OUTCOME_PREDICATES = {
    "fish_folded": lambda o: bool(o.get("fish_folded")),
    "hero_folded": lambda o: bool(o.get("hero_folded")),
    "showdown": lambda o: bool(o.get("showdown")),
}


def hand_outcome(hand, players, roles: Dict[str, str]) -> Dict[str, bool]:
    """Observable outcome of a finished hand, for verdict branching.

    `roles` is {role: live_name}. Returns {hero_folded, fish_folded, showdown},
    where showdown ≈ neither the hero nor the fish folded (both saw it through).
    """
    by_name = {p.name: p for p in players}

    def _folded(role: str) -> bool:
        p = by_name.get(roles.get(role) or "")
        return p is None or p.is_folded

    hero_folded = _folded("hero")
    fish_folded = _folded("fish")
    return {
        "hero_folded": hero_folded,
        "fish_folded": fish_folded,
        "showdown": not hero_folded and not fish_folded,
    }


def select_verdict_line(hand, passed: bool, outcome: Dict[str, bool]) -> str:
    """The mentor's post-hand line, with outcome-conditional branches.

    A hand may declare `sal_verdict_branches` = ((predicate, line), ...); the
    first predicate that holds for `outcome` wins, else the binary pass/fail line.
    This is the choice/outcome-edge slice of the Scene Engine router (Pillar 2) —
    the hero's pass/fail *count* is unchanged (`judge_hand`); only narration forks.
    """
    for cond, line in getattr(hand, "sal_verdict_branches", ()) or ():
        pred = _OUTCOME_PREDICATES.get(cond)
        if pred and pred(outcome):
            return line
    return verdict_line(hand, passed)


def setup_lines(hand) -> tuple:
    """The (mentor, fish) opening lines for a hand — emitted as it's set up."""
    return (getattr(hand, "sal_setup", "") or "", getattr(hand, "fish_setup", "") or "")


def fish_street_line(hand, phase_name: str) -> str:
    """The fish's per-street tell for `phase_name`, or "" (rigged hands only)."""
    if not getattr(hand, "rigged", False):
        return ""
    return (getattr(hand, "fish_streets", None) or {}).get(phase_name, "") or ""


def holes_by_name(hand, roles: Dict[str, str]) -> Dict[str, tuple]:
    """Map a rigged hand's role→cards to the live name→cards the deck rig wants.

    Keyed by player NAME so the engine's `provide_hand_holes` stays immune to the
    per-hand button rotation. Roles with no seated name are dropped.
    """
    from core.card import Card

    out: Dict[str, tuple] = {}
    for role, shorts in (hand.holes or {}).items():
        name = roles.get(role)
        if name:
            out[name] = tuple(Card.from_short(s) for s in shorts)
    return out


def _plan_entry(entry):
    """Normalize a plan entry to (intent, size_tag). Entry is a bare intent string
    or an (intent, size_tag) tuple."""
    if isinstance(entry, tuple | list):
        return entry[0], (entry[1] if len(entry) > 1 else None)
    return entry, None


def resolve_cast_action(
    hand, plan: dict, game_state, current_player, phase_name: str
) -> Optional[dict]:
    """Scripted `{'action','amount'}` for a cast member on a rigged hand, or None.

    `plan` is the role's per-phase plan (`fish_plan` / `mentor_plan`). None means
    "no scripted move this phase — let the bot decide" (the live handler's
    fallback; the runner's is a safe check/fold). Mirrors the extraction of
    `game_handler._scene_scripted_action` so both resolve identically.
    """
    if not getattr(hand, "rigged", False) or not plan:
        return None
    entry = plan.get(phase_name)
    if not entry:
        return None
    intent, size_tag = _plan_entry(entry)
    size_frac = career_scene.SIZE_FRAC.get(size_tag, 0.7)
    return career_scene.resolve_scripted_action(
        intent=intent,
        valid_actions=game_state.current_player_options,
        cost_to_call=game_state.highest_bet - current_player.bet,
        pot_total=int((game_state.pot or {}).get("total", 0)),
        stack=current_player.stack,
        big_blind=game_state.current_ante,
        size_frac=size_frac,
        allow_bust=getattr(hand, "bust_ok", False),
        current_bet=current_player.bet,
    )


# ---------------------------------------------------------------------------
# Hero choice providers — express the hero's line as an intent, resolved against
# the live legal actions the same way the cast's intents are.
# ---------------------------------------------------------------------------

HeroChoice = Callable[[object, object, object], Optional[dict]]


def hero_intent(intent: str) -> HeroChoice:
    """A hero provider that plays one intent every decision.

    'passive' = check/call down to showdown (stay in — passes value/bluff-catch).
    'fold'    = check when free, else lay it down (passes discipline; sits out
                the finale). Both resolve against the live legal actions.
    """

    def _choose(_hand, game_state, player) -> Optional[dict]:
        action = career_scene.resolve_scripted_action(
            intent=intent,
            valid_actions=game_state.current_player_options,
            cost_to_call=game_state.highest_bet - player.bet,
            pot_total=int((game_state.pot or {}).get("total", 0)),
            stack=player.stack,
            big_blind=game_state.current_ante,
            # The hero never busts the runner cast by accident; a 'passive' hero
            # folds to an all-in unless we explicitly allow it (we don't here).
            allow_bust=False,
            current_bet=player.bet,
        )
        return action

    return _choose


def hero_by_lesson(by_lesson: Dict[str, str], *, default: str = "fold") -> HeroChoice:
    """A hero provider keyed by a hand's `lesson` → intent (per-hand `default`).

    Lets a test give the hero a different line per teaching hand in one provider —
    e.g. ``{'bluff_catch': 'passive', 'discipline': 'fold'}`` — to drive each fork.
    Non-teaching (filler / finale) hands use `default`.
    """
    cache: Dict[str, HeroChoice] = {}

    def _choose(hand, game_state, player) -> Optional[dict]:
        intent = by_lesson.get(str(getattr(hand, "lesson", None) or ""), default)
        chooser = cache.get(intent)
        if chooser is None:
            chooser = hero_intent(intent)
            cache[intent] = chooser
        return chooser(hand, game_state, player)

    return _choose


def _default_cast_action(game_state) -> dict:
    """The runner's "bot": check when free, else fold. Keeps unscripted cast
    members cheap and non-busting (hand 0, unplanned phases)."""
    options = set(game_state.current_player_options)
    if "check" in options:
        return {"action": "check", "amount": 0}
    return {"action": "fold", "amount": 0}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NarrationEvent:
    trigger: str  # 'hand_open' | 'street' | 'verdict' | 'fish_react' | 'graduation'
    role: str  # 'mentor' | 'fish'
    line: str
    hand_idx: int


@dataclass
class SceneResult:
    final_stacks: Dict[str, int]  # name -> stack at scene end
    busted: List[str]  # names at 0 chips after the scene
    passed: int  # teaching hands the hero passed
    hands_played: int
    completed: bool  # the script ran to the end
    on_complete: Optional[str]  # the scene's completion-effect key, if reached
    narration: List[NarrationEvent] = field(default_factory=list)
    roles: Dict[str, str] = field(default_factory=dict)  # role -> name
    initial_total: int = 0  # total chips in play at seat-down
    chips_injected: int = 0  # cast top-up chips (the bankroll stand-in)

    @property
    def conserved(self) -> bool:
        """Chips moved, never minted: final total == seeded total + top-ups."""
        return sum(self.final_stacks.values()) == self.initial_total + self.chips_injected

    def lines(self, *, role: Optional[str] = None, trigger: Optional[str] = None) -> List[str]:
        """Convenience: the narration text filtered by role and/or trigger."""
        return [
            e.line
            for e in self.narration
            if (role is None or e.role == role) and (trigger is None or e.trigger == trigger)
        ]


# ---------------------------------------------------------------------------
# The headless runner
# ---------------------------------------------------------------------------


def run_scene(
    scene,
    *,
    hero: HeroChoice,
    starting_stack: int = 100_000,
    big_blind: int = 50,
    fish_stack: int = 2_000,
    mentor_stack: int = 4_000,
    cast_topup: bool = True,
    max_hands: Optional[int] = None,
    max_steps_per_hand: int = 400,
) -> SceneResult:
    """Drive `scene` to completion in-process and return a `SceneResult`.

    The cast is seeded at `fish_stack` / `mentor_stack` and (when `cast_topup`)
    restored to those targets before each rigged hand — mirroring the live
    `_scene_top_up_cast`, whose whole point is that the mentor stays deep enough to
    cover the fish so the finale's `bust_ok` actually busts the fish (the mentor is
    seeded 2× the fish here, matching the live 4×/2× ratio). Those restores are
    counted in `chips_injected`, so conservation is checkable as
    `sum(final_stacks) == initial_total + chips_injected` (the in-memory stand-in
    for the bankroll-funded top-up — chips move, never mint). `hero` decides the
    human's action each turn (see `hero_intent` / `hero_by_lesson`).
    """
    from poker.poker_game import (
        advance_to_next_active_player,
        initialize_game_state,
        play_turn,
    )
    from poker.poker_state_machine import PokerPhase, PokerStateMachine

    # --- seat the cast: human = hero, each cast role = its persona_id as name ---
    cast = scene.cast or {}
    role_to_name: Dict[str, str] = {"hero": "You"}
    cast_names: List[str] = []
    for role, pid in cast.items():
        role_to_name[role] = pid
        cast_names.append(pid)
    name_to_role = {name: role for role, name in role_to_name.items()}

    gs = initialize_game_state(
        player_names=cast_names,
        human_name="You",
        starting_stack=starting_stack,
        big_blind=big_blind,
    )
    # Seat the cast at their target stacks (mentor deep enough to cover the fish).
    targets = {"fish": fish_stack, "mentor": mentor_stack}
    seeded = []
    for p in gs.players:
        target = targets.get(name_to_role.get(p.name) or "")
        seeded.append(p.update(stack=target) if target is not None else p)
    gs = gs.update(players=tuple(seeded))
    sm = PokerStateMachine(game_state=gs, record_snapshots=False)

    initial_total = sum(p.stack for p in sm.game_state.players)
    chips_injected = 0

    narration: List[NarrationEvent] = []
    idx = 0
    passed_count = 0
    hands_played = 0
    completed = False
    on_complete: Optional[str] = None

    def _topup_cast() -> int:
        """Restore the cast to their target stacks (mirror `_scene_top_up_cast`).
        Returns the chips injected. Only tops up (never removes)."""
        if not cast_topup:
            return 0
        injected = 0
        gstate = sm.game_state
        updated = list(gstate.players)
        for i, p in enumerate(updated):
            target = targets.get(name_to_role.get(p.name) or "")
            if target is not None and p.stack < target:
                injected += target - p.stack
                updated[i] = p.update(stack=target)
        if injected:
            sm.game_state = gstate.update(players=tuple(updated))
        return injected

    def _say(trigger: str, role: str, line: str, at: int) -> None:
        if line:
            narration.append(NarrationEvent(trigger=trigger, role=role, line=line, hand_idx=at))

    def _hero_choice(hand, game_state, player) -> dict:
        chosen = hero(hand, game_state, player)
        if not chosen:
            chosen = _default_cast_action(game_state)
        if chosen["action"] not in set(game_state.current_player_options):
            raise ValueError(
                f"hero chose illegal action {chosen['action']!r}; "
                f"legal={sorted(game_state.current_player_options)}"
            )
        return chosen

    def _advance_to_action_or_over() -> None:
        # Step off a finished hand so the next one is dealt (consuming any rig),
        # then run to the first decision or HAND_OVER.
        if sm.current_phase == PokerPhase.HAND_OVER:
            sm.advance_state()
        sm.run_until([PokerPhase.HAND_OVER])

    # --- opening hand setup (mirror _init_scene): hand 0's lines fire first. ---
    hand = scene.hand_for_index(0)
    if hand is not None:
        m, f = setup_lines(hand)
        _say("hand_open", "mentor", m, 0)
        _say("hand_open", "fish", f, 0)

    _advance_to_action_or_over()  # deal hand 0, reach its first decision

    # --- the hand loop ---------------------------------------------------------
    last_street: Optional[str] = None
    steps = 0
    while True:
        hand = scene.hand_for_index(idx)
        if hand is None:
            break

        # Play the current hand to HAND_OVER.
        last_street = None
        steps = 0
        while sm.current_phase != PokerPhase.HAND_OVER:
            steps += 1
            if steps > max_steps_per_hand:
                raise RuntimeError(f"scene_runner: hand {idx} did not terminate")

            phase = sm.current_phase
            phase_name = phase.name
            # Per-street fish tell — fire once when a community street is entered.
            if phase_name in ("FLOP", "TURN", "RIVER") and phase_name != last_street:
                last_street = phase_name
                _say("street", "fish", fish_street_line(hand, phase_name), idx)

            if not sm.awaiting_action:
                # Defensive: run_until stopped on a phase boundary, not an action.
                _advance_to_action_or_over()
                continue

            gstate = sm.game_state
            # Run-it-out: every remaining player is all-in, so there's no decision
            # to make — the board just runs out. The state machine hands control
            # back with run_it_out set; force the next phase exactly like
            # progress_game (RIVER → SHOWDOWN, else deal the next street).
            if gstate.run_it_out:
                next_phase = (
                    PokerPhase.SHOWDOWN if phase == PokerPhase.RIVER else PokerPhase.DEALING_CARDS
                )
                sm.game_state = gstate.update(awaiting_action=False, run_it_out=False)
                sm.phase = next_phase
                continue

            player = gstate.current_player
            if name_to_role.get(player.name) == "hero":
                chosen = _hero_choice(hand, gstate, player)
            else:
                role = name_to_role.get(player.name)
                plan = (hand.fish_plan if role == "fish" else hand.mentor_plan) if role else None
                chosen = resolve_cast_action(hand, plan or {}, gstate, player, phase_name)
                if chosen is None:
                    chosen = _default_cast_action(gstate)

            # Apply the action, then advance to the next active player (the state
            # machine's advance handles PHASE transitions, not the within-round
            # player hand-off — mirror progress_game's apply path exactly).
            gstate = play_turn(gstate, chosen["action"], int(chosen.get("amount", 0) or 0))
            advanced = advance_to_next_active_player(gstate)
            if advanced is not None:
                gstate = advanced
            sm.game_state = gstate
            sm.run_until([PokerPhase.HAND_OVER])

        hands_played += 1

        # --- HAND_OVER: judge, react, then advance (mirror _advance_scene) ----
        hero_name = role_to_name.get("hero")
        hero_player = next((p for p in sm.game_state.players if p.name == hero_name), None)
        hero_folded = hero_player is None or hero_player.is_folded
        passed = judge_hand(hand, hero_folded)
        if passed is not None:
            outcome = hand_outcome(hand, sm.game_state.players, role_to_name)
            _say("verdict", "mentor", select_verdict_line(hand, passed, outcome), idx)
            if passed:
                passed_count += 1
        _say("fish_react", "fish", getattr(hand, "fish_react", "") or "", idx)

        if max_hands is not None and hands_played >= max_hands:
            break

        # Advance the index; set up the next hand or complete the scene.
        idx += 1
        nxt = scene.hand_for_index(idx)
        if nxt is None:
            completed = True
            on_complete = scene.on_complete
            for line in scene.graduation_lines:
                _say("graduation", "mentor", line, idx)
            break

        # Top the cast back up + rig the upcoming hand (mirror _advance_scene: the
        # top-up keeps the mentor deep enough to cover the fish at the finale).
        if getattr(nxt, "rigged", False):
            from core.card import Card

            chips_injected += _topup_cast()
            board = tuple(Card.from_short(s) for s in nxt.board)
            sm.provide_hand_holes(holes_by_name(nxt, role_to_name), board)
        m, f = setup_lines(nxt)
        _say("hand_open", "mentor", m, idx)
        _say("hand_open", "fish", f, idx)

        _advance_to_action_or_over()  # deal the next hand, reach its first decision

    final_stacks = {p.name: p.stack for p in sm.game_state.players}
    busted = [name for name, stack in final_stacks.items() if stack == 0]
    return SceneResult(
        final_stacks=final_stacks,
        busted=busted,
        passed=passed_count,
        hands_played=hands_played,
        completed=completed,
        on_complete=on_complete,
        narration=narration,
        roles=role_to_name,
        initial_total=initial_total,
        chips_injected=chips_injected,
    )


# ---------------------------------------------------------------------------
# Authoring validation — an inconsistent scene fails in CI, not a playtest.
# ---------------------------------------------------------------------------

_VALID_PASS_WHEN = frozenset({"folded", "not_folded"})


def validate_scene(scene) -> List[str]:
    """Return a list of authoring errors for `scene` (empty = valid).

    Lifts the runtime checks (deck-collision, board length) to author/CI time and
    adds the ones a playtest would catch late: unknown cast intents, an invalid
    `pass_when`, a judged hand missing a verdict line, a hole role not in the cast.
    """
    errors: List[str] = []
    cast_roles = set(scene.cast or {}) | {"hero"}

    def _check_plan(label: str, plan: dict, where: str) -> None:
        for phase_name, entry in (plan or {}).items():
            intent, size_tag = _plan_entry(entry)
            if intent not in KNOWN_INTENTS:
                errors.append(f"{where}: {label}[{phase_name}] unknown intent {intent!r}")
            if size_tag is not None and size_tag not in career_scene.SIZE_FRAC:
                errors.append(f"{where}: {label}[{phase_name}] unknown size tag {size_tag!r}")

    for i, hand in enumerate(scene.script):
        where = f"{scene.scene_id}#{i}"
        if not getattr(hand, "rigged", False):
            continue

        # Board length.
        if len(hand.board) != 5:
            errors.append(f"{where}: rigged hand needs a 5-card board, got {len(hand.board)}")

        # Card collisions across all holes + board.
        cards: List[str] = []
        for role, shorts in (hand.holes or {}).items():
            if role not in cast_roles:
                errors.append(f"{where}: hole role {role!r} not in cast {sorted(cast_roles)}")
            cards.extend(shorts)
        cards.extend(hand.board)
        seen = set()
        for c in cards:
            key = c.upper()
            if key in seen:
                errors.append(f"{where}: card {c!r} placed more than once")
            seen.add(key)

        # Cast intents.
        _check_plan("fish_plan", hand.fish_plan, where)
        _check_plan("mentor_plan", hand.mentor_plan, where)

        # Lesson consistency.
        if getattr(hand, "lesson", None):
            if hand.pass_when not in _VALID_PASS_WHEN:
                errors.append(f"{where}: invalid pass_when {hand.pass_when!r}")
            if not (hand.sal_pass and hand.sal_fail):
                errors.append(f"{where}: judged hand missing sal_pass/sal_fail verdict line")

        # Verdict branches (the Pillar-2 slice): known predicate + non-empty line.
        for cond, line in getattr(hand, "sal_verdict_branches", ()) or ():
            if cond not in _OUTCOME_PREDICATES:
                errors.append(f"{where}: verdict branch unknown predicate {cond!r}")
            if not line:
                errors.append(f"{where}: verdict branch {cond!r} has an empty line")

    return errors
