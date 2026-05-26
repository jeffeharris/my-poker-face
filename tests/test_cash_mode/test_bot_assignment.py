"""Tests for poker.cash_bot_assignment.

Career mode ships one core controller: ``sharp`` (the tiered solver bot).
Every non-fish personality plays tiered unless it carries an explicit
``bot_profile`` override. A future ``mode="sandbox"`` re-enables the
poise-based engine MIX (chaos/standard/sharp) for a let-it-happen mode.

Mapping order:
  career  : override → tiered default
  sandbox : override → poise quantile → tiered default
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


class TestCareerDefault:
    """Career mode is always tiered, regardless of poise (no override)."""

    def test_default_bot_type_is_sharp(self):
        assert DEFAULT_BOT_TYPE == "sharp"

    @pytest.mark.parametrize("poise", [0.10, 0.40, 0.50, 0.65, 0.95])
    def test_career_ignores_poise_and_returns_tiered(self, poise: float):
        result = assign_bot(_personality(poise=poise))
        assert result == BotAssignment("sharp", dict(BUCKET_DEFAULTS["sharp"]))

    def test_career_is_the_default_mode(self):
        # Explicit mode="career" matches the no-arg default.
        assert assign_bot(_personality(poise=0.20)) == assign_bot(
            _personality(poise=0.20), mode="career"
        )


class TestSandboxMode:
    """mode='sandbox' re-enables the poise-derived engine mix."""

    def test_high_poise_routes_to_sharp(self):
        result = assign_bot(_personality(poise=0.80), mode="sandbox")
        assert result == BotAssignment("sharp", dict(BUCKET_DEFAULTS["sharp"]))

    def test_threshold_poise_065_routes_to_sharp(self):
        result = assign_bot(_personality(poise=0.65), mode="sandbox")
        assert result.bot_type == "sharp"

    def test_mid_poise_routes_to_standard(self):
        result = assign_bot(_personality(poise=0.50), mode="sandbox")
        assert result == BotAssignment("standard", dict(BUCKET_DEFAULTS["standard"]))

    def test_threshold_poise_040_routes_to_standard(self):
        result = assign_bot(_personality(poise=0.40), mode="sandbox")
        assert result.bot_type == "standard"

    def test_low_poise_routes_to_chaos(self):
        result = assign_bot(_personality(poise=0.20), mode="sandbox")
        assert result == BotAssignment("chaos", dict(BUCKET_DEFAULTS["chaos"]))

    def test_sandbox_without_poise_falls_back_to_tiered(self):
        result = assign_bot(_personality(), mode="sandbox")
        assert result.bot_type == DEFAULT_BOT_TYPE


class TestOverride:
    """config_json.bot_profile overrides the default in BOTH modes."""

    def test_override_wins_in_career(self):
        # Career would return tiered, but the override forces chaos.
        result = assign_bot(_personality(poise=0.80, bot_profile={"bot_type": "chaos"}))
        assert result.bot_type == "chaos"
        assert result.llm_config == BUCKET_DEFAULTS["chaos"]

    def test_override_wins_over_sandbox_poise(self):
        # poise 0.80 would route to sharp in sandbox, but override forces chaos.
        result = assign_bot(
            _personality(poise=0.80, bot_profile={"bot_type": "chaos"}),
            mode="sandbox",
        )
        assert result.bot_type == "chaos"

    def test_override_can_swap_llm_provider(self):
        result = assign_bot(
            _personality(
                bot_profile={
                    "bot_type": "sharp",
                    "provider": "openai",
                    "model": "gpt-5-nano",
                },
            )
        )
        assert result == BotAssignment(
            "sharp",
            {"provider": "openai", "model": "gpt-5-nano"},
        )

    def test_partial_override_keeps_bucket_default(self):
        result = assign_bot(_personality(bot_profile={"bot_type": "chaos"}))
        assert result.llm_config == BUCKET_DEFAULTS["chaos"]

    def test_unknown_bot_type_in_override_falls_through_to_career_default(self):
        result = assign_bot(_personality(poise=0.50, bot_profile={"bot_type": "wizard"}))
        assert result.bot_type == DEFAULT_BOT_TYPE

    def test_unknown_bot_type_with_no_anchors_uses_default(self):
        result = assign_bot(_personality(bot_profile={"bot_type": "wizard"}))
        assert result == BotAssignment(DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG))


class TestFallback:
    """Missing/malformed input falls back to the tiered default."""

    def test_none_config_uses_default(self):
        assert assign_bot(None) == BotAssignment(DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG))

    def test_empty_config_uses_default(self):
        assert assign_bot({}) == BotAssignment(DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG))

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
        a = assign_bot(_personality(poise=0.50))
        a.llm_config["model"] = "mutated"
        b = assign_bot(_personality(poise=0.50))
        assert b.llm_config["model"] != "mutated"
