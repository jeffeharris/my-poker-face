"""Tests for the chat-send → relationship-event dispatch.

The Flask chat-send route extracts `(tone, intensity, addressing)` from
the request body and forwards to `_dispatch_chat_relationship_event`,
which maps the tone to a `RelationshipEvent` and fires `record_event`.
These tests target the dispatch helper directly with a real
`OpponentModelManager` + `RelationshipRepository` so the assertion is
"the right axes moved by the right amount" rather than "this mock was
called."
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

from flask_app.handlers.chat_relationship import dispatch_chat_relationship_event
from poker.memory.opponent_model import OpponentModelManager
from poker.memory.relationship_events import (
    ACTOR_AXIS_SHIFTS,
    RelationshipEvent,
)
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager

# Disposition anchor sets, shared across the emotional-reaction and
# temperament-divergence test classes.
# Napoleon-like: proud + reserved → 'stung'.
STUNG_ANCHORS = {"ego": 0.86, "poise": 0.65, "expressiveness": 0.32, "baseline_aggression": 0.8}
# Wilde-like: proud + expressive → 'energized'.
ENERGIZED_ANCHORS = {"ego": 0.8, "poise": 0.62, "expressiveness": 0.68, "baseline_aggression": 0.35}


@pytest.fixture
def repo(tmp_path):
    path = str(tmp_path / "rel.db")
    SchemaManager(path).ensure_schema()
    r = RelationshipRepository(path)
    yield r
    r.close()


@pytest.fixture
def opp_manager(repo):
    mgr = OpponentModelManager(relationship_repo=repo)
    # Register both sides so resolve_player_id returns stable ids
    # rather than falling back to display names. Either path works for
    # the relationship layer; this just keeps the test fixture mirror
    # of production setup where player_ids are registered at startup.
    mgr.register_player_id("alice", "alice_pid")
    mgr.register_player_id("bob", "bob_pid")
    return mgr


@pytest.fixture
def game_data(opp_manager):
    # Minimal shape: just the manager hook the dispatch helper needs.
    memory_manager = SimpleNamespace(
        get_opponent_model_manager=lambda: opp_manager,
    )
    return {"memory_manager": memory_manager}


class TestDispatchSkipsWhenInputMissing:
    def test_no_tone_skips_dispatch(self, game_data, repo):
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone=None,
            intensity=None,
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_no_addressing_skips_dispatch(self, game_data, repo):
        # Table-broadcast: no specific target, so no relationship event.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            None,
            tone="goad",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_empty_addressing_skips_dispatch(self, game_data, repo):
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            [],
            tone="goad",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_bluff_tone_skips_dispatch(self, game_data, repo):
        # bluff is the documented no-op tone (about speaker's own hand).
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="bluff",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_unknown_tone_skips_dispatch(self, game_data, repo):
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="snarky",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_no_memory_manager_skips_dispatch(self, repo):
        dispatch_chat_relationship_event(
            {},
            "alice",
            ["bob"],
            tone="goad",
            intensity="spicy",
        )
        # No assertion needed beyond "didn't raise" — there's no repo
        # to inspect in this game_data shape.


class TestDispatchFiresEvent:
    def test_spicy_goad_applies_full_trash_talk_shift(
        self,
        game_data,
        repo,
    ):
        # spicy + goad → TRASH_TALK at multiplier 1.0.
        # Actor (alice) shift from the dispatch table: heat +0.10,
        # likability -0.05. The bilateral update also writes the
        # mirror row.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="goad",
            intensity="spicy",
        )
        actor_state = repo.load_raw_relationship_state("alice_pid", "bob_pid")
        assert actor_state is not None
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.TRASH_TALK]
        # Default RelationshipState starts at heat=0, respect=0.5,
        # likability=0.5; the shift is applied on top.
        assert actor_state.heat == pytest.approx(expected.heat)
        assert actor_state.respect == pytest.approx(0.5 + expected.respect)
        assert actor_state.likability == pytest.approx(0.5 + expected.likability)

    def test_chill_needle_compounds_to_quarter_shift(
        self,
        game_data,
        repo,
    ):
        # needle base = 0.5, chill modifier = 0.5 → composed multiplier
        # 0.25. The applied TRASH_TALK actor shift is scaled accordingly.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="needle",
            intensity="chill",
        )
        actor_state = repo.load_raw_relationship_state("alice_pid", "bob_pid")
        assert actor_state is not None
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.TRASH_TALK]
        assert actor_state.heat == pytest.approx(expected.heat * 0.25)
        assert actor_state.respect == pytest.approx(0.5 + expected.respect * 0.25)
        assert actor_state.likability == pytest.approx(0.5 + expected.likability * 0.25)

    def test_props_applies_respect_weighted_shift(self, game_data, repo):
        # props → PROPS: the one chat tone that meaningfully raises respect.
        dispatch_chat_relationship_event(game_data, "alice", ["bob"], tone="props", intensity=None)
        actor_state = repo.load_raw_relationship_state("alice_pid", "bob_pid")
        assert actor_state is not None
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.PROPS]
        assert expected.respect > 0
        assert actor_state.respect == pytest.approx(0.5 + expected.respect)
        assert actor_state.likability == pytest.approx(0.5 + expected.likability)

    def test_gloat_applies_taunt_post_win(self, game_data, repo):
        # Post-round tone; intensity is ignored.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="gloat",
            intensity=None,
        )
        actor_state = repo.load_raw_relationship_state("alice_pid", "bob_pid")
        assert actor_state is not None
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.TAUNT_POST_WIN]
        assert actor_state.heat == pytest.approx(expected.heat)
        assert actor_state.respect == pytest.approx(0.5 + expected.respect)
        assert actor_state.likability == pytest.approx(0.5 + expected.likability)

    def test_memorable_hand_attached_when_hand_count_present(
        self,
        opp_manager,
        repo,
    ):
        """When the chat path is invoked during an active hand, the
        bilateral axis update should attach a MemorableHand sidecar
        on the actor's in-memory PlayerModel — same surface
        hand-outcome events use. Without this, chat-driven movement
        is invisible in the debug view (axes shift but no narrative).
        """
        from types import SimpleNamespace

        memory_manager = SimpleNamespace(
            get_opponent_model_manager=lambda: opp_manager,
            hand_count=7,
        )
        game_data = {"memory_manager": memory_manager}

        # Pre-create the model so add_memorable_hand has a target to
        # attach to. (Production path creates models on register +
        # first interaction; the test fixture does this explicitly.)
        opp_manager.get_model("alice", "bob")

        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="goad",
            intensity="spicy",
        )
        model = opp_manager.get_model("alice", "bob")
        assert len(model.memorable_hands) == 1
        assert model.memorable_hands[0].hand_id == 7
        assert "alice → bob" in model.memorable_hands[0].narrative

    def test_self_targeted_message_is_silently_skipped(
        self,
        game_data,
        repo,
    ):
        # actor_id == target_id should never fire. The route is meant
        # to be human-to-AI but a misrouted self-addressed message
        # shouldn't crash and shouldn't write any state.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["alice"],
            tone="goad",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "alice_pid") is None


class TestDispatchAppliesEmotionalReaction:
    """The dispatch also moves the target AI's own psychology axes,
    branched by disposition. These assert the right axis moved in the
    right direction — psychology is repo-independent, held on the
    controller in `game_data['ai_controllers']`.
    """

    def _ai_game_data(self, opp_manager, name, anchors):
        from poker.player_psychology import PlayerPsychology

        psych = PlayerPsychology.from_personality_config(name, {"anchors": anchors})
        controller = SimpleNamespace(psychology=psych)
        memory_manager = SimpleNamespace(
            get_opponent_model_manager=lambda: opp_manager,
            hand_count=3,
        )
        game_data = {
            "memory_manager": memory_manager,
            "ai_controllers": {name: controller},
        }
        return game_data, psych

    # 'stung' → composure drops; 'energized' → energy rises.
    STUNG = STUNG_ANCHORS
    ENERGIZED = ENERGIZED_ANCHORS

    def test_jab_stings_a_proud_character(self, opp_manager):
        game_data, psych = self._ai_game_data(opp_manager, "bob", self.STUNG)
        before = psych.composure
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="goad", intensity="spicy"
        )
        assert psych.composure < before

    def test_jab_energizes_a_charmer(self, opp_manager):
        game_data, psych = self._ai_game_data(opp_manager, "bob", self.ENERGIZED)
        before = psych.energy
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="goad", intensity="spicy"
        )
        assert psych.energy > before

    def test_chill_needle_moves_less_than_spicy_goad(self, opp_manager):
        gd1, p1 = self._ai_game_data(opp_manager, "bob", self.STUNG)
        c0 = p1.composure
        dispatch_chat_relationship_event(gd1, "alice", ["bob"], tone="needle", intensity="chill")
        chill_drop = c0 - p1.composure

        gd2, p2 = self._ai_game_data(opp_manager, "bob", self.STUNG)
        c0b = p2.composure
        dispatch_chat_relationship_event(gd2, "alice", ["bob"], tone="goad", intensity="spicy")
        spicy_drop = c0b - p2.composure

        assert 0 < chill_drop < spicy_drop

    def test_non_ai_target_is_a_noop(self, opp_manager):
        # No controller registered → reaction silently skipped, no raise.
        memory_manager = SimpleNamespace(
            get_opponent_model_manager=lambda: opp_manager,
            hand_count=1,
        )
        game_data = {"memory_manager": memory_manager, "ai_controllers": {}}
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="goad", intensity="spicy"
        )

    def test_props_warms_the_target(self, opp_manager):
        # props is a praise stimulus → a non-stoic target warms (confidence up).
        game_data, psych = self._ai_game_data(opp_manager, "bob", self.STUNG)
        before = psych.confidence
        dispatch_chat_relationship_event(game_data, "alice", ["bob"], tone="props", intensity=None)
        assert psych.confidence > before

    def test_bluff_tone_moves_no_axes(self, opp_manager):
        # bluff → no RelationshipEvent → no stimulus → axes untouched.
        game_data, psych = self._ai_game_data(opp_manager, "bob", self.STUNG)
        snapshot = (psych.confidence, psych.composure, psych.energy)
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="bluff", intensity="spicy"
        )
        assert (psych.confidence, psych.composure, psych.energy) == snapshot

    # Flattery rides vanity, not the jab/praise axis — valence flips per target.
    VAIN = {"ego": 0.86, "adaptation_bias": 0.5}  # proud → laps it up
    PERCEPTIVE = {"ego": 0.42, "adaptation_bias": 0.7}  # reads the ploy → backfires
    OBLIVIOUS = {"ego": 0.40, "adaptation_bias": 0.30}  # unmoved

    def test_flatter_charms_a_vain_target(self, opp_manager, repo):
        game_data, psych = self._ai_game_data(opp_manager, "bob", self.VAIN)
        before = psych.confidence
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="flatter", intensity=None
        )
        assert psych.confidence > before  # vain → flattered
        # FLATTERY_LANDED: target warms to the flatterer (mirror likability up).
        mirror = repo.load_raw_relationship_state("bob_pid", "alice_pid")
        assert mirror is not None and mirror.likability > 0.5

    def test_flatter_backfires_on_a_perceptive_target(self, opp_manager, repo):
        game_data, psych = self._ai_game_data(opp_manager, "bob", self.PERCEPTIVE)
        before = psych.composure
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="flatter", intensity=None
        )
        assert psych.composure < before  # sees through → bristles
        # FLATTERY_BACKFIRED: target respects the manipulator less (mirror respect down).
        mirror = repo.load_raw_relationship_state("bob_pid", "alice_pid")
        assert mirror is not None and mirror.respect < 0.5

    def test_flatter_washes_over_the_unmoved(self, opp_manager, repo):
        game_data, psych = self._ai_game_data(opp_manager, "bob", self.OBLIVIOUS)
        snapshot = (psych.confidence, psych.composure, psych.energy)
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="flatter", intensity=None
        )
        assert (psych.confidence, psych.composure, psych.energy) == snapshot  # no axis move
        assert repo.load_raw_relationship_state("bob_pid", "alice_pid") is None  # no event


class TestBroadcastFanOut:
    """A tone with no specific target (gloat after a win, or a 'to the
    table' jab) fans the emotional reaction out to every seated AI at a
    reduced scale, and leaves the relationship layer untouched.
    """

    STUNG = STUNG_ANCHORS

    def _multi_ai_game_data(self, opp_manager, specs):
        from poker.player_psychology import PlayerPsychology

        controllers = {
            name: SimpleNamespace(
                psychology=PlayerPsychology.from_personality_config(name, {"anchors": anchors})
            )
            for name, anchors in specs.items()
        }
        # alice is the human sender (seated, but no controller).
        players = [SimpleNamespace(name=n) for n in (["alice"] + list(specs.keys()))]
        state_machine = SimpleNamespace(game_state=SimpleNamespace(players=players))
        memory_manager = SimpleNamespace(
            get_opponent_model_manager=lambda: opp_manager,
            hand_count=5,
        )
        game_data = {
            "memory_manager": memory_manager,
            "ai_controllers": controllers,
            "state_machine": state_machine,
        }
        return game_data, controllers

    def test_gloat_with_no_target_fans_out_to_all_seated_ais(self, opp_manager):
        game_data, controllers = self._multi_ai_game_data(
            opp_manager, {"bob": self.STUNG, "carol": self.STUNG}
        )
        before = {n: c.psychology.composure for n, c in controllers.items()}
        dispatch_chat_relationship_event(game_data, "alice", None, tone="gloat", intensity=None)
        for n, c in controllers.items():
            assert c.psychology.composure < before[n], f"{n} should have been stung"

    def test_broadcast_does_not_move_relationship_axes(self, opp_manager, repo):
        game_data, _ = self._multi_ai_game_data(opp_manager, {"bob": self.STUNG})
        dispatch_chat_relationship_event(game_data, "alice", None, tone="gloat", intensity=None)
        # No explicit pairwise target → relationship layer untouched.
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_broadcast_hits_softer_than_a_direct_jab(self, opp_manager):
        gd_direct, c_direct = self._multi_ai_game_data(opp_manager, {"bob": self.STUNG})
        b0 = c_direct["bob"].psychology.composure
        dispatch_chat_relationship_event(
            gd_direct, "alice", ["bob"], tone="goad", intensity="spicy"
        )
        direct_drop = b0 - c_direct["bob"].psychology.composure

        gd_bcast, c_bcast = self._multi_ai_game_data(opp_manager, {"bob": self.STUNG})
        b0b = c_bcast["bob"].psychology.composure
        dispatch_chat_relationship_event(gd_bcast, "alice", None, tone="goad", intensity="spicy")
        bcast_drop = b0b - c_bcast["bob"].psychology.composure

        assert 0 < bcast_drop < direct_drop

    def test_sender_excluded_from_broadcast(self, opp_manager):
        # If the sender somehow has a controller, they shouldn't react to
        # their own message. Seat 'alice' with a controller and confirm
        # only 'bob' moves.
        game_data, controllers = self._multi_ai_game_data(
            opp_manager, {"alice": self.STUNG, "bob": self.STUNG}
        )
        alice_before = controllers["alice"].psychology.composure
        bob_before = controllers["bob"].psychology.composure
        dispatch_chat_relationship_event(game_data, "alice", None, tone="goad", intensity="spicy")
        assert controllers["alice"].psychology.composure == alice_before
        assert controllers["bob"].psychology.composure < bob_before


class TestTemperamentDivergence:
    """The same needle lands on the RECIPIENT's relationship axes
    differently by their social temperament: an 'energized' banter-lover
    bonds over it (likability up, heat flat), a 'stung' character takes it
    harder (heat/likability amplified). This is the mirror (target's-POV)
    row — the actor side is never temperament-adjusted.
    """

    def _ai_game_data(self, opp_manager, name, anchors):
        from poker.player_psychology import PlayerPsychology

        psych = PlayerPsychology.from_personality_config(name, {"anchors": anchors})
        controller = SimpleNamespace(psychology=psych)
        memory_manager = SimpleNamespace(
            get_opponent_model_manager=lambda: opp_manager,
            hand_count=3,
        )
        game_data = {
            "memory_manager": memory_manager,
            "ai_controllers": {name: controller},
        }
        return game_data

    def test_energized_recipient_bonds_over_trash_talk(self, opp_manager, repo):
        from poker.memory.relationship_events import (
            RelationshipEvent,
            temperament_adjusted_mirror_shift,
        )

        game_data = self._ai_game_data(opp_manager, "bob", ENERGIZED_ANCHORS)
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="goad", intensity="spicy"
        )
        # Mirror = bob's view of alice. Energized → heat flat, likability up.
        mirror = repo.load_raw_relationship_state("bob_pid", "alice_pid")
        assert mirror is not None
        expected = temperament_adjusted_mirror_shift(RelationshipEvent.TRASH_TALK, 'energized')
        assert mirror.heat == pytest.approx(0.0)
        assert mirror.likability == pytest.approx(0.5 + expected.likability)
        assert mirror.likability > 0.5  # the needle BUILT warmth

    def test_stung_recipient_takes_trash_talk_harder(self, opp_manager, repo):
        from poker.memory.relationship_events import (
            RelationshipEvent,
            mirror_shift,
            temperament_adjusted_mirror_shift,
        )

        game_data = self._ai_game_data(opp_manager, "bob", STUNG_ANCHORS)
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="goad", intensity="spicy"
        )
        mirror = repo.load_raw_relationship_state("bob_pid", "alice_pid")
        assert mirror is not None
        neutral = mirror_shift(RelationshipEvent.TRASH_TALK)
        stung = temperament_adjusted_mirror_shift(RelationshipEvent.TRASH_TALK, 'stung')
        assert mirror.heat == pytest.approx(stung.heat)
        assert mirror.heat > neutral.heat  # bites harder than neutral
        assert mirror.likability == pytest.approx(0.5 + stung.likability)
        assert mirror.likability < 0.5 + neutral.likability

    def test_same_needle_diverges_by_recipient_temperament(self, opp_manager, repo):
        # Energized bob and stung bob hear the identical jab; their views of
        # the sender move in opposite directions.
        gd_e = self._ai_game_data(opp_manager, "bob", ENERGIZED_ANCHORS)
        dispatch_chat_relationship_event(gd_e, "alice", ["bob"], tone="goad", intensity="spicy")
        energized_mirror = repo.load_raw_relationship_state("bob_pid", "alice_pid")

        # Fresh sender/target pair to avoid compounding on the same row.
        opp_manager.register_player_id("carol", "carol_pid")
        gd_s = self._ai_game_data(opp_manager, "carol", STUNG_ANCHORS)
        dispatch_chat_relationship_event(gd_s, "alice", ["carol"], tone="goad", intensity="spicy")
        stung_mirror = repo.load_raw_relationship_state("carol_pid", "alice_pid")

        assert energized_mirror.heat < stung_mirror.heat
        assert energized_mirror.likability > stung_mirror.likability

    def test_actor_side_is_not_temperament_adjusted(self, opp_manager, repo):
        # Alice's view of an energized bob still uses the neutral ACTOR shift —
        # only the recipient's mirror reception is reshaped.
        game_data = self._ai_game_data(opp_manager, "bob", ENERGIZED_ANCHORS)
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="goad", intensity="spicy"
        )
        actor_state = repo.load_raw_relationship_state("alice_pid", "bob_pid")
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.TRASH_TALK]
        assert actor_state.heat == pytest.approx(expected.heat)
        assert actor_state.likability == pytest.approx(0.5 + expected.likability)


# Emotional-layer asymmetry anchor sets. Intimidate keys on poise (composure
# filter), dare keys on ego (confidence filter, inverted — the proud puff up).
_TIMID_ANCHORS = {"ego": 0.5, "poise": 0.15, "expressiveness": 0.5, "baseline_aggression": 0.5}
_COMPOSED_ANCHORS = {"ego": 0.5, "poise": 0.90, "expressiveness": 0.5, "baseline_aggression": 0.5}
_PROUD_ANCHORS = {"ego": 0.90, "poise": 0.5, "expressiveness": 0.5, "baseline_aggression": 0.5}
_MODEST_ANCHORS = {"ego": 0.15, "poise": 0.5, "expressiveness": 0.5, "baseline_aggression": 0.5}


def _ai_game_data(opp_manager, name, anchors):
    from poker.player_psychology import PlayerPsychology

    psych = PlayerPsychology.from_personality_config(name, {"anchors": anchors})
    controller = SimpleNamespace(psychology=psych)
    memory_manager = SimpleNamespace(
        get_opponent_model_manager=lambda: opp_manager,
        hand_count=3,
    )
    game_data = {
        "memory_manager": memory_manager,
        "ai_controllers": {name: controller},
    }
    return game_data, psych


class TestEmotionalLayerTones:
    """`intimidate` and `dare` move the target's psychology axes (their play),
    not the relationship axes — and the asymmetry each is named for falls out
    of the apply_pressure_event filters for free.
    """

    def test_intimidate_drops_composure_and_writes_no_relationship_row(self, opp_manager, repo):
        game_data, psych = _ai_game_data(opp_manager, "bob", _TIMID_ANCHORS)
        before = psych.composure
        dispatch_chat_relationship_event(
            game_data, "alice", ["bob"], tone="intimidate", intensity="spicy"
        )
        assert psych.composure < before  # rattled
        # Emotional-only: no bilateral relationship state should exist.
        assert repo.load_raw_relationship_state("bob_pid", "alice_pid") is None
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_intimidate_rattles_the_timid_more_than_the_composed(self, opp_manager):
        gd_t, p_t = _ai_game_data(opp_manager, "bob", _TIMID_ANCHORS)
        c0t = p_t.composure
        dispatch_chat_relationship_event(
            gd_t, "alice", ["bob"], tone="intimidate", intensity="spicy"
        )
        timid_drop = c0t - p_t.composure

        gd_c, p_c = _ai_game_data(opp_manager, "bob", _COMPOSED_ANCHORS)
        c0c = p_c.composure
        dispatch_chat_relationship_event(
            gd_c, "alice", ["bob"], tone="intimidate", intensity="spicy"
        )
        composed_drop = c0c - p_c.composure

        assert timid_drop > composed_drop > 0

    def test_dare_puffs_the_proud_more_than_the_modest(self, opp_manager):
        # Inverted asymmetry: a dare lands on the PROUD (confidence/ego), and
        # barely registers on the modest. "You can't dare a humble man."
        gd_p, p_p = _ai_game_data(opp_manager, "bob", _PROUD_ANCHORS)
        f0p = p_p.confidence
        dispatch_chat_relationship_event(gd_p, "alice", ["bob"], tone="dare", intensity="spicy")
        proud_puff = p_p.confidence - f0p

        gd_m, p_m = _ai_game_data(opp_manager, "bob", _MODEST_ANCHORS)
        f0m = p_m.confidence
        dispatch_chat_relationship_event(gd_m, "alice", ["bob"], tone="dare", intensity="spicy")
        modest_puff = p_m.confidence - f0m

        assert proud_puff > modest_puff > 0


class TestSarcasmReception:
    """The `sarcastic` register replaces the neutral mirror shift with the
    disposition-keyed sarcasm transform: trash_talk softens into banter,
    props sharpens into a backhand. The actor side stays on the sincere event.
    """

    def test_sarcastic_trash_talk_softens_vs_sincere(self, opp_manager, repo):
        # Sincere spicy trash talk on a stung target → heat up, likability down.
        gd_s, _ = _ai_game_data(opp_manager, "bob", STUNG_ANCHORS)
        dispatch_chat_relationship_event(
            gd_s, "alice", ["bob"], tone="trash_talk", intensity="spicy"
        )
        sincere = repo.load_raw_relationship_state("bob_pid", "alice_pid")

        # Sarcastic trash talk (banter) on the SAME disposition, fresh pair.
        opp_manager.register_player_id("carol", "carol_pid")
        gd_b, _ = _ai_game_data(opp_manager, "carol", STUNG_ANCHORS)
        dispatch_chat_relationship_event(
            gd_b, "alice", ["carol"], tone="trash_talk", intensity="sarcastic"
        )
        banter = repo.load_raw_relationship_state("carol_pid", "alice_pid")

        # The edge comes off: banter is warmer (less heat, more likability).
        assert banter.heat < sincere.heat
        assert banter.likability > sincere.likability

    def test_sarcastic_props_is_a_backhand_to_the_stung(self, opp_manager, repo):
        # Sincere props lifts respect; a sarcastic backhand does the opposite
        # on a stung recipient (condescension cuts respect down).
        gd_s, _ = _ai_game_data(opp_manager, "bob", STUNG_ANCHORS)
        dispatch_chat_relationship_event(gd_s, "alice", ["bob"], tone="props", intensity="spicy")
        sincere = repo.load_raw_relationship_state("bob_pid", "alice_pid")

        opp_manager.register_player_id("carol", "carol_pid")
        gd_b, _ = _ai_game_data(opp_manager, "carol", STUNG_ANCHORS)
        dispatch_chat_relationship_event(
            gd_b, "alice", ["carol"], tone="props", intensity="sarcastic"
        )
        backhand = repo.load_raw_relationship_state("carol_pid", "alice_pid")

        assert sincere.respect > 0.5  # sincere props built respect
        assert backhand.respect < sincere.respect  # the backhand cut it
        assert backhand.likability < sincere.likability

    def test_sarcastic_actor_side_stays_sincere(self, opp_manager, repo):
        # Same asymmetry as temperament: only the recipient's mirror is
        # reshaped by sarcasm; the sender keeps the neutral event ACTOR shift.
        gd, _ = _ai_game_data(opp_manager, "bob", STUNG_ANCHORS)
        dispatch_chat_relationship_event(gd, "alice", ["bob"], tone="props", intensity="sarcastic")
        actor_state = repo.load_raw_relationship_state("alice_pid", "bob_pid")
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.PROPS]
        assert actor_state.respect == pytest.approx(0.5 + expected.respect)


# adaptation_bias gates sarcasm detection (floor 0.45). These two sets differ
# ONLY in that trait, so the disposition (and thus the literal/sarcasm
# receptions) is identical — isolating the detection effect.
_OBLIVIOUS_ANCHORS = {
    "ego": 0.5,
    "poise": 0.6,
    "expressiveness": 0.4,
    "baseline_aggression": 0.5,
    "adaptation_bias": 0.2,
}
_PERCEPTIVE_ANCHORS = {
    "ego": 0.5,
    "poise": 0.6,
    "expressiveness": 0.4,
    "baseline_aggression": 0.5,
    "adaptation_bias": 0.75,
}


class TestSarcasmDetectionGate:
    """A recipient who misses the sarcasm (low adaptation_bias) reacts to the
    LITERAL surface — a backhanded compliment pleases them, friendly banter
    offends them. The inversion that makes sarcasm a read-dependent tool.
    """

    def test_oblivious_takes_a_backhand_as_a_sincere_compliment(self, opp_manager, repo):
        from poker.memory.relationship_events import RelationshipEvent, mirror_shift

        gd_o, _ = _ai_game_data(opp_manager, "bob", _OBLIVIOUS_ANCHORS)
        dispatch_chat_relationship_event(
            gd_o, "alice", ["bob"], tone="props", intensity="sarcastic"
        )
        oblivious = repo.load_raw_relationship_state("bob_pid", "alice_pid")

        opp_manager.register_player_id("carol", "carol_pid")
        gd_p, _ = _ai_game_data(opp_manager, "carol", _PERCEPTIVE_ANCHORS)
        dispatch_chat_relationship_event(
            gd_p, "alice", ["carol"], tone="props", intensity="sarcastic"
        )
        perceptive = repo.load_raw_relationship_state("carol_pid", "alice_pid")

        # Missed it → literal PROPS reception → respect rises exactly as a
        # sincere compliment would; the perceptive reader's respect is lower.
        sincere = mirror_shift(RelationshipEvent.PROPS)
        assert oblivious.respect == pytest.approx(0.5 + sincere.respect)
        assert oblivious.respect > 0.5
        assert perceptive.respect < oblivious.respect

    def test_oblivious_takes_banter_as_a_real_jab(self, opp_manager, repo):
        gd_o, _ = _ai_game_data(opp_manager, "bob", _OBLIVIOUS_ANCHORS)
        dispatch_chat_relationship_event(
            gd_o, "alice", ["bob"], tone="trash_talk", intensity="sarcastic"
        )
        oblivious = repo.load_raw_relationship_state("bob_pid", "alice_pid")

        opp_manager.register_player_id("carol", "carol_pid")
        gd_p, _ = _ai_game_data(opp_manager, "carol", _PERCEPTIVE_ANCHORS)
        dispatch_chat_relationship_event(
            gd_p, "alice", ["carol"], tone="trash_talk", intensity="sarcastic"
        )
        perceptive = repo.load_raw_relationship_state("carol_pid", "alice_pid")

        # Missed the banter → literal trash talk → strictly more heat than the
        # perceptive reader, who hears the edge come off.
        assert oblivious.heat > perceptive.heat

    def test_detection_flips_the_emotional_reaction(self, opp_manager):
        # Perceived backhand presses composure (a jab); a missed one warms
        # (praise) and leaves composure unpressed.
        gd_p, p_p = _ai_game_data(opp_manager, "carol", _PERCEPTIVE_ANCHORS)
        c_p = p_p.composure
        dispatch_chat_relationship_event(
            gd_p, "alice", ["carol"], tone="props", intensity="sarcastic"
        )
        assert p_p.composure < c_p  # caught the barb → stung

        gd_o, p_o = _ai_game_data(opp_manager, "bob", _OBLIVIOUS_ANCHORS)
        c_o = p_o.composure
        dispatch_chat_relationship_event(
            gd_o, "alice", ["bob"], tone="props", intensity="sarcastic"
        )
        assert p_o.composure >= c_o  # took the compliment at face value

    def test_flag_off_restores_universal_sarcasm(self, opp_manager, repo, monkeypatch):
        # With detection disabled, even the oblivious get the sarcasm transform
        # (the prior behavior) — the backhand cuts rather than flatters.
        import flask_app.handlers.chat_relationship as cr

        monkeypatch.setattr(cr, "SARCASM_DETECTION_ENABLED", False)
        from poker.memory.relationship_events import RelationshipEvent, mirror_shift

        gd, _ = _ai_game_data(opp_manager, "bob", _OBLIVIOUS_ANCHORS)
        dispatch_chat_relationship_event(gd, "alice", ["bob"], tone="props", intensity="sarcastic")
        mirror = repo.load_raw_relationship_state("bob_pid", "alice_pid")

        sincere = mirror_shift(RelationshipEvent.PROPS)
        assert mirror.respect < 0.5 + sincere.respect  # sarcasm applied, not literal
