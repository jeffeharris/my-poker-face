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

        # Capture detector output for the dramatic hand so we can
        # print which events fired (the dispatch path also runs them
        # through record_event; we duplicate the detect call to log
        # the events, then process_relationship_events for the real
        # state mutation).
        drama_hand = _build_threway_drama_hand(hand_number=2)
        preview = mgr.hand_outcome_detector
        # detect_events has dedup state — call once via _process for
        # real, then inspect after via the manager's reset detector.
        from poker.memory.hand_outcome_detector import HandOutcomeDetector
        diagnostic_detector = HandOutcomeDetector(
            name_to_id=mgr.opponent_model_manager._name_to_id,
        )
        diagnostic_events = diagnostic_detector.detect_events(drama_hand)

        # Hand 1: three heads-up big pots (BIG_WIN/BIG_LOSS only)
        for hand_number in (1, 3, 4):
            recorded = _build_big_hand(hand_number)
            mgr._process_relationship_events(recorded)

        # Hand 2 (drama): the multiway BLUFFED_OFF + HERO_CALL hand
        mgr._process_relationship_events(drama_hand)

        # -- Diagnostic output ----------------------------------------

        print("=== Events emitted by the multiway drama hand ===")
        for e in diagnostic_events:
            print(
                f"  {e.event.value:14s}  actor={e.actor_id:9s} "
                f"target={e.target_id:9s} chips={e.chips_won:+5d}"
            )
            if e.narrative:
                print(f"      {e.narrative}")

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

        emitted_kinds = {e.event.value for e in diagnostic_events}
        for required in ("big_win", "big_loss", "hero_call", "bluffed_off"):
            assert required in emitted_kinds, (
                f"{required} did not fire on the multiway drama hand; "
                f"emitted: {sorted(emitted_kinds)}"
            )

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

        # bob → alice: BIG_LOSS actor + HERO_CALL mirror + (three
        # prior BIG_LOSSes from hands 1, 3, 4 with heat clamped via
        # the bilateral mirror update). bob just got hero-called and
        # lost three big pots — heat toward alice should be
        # substantial.
        bob_alice = repo.load_raw_relationship_state("bob_v1", "alice_v1")
        assert bob_alice.heat > 0.4, (
            f"bob should be very heated at alice; got {bob_alice.heat}"
        )
        # HERO_CALL mirror is respect +0.05 — bob respects alice for
        # making the call (and BIG_LOSS actor adds +0.08 per loss).
        assert bob_alice.respect > 0.5, (
            f"bob should respect alice; got {bob_alice.respect}"
        )

        # alice → bob: catching a bluff and winning three pots —
        # alice's view should be the opposite: low heat, lower
        # respect (HERO_CALL actor: respect -0.10 reads as "your
        # bluff was bad enough to be call-worthy"), higher liking.
        alice_bob = repo.load_raw_relationship_state("alice_v1", "bob_v1")
        assert alice_bob.heat == 0.0, (
            f"alice's heat at bob should stay clamped at 0; "
            f"got {alice_bob.heat}"
        )
        assert alice_bob.respect < 0.5, (
            f"alice should respect bob less after catching his bluff; "
            f"got {alice_bob.respect}"
        )

        # cash_pair_stats: across 3 heads-up + 1 multiway big pot,
        # alice's PnL vs bob = 3 × 400 + 450 (drama hand chip flow)
        # = 1650. vs carol = 100 (drama hand only).
        alice_bob_stats = repo.load_cash_pair_stats("alice_v1", "bob_v1")
        alice_carol_stats = repo.load_cash_pair_stats("alice_v1", "carol_v1")
        assert alice_bob_stats.cumulative_pnl == 1650, alice_bob_stats
        assert alice_bob_stats.hands_played_cash == 4, alice_bob_stats
        assert alice_carol_stats.cumulative_pnl == 100, alice_carol_stats
        assert alice_carol_stats.hands_played_cash == 1, alice_carol_stats

        repo.close()
        print(
            "\nOK — all four event types fire and axes move in the "
            "directions the design doc expects."
        )


if __name__ == "__main__":
    main()
