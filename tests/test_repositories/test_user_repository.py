"""Tests for UserRepository."""
import pytest
from poker.repositories.user_repository import UserRepository


@pytest.fixture
def repo(db_path):
    r = UserRepository(db_path)
    yield r
    r.close()


# --- User CRUD ---

def test_create_google_user(repo):
    user = repo.create_google_user("sub123", "alice@example.com", "Alice")
    assert user["id"] == "google_sub123"
    assert user["email"] == "alice@example.com"
    assert user["name"] == "Alice"
    assert user["is_guest"] is False


def test_create_google_user_with_linked_guest(repo):
    user = repo.create_google_user(
        "sub456", "bob@example.com", "Bob",
        picture="https://pic.example.com/bob.jpg",
        linked_guest_id="guest_abc"
    )
    assert user["linked_guest_id"] == "guest_abc"
    assert user["picture"] == "https://pic.example.com/bob.jpg"


def test_get_user_by_id(repo):
    repo.create_google_user("sub1", "u@e.com", "User1")
    found = repo.get_user_by_id("google_sub1")
    assert found is not None
    assert found["email"] == "u@e.com"


def test_get_user_by_id_not_found(repo):
    assert repo.get_user_by_id("nonexistent") is None


def test_get_user_by_email(repo):
    repo.create_google_user("sub2", "find@me.com", "Finder")
    found = repo.get_user_by_email("find@me.com")
    assert found is not None
    assert found["name"] == "Finder"


def test_get_user_by_email_not_found(repo):
    assert repo.get_user_by_email("nobody@nowhere.com") is None


def test_get_user_by_linked_guest(repo):
    repo.create_google_user("sub3", "linked@e.com", "Linked", linked_guest_id="guest_xyz")
    found = repo.get_user_by_linked_guest("guest_xyz")
    assert found is not None
    assert found["id"] == "google_sub3"


def test_get_user_by_linked_guest_not_found(repo):
    assert repo.get_user_by_linked_guest("guest_nope") is None


def test_update_user_last_login(repo):
    repo.create_google_user("sub4", "login@e.com", "Logger")
    user_before = repo.get_user_by_id("google_sub4")
    old_login = user_before["last_login"]

    repo.update_user_last_login("google_sub4")
    user_after = repo.get_user_by_id("google_sub4")
    # last_login should be updated (at least not None)
    assert user_after["last_login"] is not None
    assert user_after["last_login"] >= old_login


# --- Rate-limiting helpers ---

def test_game_creation_time_round_trip(repo):
    repo.create_google_user("sub5", "rate@e.com", "RateLimited")
    assert repo.get_last_game_creation_time("google_sub5") is None

    repo.update_last_game_creation_time("google_sub5", 1700000000.0)
    assert repo.get_last_game_creation_time("google_sub5") == 1700000000.0


def test_count_user_games_empty(repo):
    assert repo.count_user_games("nobody") == 0


# --- Group / RBAC ---

def test_get_all_users(repo):
    repo.create_google_user("s1", "a@e.com", "A")
    repo.create_google_user("s2", "b@e.com", "B")
    users = repo.get_all_users()
    assert len(users) >= 2
    emails = [u["email"] for u in users]
    assert "a@e.com" in emails
    assert "b@e.com" in emails


def test_assign_and_get_user_groups(repo):
    repo.create_google_user("grp1", "grp@e.com", "GroupUser")
    # 'user' group is auto-assigned on creation
    groups = repo.get_user_groups("google_grp1")
    assert "user" in groups


def test_assign_user_to_admin_group(repo):
    repo.create_google_user("adm1", "admin@e.com", "Admin")
    success = repo.assign_user_to_group("google_adm1", "admin", assigned_by="test")
    assert success is True
    groups = repo.get_user_groups("google_adm1")
    assert "admin" in groups


def test_assign_guest_to_admin_raises(repo):
    with pytest.raises(ValueError, match="Guest users cannot"):
        repo.assign_user_to_group("guest_123", "admin")


def test_assign_to_nonexistent_group(repo):
    repo.create_google_user("ng1", "ng@e.com", "NoGroup")
    assert repo.assign_user_to_group("google_ng1", "nonexistent_group") is False


def test_assign_nonexistent_user_raises(repo):
    with pytest.raises(ValueError, match="does not exist"):
        repo.assign_user_to_group("google_doesnt_exist", "user")


def test_remove_user_from_group(repo):
    repo.create_google_user("rm1", "rm@e.com", "RemoveMe")
    groups_before = repo.get_user_groups("google_rm1")
    assert "user" in groups_before

    removed = repo.remove_user_from_group("google_rm1", "user")
    assert removed is True
    groups_after = repo.get_user_groups("google_rm1")
    assert "user" not in groups_after


def test_remove_user_from_group_not_in(repo):
    repo.create_google_user("rm2", "rm2@e.com", "NotIn")
    assert repo.remove_user_from_group("google_rm2", "admin") is False


def test_get_user_permissions(repo):
    repo.create_google_user("perm1", "perm@e.com", "PermUser")
    perms = repo.get_user_permissions("google_perm1")
    # 'user' group should have some permissions
    assert isinstance(perms, list)


def test_count_users_in_group(repo):
    repo.create_google_user("cnt1", "cnt@e.com", "Counter")
    count = repo.count_users_in_group("user")
    assert count >= 1


def test_get_all_groups(repo):
    groups = repo.get_all_groups()
    assert len(groups) > 0
    names = [g["name"] for g in groups]
    assert "user" in names
    assert "admin" in names


# --- User stats ---

def test_get_user_stats(repo):
    repo.create_google_user("st1", "stats@e.com", "StatsUser")
    stats = repo.get_user_stats("google_st1")
    assert stats["total_cost"] == 0
    assert stats["hands_played"] == 0
    assert stats["games_completed"] == 0


# --- Transfer ---

def test_transfer_guest_to_user(repo):
    # Create a target user
    repo.create_google_user("tgt1", "target@e.com", "Target")
    # Transfer from a guest (no games to transfer, but should not error)
    transferred = repo.transfer_guest_to_user("guest_old", "google_tgt1", "Target")
    assert transferred == 0


# --- Admin initialization ---

def test_initialize_admin_from_env_no_env(repo, monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    assert repo.initialize_admin_from_env() is None


def test_initialize_admin_from_env_email_not_found(repo, monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "nobody@example.com")
    assert repo.initialize_admin_from_env() is None


def test_initialize_admin_from_env_success(repo, monkeypatch):
    repo.create_google_user("adm_init", "init_admin@e.com", "InitAdmin")
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "init_admin@e.com")
    result = repo.initialize_admin_from_env()
    assert result == "google_adm_init"
    groups = repo.get_user_groups("google_adm_init")
    assert "admin" in groups


def test_initialize_admin_from_env_guest_id(repo, monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "guest_special")
    result = repo.initialize_admin_from_env()
    # guest_special should get admin group assigned
    assert result == "guest_special"
    groups = repo.get_user_groups("guest_special")
    assert "admin" in groups


def test_initialize_admin_idempotent(repo, monkeypatch):
    repo.create_google_user("adm_idem", "idem@e.com", "Idempotent")
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "idem@e.com")
    repo.initialize_admin_from_env()
    # Second call should still return user_id
    result = repo.initialize_admin_from_env()
    assert result == "google_adm_idem"
