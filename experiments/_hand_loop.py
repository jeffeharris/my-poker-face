"""Shared deterministic hand-driving skeleton for the sim eval instruments.

`simulate_bb100.run_hand` and `measure_passivity.run_passivity_hand` were
byte-for-byte copies of the same action-driving loop (run_until / run_it_out /
decide_action / play_turn / advance_to_next_active_player / per-street sim
aggressor bookkeeping / MAX_ACTIONS_PER_HAND fallback), differing only in the
instrumentation woven between the steps. measure_passivity's own docstring said
it "Mirrors simulate_bb100.run_hand's action driving". This module extracts that
genuinely-shared skeleton so the two callers can't silently drift.

The loop is parameterized by a small set of optional callback hooks; each caller
passes its own instrumentation through them. The skeleton owns ONLY the parts
that were verified identical in both copies:

  - the while/run_until/terminal-break frame
  - the run_it_out auto-advance block
  - current_player / controller resolution + ``controller.state_machine = sm``
  - ``decision = controller.decide_action(...)`` and action/raise_to/phase_name
  - the play_turn call and advance_to_next_active_player + game_state writeback
  - the per-hand reset of the hero controller's ``_sim_*`` fields at hand start
  - the accepted-action ``_sim_last_preflop_aggressor`` / ``_sim_recent_aggressor``
    / ``_sim_hero_bet_by_street`` / ``_sim_opp_bet_by_street`` bookkeeping, which
    was identical logic in both callers (preflop set on raise/all_in; postflop
    per-street live aggressor reset on street change; hero/opp split on bet/
    raise/all_in)
  - the action_count / MAX_ACTIONS_PER_HAND termination

Everything caller-specific (equity logging, c-bet detection, opponent-model
observation, passivity stats, node traces, the passivity snapshot-clear) lives
in the caller via the hooks. Parity with the pre-refactor copies is the hard
requirement: the hooks fire at exactly the points the inline code used to run.
"""

from typing import Callable, Dict, List, Optional, Protocol

from poker.poker_game import advance_to_next_active_player, play_turn
from poker.poker_state_machine import PokerPhase, PokerStateMachine

# Re-exported by callers; kept here so the skeleton is self-contained.
TERMINAL_PHASES = {PokerPhase.HAND_OVER, PokerPhase.GAME_OVER}
MAX_ACTIONS_PER_HAND = 100

_AGGRESSIVE = ('bet', 'raise', 'all_in')
_POSTFLOP_STREETS = ('FLOP', 'TURN', 'RIVER')


class _Controller(Protocol):
    player_name: str

    def decide_action(self): ...


def _run_it_out_advance(sm: PokerStateMachine) -> None:
    """Advance past the betting round on an all-in runout.

    Identical in both original copies: clear the flags and bump the phase so
    the SM deals the remaining community cards (simply clearing the flags would
    let run_betting_round_transition re-set them → infinite loop).
    """
    gs = sm.game_state
    sm.game_state = gs.update(run_it_out=False, awaiting_action=False)
    next_phase = {
        PokerPhase.PRE_FLOP: PokerPhase.DEALING_CARDS,
        PokerPhase.FLOP: PokerPhase.DEALING_CARDS,
        PokerPhase.TURN: PokerPhase.DEALING_CARDS,
        PokerPhase.RIVER: PokerPhase.EVALUATING_HAND,
    }.get(sm.phase, PokerPhase.EVALUATING_HAND)
    sm.phase = next_phase


def reset_hero_sim_state(hero_controller) -> None:
    """Reset the hero controller's per-hand sim aggressor / line fields.

    Production paths get this via MemoryManager.on_hand_start; the sims bypass
    MM so they drive it directly at hand start. No-op when hero_controller is
    None. Identical in both original copies.
    """
    if hero_controller is None:
        return
    hero_controller._sim_last_preflop_aggressor = None
    hero_controller._sim_recent_aggressor = None
    hero_controller._sim_hero_bet_by_street = {}
    hero_controller._sim_opp_bet_by_street = {}


def drive_hand(
    sm: PokerStateMachine,
    controllers: List[_Controller],
    *,
    hero_name: Optional[str] = None,
    hero_controller=None,
    pre_decision: Optional[Callable] = None,
    on_decision: Optional[Callable] = None,
    post_action: Optional[Callable] = None,
    max_actions: int = MAX_ACTIONS_PER_HAND,
    on_max_actions: Optional[Callable[[], None]] = None,
) -> Dict[str, int]:
    """Drive one complete hand to completion, returning {name: final_stack}.

    The shared skeleton; the caller supplies instrumentation via hooks:

      pre_decision(controller, current_player, phase_name)
          Called right before ``controller.decide_action()``. (measure_passivity
          uses this to clear the hero's stale pipeline snapshot.)

      on_decision(current_player, controller, action, raise_to, phase_name, gs,
                  sim_current_street, decision)
          Called after the decision is read but BEFORE ``play_turn`` — the slot
          for "capture state the actor saw" instrumentation (board snapshots,
          opponent-model observation, c-bet detector seeding, passivity stat
          recording, node traces, verbose logging). ``sim_current_street`` is
          the street tracked by the previous accepted action (None on the first
          action of the hand); simulate_bb100 needs it to derive
          was_facing_bet at the same point the inline code did, before the
          skeleton updates it post-play_turn.

      post_action(current_player, action, raise_to, phase_name, gs, new_gs)
          Called after ``play_turn`` (with both the pre- and post-action game
          states) but before advance — for c-bet response application, opp
          bet-by-street trackers, etc. The shared ``_sim_*`` aggressor
          bookkeeping is applied by the skeleton itself (it was identical in
          both copies); post_action runs after that bookkeeping.

    ``hero_controller`` drives the ``_sim_*`` bookkeeping (reset at hand start +
    accepted-action updates). When None, the hero fields aren't touched (matches
    a None hero in the originals). Pass the controller resolved from hero_name.
    """
    controller_map = {c.player_name: c for c in controllers}
    action_count = 0

    reset_hero_sim_state(hero_controller)
    sim_current_street: Optional[str] = None

    while sm.phase not in TERMINAL_PHASES:
        sm.run_until(list(TERMINAL_PHASES))

        if sm.phase in TERMINAL_PHASES:
            break

        gs = sm.game_state

        if gs.run_it_out:
            _run_it_out_advance(sm)
            continue

        current_player = gs.current_player
        controller = controller_map[current_player.name]
        controller.state_machine = sm

        phase_name = sm.phase.name

        if pre_decision is not None:
            pre_decision(controller, current_player, phase_name)

        decision = controller.decide_action()
        action = decision['action']
        raise_to = decision.get('raise_to', 0) or 0

        if on_decision is not None:
            on_decision(
                current_player,
                controller,
                action,
                raise_to,
                phase_name,
                gs,
                sim_current_street,
                decision,
            )

        new_gs = play_turn(gs, action, raise_to)

        # ── Accepted-action sim aggressor / multi-street bookkeeping ─────────
        # Set AFTER play_turn so we mirror MemoryManager.on_action's "accepted
        # action" semantics (controller intent the engine rejects must not move
        # the c-bet aggressor). Identical logic in both original copies.
        if (
            phase_name == 'PRE_FLOP'
            and action in ('raise', 'all_in')
            and hero_controller is not None
        ):
            hero_controller._sim_last_preflop_aggressor = current_player.name

        if hero_controller is not None:
            if sim_current_street != phase_name:
                hero_controller._sim_recent_aggressor = None
                sim_current_street = phase_name
            if phase_name in _POSTFLOP_STREETS and action in _AGGRESSIVE:
                hero_controller._sim_recent_aggressor = current_player.name
                if current_player.name == hero_name:
                    hero_controller._sim_hero_bet_by_street[phase_name] = True
                else:
                    hero_controller._sim_opp_bet_by_street[phase_name] = True

        if post_action is not None:
            post_action(current_player, action, raise_to, phase_name, gs, new_gs)

        advanced = advance_to_next_active_player(new_gs)
        sm.game_state = advanced if advanced is not None else new_gs

        action_count += 1
        if action_count >= max_actions:
            if on_max_actions is not None:
                on_max_actions()
            break

    return {p.name: p.stack for p in sm.game_state.players}
