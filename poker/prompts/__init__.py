"""
Prompt template management with YAML-based storage.

This module provides externalized prompt templates stored as YAML files
with hot-reload support in development mode.

Security features:
- Path traversal prevention via name validation
- YAML safe_load() to prevent arbitrary code execution
- Template schema validation
"""

import re
from pathlib import Path
from typing import Optional

# Valid template name pattern: lowercase letters and underscores only
TEMPLATE_NAME_PATTERN = re.compile(r'^[a-z_]+$')

# Directory containing YAML template files
PROMPTS_DIR = Path(__file__).parent


def validate_template_name(name: str) -> bool:
    """
    Validate a template name to prevent path traversal attacks.

    Args:
        name: Template name to validate

    Returns:
        True if valid, False otherwise

    Security:
        Only allows lowercase letters and underscores.
        Prevents: ../../../etc/passwd, name.yaml.bak, etc.
    """
    if not name or not isinstance(name, str):
        return False
    return bool(TEMPLATE_NAME_PATTERN.match(name))


def get_template_path(name: str) -> Optional[Path]:
    """
    Get the safe path for a template file.

    Args:
        name: Template name (without .yaml extension)

    Returns:
        Path object if valid and within prompts dir, None otherwise

    Security:
        Validates name AND verifies resolved path is within PROMPTS_DIR.
    """
    if not validate_template_name(name):
        return None

    yaml_path = PROMPTS_DIR / f"{name}.yaml"

    # Resolve to absolute path and verify it's within prompts dir
    try:
        resolved = yaml_path.resolve()
        prompts_resolved = PROMPTS_DIR.resolve()

        # Check path is within prompts directory
        if not str(resolved).startswith(str(prompts_resolved)):
            return None

        return yaml_path
    except (OSError, ValueError):
        return None


# Template schema definitions: required sections per template type
TEMPLATE_SCHEMAS = {
    'poker_player': {
        'required_sections': ['persona_details', 'strategy', 'direction', 'response_format', 'reminder'],
        'description': 'Main AI player persona template',
    },
    'decision': {
        'required_sections': ['instruction'],
        'description': 'Player decision-making template',
    },
    'end_of_hand_commentary': {
        'required_sections': ['context', 'instruction'],
        'description': 'Post-hand reflection template',
    },
    # Quick chat templates
    'quick_chat_tilt': {
        'required_sections': ['instruction'],
        'description': 'Manipulation tactic: tilt opponent',
    },
    'quick_chat_false_confidence': {
        'required_sections': ['instruction'],
        'description': 'Manipulation tactic: false confidence',
    },
    'quick_chat_doubt': {
        'required_sections': ['instruction'],
        'description': 'Manipulation tactic: plant doubt',
    },
    'quick_chat_goad': {
        'required_sections': ['instruction'],
        'description': 'Manipulation tactic: goad into action',
    },
    'quick_chat_mislead': {
        'required_sections': ['instruction'],
        'description': 'Manipulation tactic: mislead about hand',
    },
    'quick_chat_befriend': {
        'required_sections': ['instruction'],
        'description': 'Manipulation tactic: build rapport',
    },
    'quick_chat_table': {
        'required_sections': ['instruction'],
        'description': 'Table-wide announcement template',
    },
    # Post-round templates
    'post_round_gloat': {
        'required_sections': ['instruction'],
        'description': 'Winner reaction: gloating',
    },
    'post_round_humble': {
        'required_sections': ['instruction'],
        'description': 'Winner reaction: humble',
    },
    'post_round_salty': {
        'required_sections': ['instruction'],
        'description': 'Loser reaction: salty',
    },
    'post_round_gracious': {
        'required_sections': ['instruction'],
        'description': 'Loser reaction: gracious',
    },
}


def validate_template_schema(name: str, sections: dict) -> tuple[bool, Optional[str]]:
    """
    Validate that a template has all required sections.

    Args:
        name: Template name
        sections: Dict of section_name -> content

    Returns:
        Tuple of (is_valid, error_message)
    """
    if name not in TEMPLATE_SCHEMAS:
        # Unknown template type - allow any sections
        return True, None

    schema = TEMPLATE_SCHEMAS[name]
    required = set(schema['required_sections'])
    provided = set(sections.keys())

    missing = required - provided
    if missing:
        return False, f"Missing required sections: {', '.join(sorted(missing))}"

    # Check for empty sections
    empty = [s for s in required if not sections.get(s, '').strip()]
    if empty:
        return False, f"Empty required sections: {', '.join(sorted(empty))}"

    return True, None


def extract_variables(template_content: str) -> list[str]:
    """
    Extract variable placeholders from template content.

    Args:
        template_content: String containing {variable} placeholders

    Returns:
        Sorted list of unique variable names

    Note:
        Uses simple regex. Does not handle escaped braces or nested structures.
    """
    # Match {word} but not {{word}} (JSON examples)
    variables = set()
    for match in re.finditer(r'(?<!\{)\{(\w+)\}(?!\})', template_content):
        variables.add(match.group(1))
    return sorted(variables)
