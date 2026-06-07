"""Tests for the feature-flag registry (`core/feature_flags.py`).

Covers:
- resolution order: graduated/retired locks, env override, per-env defaults;
- registry integrity (stages and locked-flag defaults are self-consistent);
- back-compat: the `economy_flags` module globals match the registry;
- the **centralization guard**: a registered flag name must not be read via a
  raw `os.environ.get` / `os.getenv` / `_env_flag` outside the registry itself,
  so flags can't quietly sprawl back across the codebase.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import feature_flags as ff
from core.feature_flags import FeatureFlag, Stage, is_enabled, resolve

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files allowed to mention a flag name in an env-reading construct: the registry
# (which never reads env for *other* flags) and the back-compat binding module.
_DECLARATION_FILES = {"core/feature_flags.py", "cash_mode/economy_flags.py"}


def _iter_source_text():
    """(relpath, text) for every .py file in the repo, skipping vendor dirs."""
    skip = ("/node_modules/", "/.git/", "my_poker_face_venv", "/.venv/", "/site-packages/")
    for path in REPO_ROOT.rglob("*.py"):
        s = str(path)
        if any(x in s for x in skip):
            continue
        try:
            yield str(path.relative_to(REPO_ROOT)), path.read_text(errors="ignore")
        except OSError:
            continue


# --- resolution order -------------------------------------------------------


def test_graduated_is_locked_on_even_with_env_off(monkeypatch):
    flag = FeatureFlag("X_GRAD", Stage.GRADUATED, "t")
    monkeypatch.setenv("X_GRAD", "0")
    value, source = resolve(flag)
    assert value is True
    assert source == "locked:graduated"


def test_retired_is_locked_off_even_with_env_on(monkeypatch):
    flag = FeatureFlag("X_RET", Stage.RETIRED, "t")
    monkeypatch.setenv("X_RET", "1")
    value, source = resolve(flag)
    assert value is False
    assert source == "locked:retired"


def test_env_overrides_default(monkeypatch):
    flag = FeatureFlag("X_EXP", Stage.EXPERIMENTAL, "t", dev=False, prod=False)
    monkeypatch.setenv("X_EXP", "yes")
    value, source = resolve(flag)
    assert value is True
    assert source == "env"


def test_unparseable_env_falls_through_to_default(monkeypatch):
    flag = FeatureFlag("X_EXP2", Stage.EXPERIMENTAL, "t", dev=True, prod=False)
    monkeypatch.setenv("X_EXP2", "banana")
    # prod default
    assert resolve(flag, env="prod") == (False, "default:prod")
    # dev default
    assert resolve(flag, env="dev") == (True, "default:dev")


def test_per_env_defaults():
    flag = FeatureFlag("X_BETA", Stage.BETA, "t", dev=True, prod=False)
    assert resolve(flag, env="dev") == (True, "default:dev")
    assert resolve(flag, env="prod") == (False, "default:prod")


def test_is_enabled_unknown_flag_raises():
    with pytest.raises(KeyError):
        is_enabled("DEFINITELY_NOT_A_FLAG")


# --- registry integrity -----------------------------------------------------


def test_registry_is_populated():
    assert len(ff.REGISTRY) > 10


def test_registry_self_consistent():
    """Locked stages must declare a consistent intent; live stages must too."""
    for flag in ff.REGISTRY.values():
        if flag.stage is Stage.RETIRED:
            assert flag.default_for("dev") is False
            assert flag.default_for("prod") is False
        elif flag.stage is Stage.GRADUATED:
            assert flag.default_for("dev") is True
            assert flag.default_for("prod") is True
        elif flag.stage is Stage.STABLE:
            # STABLE == live in prod. dev may lag (e.g. the Director thermostat
            # is prod-only), which the dev/prod split makes visible.
            assert flag.prod is True, flag.name
        elif flag.stage is Stage.BETA:
            # On in dev, off in prod — the baking state.
            assert flag.dev is True and flag.prod is False, flag.name
        elif flag.stage is Stage.EXPERIMENTAL:
            # off in prod by definition (opt-in only).
            assert flag.prod is False, flag.name


def test_duplicate_registration_raises():
    with pytest.raises(ValueError):
        ff.register(FeatureFlag("REGEN_ENABLED", Stage.EXPERIMENTAL, "dup"))


# --- back-compat: economy_flags globals track the registry ------------------


def test_economy_flags_globals_match_registry():
    """The economy_flags module globals must reflect the registry, not stale
    hardcoded values.

    Only env-stable flags are compared: the autouse `_reset_cutover_flags`
    fixture (conftest) pins the env-driven cutover globals to False without
    touching os.environ, so comparing those to a live `is_enabled()` would be
    apples-to-oranges. The flags below are not in that reset set and are not set
    in the test environment, so the import-time global equals the live value.
    """
    from cash_mode import economy_flags

    env_stable = [
        "REGEN_ENABLED",  # experimental, off
        "SIDE_HUSTLE_ENABLED",  # stable, on
        "RAKE_ENABLED",  # graduated, locked on
        "RAKE_PLAYER_TABLES",  # stable, on
        "DOSSIER_SCOUTING_GATE_ENABLED",  # stable, on
        "PRESENCE_AUTHORITY_ENABLED",  # graduated, locked on
        "PRESENCE_SHADOW_WRITE_ENABLED",  # retired, locked off
    ]
    for name in env_stable:
        import os

        if os.environ.get(name) is not None:
            continue  # an ambient override would desync the frozen global
        assert getattr(economy_flags, name) == is_enabled(name), name


# --- centralization guard ---------------------------------------------------


def test_flags_are_only_read_through_the_registry():
    """No registered flag may be read via a raw env construct elsewhere.

    This is what stops flags from sprawling back into scattered
    `os.environ.get(...)` reads. If this fails, declare the flag in
    core/feature_flags.py and read it via `is_enabled(...)` (or the
    economy_flags module global) instead.
    """
    sources = [(rel, text) for rel, text in _iter_source_text() if rel not in _DECLARATION_FILES]
    offenders: dict[str, list[str]] = {}
    for name in ff.REGISTRY:
        patterns = [
            f'os.environ.get("{name}"',
            f"os.environ.get('{name}'",
            f'os.getenv("{name}"',
            f"os.getenv('{name}'",
            f'_env_flag("{name}"',
            f"_env_flag('{name}'",
        ]
        for rel, text in sources:
            for pat in patterns:
                if pat in text:
                    offenders.setdefault(name, []).append(f"{rel}: {pat}")

    assert not offenders, "Flags read via raw env outside the registry:\n" + "\n".join(
        f"  {k}: {v}" for k, v in offenders.items()
    )
