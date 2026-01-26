"""
Variant Configuration for Experiments

Provides shared variant configuration classes used by both tournament experiments
and replay experiments. Supports:
- Model/provider selection
- Personality assignment per variant
- Prompt presets (saved configurations)
- Guidance injection (extra instructions)
- Psychology/commentary toggles
"""

import json
from dataclasses import dataclass
from typing import Dict, Optional, Any

# Import prompt config for type hints
from poker.prompt_config import PromptConfig


@dataclass
class VariantConfig:
    """Configuration for a single experiment variant.

    Used by both tournament experiments (full feature set) and
    replay experiments (simplified - no prompt_config toggles).

    Attributes:
        label: Human-readable name for this variant (required)
        model: LLM model to use (inherits from experiment if not set)
        provider: LLM provider to use (inherits from experiment if not set)
        personality: Personality name to use (for per-variant personality)
        game_mode: Game mode preset ('casual', 'standard', 'pro', 'competitive')
        prompt_preset_id: ID of saved prompt preset to use
        prompt_config: Inline prompt config dict (overrides preset and game_mode)
        guidance_injection: Extra text to append to decision prompts
        reasoning_effort: LLM reasoning effort level
        enable_psychology: Enable tilt + emotional state generation
        enable_commentary: Enable commentary generation
    """
    label: str
    model: Optional[str] = None
    provider: Optional[str] = None
    personality: Optional[str] = None
    game_mode: Optional[str] = None  # 'casual', 'standard', 'pro', 'competitive'
    prompt_preset_id: Optional[int] = None
    prompt_config: Optional[Dict[str, Any]] = None
    guidance_injection: Optional[str] = None
    reasoning_effort: Optional[str] = None
    enable_psychology: bool = False
    enable_commentary: bool = False

    def resolve_prompt_config(self, persistence=None) -> Optional[PromptConfig]:
        """Resolve the effective prompt config for this variant.

        Priority:
        1. Inline prompt_config if set
        2. Load from preset if prompt_preset_id is set
        3. Return None (use experiment/control default)

        Args:
            persistence: GamePersistence instance for loading presets

        Returns:
            PromptConfig instance or None
        """
        # Inline config takes priority
        if self.prompt_config is not None:
            return PromptConfig.from_dict(self.prompt_config)

        # Load from preset if available
        if self.prompt_preset_id is not None and persistence is not None:
            preset = persistence.get_prompt_preset(self.prompt_preset_id)
            if preset and preset.get('prompt_config'):
                config_dict = preset['prompt_config']
                # Handle both dict and JSON string
                if isinstance(config_dict, str):
                    config_dict = json.loads(config_dict)
                return PromptConfig.from_dict(config_dict)

        return None

    def resolve_guidance_injection(self, persistence=None) -> Optional[str]:
        """Resolve the effective guidance injection text.

        Priority:
        1. Inline guidance_injection if set
        2. Load from preset if prompt_preset_id is set
        3. Return None

        Args:
            persistence: GamePersistence instance for loading presets

        Returns:
            Guidance text or None
        """
        # Inline guidance takes priority
        if self.guidance_injection:
            return self.guidance_injection

        # Load from preset if available
        if self.prompt_preset_id is not None and persistence is not None:
            preset = persistence.get_prompt_preset(self.prompt_preset_id)
            if preset and preset.get('guidance_injection'):
                return preset['guidance_injection']

        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'label': self.label,
            'model': self.model,
            'provider': self.provider,
            'personality': self.personality,
            'game_mode': self.game_mode,
            'prompt_preset_id': self.prompt_preset_id,
            'prompt_config': self.prompt_config,
            'guidance_injection': self.guidance_injection,
            'reasoning_effort': self.reasoning_effort,
            'enable_psychology': self.enable_psychology,
            'enable_commentary': self.enable_commentary,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'VariantConfig':
        """Create VariantConfig from dictionary.

        Filters out unknown keys for forward compatibility.
        """
        known_fields = {
            'label', 'model', 'provider', 'personality', 'game_mode',
            'prompt_preset_id', 'prompt_config', 'guidance_injection',
            'reasoning_effort', 'enable_psychology', 'enable_commentary'
        }
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class ControlConfig:
    """Control (baseline) configuration for A/B testing.

    The control config establishes default settings that variants inherit from
    unless explicitly overridden.

    Attributes:
        label: Human-readable name for control group (required)
        model: LLM model (uses experiment default if not set)
        provider: LLM provider (uses experiment default if not set)
        game_mode: Game mode preset ('casual', 'standard', 'pro', 'competitive')
        prompt_config: Baseline prompt config dict (overrides game_mode)
        guidance_injection: Extra text to append to prompts
        reasoning_effort: LLM reasoning effort level
        enable_psychology: Enable tilt + emotional state generation
        enable_commentary: Enable commentary generation
    """
    label: str
    model: Optional[str] = None
    provider: Optional[str] = None
    game_mode: Optional[str] = None  # 'casual', 'standard', 'pro', 'competitive'
    prompt_config: Optional[Dict[str, Any]] = None
    guidance_injection: Optional[str] = None
    reasoning_effort: Optional[str] = None
    enable_psychology: bool = False
    enable_commentary: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'label': self.label,
            'model': self.model,
            'provider': self.provider,
            'game_mode': self.game_mode,
            'prompt_config': self.prompt_config,
            'guidance_injection': self.guidance_injection,
            'reasoning_effort': self.reasoning_effort,
            'enable_psychology': self.enable_psychology,
            'enable_commentary': self.enable_commentary,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'ControlConfig':
        """Create ControlConfig from dictionary."""
        known_fields = {
            'label', 'model', 'provider', 'game_mode', 'prompt_config',
            'guidance_injection', 'reasoning_effort',
            'enable_psychology', 'enable_commentary'
        }
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)


def build_effective_variant_config(
    variant_dict: Dict[str, Any],
    control_dict: Optional[Dict[str, Any]] = None,
    experiment_model: str = "gpt-5-nano",
    experiment_provider: str = "openai",
) -> Dict[str, Any]:
    """Build effective variant config by merging with control and experiment defaults.

    This function applies the inheritance rules:
    1. Variant-specific settings take priority
    2. Control settings are used as fallback for missing variant settings
    3. Experiment-level model/provider are used as final fallback

    game_mode inheritance: variant.game_mode → control.game_mode → None
    When resolved, game_mode provides base PromptConfig that prompt_config overrides.

    Args:
        variant_dict: Raw variant configuration dict
        control_dict: Optional control configuration dict
        experiment_model: Experiment-level default model
        experiment_provider: Experiment-level default provider

    Returns:
        Fully resolved configuration dict ready for use
    """
    # Start with experiment defaults
    effective = {
        'model': experiment_model,
        'provider': experiment_provider,
        'game_mode': None,
        'prompt_config': None,
        'guidance_injection': None,
        'reasoning_effort': None,
        'enable_psychology': False,
        'enable_commentary': False,
        'personality': None,
        'prompt_preset_id': None,
    }

    # Apply control settings if available
    if control_dict:
        for key in ['game_mode', 'prompt_config', 'guidance_injection', 'reasoning_effort',
                    'enable_psychology', 'enable_commentary']:
            if key in control_dict and control_dict[key] is not None:
                effective[key] = control_dict[key]

    # Apply variant-specific settings (explicit None check for prompt_config)
    effective['label'] = variant_dict.get('label', 'Variant')

    # Model/provider: variant overrides experiment default
    if variant_dict.get('model'):
        effective['model'] = variant_dict['model']
    if variant_dict.get('provider'):
        effective['provider'] = variant_dict['provider']

    # Personality: variant-specific, no inheritance
    if variant_dict.get('personality'):
        effective['personality'] = variant_dict['personality']

    # Prompt preset: variant-specific
    if variant_dict.get('prompt_preset_id'):
        effective['prompt_preset_id'] = variant_dict['prompt_preset_id']

    # Game mode: variant overrides control
    if 'game_mode' in variant_dict and variant_dict['game_mode'] is not None:
        effective['game_mode'] = variant_dict['game_mode']

    # Prompt config: use variant if explicitly set, else control
    if 'prompt_config' in variant_dict:
        effective['prompt_config'] = variant_dict['prompt_config']

    # Guidance injection: use variant if set, else control
    if variant_dict.get('guidance_injection'):
        effective['guidance_injection'] = variant_dict['guidance_injection']

    # Reasoning effort: variant or control
    if 'reasoning_effort' in variant_dict:
        effective['reasoning_effort'] = variant_dict['reasoning_effort']

    # Psychology flags: variant or control
    if 'enable_psychology' in variant_dict:
        effective['enable_psychology'] = variant_dict['enable_psychology']
    if 'enable_commentary' in variant_dict:
        effective['enable_commentary'] = variant_dict['enable_commentary']

    return effective
