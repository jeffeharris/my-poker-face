"""Build a poker state machine from a training scenario.

Phase 2 handles `TablePreset` — a normal freshly-initialized table at the
preset's seat count, stack depth, and blinds. This is the seam Phase 3's
`ScriptedSpot` (fixed hole cards + board, injected mid-hand via
`PokerStateMachine.from_saved_state`) will extend with a second branch.

Returns an un-advanced `StateMachineAdapter`; the caller wires controllers and
then calls `run_until_player_action()` (so hole cards exist before the memory
manager records the hand start).
"""

from __future__ import annotations

from .scenario import ScriptedSpot, TablePreset

# Board-card count required for each street — used to validate scripted spots.
_PHASE_BOARD_COUNT = {"PRE_FLOP": 0, "FLOP": 3, "TURN": 4, "RIVER": 5}


def _flat_blind_config(big_blind: int) -> dict:
    """Flat blinds — a stable practice table (no escalation), like cash."""
    return {"growth": 1.0, "hands_per_level": 999999, "max_blind": big_blind}


def build_table_preset_state_machine(preset: TablePreset, human_name: str, ai_names: list[str]):
    """Build an un-advanced StateMachineAdapter for a table-preset game."""
    from flask_app.game_adapter import StateMachineAdapter
    from poker.poker_game import initialize_game_state
    from poker.poker_state_machine import PokerStateMachine

    game_state = initialize_game_state(
        player_names=ai_names,
        human_name=human_name,
        starting_stack=preset.starting_stack,
        big_blind=preset.big_blind,
    )
    base = PokerStateMachine(
        game_state=game_state,
        blind_config=_flat_blind_config(preset.big_blind),
    )
    return StateMachineAdapter(base)


def build_scripted_spot_state_machine(
    spot: ScriptedSpot, human_name: str, ai_names: list[str], *, seed: int | None = None
):
    """Build an un-advanced StateMachineAdapter positioned at a scripted spot.

    Injects a hand-crafted mid-street state via `from_saved_state` (the same
    entry point cold-load uses). The hero (`human_name`) is seat 0 and is the
    one to act. Do NOT call `run_until_player_action()` afterward — the state is
    already at the human's decision.

    Hard asserts guard the ghost-seat / illegal-state classes this codebase is
    prone to; a malformed spot raises here (callers validate at library-load
    time so it never reaches a live game).
    """
    from core.card import Card
    from flask_app.game_adapter import StateMachineAdapter
    from poker.poker_game import Player, PokerGameState, create_deck
    from poker.poker_state_machine import PokerPhase, PokerStateMachine

    phase = spot.phase
    if phase not in _PHASE_BOARD_COUNT:
        raise ValueError(f"scripted spot: unknown phase {phase!r}")

    hero_hole = [Card.from_short(c) for c in spot.hero_hole]
    if len(hero_hole) != 2:
        raise ValueError("scripted spot: hero must have exactly 2 hole cards")
    community = [Card.from_short(c) for c in spot.community]
    if len(community) != _PHASE_BOARD_COUNT[phase]:
        raise ValueError(
            f"scripted spot: phase {phase} needs {_PHASE_BOARD_COUNT[phase]} board "
            f"cards, got {len(community)}"
        )

    n_villains = len(spot.villain_stacks_bb)
    if n_villains < 1:
        raise ValueError("scripted spot: need at least one villain")
    if len(ai_names) < n_villains:
        raise ValueError("scripted spot: not enough ai_names for the villain stacks")
    villain_names = ai_names[:n_villains]

    # Cards already on the table (hero hole + board + any pinned villain holes).
    placed: list[Card] = [*hero_hole, *community]
    villain_holes: list[list[Card]]
    if spot.villain_holes is not None:
        if len(spot.villain_holes) != n_villains:
            raise ValueError("scripted spot: villain_holes count != villain_stacks count")
        villain_holes = [[Card.from_short(c) for c in vh] for vh in spot.villain_holes]
        for vh in villain_holes:
            placed.extend(vh)

    # Remaining deck = full deck minus everything already placed. Card defines
    # __eq__ but not __hash__ (unhashable), so filter with list membership.
    full_deck = list(create_deck(shuffled=True, random_seed=seed))
    remaining = [c for c in full_deck if c not in placed]

    if spot.villain_holes is None:
        # Deal villains random hole cards off the top of the remaining deck.
        villain_holes = []
        for _ in range(n_villains):
            villain_holes.append([remaining.pop(0), remaining.pop(0)])

    deck = tuple(remaining)

    hero = Player(
        name=human_name,
        stack=spot.chips(spot.hero_stack_bb),
        is_human=True,
        bet=spot.chips(spot.hero_bet_bb),
        hand=tuple(hero_hole),
        has_acted=False,
    )
    villain_bets = spot.villain_bets_bb or [0.0] * n_villains
    villains = [
        Player(
            name=name,
            stack=spot.chips(spot.villain_stacks_bb[i]),
            is_human=False,
            bet=spot.chips(villain_bets[i] if i < len(villain_bets) else 0.0),
            hand=tuple(villain_holes[i]),
            has_acted=True,  # villains have already acted into this spot
        )
        for i, name in enumerate(villain_names)
    ]
    players = (hero, *villains)

    bb = spot.big_blind
    facing_bet = any(p.bet > hero.bet for p in villains)
    last_raise = max([bb, *(p.bet for p in villains)]) if facing_bet else bb

    game_state = PokerGameState(
        players=players,
        deck=deck,
        pot={"total": spot.chips(spot.pot_bb)},
        current_player_idx=0,  # hero acts
        current_dealer_idx=0,  # display-label only in Phase 3 (see ScriptedSpot)
        community_cards=tuple(community),
        current_ante=bb,
        last_raise_amount=last_raise,
        raises_this_round=1 if facing_bet else 0,
        pre_flop_action_taken=(phase != "PRE_FLOP"),
        awaiting_action=True,
    )

    # --- Ghost-seat / legality guards (the load-bearing invariants) ---
    assert game_state.players[
        game_state.current_player_idx
    ].is_human, "scripted spot factory: current player is not the human (ghost-seat risk)"
    assert game_state.awaiting_action, "scripted spot factory: not awaiting action"
    assert len(deck) == 52 - len(placed) - 2 * (
        n_villains if spot.villain_holes is None else 0
    ), "scripted spot factory: deck size inconsistent with placed cards"
    assert all(
        c not in deck for c in placed
    ), "scripted spot factory: a placed card is still in the deck"

    base = PokerStateMachine.from_saved_state(
        game_state,
        PokerPhase[phase],
        blind_config=_flat_blind_config(bb),
    )
    return StateMachineAdapter(base)
