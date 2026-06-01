"""End-to-end (data-level) wiring of the in-table scripted scene (Scene 0).

Exercises the generalized game-handler hook chain over well-tested pieces: role
resolution, the hand-boundary driver that pre-stacks the next teaching hand's
deck (by player name, rotation-immune), the deal producing the scripted cards,
and the scripted-action resolver for the cast. Scene 0 is the registered scene.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.flask, pytest.mark.integration]

from core.card import Card
from cash_mode import career_scene as cs
from cash_mode import table_scenes
from cash_mode.career_progression import SCENE0_FISH_ID, SCENE0_TABLE_ID, SAL_ID
from flask_app.handlers import game_handler as gh
from poker.poker_game import deal_hole_cards, initialize_game_state
from poker.poker_state_machine import PokerStateMachine, _deck_from_scripted_holes

SCENE0 = table_scenes.SCENE0


def _k(c):
    return (c.rank, c.suit)


def _keys(shorts):
    return {_k(Card.from_short(s)) for s in shorts}


def _scene0_game():
    gs = initialize_game_state(player_names=["Sal Moretti", "Loose Larry"], human_name="You")
    sm = PokerStateMachine(game_state=gs)
    game_data = {
        "cash_table_id": SCENE0_TABLE_ID,
        "cash_personality_ids": {"Sal Moretti": SAL_ID, "Loose Larry": SCENE0_FISH_ID},
        "state_machine": sm,
    }
    return game_data, sm


def test_scene_resolves_from_the_table():
    game_data, sm = _scene0_game()
    assert gh._scene_for_game("g1", game_data) is SCENE0
    assert table_scenes.is_scene_table(SCENE0_TABLE_ID)
    assert table_scenes.scene_for_table("cash-table-2-001") is None


def test_init_resolves_roles_and_seats():
    game_data, sm = _scene0_game()
    gh._init_scene("g1", game_data, sm, SCENE0)
    roles = game_data["scene_roles"]
    assert roles["hero"] == "You"
    assert roles["mentor"] == "Sal Moretti"
    assert roles["fish"] == "Loose Larry"
    assert game_data["scene_idx"] == 0
    # Seats line up with the player list (human first).
    assert game_data["scene_seats"]["hero"] == 0


def test_advance_pre_stacks_next_rigged_hand_and_it_deals():
    game_data, sm = _scene0_game()
    gh._init_scene("g1", game_data, sm, SCENE0)
    # End of hand 1 → driver advances to hand 2 (index 1, a rigged filler) and
    # pre-stacks its deck on the state machine.
    gh._advance_scene("g1", game_data, sm)
    assert game_data["scene_idx"] == 1
    # The rig is now provided as scripted holes keyed by player NAME (immune to
    # the per-hand button rotation), not a seat-indexed deck.
    assert sm._state.hand_holes_provided is True

    # Deal it: the machine consumes the provided deck and deals the scripted hand.
    expected_hero = {_k(c) for c in (Card.from_short(s) for s in cs.SCENE0_SCRIPT[1].holes["hero"])}
    for _ in range(60):
        if any(p.hand for p in sm.game_state.players):
            break
        sm = sm.advance()
    by_name = {p.name: p for p in sm.game_state.players}
    assert {_k(c) for c in by_name["You"].hand} == expected_hero


def test_bluff_catch_deck_deals_top_pair_vs_air():
    """Advancing to the bluff-catch hand pre-stacks Farha's top pair vs the air.

    Real-hand anchor (Moneymaker vs Farha): the HERO sits in Farha's seat with
    Q♠9♥ (top pair), and the fish barrels Moneymaker's king-high air (K♠7♥).
    """
    game_data, sm = _scene0_game()
    gh._init_scene("g1", game_data, sm, SCENE0)
    bluff_idx = next(i for i, h in enumerate(cs.SCENE0_SCRIPT) if h.lesson == "bluff_catch")
    while game_data["scene_idx"] < bluff_idx:
        gh._advance_scene("g1", game_data, sm)
    assert sm._state.hand_holes_provided is True
    for _ in range(60):
        if any(p.hand for p in sm.game_state.players):
            break
        sm = sm.advance()
    by_name = {p.name: p for p in sm.game_state.players}
    assert {_k(c) for c in by_name["You"].hand} == _keys(["Qs", "9h"])
    assert {_k(c) for c in by_name["Loose Larry"].hand} == _keys(["Ks", "7h"])


def test_fish_river_bluff_resolves_when_checked_to():
    """On the bluff-catch hand, Larry (fish) bets the river when checked to."""
    game_data, sm = _scene0_game()
    gh._init_scene("g1", game_data, sm, SCENE0)
    bluff_idx = next(i for i, h in enumerate(cs.SCENE0_SCRIPT) if h.lesson == "bluff_catch")
    game_data["scene_idx"] = bluff_idx  # the bluff-catch teaching hand is active

    # Stand Larry up as the current player on the river, checked to (no bet faced):
    # zero everyone's street bet so highest_bet == 0 and cost_to_call == 0.
    from poker.poker_state_machine import PokerPhase

    gs = sm.game_state
    larry_idx = next(i for i, p in enumerate(gs.players) if p.name == "Loose Larry")
    cleared = tuple(p.update(bet=0) for p in gs.players)
    gs = gs.update(
        players=cleared,
        current_player_idx=larry_idx,
        community_cards=tuple(Card.from_short(s) for s in ["9c", "2d", "6s", "8h", "3c"]),
    )
    sm = sm.with_game_state(gs).with_phase(PokerPhase.RIVER)
    game_data["state_machine"] = sm

    action = gh._scene_scripted_action(game_data, sm, sm.game_state.players[larry_idx], SCENE0)
    assert action is not None
    assert action["action"] == "raise"  # the over-bluff
    assert action["amount"] > 0


def test_fish_tell_fires_on_the_flop_of_the_discipline_hand(monkeypatch):
    """The per-STREET fish line is dispatched by the community-card hook: on the
    discipline hand Larry comes alive on the flop (when he makes the nut straight),
    not at hand open. Drives the flop directly and asserts the line is emitted."""
    from poker.poker_state_machine import PokerPhase

    game_data, sm = _scene0_game()
    gh._init_scene("g1", game_data, sm, SCENE0)  # sets scene_roles (the fish name)
    disc_idx = next(i for i, h in enumerate(cs.SCENE0_SCRIPT) if h.lesson == "discipline")
    game_data["scene_idx"] = disc_idx
    disc = cs.SCENE0_SCRIPT[disc_idx]

    said: list = []
    monkeypatch.setattr(gh, "_fish_say", lambda gid, gd, line: said.append(line))
    monkeypatch.setattr(gh, "send_message", lambda *a, **k: None)

    flop = tuple(Card.from_short(s) for s in disc.board[:3])
    sm = sm.with_game_state(sm.game_state.update(community_cards=flop)).with_phase(
        PokerPhase.FLOP
    )
    game_data["state_machine"] = sm

    gh.handle_phase_cards_dealt("g1", sm, sm.game_state, game_data)
    assert said == [disc.fish_streets["FLOP"]]


def test_no_fish_tell_on_a_street_without_a_scripted_line(monkeypatch):
    """The hook is silent on streets the hand doesn't script (turn here), and on
    ordinary (non-scene) hands — it only speaks when a line is keyed to the phase."""
    from poker.poker_state_machine import PokerPhase

    game_data, sm = _scene0_game()
    gh._init_scene("g1", game_data, sm, SCENE0)
    disc_idx = next(i for i, h in enumerate(cs.SCENE0_SCRIPT) if h.lesson == "discipline")
    game_data["scene_idx"] = disc_idx

    said: list = []
    monkeypatch.setattr(gh, "_fish_say", lambda gid, gd, line: said.append(line))
    monkeypatch.setattr(gh, "send_message", lambda *a, **k: None)

    board4 = tuple(Card.from_short(s) for s in cs.SCENE0_SCRIPT[disc_idx].board[:4])
    sm = sm.with_game_state(sm.game_state.update(community_cards=board4)).with_phase(
        PokerPhase.TURN
    )
    gh.handle_phase_cards_dealt("g1", sm, sm.game_state, game_data)
    assert said == []  # the discipline hand scripts no TURN line


def test_scene_busted_seat_is_not_refilled_with_a_stranger():
    """Regression: a busted AI at a scripted scene table must NOT be swapped for a
    random eligible persona — that's how 'Loose Larry' became 'a guy who tells too
    many dad jokes' after the finale busted him. The scene owns its cast, so
    `_refill_cash_seats` is a no-op for scene games (the scripted top-up rebuys the
    fish instead)."""
    game_data, sm = _scene0_game()
    # Bust Loose Larry (stack 0) — the state a generic refill would "fix".
    gs = sm.game_state
    gs = gs.update(
        players=tuple(p.update(stack=0) if p.name == "Loose Larry" else p for p in gs.players)
    )
    sm = sm.with_game_state(gs)
    game_data["state_machine"] = sm

    before = [(p.name, p.stack) for p in sm.game_state.players]
    gh._refill_cash_seats(SCENE0_TABLE_ID, game_data, sm)
    after = [(p.name, p.stack) for p in game_data["state_machine"].game_state.players]
    assert after == before  # nobody swapped in; Larry keeps his (busted) seat


def test_scripted_holes_follow_player_name_through_button_rotation():
    """Regression for the rig bug: cards are keyed by NAME, so the per-hand button
    rotation (reset_game_state_for_new_hand reorders the players tuple each hand)
    can't deal the hero's monster to whoever now sits in seat 0. Before the fix, a
    stale seat snapshot dealt Loose Larry the hero's hand.
    """
    gs = initialize_game_state(player_names=["Sal Moretti", "Loose Larry"], human_name="You")
    holes = {
        "You": (Card.from_short("7s"), Card.from_short("7h")),          # hero's set
        "Loose Larry": (Card.from_short("Kh"), Card.from_short("Qd")),  # fish's top pair
    }
    board = tuple(Card.from_short(s) for s in ["Ks", "7d", "2c", "9h", "4s"])
    # Move the button: rotate the players tuple so the hero is no longer seat 0.
    gs = gs.update(players=gs.players[1:] + gs.players[:1])  # [Sal, Larry, You]
    assert gs.players[0].name != "You"  # hero is genuinely not in seat 0 now
    deck = _deck_from_scripted_holes(gs.players, holes, board)
    dealt = deal_hole_cards(gs.update(deck=deck))
    by_name = {p.name: p for p in dealt.players}
    assert {_k(c) for c in by_name["You"].hand} == _keys(["7s", "7h"])
    assert {_k(c) for c in by_name["Loose Larry"].hand} == _keys(["Kh", "Qd"])


class _StubRepo:
    """Minimal CareerProgressRepository stand-in for the cold-load tests."""

    def __init__(self, scene_progress):
        self._sp = scene_progress
        self.saved = None

    def load(self, sandbox_id, owner_id):
        from poker.repositories.career_progress_repository import CareerProgress

        return CareerProgress(
            sandbox_id=sandbox_id, owner_id=owner_id, scene_progress=dict(self._sp)
        )

    def save(self, progress, now=None):
        self.saved = progress


def _wire_persistence(monkeypatch, repo):
    monkeypatch.setattr("flask_app.extensions.career_progress_repo", repo, raising=False)
    monkeypatch.setattr(gh, "_sandbox_id_for", lambda gd: "sb-1")
    from flask_app.services import game_state_service

    monkeypatch.setattr(game_state_service, "get_game_owner_info", lambda gid: ("owner-1", "Owner"))


def test_cold_load_restores_scene_position(monkeypatch):
    """A cold-load (in-memory scene state gone) RESTORES the persisted script
    position instead of restarting at hand 0 — so a backend restart mid-tutorial
    resumes where it left off rather than losing the rig."""
    repo = _StubRepo({"scene0": {"idx": 4, "passed": 2, "complete": False}})
    _wire_persistence(monkeypatch, repo)

    game_data, sm = _scene0_game()  # no scene_* keys = cold-loaded
    gh._init_scene("g1", game_data, sm, SCENE0)

    assert game_data["scene_idx"] == 4  # resumed, not reset to 0
    assert game_data["scene_passed"] == 2
    # Roles are still re-derived fresh from the live seating.
    assert game_data["scene_roles"]["hero"] == "You"
    assert game_data["scene_roles"]["fish"] == "Loose Larry"


def test_fresh_scene_starts_at_zero_and_persists(monkeypatch):
    """A genuinely new game (no persisted progress) starts at hand 0 and writes it."""
    repo = _StubRepo({})
    _wire_persistence(monkeypatch, repo)

    game_data, sm = _scene0_game()
    gh._init_scene("g1", game_data, sm, SCENE0)

    assert game_data["scene_idx"] == 0
    assert repo.saved is not None  # progress was persisted on fresh start
    assert repo.saved.scene_progress["scene0"]["idx"] == 0


def test_advance_persists_progress(monkeypatch):
    """Each hand-boundary advance persists the new position for cold-load safety."""
    repo = _StubRepo({})
    _wire_persistence(monkeypatch, repo)

    game_data, sm = _scene0_game()
    gh._init_scene("g1", game_data, sm, SCENE0)
    gh._advance_scene("g1", game_data, sm)

    assert game_data["scene_idx"] == 1
    assert repo.saved.scene_progress["scene0"]["idx"] == 1


def test_completed_scene_not_restored_as_mid_scene(monkeypatch):
    """A completed scene doesn't get resumed as if mid-tutorial."""
    repo = _StubRepo({"scene0": {"idx": 9, "passed": 3, "complete": True}})
    _wire_persistence(monkeypatch, repo)

    game_data, sm = _scene0_game()
    gh._init_scene("g1", game_data, sm, SCENE0)

    # complete → falls through to a fresh start rather than resuming at idx 9.
    assert game_data["scene_idx"] == 0


def test_scene_progress_round_trips_through_json():
    """CareerProgress.scene_progress survives serialize → deserialize."""
    from poker.repositories.career_progress_repository import CareerProgress

    cp = CareerProgress(
        sandbox_id="sb",
        owner_id="o",
        scene_progress={"scene0": {"idx": 3, "passed": 1, "complete": False}},
    )
    back = CareerProgress.from_row("sb", "o", cp.to_json())
    assert back.scene_progress == {"scene0": {"idx": 3, "passed": 1, "complete": False}}


def test_non_scene_game_is_untouched():
    gs = initialize_game_state(player_names=["A", "B"], human_name="You")
    sm = PokerStateMachine(game_state=gs)
    game_data = {"cash_table_id": "cash-table-2-001", "state_machine": sm}
    # Not a scene table → driver is a no-op, no scene state written.
    gh._advance_scene("g2", game_data, sm)
    assert "scene_idx" not in game_data
