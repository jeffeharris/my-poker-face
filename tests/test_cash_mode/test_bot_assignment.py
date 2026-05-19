"""Tests for poker.cash_bot_assignment.

Cash mode is sandbox: each personality gets a sticky, deterministic
(bot_type, llm_config) assignment. The mapping order is:
explicit override → poise quantile → safe default.
"""

from __future__ import annotations

import pytest

from poker.cash_bot_assignment import (
    BUCKET_DEFAULTS,
    DEFAULT_BOT_TYPE,
    DEFAULT_LLM_CONFIG,
    BotAssignment,
    assign_bot,
)


def _personality(poise: float | None = None, bot_profile: dict | None = None) -> dict:
    config: dict = {}
    if poise is not None:
        config["anchors"] = {"poise": poise}
    if bot_profile is not None:
        config["bot_profile"] = bot_profile
    return config


class TestAnchorDerivedBuckets:
    """Poise quantile drives the bucket when no override is present."""

    def test_high_poise_routes_to_sharp(self):
        result = assign_bot(_personality(poise=0.80))
        assert result == BotAssignment("sharp", dict(BUCKET_DEFAULTS["sharp"]))

    def test_threshold_poise_065_routes_to_sharp(self):
        # Boundary: >= 0.65 is sharp.
        result = assign_bot(_personality(poise=0.65))
        assert result.bot_type == "sharp"

    def test_mid_poise_routes_to_standard(self):
        result = assign_bot(_personality(poise=0.50))
        assert result == BotAssignment("standard", dict(BUCKET_DEFAULTS["standard"]))

    def test_threshold_poise_040_routes_to_standard(self):
        # Boundary: >= 0.40 is standard.
        result = assign_bot(_personality(poise=0.40))
        assert result.bot_type == "standard"

    def test_low_poise_routes_to_chaos(self):
        result = assign_bot(_personality(poise=0.20))
        assert result == BotAssignment("chaos", dict(BUCKET_DEFAULTS["chaos"]))


class TestOverride:
    """config_json.bot_profile overrides the anchor-derived bucket."""

    def test_override_wins_over_anchors(self):
        # poise 0.80 would route to sharp, but override forces chaos.
        result = assign_bot(_personality(
            poise=0.80,
            bot_profile={"bot_type": "chaos"},
        ))
        assert result.bot_type == "chaos"
        assert result.llm_config == BUCKET_DEFAULTS["chaos"]

    def test_override_can_swap_llm_provider(self):
        result = assign_bot(_personality(
            poise=0.20,
            bot_profile={
                "bot_type": "sharp",
                "provider": "openai",
                "model": "gpt-5-nano",
            },
        ))
        assert result == BotAssignment(
            "sharp",
            {"provider": "openai", "model": "gpt-5-nano"},
        )

    def test_partial_override_keeps_bucket_default(self):
        # Override provides bot_type only — provider/model fall back to bucket.
        result = assign_bot(_personality(bot_profile={"bot_type": "sharp"}))
        assert result.llm_config == BUCKET_DEFAULTS["sharp"]

    def test_unknown_bot_type_in_override_falls_through(self):
        # Falls through to anchor-derived (here: standard).
        result = assign_bot(_personality(
            poise=0.50,
            bot_profile={"bot_type": "wizard"},
        ))
        assert result.bot_type == "standard"

    def test_unknown_bot_type_with_no_anchors_uses_default(self):
        result = assign_bot(_personality(bot_profile={"bot_type": "wizard"}))
        assert result == BotAssignment(DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG))


class TestFallback:
    """Missing/malformed input falls back to the safe default."""

    def test_none_config_uses_default(self):
        assert assign_bot(None) == BotAssignment(
            DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG),
        )

    def test_empty_config_uses_default(self):
        assert assign_bot({}) == BotAssignment(
            DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG),
        )

    def test_anchors_without_poise_uses_default(self):
        result = assign_bot({"anchors": {"ego": 0.5}})
        assert result.bot_type == DEFAULT_BOT_TYPE

    def test_non_numeric_poise_uses_default(self):
        result = assign_bot({"anchors": {"poise": "high"}})
        assert result.bot_type == DEFAULT_BOT_TYPE

    def test_non_dict_config_uses_default(self):
        assert assign_bot("not-a-dict").bot_type == DEFAULT_BOT_TYPE  # type: ignore[arg-type]


class TestDeterminism:
    """Same input → same output, every time. Cash mode relies on stickiness."""

    @pytest.mark.parametrize("poise", [0.10, 0.40, 0.50, 0.65, 0.95])
    def test_repeated_calls_match(self, poise: float):
        first = assign_bot(_personality(poise=poise))
        second = assign_bot(_personality(poise=poise))
        assert first == second

    def test_llm_config_is_a_fresh_dict(self):
        # Callers may mutate the returned config; the helper must not share state.
        a = assign_bot(_personality(poise=0.50))
        a.llm_config["model"] = "mutated"
        b = assign_bot(_personality(poise=0.50))
        assert b.llm_config["model"] != "mutated"
