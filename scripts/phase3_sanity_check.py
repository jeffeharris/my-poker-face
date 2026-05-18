"""Phase 3 sanity check — end-to-end relationship layer smoke test.

Runs synthetic completed hands through `AIMemoryManager._process_
relationship_events` with the relationship repo wired, then queries
`relationship_states` + `cash_pair_stats` to verify all four event
types populate. Hands chosen to exercise multiple events in
realistic combinations so the post-state can be sanity-checked
against the design doc's expected axis semantics.

Runs in ~1s, no LLM calls. Exits non-zero on regression.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime

from poker.equity_snapshot import EquitySnapshot, HandEquityHistory
from poker.memory.hand_history import (
    PlayerHandInfo,
    RecordedAction,
    RecordedHand,
    WinnerInfo,
)
from poker.memory.memory_manager import AIMemoryManager
from poker.repositories import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager


# -- Hand 1: heads-up BIG_WIN/BIG_LOSS accumulation ----------------------


def _build_big_hand(hand_number: int) -> RecordedHand:
    """Heads-up big pot — drives BIG_WIN/BIG_LOSS accumulation."""
    return RecordedHand(
        game_id="sanity",
        hand_number=hand_number,
        timestamp=datetime(2026, 5, 18, 12, hand_number, 0),
        players=(
            PlayerHandInfo(name="alice", starting_stack=1000, position="BTN", is_human=False),
            PlayerHandInfo(name="bob", starting_stack=1000, position="BB", is_human=False),
        ),
        hole_cards={"alice": ["Ah", "Ks"], "bob": ["7h", "2d"]},
        community_cards=("2c", "7d", "9s", "Th", "Jc"),
        actions=(
            RecordedAction(player_name="alice", action="raise", amount=400, phase="PRE_FLOP", pot_after=400),
            RecordedAction(player_name="bob", action="call", amount=400, phase="PRE_FLOP", pot_after=800),
        ),
        winners=(WinnerInfo(name="alice", amount_won=800, hand_name="Pair", hand_rank=8),),
        pot_size=800,
        was_showdown=True,
    )


# -- BAD_BEAT hand: heads-up favorite lost to a river suckout ------------


def _build_bad_beat_hand(hand_number: int) -> RecordedHand:
    """alice is 82% on the turn with AA on a dry board; bob hits a
    running flush with 76s by the river. The classic bad beat.
    """
    return RecordedHand(
        game_id="sanity",
        hand_number=hand_number,
        timestamp=datetime(2026, 5, 18, 14, hand_number, 0),
        players=(
            PlayerHandInfo(name="alice", starting_stack=1000, position="BTN", is_human=False),
            PlayerHandInfo(name="bob", starting_stack=1000, position="BB", is_human=False),
        ),
        hole_cards={"alice": ["Ah", "Ad"], "bob": ["7s", "6s"]},
        community_cards=("2c", "5s", "8s", "Tc", "Js"),
        actions=(
            RecordedAction(player_name="alice", action="raise", amount=300, phase="PRE_FLOP", pot_after=300),
            RecordedAction(player_name="bob", action="call", amount=300, phase="PRE_FLOP", pot_after=600),
            RecordedAction(player_name="alice", action="bet", amount=200, phase="FLOP", pot_after=800),
            RecordedAction(player_name="bob", action="call", amount=200, phase="FLOP", pot_after=1000),
        ),
        winners=(WinnerInfo(name="bob", amount_won=1000, hand_name="Flush", hand_rank=4),),
        pot_size=1000,
        was_showdown=True,
    )


def _build_bad_beat_equity_history(hand_number: int) -> HandEquityHistory:
    """Equity arc for the bad-beat hand: alice ahead all the way until
    the river bricks her aces."""
    def snap(player, street, equity):
        return EquitySnapshot(
            player_name=player, street=street, equity=equity,
            hole_cards=(), board_cards=(), was_active=True,
        )
    return HandEquityHistory(
        hand_history_id=None, game_id="sanity",
        hand_number=hand_number,
        snapshots=(
            snap("alice", "PRE_FLOP", 0.82),
            snap("bob", "PRE_FLOP", 0.18),
            snap("alice", "FLOP", 0.78),
            snap("bob", "FLOP", 0.22),
            snap("alice", "TURN", 0.82),
            snap("bob", "TURN", 0.18),
            snap("alice", "RIVER", 0.0),
            snap("bob", "RIVER", 1.0),
        ),
    )


# -- Hand 2: 3-way with bluff catch + folded winner ----------------------


def _build_threway_drama_hand(hand_number: int) -> RecordedHand:
    """3-way pot exercising BIG_WIN/BIG_LOSS + HERO_CALL + BLUFFED_OFF.

    Board: 2c 5h 8c Tc Jd (no obvious draws, no board pair)
    Hole cards:
      alice: AA  → pair of aces (rank 9), strongest
      bob:   76s → high card jack (rank 10), bluffing
      carol: KK  → pair of kings (rank 9), would beat bob

    Action:
      preflop:  alice raise 100, bob + carol call    (pot 300)
      flop:     bob bet 150, carol FOLD (bluffed off), alice call (pot 600)
      turn:     check / check                         (pot 600)
      river:    bob bet 200, alice CALL (hero call)   (pot 1000)
      showdown: alice's AA beats bob's high card.

    Pot 1000 > 750 (0.75 * avg stack 1000) → big-pot threshold met.
    Chip flow allocation:
      alice's net gain = 1000 - 450 (her contrib) = 550
      Split proportionally across losers:
        bob:   450/550 × 550 = 450 to alice
        carol: 100/550 × 550 = 100 to alice
    """
    return RecordedHand(
        game_id="sanity",
        hand_number=hand_number,
        timestamp=datetime(2026, 5, 18, 13, hand_number, 0),
        players=(
            PlayerHandInfo(name="alice", starting_stack=1000, position="BTN", is_human=False),
            PlayerHandInfo(name="bob", starting_stack=1000, position="BB", is_human=False),
            PlayerHandInfo(name="carol", starting_stack=1000, position="SB", is_human=False),
        ),
        hole_cards={
            "alice": ["Ah", "Ad"],
            "bob": ["7s", "6s"],
            "carol": ["Ks", "Kd"],
        },
        community_cards=("2c", "5h", "8c", "Tc", "Jd"),
        actions=(
            RecordedAction(player_name="alice", action="raise", amount=100, phase="PRE_FLOP", pot_after=100),
            RecordedAction(player_name="bob", action="call", amount=100, phase="PRE_FLOP", pot_after=200),
            RecordedAction(player_name="carol", action="call", amount=100, phase="PRE_FLOP", pot_after=300),
            RecordedAction(player_name="bob", action="bet", amount=150, phase="FLOP", pot_after=450),
            RecordedAction(player_name="carol", action="fold", amount=0, phase="FLOP", pot_after=450),
            RecordedAction(player_name="alice", action="call", amount=150, phase="FLOP", pot_after=600),
            RecordedAction(player_name="alice", action="check", amount=0, phase="TURN", pot_after=600),
            RecordedAction(player_name="bob", action="check", amount=0, phase="TURN", pot_after=600),
            RecordedAction(player_name="bob", action="bet", amount=200, phase="RIVER", pot_after=800),
            RecordedAction(player_name="alice", action="call", amount=200, phase="RIVER", pot_after=1000),
        ),
        winners=(WinnerInfo(name="alice", amount_won=1000, hand_name="Pair", hand_rank=9),),
        pot_size=1000,
        was_showdown=True,
    )


def _print_pair(repo, obs, opp, label):
    state = repo.load_raw_relationship_state(obs, opp)
    if state is None:
        print(f"  {label}: (no row)")
        return
    print(
        f"  {label}: heat={state.heat:+.3f} respect={state.respect:.3f} "
        f"lik={state.likability:.3f}"
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "sanity.db")
        SchemaManager(db_path).ensure_schema()
        repo = RelationshipRepository(db_path)

        mgr = AIMemoryManager(game_id="sanity", db_path=db_path)
        for name in ("alice", "bob", "carol"):
            mgr.initialize_for_player(name, personality_id=f"{name}_v1")
        mgr.set_relationship_repo(repo, cash_mode=True)

        # Diagnostic detector: separate instance so we can inspect the
        # events that the real detector emits without disturbing its
        # dedup state. Shares the same name→id registry by reference.
        from poker.memory.hand_outcome_detector import HandOutcomeDetector
        diagnostic_detector = HandOutcomeDetector(
            name_to_id=mgr.opponent_model_manager._name_to_id,
        )

        drama_hand = _build_threway_drama_hand(hand_number=2)
        drama_events = diagnostic_detector.detect_events(drama_hand)

        bad_beat_hand = _build_bad_beat_hand(hand_number=5)
        bad_beat_history = _build_bad_beat_equity_history(hand_number=5)
        bad_beat_events = diagnostic_detector.detect_events(
            bad_beat_hand, equity_history=bad_beat_history,
        )

        # Hand 1: three heads-up big pots (BIG_WIN/BIG_LOSS only)
        for hand_number in (1, 3, 4):
            recorded = _build_big_hand(hand_number)
            mgr._process_relationship_events(recorded)

        # Hand 2 (drama): the multiway BLUFFED_OFF + HERO_CALL hand
        mgr._process_relationship_events(drama_hand)

        # Hand 5 (bad beat): heads-up favorite loses to a runner-runner
        # flush. Equity history is the load-bearing input that turns
        # BAD_BEAT detection on.
        mgr._process_relationship_events(
            bad_beat_hand, equity_history=bad_beat_history,
        )

        # -- Diagnostic output ----------------------------------------

        def _print_events(label, events):
            print(f"=== Events emitted: {label} ===")
            for e in events:
                print(
                    f"  {e.event.value:14s}  actor={e.actor_id:9s} "
                    f"target={e.target_id:9s} chips={e.chips_won:+5d}"
                )
                if e.narrative:
                    print(f"      {e.narrative}")

        _print_events("multiway drama hand", drama_events)
        print()
        _print_events("bad-beat hand", bad_beat_events)

        print("\n=== Final relationship_states ===")
        for (obs, opp) in [
            ("alice_v1", "bob_v1"),
            ("bob_v1", "alice_v1"),
            ("alice_v1", "carol_v1"),
            ("carol_v1", "alice_v1"),
            ("bob_v1", "carol_v1"),
            ("carol_v1", "bob_v1"),
        ]:
            label = f"{obs} → {opp}"
            _print_pair(repo, obs, opp, label)

        print("\n=== cash_pair_stats ===")
        for (obs, opp) in [
            ("alice_v1", "bob_v1"),
            ("bob_v1", "alice_v1"),
            ("alice_v1", "carol_v1"),
            ("carol_v1", "alice_v1"),
        ]:
            stats = repo.load_cash_pair_stats(obs, opp)
            if stats is None:
                print(f"  {obs} → {opp}: (no row)")
                continue
            print(
                f"  {obs} → {opp}: pnl={stats.cumulative_pnl:+5d} "
                f"hands={stats.hands_played_cash}"
            )

        # -- Hard assertions on event types --------------------------

        drama_kinds = {e.event.value for e in drama_events}
        for required in ("big_win", "big_loss", "hero_call", "bluffed_off"):
            assert required in drama_kinds, (
                f"{required} did not fire on the multiway drama hand; "
                f"emitted: {sorted(drama_kinds)}"
            )

        bad_beat_kinds = {e.event.value for e in bad_beat_events}
        assert "bad_beat" in bad_beat_kinds, (
            f"bad_beat did not fire on the favorite-lost hand; "
            f"emitted: {sorted(bad_beat_kinds)}"
        )
        bb = next(e for e in bad_beat_events if e.event.value == "bad_beat")
        assert bb.actor_id == "alice_v1" and bb.target_id == "bob_v1", bb

        # -- Semantic sanity on key axes -----------------------------
        #
        # carol → bob: carol folded a winner to bob's bluff. Per the
        # dispatch table BLUFFED_OFF actor shift is heat +0.20,
        # respect -0.05, likability -0.02 (the "they got me with
        # junk" anger). bob → carol mirror is all zeros (bob doesn't
        # see the fold reveal).
        carol_bob = repo.load_raw_relationship_state("carol_v1", "bob_v1")
        bob_carol = repo.load_raw_relationship_state("bob_v1", "carol_v1")
        assert carol_bob.heat > 0.15, (
            f"carol should be heated at bob after BLUFFED_OFF; "
            f"got {carol_bob.heat}"
        )
        assert carol_bob.respect < 0.5, (
            f"carol should respect bob less; got {carol_bob.respect}"
        )
        # Mirror: bob → carol from BLUFFED_OFF alone is all zeros, so
        # the heat stays at default 0 even though the row was created.
        assert bob_carol.heat == 0.0, (
            f"bob should not be heated at carol from a bluff he didn't "
            f"see revealed; got {bob_carol.heat}"
        )

        # bob → alice: BIG_LOSS actor + HERO_CALL mirror + 3 prior
        # BIG_LOSSes — then the bad-beat hand softens the picture
        # slightly (bob WON that one, so BIG_WIN actor: heat -0.10,
        # plus BAD_BEAT mirror: respect +0.05). Net: still very
        # heated, even more respect.
        bob_alice = repo.load_raw_relationship_state("bob_v1", "alice_v1")
        assert bob_alice.heat > 0.4, (
            f"bob should still be heated at alice; got {bob_alice.heat}"
        )
        assert bob_alice.respect > 0.5, (
            f"bob should respect alice; got {bob_alice.respect}"
        )

        # alice → bob: previously low heat (clamped at 0), low
        # respect, high liking. The bad-beat hand swings it
        # sharply: BAD_BEAT actor shift is heat +0.30, respect
        # -0.15, likability -0.10 — the strongest single-event
        # axis movement in the vocabulary. Plus BIG_LOSS actor and
        # BIG_WIN mirror add more heat. So alice's heat at bob
        # should now be SUBSTANTIAL, not zero. This is the
        # behavioral signal that proves BAD_BEAT is wired in: the
        # relationship layer reacts emotionally to a suckout in a
        # way that pure chip-flow accounting wouldn't.
        alice_bob = repo.load_raw_relationship_state("alice_v1", "bob_v1")
        assert alice_bob.heat > 0.4, (
            f"alice should be heated at bob after the bad beat; "
            f"got {alice_bob.heat}"
        )
        assert alice_bob.respect < 0.3, (
            f"alice's respect for bob should crater after the bad "
            f"beat (he sucked out with 7-6 vs aces); "
            f"got {alice_bob.respect}"
        )

        # cash_pair_stats: 3 heads-up wins + multiway big pot give
        # alice +1650 vs bob and +100 vs carol. Then the bad-beat
        # hand REVERSES 500 chips (bob wins net 500 from alice):
        # alice vs bob = 1650 - 500 = 1150. hands = 5.
        alice_bob_stats = repo.load_cash_pair_stats("alice_v1", "bob_v1")
        alice_carol_stats = repo.load_cash_pair_stats("alice_v1", "carol_v1")
        assert alice_bob_stats.cumulative_pnl == 1150, alice_bob_stats
        assert alice_bob_stats.hands_played_cash == 5, alice_bob_stats
        assert alice_carol_stats.cumulative_pnl == 100, alice_carol_stats
        assert alice_carol_stats.hands_played_cash == 1, alice_carol_stats

        repo.close()
        print(
            "\nOK — all four event types fire and axes move in the "
            "directions the design doc expects."
        )


if __name__ == "__main__":
    main()
