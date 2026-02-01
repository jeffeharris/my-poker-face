"""Tests for PersonalityRepository."""
import json
import pytest
from poker.repositories.personality_repository import PersonalityRepository


@pytest.fixture
def repo(db_path):
    r = PersonalityRepository(db_path)
    yield r
    r.close()


# --- Personality CRUD ---

def test_save_and_load_personality(repo):
    config = {"play_style": "aggressive", "confidence": 0.8}
    repo.save_personality("TestBot", config)
    loaded = repo.load_personality("TestBot")
    assert loaded is not None
    assert loaded["play_style"] == "aggressive"
    assert loaded["confidence"] == 0.8


def test_load_personality_not_found(repo):
    assert repo.load_personality("Nonexistent") is None


def test_save_personality_with_elasticity_config(repo):
    config = {
        "play_style": "cautious",
        "elasticity_config": {"pressure_sensitivity": 0.5}
    }
    repo.save_personality("ElasticBot", config)
    loaded = repo.load_personality("ElasticBot")
    assert loaded is not None
    assert loaded["play_style"] == "cautious"
    # elasticity_config should be stored separately and merged back
    assert "elasticity_config" in loaded
    assert loaded["elasticity_config"]["pressure_sensitivity"] == 0.5


def test_load_personality_increments_usage(repo):
    config = {"play_style": "passive"}
    repo.save_personality("UsageBot", config)

    repo.load_personality("UsageBot")
    repo.load_personality("UsageBot")

    personalities = repo.list_personalities()
    usage_bot = next(p for p in personalities if p["name"] == "UsageBot")
    # 2 loads = 2 increments
    assert usage_bot["times_used"] >= 2


def test_list_personalities(repo):
    repo.save_personality("Bot1", {"style": "a"})
    repo.save_personality("Bot2", {"style": "b"})

    result = repo.list_personalities()
    names = [p["name"] for p in result]
    assert "Bot1" in names
    assert "Bot2" in names


def test_list_personalities_with_limit(repo):
    for i in range(5):
        repo.save_personality(f"Bot{i}", {"style": str(i)})

    result = repo.list_personalities(limit=3)
    assert len(result) == 3


def test_delete_personality(repo):
    repo.save_personality("ToDelete", {"style": "x"})
    assert repo.delete_personality("ToDelete") is True
    assert repo.load_personality("ToDelete") is None


def test_delete_personality_not_found(repo):
    assert repo.delete_personality("Nonexistent") is False


def test_save_personality_upsert(repo):
    repo.save_personality("UpsertBot", {"style": "v1"})
    repo.save_personality("UpsertBot", {"style": "v2"})
    loaded = repo.load_personality("UpsertBot")
    assert loaded["style"] == "v2"


def test_seed_personalities_from_json(repo, tmp_path):
    json_data = {
        "personalities": {
            "Alice": {"play_style": "tight"},
            "Bob": {"play_style": "loose"}
        }
    }
    json_file = tmp_path / "personalities.json"
    json_file.write_text(json.dumps(json_data))

    result = repo.seed_personalities_from_json(str(json_file))
    assert result["added"] == 2
    assert result["skipped"] == 0

    assert repo.load_personality("Alice") is not None
    assert repo.load_personality("Bob") is not None


def test_seed_personalities_skips_existing(repo, tmp_path):
    repo.save_personality("Alice", {"play_style": "original"})

    json_data = {
        "personalities": {
            "Alice": {"play_style": "new"},
            "Charlie": {"play_style": "fresh"}
        }
    }
    json_file = tmp_path / "personalities.json"
    json_file.write_text(json.dumps(json_data))

    result = repo.seed_personalities_from_json(str(json_file))
    assert result["skipped"] == 1
    assert result["added"] == 1


def test_seed_personalities_file_not_found(repo):
    result = repo.seed_personalities_from_json("/nonexistent/path.json")
    assert "error" in result


# --- Avatar CRUD ---

def test_save_and_load_avatar(repo):
    image_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    repo.save_avatar_image("TestBot", "happy", image_data)

    loaded = repo.load_avatar_image("TestBot", "happy")
    assert loaded == image_data


def test_load_avatar_not_found(repo):
    assert repo.load_avatar_image("Nobody", "happy") is None


def test_load_avatar_with_metadata(repo):
    image_data = b"\x89PNG" + b"\x00" * 50
    repo.save_avatar_image("MetaBot", "confident", image_data, width=128, height=128)

    result = repo.load_avatar_image_with_metadata("MetaBot", "confident")
    assert result is not None
    assert result["image_data"] == image_data
    assert result["width"] == 128
    assert result["height"] == 128
    assert result["file_size"] == len(image_data)


def test_save_and_load_full_avatar(repo):
    icon = b"\x89PNG_icon"
    full = b"\x89PNG_full_image_much_larger"
    repo.save_avatar_image("FullBot", "angry", icon,
                           full_image_data=full, full_width=512, full_height=512)

    loaded_full = repo.load_full_avatar_image("FullBot", "angry")
    assert loaded_full == full


def test_has_avatar_image(repo):
    assert repo.has_avatar_image("NoBot", "happy") is False
    repo.save_avatar_image("HasBot", "happy", b"\x00" * 10)
    assert repo.has_avatar_image("HasBot", "happy") is True


def test_has_full_avatar_image(repo):
    repo.save_avatar_image("PartialBot", "happy", b"\x00" * 10)
    assert repo.has_full_avatar_image("PartialBot", "happy") is False

    repo.save_avatar_image("FullBot", "happy", b"\x00" * 10,
                           full_image_data=b"\x00" * 20, full_width=512, full_height=512)
    assert repo.has_full_avatar_image("FullBot", "happy") is True


def test_get_available_avatar_emotions(repo):
    repo.save_avatar_image("EmotionBot", "happy", b"\x00" * 10)
    repo.save_avatar_image("EmotionBot", "angry", b"\x00" * 10)
    repo.save_avatar_image("EmotionBot", "confident", b"\x00" * 10)

    emotions = repo.get_available_avatar_emotions("EmotionBot")
    assert sorted(emotions) == ["angry", "confident", "happy"]


def test_has_all_avatar_emotions(repo):
    assert repo.has_all_avatar_emotions("IncompleteBot") is False

    for emotion in ["confident", "happy", "thinking", "nervous", "angry", "shocked"]:
        repo.save_avatar_image("CompleteBot", emotion, b"\x00" * 10)

    assert repo.has_all_avatar_emotions("CompleteBot") is True


def test_delete_avatar_images(repo):
    repo.save_avatar_image("DeleteBot", "happy", b"\x00" * 10)
    repo.save_avatar_image("DeleteBot", "angry", b"\x00" * 10)

    count = repo.delete_avatar_images("DeleteBot")
    assert count == 2
    assert repo.load_avatar_image("DeleteBot", "happy") is None


def test_list_personalities_with_avatars(repo):
    repo.save_avatar_image("AvatarBot1", "happy", b"\x00" * 10)
    repo.save_avatar_image("AvatarBot1", "angry", b"\x00" * 10)
    repo.save_avatar_image("AvatarBot2", "happy", b"\x00" * 10)

    result = repo.list_personalities_with_avatars()
    names = {r["personality_name"]: r["emotion_count"] for r in result}
    assert names["AvatarBot1"] == 2
    assert names["AvatarBot2"] == 1


def test_get_avatar_stats(repo):
    stats = repo.get_avatar_stats()
    assert stats["total_images"] == 0

    repo.save_avatar_image("StatsBot", "happy", b"\x00" * 100)
    repo.save_avatar_image("StatsBot", "angry", b"\x00" * 200)

    stats = repo.get_avatar_stats()
    assert stats["total_images"] == 2
    assert stats["total_size_bytes"] == 300
    assert stats["personality_count"] == 1
