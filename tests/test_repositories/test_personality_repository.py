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
    config = {"play_style": "cautious", "elasticity_config": {"pressure_sensitivity": 0.5}}
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


def test_resave_preserves_times_used(repo):
    """Regression: re-saving an existing persona (avatar regen, edit,
    re-seed) must NOT reset times_used. The old INSERT OR REPLACE did a
    DELETE+INSERT that zeroed it; the fix upserts via ON CONFLICT DO
    UPDATE leaving usage/birth-time/id untouched."""
    repo.save_personality("EditBot", {"play_style": "tight"})
    repo.load_personality("EditBot")
    repo.load_personality("EditBot")
    repo.load_personality("EditBot")  # times_used -> 3

    before = next(p for p in repo.list_personalities() if p["name"] == "EditBot")
    assert before["times_used"] >= 3

    # An edit re-save (no usage bump) must keep the count.
    repo.save_personality("EditBot", {"play_style": "loose", "confidence": 0.9})

    after = next(p for p in repo.list_personalities() if p["name"] == "EditBot")
    assert after["times_used"] == before["times_used"]  # NOT reset to 0
    # ...and the edit still landed.
    assert repo.load_personality("EditBot")["play_style"] == "loose"


def test_resave_preserves_id_and_created_at(repo):
    """Re-save must keep the AUTOINCREMENT id and original created_at
    (DELETE+INSERT reallocated id and reset created_at to now)."""
    import sqlite3

    repo.save_personality("StableBot", {"play_style": "tight"})
    with sqlite3.connect(repo.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row1 = conn.execute(
            "SELECT id, created_at FROM personalities WHERE name = ?", ("StableBot",)
        ).fetchone()

    repo.save_personality("StableBot", {"play_style": "loose"})
    with sqlite3.connect(repo.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row2 = conn.execute(
            "SELECT id, created_at FROM personalities WHERE name = ?", ("StableBot",)
        ).fetchone()

    assert row2["id"] == row1["id"]
    assert row2["created_at"] == row1["created_at"]


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


def test_list_personalities_circulating_only(repo):
    # A circulating public persona, a demoted public persona (circulating=0),
    # and the caller's own private persona.
    repo.save_personality("Star", {"style": "a"}, circulating=True)
    repo.save_personality("Zombie", {"style": "b"}, circulating=False)
    repo.save_personality(
        "Mine", {"style": "c"}, owner_id="user_1", visibility="private", circulating=False
    )

    # Default (management view): everything public shows, including the zombie.
    default_names = {p["name"] for p in repo.list_personalities(user_id="user_1")}
    assert {"Star", "Zombie", "Mine"} <= default_names

    # Player-facing: the demoted zombie is hidden, but Star (circulating) and
    # Mine (the user's own) remain.
    gated_names = {
        p["name"] for p in repo.list_personalities(user_id="user_1", circulating_only=True)
    }
    assert "Star" in gated_names
    assert "Mine" in gated_names
    assert "Zombie" not in gated_names


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
        "personalities": {"Alice": {"play_style": "tight"}, "Bob": {"play_style": "loose"}}
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
        "personalities": {"Alice": {"play_style": "new"}, "Charlie": {"play_style": "fresh"}}
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


# v138: avatar_images is keyed SOLELY on `personality_id`. An avatar can only be
# stored for a real persona (a row in `personalities`); a key matching no persona
# is a no-op. So these tests seed the persona first. `save_personality` assigns
# the slug pid, and avatar methods accept EITHER the display name or the pid (both
# resolve to the same canonical id at the storage boundary).


def test_save_and_load_avatar(repo):
    repo.save_personality("TestBot", {"play_style": "aggressive"})
    image_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    repo.save_avatar_image("TestBot", "happy", image_data)

    loaded = repo.load_avatar_image("TestBot", "happy")
    assert loaded == image_data


def test_load_avatar_not_found(repo):
    assert repo.load_avatar_image("Nobody", "happy") is None


def test_save_for_non_persona_is_noop(repo):
    """v138: avatars are keyed on personality_id, so a key matching no persona
    can't be stored (nothing to key it on) — the save is a logged no-op."""
    repo.save_avatar_image("NotAPersona", "happy", b"\x00" * 10)
    assert repo.load_avatar_image("NotAPersona", "happy") is None
    assert repo.has_avatar_image("NotAPersona", "happy") is False


def test_avatar_keyed_on_personality_id(repo, db_path):
    """v138: avatars key SOLELY on the stable `personality_id`. A save by id OR
    display name resolves to the same canonical id, and a load by EITHER key finds
    the SAME avatar — so a tournament (looks up by id) and a cash game (looks up by
    display name) hit one shared avatar. The legacy `personality_name` column is
    gone; storage is pid-only."""
    import sqlite3

    pid = repo.save_personality("Napoleon", {"play_style": "aggressive"})

    img = b"\x89PNG" + b"\x00" * 20
    repo.save_avatar_image(pid, "happy", img)  # written by the id (tournament path)

    assert repo.load_avatar_image(pid, "happy") == img  # tournament lookup (id)
    assert repo.load_avatar_image("Napoleon", "happy") == img  # cash lookup (name)
    assert repo.has_avatar_image(pid, "happy")

    # Storage is keyed on personality_id; the legacy name column no longer exists.
    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(avatar_images)")}
    row = conn.execute("SELECT personality_id FROM avatar_images WHERE emotion='happy'").fetchone()
    conn.close()
    assert "personality_name" not in cols  # legacy column dropped (v138)
    assert row == (pid,)

    # A save BY DISPLAY NAME resolves to the same id.
    repo.save_avatar_image("Napoleon", "angry", b"X")
    assert repo.load_avatar_image(pid, "angry") == b"X"


def test_load_avatar_with_metadata(repo):
    repo.save_personality("MetaBot", {})
    image_data = b"\x89PNG" + b"\x00" * 50
    repo.save_avatar_image("MetaBot", "confident", image_data, width=128, height=128)

    result = repo.load_avatar_image_with_metadata("MetaBot", "confident")
    assert result is not None
    assert result["image_data"] == image_data
    assert result["width"] == 128
    assert result["height"] == 128
    assert result["file_size"] == len(image_data)


def test_save_and_load_full_avatar(repo):
    repo.save_personality("FullBot", {})
    icon = b"\x89PNG_icon"
    full = b"\x89PNG_full_image_much_larger"
    repo.save_avatar_image(
        "FullBot", "angry", icon, full_image_data=full, full_width=512, full_height=512
    )

    loaded_full = repo.load_full_avatar_image("FullBot", "angry")
    assert loaded_full == full


def test_has_avatar_image(repo):
    repo.save_personality("HasBot", {})
    assert repo.has_avatar_image("HasBot", "happy") is False
    repo.save_avatar_image("HasBot", "happy", b"\x00" * 10)
    assert repo.has_avatar_image("HasBot", "happy") is True


def test_has_full_avatar_image(repo):
    repo.save_personality("PartialBot", {})
    repo.save_personality("FullBot", {})
    repo.save_avatar_image("PartialBot", "happy", b"\x00" * 10)
    assert repo.has_full_avatar_image("PartialBot", "happy") is False

    repo.save_avatar_image(
        "FullBot",
        "happy",
        b"\x00" * 10,
        full_image_data=b"\x00" * 20,
        full_width=512,
        full_height=512,
    )
    assert repo.has_full_avatar_image("FullBot", "happy") is True


def test_get_available_avatar_emotions(repo):
    repo.save_personality("EmotionBot", {})
    repo.save_avatar_image("EmotionBot", "happy", b"\x00" * 10)
    repo.save_avatar_image("EmotionBot", "angry", b"\x00" * 10)
    repo.save_avatar_image("EmotionBot", "confident", b"\x00" * 10)

    emotions = repo.get_available_avatar_emotions("EmotionBot")
    assert sorted(emotions) == ["angry", "confident", "happy"]


def test_has_all_avatar_emotions(repo):
    repo.save_personality("CompleteBot", {})
    assert repo.has_all_avatar_emotions("CompleteBot") is False

    for emotion in ["confident", "happy", "thinking", "nervous", "angry", "shocked"]:
        repo.save_avatar_image("CompleteBot", emotion, b"\x00" * 10)

    assert repo.has_all_avatar_emotions("CompleteBot") is True


def test_delete_avatar_images(repo):
    repo.save_personality("DeleteBot", {})
    repo.save_avatar_image("DeleteBot", "happy", b"\x00" * 10)
    repo.save_avatar_image("DeleteBot", "angry", b"\x00" * 10)

    count = repo.delete_avatar_images("DeleteBot")
    assert count == 2
    assert repo.load_avatar_image("DeleteBot", "happy") is None


def test_list_personalities_with_avatars(repo):
    repo.save_personality("AvatarBot1", {})
    repo.save_personality("AvatarBot2", {})
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

    repo.save_personality("StatsBot", {})
    repo.save_avatar_image("StatsBot", "happy", b"\x00" * 100)
    repo.save_avatar_image("StatsBot", "angry", b"\x00" * 200)

    stats = repo.get_avatar_stats()
    assert stats["total_images"] == 2
    assert stats["total_size_bytes"] == 300
    assert stats["personality_count"] == 1


# --- personality_id handling (v85 onward) ---


class TestPersonalityIdSaveLoad:
    def test_save_generates_personality_id_from_name(self, repo):
        returned = repo.save_personality("Bob Ross", {"play_style": "calm"})
        assert returned == "bob_ross"

    def test_load_returns_personality_id_in_config(self, repo):
        repo.save_personality("Bob Ross", {"play_style": "calm"})
        loaded = repo.load_personality("Bob Ross")
        assert loaded["id"] == "bob_ross"

    def test_explicit_personality_id_wins_over_name_slug(self, repo):
        returned = repo.save_personality(
            "Bob Ross", {"play_style": "calm"}, personality_id="legacy_bob_id"
        )
        assert returned == "legacy_bob_id"
        loaded = repo.load_personality("Bob Ross")
        assert loaded["id"] == "legacy_bob_id"

    def test_json_id_hint_used_when_no_explicit_arg(self, repo):
        # Mirrors the seed-from-json path: config carries `id`, save
        # method picks it up.
        returned = repo.save_personality(
            "Bob Ross",
            {"play_style": "calm", "id": "bob_ross_v_seed"},
        )
        assert returned == "bob_ross_v_seed"

    def test_explicit_arg_beats_config_id(self, repo):
        returned = repo.save_personality(
            "Bob Ross",
            {"play_style": "calm", "id": "from_config"},
            personality_id="explicit",
        )
        assert returned == "explicit"

    def test_existing_id_preserved_on_resave(self, repo):
        """Re-saving an existing personality (e.g. renamed display name)
        keeps the original id rather than re-slugifying. Identity is
        meant to be stable across renames."""
        first = repo.save_personality("Bob Ross", {"play_style": "calm"})
        # Save again — same name, no explicit id — should preserve.
        second = repo.save_personality("Bob Ross", {"play_style": "different"})
        assert second == first == "bob_ross"

    def test_explicit_id_collision_raises(self, repo):
        # The explicit id="bob" is taken by a DIFFERENT name. The upsert
        # keys on name (no name conflict here), so the personality_id
        # UNIQUE constraint fires and the save raises. This is the
        # intended surface: an explicit-id collision fails loudly. (The
        # old INSERT OR REPLACE silently DELETED the original "Bob" row to
        # resolve the personality_id conflict — clobbering a different
        # persona. The ON CONFLICT(name) upsert no longer does that.)
        # Auto-collision resolution (assign_unique_personality_id) only
        # runs when NO explicit id is passed; callers passing explicit ids
        # own collision avoidance.
        import sqlite3

        repo.save_personality("Bob", {"play_style": "calm"}, personality_id="bob")
        with pytest.raises(sqlite3.IntegrityError):
            repo.save_personality("Bob the Builder", {"play_style": "calm"}, personality_id="bob")
        # The original persona is untouched (not silently clobbered).
        assert repo.load_personality_by_id("bob")["name"] == "Bob"


class TestLoadPersonalityById:
    def test_load_by_id_returns_config_with_name(self, repo):
        repo.save_personality("Bob Ross", {"play_style": "calm"})
        loaded = repo.load_personality_by_id("bob_ross")
        assert loaded is not None
        assert loaded["id"] == "bob_ross"
        assert loaded["name"] == "Bob Ross"
        assert loaded["play_style"] == "calm"

    def test_load_by_id_returns_none_for_unknown(self, repo):
        assert repo.load_personality_by_id("does_not_exist") is None

    def test_load_by_id_returns_elasticity_config(self, repo):
        repo.save_personality(
            "Elastic Bob",
            {"play_style": "calm", "elasticity_config": {"sensitivity": 0.7}},
        )
        loaded = repo.load_personality_by_id("elastic_bob")
        assert loaded is not None
        assert loaded["elasticity_config"]["sensitivity"] == 0.7

    def test_load_by_id_increments_times_used(self, repo):
        repo.save_personality("Bob Ross", {"play_style": "calm"})
        repo.load_personality_by_id("bob_ross")
        repo.load_personality_by_id("bob_ross")
        personalities = repo.list_personalities()
        bob = next(p for p in personalities if p["name"] == "Bob Ross")
        assert bob["times_used"] >= 2


class TestResolveNameToPersonalityId:
    def test_resolves_known_name(self, repo):
        repo.save_personality("Bob Ross", {"play_style": "calm"})
        assert repo.resolve_name_to_personality_id("Bob Ross") == "bob_ross"

    def test_returns_none_for_unknown_name(self, repo):
        assert repo.resolve_name_to_personality_id("Mystery Person") is None


class TestSeedFromJsonAlignsIds:
    def test_seed_path_writes_json_id_to_db(self, repo, tmp_path):
        """seed_personalities_from_json should pick up the `id` field
        from JSON entries and write it to personality_id. This is the
        bridge between the JSON-side backfill (commit 92293f5b) and the
        DB-side schema (commit d738ddb2)."""
        seed_data = {
            "personalities": {
                "Test Hero": {
                    "id": "seeded_hero_id",
                    "play_style": "test",
                    "anchors": {"baseline_aggression": 0.5},
                }
            }
        }
        seed_file = tmp_path / "personalities_test.json"
        seed_file.write_text(json.dumps(seed_data))

        result = repo.seed_personalities_from_json(str(seed_file))
        assert result["added"] == 1

        # ID from JSON should land in the DB
        assert repo.resolve_name_to_personality_id("Test Hero") == "seeded_hero_id"

    def test_seed_preserves_existing_id_on_update(self, repo, tmp_path):
        # First seed assigns the id
        repo.save_personality("Test Hero", {"play_style": "original"}, personality_id="original_id")
        # JSON re-seed with overwrite=True provides a different id
        seed_data = {"personalities": {"Test Hero": {"id": "new_id", "play_style": "updated"}}}
        seed_file = tmp_path / "reseed.json"
        seed_file.write_text(json.dumps(seed_data))

        repo.seed_personalities_from_json(str(seed_file), overwrite=True)

        # The id must remain stable — once assigned, never changes
        assert repo.resolve_name_to_personality_id("Test Hero") == "original_id"
