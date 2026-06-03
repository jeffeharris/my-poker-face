"""Tests for GameRepository."""

import json

import pytest

from core.card import Card
from poker.memory.opponent_model import OpponentModelManager
from poker.poker_game import Player, PokerGameState
from poker.poker_state_machine import PokerPhase, PokerStateMachine
from poker.repositories.game_repository import GameRepository, SavedGame


@pytest.fixture
def repo(db_path):
    r = GameRepository(db_path)
    yield r
    r.close()


def _make_state_machine():
    """Create a minimal PokerStateMachine for testing."""
    players = (
        Player(name="Alice", stack=1000, is_human=True, bet=0),
        Player(name="Bob", stack=1000, is_human=False, bet=0),
    )
    deck = tuple(Card(rank=r, suit=s) for r in ['2', '3', '4', '5'] for s in ['Hearts', 'Diamonds'])
    game_state = PokerGameState(
        players=players,
        deck=deck,
        pot={'total': 100},
        current_player_idx=0,
        current_dealer_idx=1,
        current_ante=10,
    )
    return PokerStateMachine.from_saved_state(game_state, PokerPhase.PRE_FLOP)


# --- Game CRUD ---


def test_save_and_load_game(repo):
    sm = _make_state_machine()
    repo.save_game("game1", sm, owner_id="owner1", owner_name="Jeff")
    loaded = repo.load_game("game1")
    assert loaded is not None
    assert len(loaded.game_state.players) == 2
    assert loaded.game_state.players[0].name == "Alice"
    assert loaded.game_state.pot['total'] == 100


def test_load_game_not_found(repo):
    assert repo.load_game("nonexistent") is None


def test_load_game_marks_seed_consumed(repo):
    """Restored seed must not leak into the next hand's deck.

    Regression: load_game previously set the seed via the public setter,
    which defaulted hand_seed_provided=True. The next hand_over_transition
    then re-resolved the seed and reused the saved one, producing back-to-
    back hands with the same shuffle but a rotated dealer.
    """
    sm = _make_state_machine()
    sm._state = sm._state.with_hand_seed(98765, provided=False)
    repo.save_game("seed_consumed", sm, owner_id="o", owner_name="N")

    loaded = repo.load_game("seed_consumed")
    assert loaded.current_hand_seed == 98765
    assert loaded._state.hand_seed_provided is False


def test_load_game_preserves_hand_count_and_blind_config(repo):
    """hand_count and blind_config must round-trip through save/load.

    Regression: state_machine.stats and blind_config used to be dropped on
    save, so restored games re-ran blind escalation from hand 0 and lost
    the user's max_blind cap from custom game settings.
    """
    from poker.poker_state_machine import (
        BlindConfig,
        ImmutableStateMachine,
        PokerStateMachine,
        StateMachineStats,
    )

    sm = _make_state_machine()
    sm._state = ImmutableStateMachine(
        game_state=sm._state.game_state,
        phase=sm._state.phase,
        stats=StateMachineStats(hand_count=17),
        blind_config=BlindConfig(growth=1.5, hands_per_level=10, max_blind=2000),
    )
    repo.save_game("blind_cfg_test", sm, owner_id="o", owner_name="N")

    loaded = repo.load_game("blind_cfg_test")
    assert loaded._state.stats.hand_count == 17
    assert loaded._state.blind_config.growth == 1.5
    assert loaded._state.blind_config.hands_per_level == 10
    assert loaded._state.blind_config.max_blind == 2000


def test_save_game_with_llm_configs(repo):
    sm = _make_state_machine()
    configs = {"default_llm_config": {"provider": "openai", "model": "gpt-4o"}}
    repo.save_game("game1", sm, llm_configs=configs)
    loaded_configs = repo.load_llm_configs("game1")
    assert loaded_configs is not None
    assert loaded_configs["default_llm_config"]["provider"] == "openai"


def test_load_llm_configs_not_found(repo):
    assert repo.load_llm_configs("nonexistent") is None


def test_list_games(repo):
    sm = _make_state_machine()
    repo.save_game("game1", sm, owner_id="owner1", owner_name="Jeff")
    repo.save_game("game2", sm, owner_id="owner1", owner_name="Jeff")
    repo.save_game("game3", sm, owner_id="owner2", owner_name="Other")

    all_games = repo.list_games()
    assert len(all_games) == 3

    owner1_games = repo.list_games(owner_id="owner1")
    assert len(owner1_games) == 2

    limited = repo.list_games(limit=1)
    assert len(limited) == 1


def test_list_games_returns_saved_game_objects(repo):
    sm = _make_state_machine()
    repo.save_game("game1", sm, owner_id="owner1", owner_name="Jeff")
    games = repo.list_games()
    assert len(games) == 1
    assert isinstance(games[0], SavedGame)
    assert games[0].game_id == "game1"
    assert games[0].owner_id == "owner1"


def test_delete_game(repo):
    sm = _make_state_machine()
    repo.save_game("game1", sm)
    repo.save_message("game1", "chat", "Hello")
    repo.save_ai_player_state(
        "game1", "Bob", [{"role": "user", "content": "hi"}], {"mood": "happy"}
    )

    repo.delete_game("game1")
    assert repo.load_game("game1") is None
    assert repo.load_messages("game1") == []
    assert repo.load_ai_player_states("game1") == {}


def test_delete_game_preserves_pressure_events(repo, db_path):
    """pressure_events is historical analytics — survives cash leave / cleanup.

    Regression: cash sessions used to lose every pressure_events row when
    /api/cash/leave called game_repo.delete_game (35-hand sessions ending
    with zero rows). Asymmetric vs hand_history/relationship_states/
    cash_pair_stats (all of which already survive), and contradicts the
    delete_game docstring's "historical data preserved" promise.
    """
    from poker.repositories.sqlite_repositories import PressureEventRepository

    sm = _make_state_machine()
    repo.save_game("game1", sm)

    event_repo = PressureEventRepository(db_path)
    event_repo.save_event(
        game_id="game1",
        player_name="Alice",
        event_type="big_win",
        hand_number=1,
        details={"pot_size": 41564},
    )
    event_repo.save_event(
        game_id="game1",
        player_name="Bob",
        event_type="big_loss",
        hand_number=1,
        details={"pot_size": 41564},
    )

    repo.delete_game("game1")

    surviving = event_repo.get_events_for_game("game1")
    assert len(surviving) == 2
    assert {e["event_type"] for e in surviving} == {"big_win", "big_loss"}


def test_coach_mode(repo):
    sm = _make_state_machine()
    repo.save_game("game1", sm)

    assert repo.load_coach_mode("game1") == 'off'
    repo.save_coach_mode("game1", "on")
    assert repo.load_coach_mode("game1") == "on"


# --- Messages ---


def test_save_and_load_messages(repo):
    repo.save_message("game1", "chat", "Alice: Hello everyone")
    repo.save_message("game1", "action", "Bob: raises $50")

    messages = repo.load_messages("game1")
    assert len(messages) == 2
    assert messages[0]['sender'] == "Alice"
    assert messages[0]['content'] == "Hello everyone"
    assert messages[1]['sender'] == "Bob"


def test_load_messages_system(repo):
    repo.save_message("game1", "system", "Game started")
    messages = repo.load_messages("game1")
    assert len(messages) == 1
    assert messages[0]['sender'] == "System"
    assert messages[0]['content'] == "Game started"


def test_load_messages_empty(repo):
    assert repo.load_messages("game1") == []


def test_load_messages_limit(repo):
    for i in range(10):
        repo.save_message("game1", "chat", f"Alice: msg {i}")
    messages = repo.load_messages("game1", limit=3)
    assert len(messages) == 3


# --- AI Player State ---


def test_ai_player_state_save_load(repo):
    messages = [{"role": "system", "content": "You are a poker player"}]
    personality = {"mood": "confident", "aggression": 0.8}

    repo.save_ai_player_state("game1", "Batman", messages, personality)
    states = repo.load_ai_player_states("game1")

    assert "Batman" in states
    assert states["Batman"]["messages"] == messages
    assert states["Batman"]["personality_state"] == personality


def test_ai_player_state_empty(repo):
    assert repo.load_ai_player_states("game1") == {}


def test_personality_snapshot(repo):
    traits = {"aggression": 0.7, "bluff_tendency": 0.5}
    pressure = {"stack_pressure": 0.3}

    repo.save_personality_snapshot("game1", "Batman", 5, traits, pressure)

    # Verify the snapshot was persisted via raw SQL
    import sqlite3

    conn = sqlite3.connect(repo.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM personality_snapshots WHERE game_id = ? AND player_name = ?",
        ("game1", "Batman"),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["hand_number"] == 5
    assert json.loads(row["personality_traits"]) == traits
    assert json.loads(row["pressure_levels"]) == pressure


def test_save_personality_snapshot_idempotent(repo):
    """T1-32 follow-up: schema v84 added UNIQUE(game_id, player_name,
    hand_number) so retried INSERTs (after a database-locked failure)
    produce exactly one row instead of duplicating the elasticity
    snapshot timeline."""
    traits = {"aggression": 0.7}
    pressure = {"stack_pressure": 0.3}

    repo.save_personality_snapshot("game1", "Batman", 5, traits, pressure)
    repo.save_personality_snapshot("game1", "Batman", 5, traits, pressure)
    repo.save_personality_snapshot("game1", "Batman", 5, traits, pressure)

    import sqlite3

    conn = sqlite3.connect(repo.db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM personality_snapshots "
        "WHERE game_id = ? AND player_name = ? AND hand_number = ?",
        ("game1", "Batman", 5),
    ).fetchone()[0]
    conn.close()
    assert count == 1, f"expected exactly 1 row, got {count}"


# --- Emotional State ---


# --- Controller State ---

# v83 PlayerPsychology snapshot shape — anchors + axes + composure_state
# + book-keeping fields. Mirrors PlayerPsychology.to_dict().
SAMPLE_PSYCHOLOGY_V2 = {
    'player_name': 'Batman',
    'anchors': {
        'baseline_aggression': 0.7,
        'baseline_looseness': 0.4,
        'ego': 0.8,
        'poise': 0.6,
        'expressiveness': 0.5,
        'risk_identity': 0.6,
        'adaptation_bias': 0.5,
        'baseline_energy': 0.6,
        'recovery_rate': 0.15,
    },
    'axes': {'confidence': 0.65, 'composure': 0.45, 'energy': 0.70},
    'composure_state': {
        'pressure_source': 'bad_beat',
        'nemesis': 'Joker',
        'recent_losses': [],
        'losing_streak': 2,
    },
    'hand_count': 12,
    'consecutive_folds': 1,
    'emotional': None,
    'playstyle_state': None,
}


def test_controller_state_save_load(repo):
    prompt_config = {'temperature': 0.7}

    repo.save_controller_state("game1", "Batman", SAMPLE_PSYCHOLOGY_V2, prompt_config)
    loaded = repo.load_controller_state("game1", "Batman")

    assert loaded is not None
    assert loaded['psychology']['axes']['confidence'] == 0.65
    assert loaded['psychology']['composure_state']['nemesis'] == 'Joker'
    assert loaded['psychology']['hand_count'] == 12
    assert loaded['prompt_config']['temperature'] == 0.7
    # Legacy columns are NULL on new writes (kept for backwards-compat).
    assert loaded['tilt_state'] is None
    assert loaded['elastic_personality'] is None


def test_controller_state_not_found(repo):
    assert repo.load_controller_state("game1", "Nobody") is None


def test_load_all_controller_states(repo):
    repo.save_controller_state("game1", "Alice", SAMPLE_PSYCHOLOGY_V2)
    repo.save_controller_state("game1", "Bob", SAMPLE_PSYCHOLOGY_V2)

    states = repo.load_all_controller_states("game1")
    assert len(states) == 2
    assert "Alice" in states
    assert "Bob" in states
    assert states['Alice']['psychology']['axes']['confidence'] == 0.65


def test_delete_controller_state(repo):
    repo.save_controller_state("game1", "Alice", SAMPLE_PSYCHOLOGY_V2)
    repo.delete_controller_state_for_game("game1")
    assert repo.load_all_controller_states("game1") == {}


def test_controller_state_null_psychology_loads_as_none(repo):
    """T1-29: pre-v83 rows have NULL psychology_json; restore must return
    None so the controller falls back to fresh-init rather than crashing."""
    # Insert directly to simulate a pre-v83 row.
    with repo._get_connection() as conn:
        conn.execute(
            "INSERT INTO controller_state (game_id, player_name, psychology_json) "
            "VALUES (?, ?, NULL)",
            ("game1", "Legacy"),
        )
    loaded = repo.load_controller_state("game1", "Legacy")
    assert loaded is not None
    assert loaded['psychology'] is None


def test_controller_state_psychology_round_trip(repo):
    """T1-29: a saved psychology dict round-trips byte-for-byte through
    JSON serialization."""
    repo.save_controller_state("game1", "Batman", SAMPLE_PSYCHOLOGY_V2)
    loaded = repo.load_controller_state("game1", "Batman")
    assert loaded['psychology'] == SAMPLE_PSYCHOLOGY_V2


# --- Opponent Models ---


def test_opponent_models_save_load(repo):
    models = {
        "Alice": {
            "Bob": {
                "tendencies": {
                    "hands_observed": 10,
                    "vpip": 0.6,
                    "pfr": 0.3,
                    "aggression_factor": 1.5,
                    "fold_to_cbet": 0.4,
                    "bluff_frequency": 0.2,
                    "showdown_win_rate": 0.55,
                    "recent_trend": "aggressive",
                },
                "memorable_hands": [
                    {
                        "hand_id": 5,
                        "memory_type": "big_bluff",
                        "impact_score": 0.9,
                        "narrative": "Bluffed with nothing",
                        "timestamp": "2024-01-01T00:00:00",
                    }
                ],
                "narrative_observations": ["Aggressive player"],
            }
        }
    }

    repo.save_opponent_models("game1", models)
    loaded = repo.load_opponent_models("game1")

    assert "Alice" in loaded
    assert "Bob" in loaded["Alice"]
    bob_model = loaded["Alice"]["Bob"]
    assert bob_model["tendencies"]["hands_observed"] == 10
    assert bob_model["tendencies"]["vpip"] == 0.6
    assert bob_model["narrative_observations"] == ["Aggressive player"]
    assert len(bob_model["memorable_hands"]) == 1
    assert bob_model["memorable_hands"][0]["memory_type"] == "big_bluff"


def test_opponent_model_full_tendencies_survive_reload_and_next_hand(repo):
    manager = OpponentModelManager()
    model = manager.get_model("Alice", "Bob")

    for hand_number in range(1, 11):
        model.record_hand_dealt(hand_number=hand_number)
    for hand_number in (1, 3, 5):
        model.observe_action("call", "PRE_FLOP", hand_number=hand_number)
    for hand_number in (7, 9):
        model.observe_action("all_in", "PRE_FLOP", hand_number=hand_number)

    repo.save_opponent_models("game1", manager)
    loaded = repo.load_opponent_models("game1")
    restored = OpponentModelManager.from_dict(loaded)
    restored_model = restored.get_model("Alice", "Bob")

    # Continue the original and reloaded paths identically. Rates should match
    # exactly because persisted counters and hands_dealt were restored, not
    # reconstructed from rounded legacy rates.
    model.record_hand_dealt(hand_number=11)
    restored_model.record_hand_dealt(hand_number=11)

    assert restored_model.tendencies.hands_dealt == model.tendencies.hands_dealt
    assert restored_model.tendencies.hands_observed == model.tendencies.hands_observed
    assert restored_model.tendencies._vpip_count == model.tendencies._vpip_count
    assert restored_model.tendencies._pfr_count == model.tendencies._pfr_count
    assert restored_model.tendencies._all_in_count == model.tendencies._all_in_count
    assert restored_model.tendencies.vpip == pytest.approx(model.tendencies.vpip)
    assert restored_model.tendencies.pfr == pytest.approx(model.tendencies.pfr)
    assert restored_model.tendencies.all_in_frequency == pytest.approx(
        model.tendencies.all_in_frequency
    )


def test_opponent_models_empty(repo):
    assert repo.load_opponent_models("game1") == {}


def test_opponent_models_empty_dict_no_op(repo):
    repo.save_opponent_models("game1", {})
    assert repo.load_opponent_models("game1") == {}


def test_delete_opponent_models(repo):
    models = {
        "Alice": {
            "Bob": {
                "tendencies": {"hands_observed": 5, "vpip": 0.5, "pfr": 0.5},
                "memorable_hands": [],
            }
        }
    }
    repo.save_opponent_models("game1", models)
    repo.delete_opponent_models_for_game("game1")
    assert repo.load_opponent_models("game1") == {}


def test_opponent_models_narrative_observations_roundtrip(repo):
    """Narrative observations survive save/load cycle via notes column."""
    models = {
        "Alice": {
            "Bob": {
                "observer": "Alice",
                "opponent": "Bob",
                "tendencies": {
                    "hands_observed": 10,
                    "vpip": 0.6,
                    "pfr": 0.3,
                    "aggression_factor": 1.5,
                },
                "memorable_hands": [],
                "narrative_observations": [
                    "Overvalues top pair",
                    "Bluffs missed draws",
                ],
            }
        }
    }

    repo.save_opponent_models("game1", models)
    loaded = repo.load_opponent_models("game1")

    assert "Alice" in loaded
    assert "Bob" in loaded["Alice"]
    bob_model = loaded["Alice"]["Bob"]
    assert bob_model["narrative_observations"] == [
        "Overvalues top pair",
        "Bluffs missed draws",
    ]


def test_load_opponent_models_json_decode_error(repo):
    """Invalid JSON in notes column should not crash, returns legacy text."""
    sm = _make_state_machine()
    repo.save_game("game1", sm, owner_id="user1")

    # Manually insert a row with invalid JSON in notes
    import sqlite3

    conn = sqlite3.connect(repo.db_path)
    conn.execute(
        """
        INSERT INTO opponent_models
        (game_id, observer_name, opponent_name, hands_observed, vpip, pfr,
         aggression_factor, fold_to_cbet, bluff_frequency, showdown_win_rate,
         recent_trend, notes, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """,
        ("game1", "Alice", "Bob", 10, 0.5, 0.3, 1.5, 0.5, 0.3, 0.5, "stable", "not valid json"),
    )
    conn.commit()
    conn.close()

    # Should not raise, should return the raw text as a legacy note
    loaded = repo.load_opponent_models("game1")
    assert "Alice" in loaded
    assert "Bob" in loaded["Alice"]
    # With the fix, this returns ["not valid json"] as legacy format
    assert loaded["Alice"]["Bob"]["narrative_observations"] == ["not valid json"]


# --- Cross-Session Opponent Models ---


def test_cross_session_opponent_models_aggregation(repo):
    """Stats are aggregated across games with weighted averages."""
    sm = _make_state_machine()

    # Game 1: Alice observes Bob for 10 hands, VPIP=0.6
    repo.save_game("game1", sm, owner_id="user1", owner_name="Alice")
    repo.save_opponent_models(
        "game1",
        {
            "Alice": {
                "Bob": {
                    "tendencies": {
                        "hands_observed": 10,
                        "vpip": 0.6,
                        "pfr": 0.3,
                        "aggression_factor": 2.0,
                    },
                    "memorable_hands": [],
                    "narrative_observations": ["Plays loose"],
                }
            }
        },
    )

    # Game 2: Alice observes Bob for 30 hands, VPIP=0.4
    repo.save_game("game2", sm, owner_id="user1", owner_name="Alice")
    repo.save_opponent_models(
        "game2",
        {
            "Alice": {
                "Bob": {
                    "tendencies": {
                        "hands_observed": 30,
                        "vpip": 0.4,
                        "pfr": 0.2,
                        "aggression_factor": 1.0,
                    },
                    "memorable_hands": [],
                    "narrative_observations": ["Tightened up"],
                }
            }
        },
    )

    # Load cross-session data
    result = repo.load_cross_session_opponent_models("Alice", "user1")

    assert "Bob" in result
    bob = result["Bob"]

    # Session count should be 2
    assert bob["session_count"] == 2

    # Total hands = 10 + 30 = 40
    assert bob["total_hands"] == 40

    # Weighted VPIP = (0.6*10 + 0.4*30) / 40 = 18/40 = 0.45
    assert bob["vpip"] == 0.45

    # Weighted PFR = (0.3*10 + 0.2*30) / 40 = 9/40 = 0.225
    assert bob["pfr"] == 0.225

    # Weighted aggression = (2.0*10 + 1.0*30) / 40 = 50/40 = 1.25
    assert bob["aggression_factor"] == 1.25

    # Notes from both sessions
    assert "Plays loose" in bob["notes"]
    assert "Tightened up" in bob["notes"]


def test_cross_session_distinct_session_count(repo):
    """session_count is the count of distinct game_ids."""
    sm = _make_state_machine()

    # Save 3 games but only 2 with Bob data
    repo.save_game("game1", sm, owner_id="user1", owner_name="Alice")
    repo.save_opponent_models(
        "game1",
        {
            "Alice": {
                "Bob": {"tendencies": {"hands_observed": 5, "vpip": 0.5}, "memorable_hands": []}
            }
        },
    )

    repo.save_game("game2", sm, owner_id="user1", owner_name="Alice")
    repo.save_opponent_models(
        "game2",
        {
            "Alice": {
                "Bob": {"tendencies": {"hands_observed": 5, "vpip": 0.5}, "memorable_hands": []}
            }
        },
    )

    repo.save_game("game3", sm, owner_id="user1", owner_name="Alice")
    # game3 has no opponent models for Bob

    result = repo.load_cross_session_opponent_models("Alice", "user1")
    assert result["Bob"]["session_count"] == 2


def test_cross_session_no_data_for_guest(repo):
    """Guest users (no user_id) get no historical data."""
    result = repo.load_cross_session_opponent_models("Alice", None)
    assert result == {}

    result = repo.load_cross_session_opponent_models("Alice", "")
    assert result == {}


def test_cross_session_filters_by_owner_id(repo):
    """Only aggregates games owned by the specified user."""
    sm = _make_state_machine()

    # User1's game
    repo.save_game("game1", sm, owner_id="user1", owner_name="Alice")
    repo.save_opponent_models(
        "game1",
        {
            "Alice": {
                "Bob": {"tendencies": {"hands_observed": 10, "vpip": 0.6}, "memorable_hands": []}
            }
        },
    )

    # User2's game - should not be included
    repo.save_game("game2", sm, owner_id="user2", owner_name="Other")
    repo.save_opponent_models(
        "game2",
        {
            "Alice": {
                "Bob": {"tendencies": {"hands_observed": 10, "vpip": 0.2}, "memorable_hands": []}
            }
        },
    )

    result = repo.load_cross_session_opponent_models("Alice", "user1")

    assert "Bob" in result
    assert result["Bob"]["session_count"] == 1
    assert result["Bob"]["vpip"] == 0.6  # Only user1's data


def test_cross_session_excludes_zero_hand_observations(repo):
    """Opponent models with hands_observed=0 are excluded from aggregation."""
    sm = _make_state_machine()

    repo.save_game("game1", sm, owner_id="user1", owner_name="Alice")
    repo.save_opponent_models(
        "game1",
        {
            "Alice": {
                "Bob": {"tendencies": {"hands_observed": 0, "vpip": 0.9}, "memorable_hands": []}
            }
        },
    )

    result = repo.load_cross_session_opponent_models("Alice", "user1")
    assert "Bob" not in result
