"""Tests for GameRepository."""
import json
import pytest
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.game_repository import GameRepository, SavedGame
from poker.poker_game import PokerGameState, Player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from core.card import Card


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    SchemaManager(db_path).ensure_schema()
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
    repo.save_ai_player_state("game1", "Bob", [{"role": "user", "content": "hi"}], {"mood": "happy"})

    repo.delete_game("game1")
    assert repo.load_game("game1") is None
    assert repo.load_messages("game1") == []
    assert repo.load_ai_player_states("game1") == {}


def test_coach_mode(repo):
    sm = _make_state_machine()
    repo.save_game("game1", sm)

    assert repo.load_coach_mode("game1") == 'off'
    repo.save_coach_mode("game1", "on")
    assert repo.load_coach_mode("game1") == "on"


# --- Tournament Tracker ---

def test_tournament_tracker_save_load(repo):
    tracker_data = {"round": 1, "players_remaining": 4}
    repo.save_tournament_tracker("game1", tracker_data)
    loaded = repo.load_tournament_tracker("game1")
    assert loaded == tracker_data


def test_tournament_tracker_not_found(repo):
    assert repo.load_tournament_tracker("nonexistent") is None


def test_tournament_tracker_upsert(repo):
    repo.save_tournament_tracker("game1", {"round": 1})
    repo.save_tournament_tracker("game1", {"round": 2})
    loaded = repo.load_tournament_tracker("game1")
    assert loaded["round"] == 2


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
    # No direct load method for snapshots — just verify no error
    # Snapshots are read by other analysis queries


# --- Emotional State ---

def test_emotional_state_save_load(repo):
    state = {
        'valence': 0.5,
        'arousal': 0.7,
        'control': 0.6,
        'focus': 0.8,
        'narrative': 'Feeling confident',
        'inner_voice': 'I got this',
        'generated_at_hand': 3,
        'source_events': ['won_big_pot'],
        'created_at': '2024-01-01T00:00:00',
        'used_fallback': False,
    }

    repo.save_emotional_state("game1", "Alice", state)
    loaded = repo.load_emotional_state("game1", "Alice")

    assert loaded is not None
    assert loaded['valence'] == 0.5
    assert loaded['arousal'] == 0.7
    assert loaded['narrative'] == 'Feeling confident'
    assert loaded['source_events'] == ['won_big_pot']


def test_emotional_state_not_found(repo):
    assert repo.load_emotional_state("game1", "Nobody") is None


def test_load_all_emotional_states(repo):
    repo.save_emotional_state("game1", "Alice", {'valence': 0.5, 'narrative': 'ok'})
    repo.save_emotional_state("game1", "Bob", {'valence': -0.3, 'narrative': 'bad'})

    states = repo.load_all_emotional_states("game1")
    assert len(states) == 2
    assert "Alice" in states
    assert "Bob" in states


def test_delete_emotional_state(repo):
    repo.save_emotional_state("game1", "Alice", {'valence': 0.5})
    repo.delete_emotional_state_for_game("game1")
    assert repo.load_all_emotional_states("game1") == {}


# --- Controller State ---

def test_controller_state_save_load(repo):
    psychology = {
        'tilt': {'level': 0.3, 'type': 'frustration'},
        'elastic': {'aggression_modifier': 1.2},
    }
    prompt_config = {'temperature': 0.7}

    repo.save_controller_state("game1", "Batman", psychology, prompt_config)
    loaded = repo.load_controller_state("game1", "Batman")

    assert loaded is not None
    assert loaded['tilt_state']['level'] == 0.3
    assert loaded['elastic_personality']['aggression_modifier'] == 1.2
    assert loaded['prompt_config']['temperature'] == 0.7


def test_controller_state_not_found(repo):
    assert repo.load_controller_state("game1", "Nobody") is None


def test_load_all_controller_states(repo):
    repo.save_controller_state("game1", "Alice", {'tilt': None, 'elastic': None})
    repo.save_controller_state("game1", "Bob", {'tilt': {'level': 0.5}, 'elastic': None})

    states = repo.load_all_controller_states("game1")
    assert len(states) == 2
    assert "Alice" in states
    assert "Bob" in states


def test_delete_controller_state(repo):
    repo.save_controller_state("game1", "Alice", {'tilt': None, 'elastic': None})
    repo.delete_controller_state_for_game("game1")
    assert repo.load_all_controller_states("game1") == {}


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
                "notes": "Aggressive player",
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
    assert bob_model["notes"] == "Aggressive player"
    assert len(bob_model["memorable_hands"]) == 1
    assert bob_model["memorable_hands"][0]["memory_type"] == "big_bluff"


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
